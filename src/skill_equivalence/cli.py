"""命令行入口。

用法：

  # 兼容旧入口：标准输入单条 JSON 请求
  printf '%s' '{"action":"health"}' | python3 -m skill_equivalence.cli

  # 解释某名学生为何满足/缺少一项（或全部）复合能力
  python3 -m skill_equivalence.cli explain --db net.db --student s-1 \
      --standard std_swtest@2024 --date 2025-09-01 [--competency c-xxx]

  # 一次换版具体影响哪些课程、教师和培养方案
  python3 -m skill_equivalence.cli impact --db net.db \
      --old std_swtest@2022 --new std_swtest@2024

  # 企业撤回后未毕业学生的补足路径
  python3 -m skill_equivalence.cli supplement --db net.db --student s-1 \
      --standard std_swtest@2024 --date 2025-09-01

  # 批量审查（断点续跑：中断后用同一 --batch-id 重跑即可）
  python3 -m skill_equivalence.cli batch --db net.db --kind review \
      --standard std_swtest@2024 --students s-1 s-2 --batch-id b-1 --max-steps 1

  # 批量装载登记数据
  python3 -m skill_equivalence.cli seed --db net.db --file seed.json
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .api import handle, handle_bundle
from .clock import FixedClock
from .service import Service
from .store import Store

_FAILURE_TEXT = {
    "link_inactive": "等价关系已失效",
    "evidence_future": "证据取得日期晚于审查日",
    "standard_not_effective": "目标标准版本在审查日尚未生效",
    "competency_removed": "目标版本已移除该能力",
    "version_changed": "能力内容随换版变更，需要重审",
    "prerequisite_missing": "先修能力未满足",
    "equipment_missing": "缺少实训设备核验",
    "teacher_unauthorized": "缺少有效的教师授权（或授权晚于取证）",
    "enterprise_withdrawn": "企业已撤回认可，新认定被阻止",
    "recognition_expired": "企业认可已超出认可期",
    "recognition_missing": "没有企业认可",
}


def _service(args: argparse.Namespace) -> Service:
    store = Store(args.db)
    clock = FixedClock(args.date) if getattr(args, "date", None) else None
    return Service(store, clock)  # type: ignore[arg-type]


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


# -- explain ----------------------------------------------------------------
def _render_proof(proof: dict[str, Any], indent: str = "    ") -> list[str]:
    lines: list[str] = []
    head = f"{indent}{'✔' if proof['ok'] else '✘'} 证据 {proof['evidence_id']}《{proof['evidence_title']}》" \
           f"（覆盖={proof['coverage']}，依据版本={proof['claim_standard_ref']}，取证={proof['obtained_date']}）"
    lines.append(head)
    if proof["teacher"]:
        t = proof["teacher"]
        if "teacher_name" in t:
            lines.append(f"{indent}  - 教师授权：{t['teacher_name']}（{t['match']}匹配）")
        else:
            lines.append(f"{indent}  - 教师授权：无效（{t.get('code')}）")
    rec = proof["recognition"]
    if rec.get("ok"):
        lines.append(f"{indent}  - 企业认可：{rec['enterprise_name']}，认可期至 {rec['valid_until']}")
    for f in proof["failures"]:
        text = _FAILURE_TEXT.get(f["code"], f["code"])
        lines.append(f"{indent}  - 阻断：{text}（{f['detail']}）")
    missing_prereq = [p["competency_id"] for p in proof["prerequisites"] if not p["satisfied"]]
    if missing_prereq:
        lines.append(f"{indent}  - 未满足先修：{', '.join(missing_prereq)}")
    for eq in proof["equipment"]["missing"]:
        lines.append(f"{indent}  - 缺少设备：{eq['equipment_id']} {eq['name']}")
    return lines


def _render_status(status: dict[str, Any], level: int = 0) -> list[str]:
    pad = "  " * level
    mark = "✔" if status["satisfied"] else ("◐" if status["coverage"] == "partial" else "✘")
    basis = {"live": "实时依据满足", "protected": "保护期内保留历史依据",
             "missing": "尚缺依据"}.get(status.get("basis", ""), status.get("basis", ""))
    lines = [f"{pad}{mark} [{status['competency_id']}] {status['name']}"
             f"（{'复合' if status['composite'] else '叶子'}，{basis}）"]
    if status.get("award"):
        a = status["award"]
        state = "保护期内，历史学分保留" if a["protected"] else "已超出保护期，按现版本重新判定"
        lines.append(f"{pad}  · 学分 {a['credits']}（{a['rule_id']}，{a['standard_ref']}）：{state}，"
                     f"保护至 {a['protection_until']}")
    if status["duplicate_complete_proofs"]:
        lines.append(f"{pad}  · 另有 {status['duplicate_complete_proofs']} 项重复完整证明，只计一次，不重复计学分")
    if status["partial_proofs"] and not status["satisfied"]:
        lines.append(f"{pad}  · {len(status['partial_proofs'])} 项局部(partial)证明只能显示进展，"
                     "不能越权拼接为完整资格")
    for proof in status["proofs"]:
        lines.extend(_render_proof(proof, pad + "  "))
    for part in status.get("parts", []):
        lines.extend(_render_status(part, level + 1))
    return lines


def cmd_explain(args: argparse.Namespace) -> int:
    service = _service(args)
    data = service.explain_student(args.student, args.standard, args.date, args.competency)
    if args.json:
        _print_json(data)
        return 0
    print(f"学生 {data['student_name']}（{data['student_id']}，{data['program_id']}）"
          f"在 {data['as_of']} 对照 {data['standard_ref']}：")
    for status in data["competencies"]:
        print("\n".join(_render_status(status)))
    print()
    print("结论：全部能力已满足，可以毕业" if data["all_satisfied"] else "结论：存在缺口，暂不满足全部培养要求")
    return 0 if data["all_satisfied"] else 2


# -- impact -----------------------------------------------------------------
def cmd_impact(args: argparse.Namespace) -> int:
    service = _service(args)
    data = service.upgrade_impact(args.old, args.new)
    if args.json:
        _print_json(data)
        return 0
    print(f"换版 {data['old_ref']} → {data['new_ref']}（重审范围仅限受影响能力）")
    print(f"受影响能力：{', '.join(data['affected_competencies']) or '无'}")
    print(f"内容变更：{', '.join(data['changed']) or '无'}；移除：{', '.join(data['removed']) or '无'}"
          f"；新增：{', '.join(data['added']) or '无'}")
    print(f"未受影响（直接沿用）：{', '.join(data['unaffected_competencies']) or '无'}\n")
    print("需更新的课程目标：")
    for c in data["courses_to_update"]:
        print(f"  - {c['course_id']}《{c['title']}》→ {c['competency_id']}（{c['reason']}）")
    if not data["courses_to_update"]:
        print("  无")
    print("需重审的教师授权：")
    for t in data["teacher_authorizations_to_recheck"]:
        print(f"  - {t['authorization_id']} {t['teacher_name']} → {t['competency_id']}（{t['reason']}）")
    if not data["teacher_authorizations_to_recheck"]:
        print("  无")
    if data["teacher_authorizations_retained"]:
        print("按能力指纹延续的教师授权：")
        for t in data["teacher_authorizations_retained"]:
            print(f"  - {t['authorization_id']} {t['teacher_name']} → {t['competency_id']}")
    print("需修订的培养方案：")
    for p in data["programs_to_revise"]:
        print(f"  - {p['program_id']}《{p['name']}》受影响要求：{', '.join(p['affected_requirements'])}")
    if not data["programs_to_revise"]:
        print("  无")
    print("历史学分授予（记录不改，保护期内继续有效）：")
    for a in data["historical_awards_preserved"]:
        print(f"  - {a['award_id']} 学生 {a['student_id']} 能力 {a['competency_id']}，保护至 {a['protection_until']}")
    if not data["historical_awards_preserved"]:
        print("  无")
    return 0


# -- supplement --------------------------------------------------------------
def _render_step(step: dict[str, Any], level: int = 0) -> list[str]:
    pad = "  " * level
    lines = [f"{pad}- [{step['competency_id']}] {step['name']}"
             + ("（复合，需先补齐下列组成）" if step["composite"] else "")]
    if step["candidate_courses"]:
        for c in step["candidate_courses"]:
            tag = "完整覆盖" if c["coverage"] == "complete" else "仅局部覆盖（不能单独据此认定）"
            lines.append(f"{pad}    可选课程：{c['course_id']}《{c['title']}》（{tag}）")
    else:
        lines.append(f"{pad}    暂无新版本课程目标，需要新设课程或更新等价关系")
    for b in step["blockers"]:
        lines.append(f"{pad}    阻断原因：{b}")
    if step["missing_prerequisites"]:
        lines.append(f"{pad}    先补先修：{', '.join(step['missing_prerequisites'])}")
    for eq in step["equipment_required"]:
        lines.append(f"{pad}    设备前提：{eq['equipment_id']} {eq['name']}")
    for sub in step.get("sub_steps", []):
        lines.extend(_render_step(sub, level + 1))
    return lines


def cmd_supplement(args: argparse.Namespace) -> int:
    service = _service(args)
    data = service.supplement_path(args.student, args.standard, args.date)
    if args.json:
        _print_json(data)
        return 0
    print(f"学生 {args.student} 在 {data['as_of']} 对照 {data['standard_ref']} 的补足路径：")
    if data["complete"]:
        print("  已满足全部要求，无需补足。")
        return 0
    for step in data["steps"]:
        print("\n".join(_render_step(step)))
    return 0


# -- batch -------------------------------------------------------------------
def cmd_batch(args: argparse.Namespace) -> int:
    service = _service(args)
    existing = service.store.get_batch(args.batch_id) if args.batch_id else None
    if existing is None:
        if not args.students:
            raise SystemExit("新建批处理必须提供 --students；续跑请提供已有的 --batch-id")
        started = service.start_batch(args.kind, args.students, args.standard, batch_id=args.batch_id)
        batch_id = started["batch_id"]
    else:
        batch_id = args.batch_id
    data = service.resume_batch(batch_id, args.date, args.max_steps)
    _print_json(data)
    if data["completed"]:
        return 0
    print(f"（断点已保存：{data['done']}/{data['total']}，剩余 {data['remaining']}；"
          f"用同一 --batch-id 续跑）", file=sys.stderr)
    return 1


# -- seed / raw ---------------------------------------------------------------
def cmd_seed(args: argparse.Namespace) -> int:
    store = Store(args.db)
    service = Service(store)
    with open(args.file, encoding="utf-8") as fh:
        print(handle_bundle(fh.read(), service))
    return 0


def cmd_raw(_args: argparse.Namespace) -> int:
    raw = sys.stdin.read().strip() or '{"action":"health"}'
    print(handle(raw))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skill_equivalence", description="技能标准课程等价网命令行")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("explain", help="解释学生为何满足/缺少能力")
    p.add_argument("--db", required=True)
    p.add_argument("--student", required=True)
    p.add_argument("--standard", required=True, help="目标标准版本，如 std_swtest@2024")
    p.add_argument("--date", help="模拟日期 YYYY-MM-DD")
    p.add_argument("--competency", help="只解释单项能力")
    p.add_argument("--json", action="store_true", help="输出原始 JSON")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("impact", help="换版影响分析")
    p.add_argument("--db", required=True)
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_impact)

    p = sub.add_parser("supplement", help="未毕业学生的补足路径")
    p.add_argument("--db", required=True)
    p.add_argument("--student", required=True)
    p.add_argument("--standard", required=True)
    p.add_argument("--date")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_supplement)

    p = sub.add_parser("batch", help="批量审查（支持断点续跑）")
    p.add_argument("--db", required=True)
    p.add_argument("--kind", choices=["review", "recheck", "supplement"], required=True)
    p.add_argument("--standard", required=True)
    p.add_argument("--students", nargs="*")
    p.add_argument("--batch-id")
    p.add_argument("--max-steps", type=int)
    p.add_argument("--date")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("seed", help="从 JSON 数组文件批量装载登记数据")
    p.add_argument("--db", required=True)
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_seed)

    parser.set_defaults(func=cmd_raw)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:  # 业务错误以非零码与中文信息呈现
        print(f"错误：{exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
