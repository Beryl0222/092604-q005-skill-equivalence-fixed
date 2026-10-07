"""技能标准课程等价网业务规则测试。

场景：软件测试、轨道车辆、智慧安防三个赛项共享能力、标准换版、
企业撤回、保护期与批量断点续跑。
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from skill_equivalence.api import handle
from skill_equivalence.cli import main as cli_main
from skill_equivalence.service import Service, ServiceError
from skill_equivalence.store import Store

from scenario_world import (
    C_CCTV, C_PLAN, C_SAFETY, C_SCRIPT, C_SW_FULL, RAIL2023, SEC2023, SW2022, SW2024,
    build_world,
)


def status_of(explain: dict, cid: str) -> dict:
    def walk(nodes):
        for node in nodes:
            if node["competency_id"] == cid:
                return node
            found = walk(node.get("parts", []))
            if found:
                return found
        return None

    found = walk(explain["competencies"])
    if found is None:
        raise KeyError(cid)
    return found


class 去重与局部证明测试(unittest.TestCase):
    def setUp(self):
        self.svc = build_world()

    def test_同一能力多赛项证明只计一次学分(self):
        result = self.svc.award_credit("s-001", "rule-safety", as_of="2023-07-15")
        self.assertTrue(result["awarded"])
        # 旧课程名下的第二条学分规则：拒绝重复计算
        dup = self.svc.award_credit("s-001", "rule-safety-dup", as_of="2023-07-16")
        self.assertFalse(dup["awarded"])
        self.assertEqual(dup["conclusion"], "denied")
        awards = self.svc.store.awards_for_student("s-001")
        self.assertEqual(
            [a["competency_id"] for a in awards].count(C_SAFETY), 1,
            "电气安全能力无论来自课程还是三个赛项，只能计一次学分",
        )
        explain = self.svc.explain_student("s-001", SW2022, "2023-07-15")
        safety = status_of(explain, C_SAFETY)
        complete_proofs = [p for p in safety["proofs"] if p["ok"]]
        self.assertGreaterEqual(len(complete_proofs), 2)
        self.assertEqual(safety["duplicate_complete_proofs"], len(complete_proofs) - 1)

    def test_多个局部证明不能越权拼成完整资格(self):
        explain = self.svc.explain_student("s-002", SW2022, "2024-03-01")
        full = status_of(explain, C_SW_FULL)
        plan = next(p for p in full["parts"] if p["competency_id"] == C_PLAN)
        self.assertFalse(plan["satisfied"])
        self.assertEqual(plan["coverage"], "partial")
        self.assertEqual(len(plan["partial_proofs"]), 2)
        self.assertFalse(full["satisfied"])
        self.assertFalse(explain["all_satisfied"])

    def test_缺设备核验和先修不满足时不能认定(self):
        explain = self.svc.explain_student("s-002", SW2022, "2024-03-01")
        full = status_of(explain, C_SW_FULL)
        script = next(p for p in full["parts"] if p["competency_id"] == C_SCRIPT)
        codes = {f["code"] for p in script["proofs"] for f in p["failures"]}
        self.assertIn("equipment_missing", codes)
        self.assertIn("prerequisite_missing", codes)


class 复合能力与先修测试(unittest.TestCase):
    def setUp(self):
        self.svc = build_world()

    def test_张三满足软件测试复合能力(self):
        explain = self.svc.explain_student("s-001", SW2022, "2023-07-15")
        full = status_of(explain, C_SW_FULL)
        self.assertTrue(full["composite"])
        self.assertTrue(full["satisfied"])
        self.assertTrue(explain["all_satisfied"])

    def test_轨道车辆复合能力同样成立(self):
        explain = self.svc.explain_student("s-005", RAIL2023, "2024-09-01")
        self.assertTrue(explain["all_satisfied"])


class 模拟日期与标准生效测试(unittest.TestCase):
    def setUp(self):
        self.svc = build_world()

    def test_标准未生效不能据此认定(self):
        # 2024 版 2024-09-01 才生效；2024-08-31 用 2024 目标判定时证明被拦下
        explain = self.svc.explain_student("s-001", SW2024, "2024-08-31",
                                           competency_id=C_PLAN)
        plan = status_of(explain, C_PLAN)
        codes = {f["code"] for p in plan["proofs"] for f in p["failures"]}
        self.assertIn("standard_not_effective", codes)

    def test_证据取得日晚于审查日不算数(self):
        explain = self.svc.explain_student("s-001", SW2022, "2023-01-01")
        # 张三的证据全部取得于 2023 年 5 月之后
        self.assertFalse(explain["all_satisfied"])


class 换版与保护期测试(unittest.TestCase):
    def setUp(self):
        self.svc = build_world()

    def test_换版只圈定受影响能力(self):
        impact = self.svc.upgrade_impact(SW2022, SW2024)
        self.assertEqual(impact["changed"], [C_PLAN])
        self.assertEqual(impact["added"], ["c_ai_test"])
        # 受影响叶子沿复合结构上卷
        self.assertIn(C_SW_FULL, impact["impacted_competencies"])
        self.assertNotIn(C_SCRIPT, impact["impacted_competencies"])
        self.assertNotIn(C_SAFETY, impact["impacted_competencies"])

    def test_换版影响具体课程教师培养方案(self):
        impact = self.svc.upgrade_impact(SW2022, SW2024)
        course_ids = {c["course_id"] for c in impact["courses_to_update"]}
        self.assertIn("SW101", course_ids)       # 测试方案课程
        self.assertIn("SW200", course_ids)       # 综合项目（复合能力上卷）
        self.assertNotIn("SW102", course_ids)    # 自动化脚本内容未变
        teacher_ids = {t["authorization_id"] for t in impact["teacher_authorizations_to_recheck"]}
        self.assertIn("auth-wang-plan", teacher_ids)       # 王老师的方案授权需重审
        self.assertNotIn("auth-li-script", teacher_ids)    # 李老师脚本授权不受影响
        programs = {p["program_id"] for p in impact["programs_to_revise"]}
        self.assertEqual(programs, {"prog-soft"})
        # 历史授予被列出且明确保留
        preserved = {a["award_id"] for a in impact["historical_awards_preserved"]}
        self.assertIn("award:s-001:rule-plan", preserved)

    def test_未变化能力跨版本连续有效(self):
        explain = self.svc.explain_student("s-001", SW2024, "2025-09-01")
        script = status_of(explain, C_SCRIPT)
        self.assertTrue(any(p["ok"] for p in script["proofs"]))
        ok = next(p for p in script["proofs"] if p["ok"])
        # 李老师 2022 版授权按能力指纹延续到 2024 版
        self.assertEqual(ok["teacher"]["match"], "hash")

    def test_保护期内保留历史依据_到期后回到实时重审(self):
        # 张三的 c_test_plan 学分保护期 730 天，约至 2025-07 中
        in_window = self.svc.explain_student("s-001", SW2024, "2025-06-01")
        plan_in = status_of(in_window, C_PLAN)
        self.assertTrue(plan_in["satisfied"])
        self.assertEqual(plan_in["basis"], "protected")
        self.assertTrue(plan_in["award"]["protected"])

        after_window = self.svc.explain_student("s-001", SW2024, "2025-09-01")
        plan_out = status_of(after_window, C_PLAN)
        self.assertFalse(plan_out["satisfied"], "保护期外且能力已变更、无新授权，应失去依据")
        codes = {f["code"] for p in plan_out["proofs"] for f in p["failures"]}
        self.assertIn("version_changed", codes)
        self.assertIn("teacher_unauthorized", codes)

    def test_学分授予记录不可篡改(self):
        conn = self.svc.store.connection
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE credit_awards SET credits=99 WHERE award_id='award:s-001:rule-plan'")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM credit_awards WHERE award_id='award:s-001:rule-plan'")
        conn.rollback()
        award = self.svc.store.find_award("s-001", "rule-plan")
        self.assertEqual(award["credits"], 4.0)
        basis = json.loads(award["basis_json"])
        self.assertIn("status_at_award", basis)


class 企业撤回测试(unittest.TestCase):
    def setUp(self):
        self.svc = build_world(withdrawn=True)

    def test_撤回阻止新的等价认定(self):
        with self.assertRaises(ServiceError):
            self.svc.register_link(
                "lk-new-cctv", "course", "SEC999", C_CCTV, "complete",
                "std_security", "2023",
            )

    def test_撤回后在校生新认定失败且进入受影响名单(self):
        explain = self.svc.explain_student("s-003", SEC2023, "2025-03-01")
        cctv = status_of(explain, C_CCTV)
        self.assertFalse(cctv["satisfied"])
        codes = {f["code"] for p in cctv["proofs"] for f in p["failures"]}
        self.assertIn("enterprise_withdrawn", codes)

    def test_往届毕业生记录不变且不生成补足路径(self):
        protected = self.svc.protected_credit_status("s-004", "2025-03-01")
        self.assertEqual(len(protected), 1)
        self.assertTrue(protected[0]["within_protection"])
        self.assertTrue(protected[0]["historical_record_unchanged"])
        with self.assertRaises(ServiceError):
            self.svc.supplement_path("s-004", SEC2023, "2025-03-01")

    def test_在校生补足路径指出缺口与可选课程(self):
        plan = self.svc.supplement_path("s-003", SEC2023, "2025-03-01")
        self.assertFalse(plan["complete"])
        step = next(s for s in plan["steps"] if s["competency_id"] == C_CCTV)
        self.assertTrue(any("撤回" in b for b in step["blockers"]))
        self.assertEqual({c["course_id"] for c in step["candidate_courses"]}, {"SEC101"})


class 批量审查断点续跑测试(unittest.TestCase):
    def test_中断后只处理剩余项_跨进程续跑(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "net.db")
            build_world(db)
            svc = Service(Store(db))
            started = svc.start_batch("review", ["s-001", "s-002", "s-005"],
                                      SW2022, batch_id="b-1")
            self.assertEqual(started["total"], 3)
            part = svc.resume_batch("b-1", as_of="2024-03-01", max_steps=1)
            self.assertFalse(part["completed"])
            self.assertEqual(part["done"], 1)
            self.assertEqual(part["processed_now"], 1)
            self.assertEqual(part["remaining"], ["s-002", "s-005"])
            # 模拟进程重启：新服务实例从同一数据库续跑
            svc2 = Service(Store(db))
            part2 = svc2.resume_batch("b-1", as_of="2024-03-01")
            self.assertTrue(part2["completed"])
            self.assertEqual(part2["processed_now"], 2)
            ids = {r["student_id"] for r in part2["results"]}
            self.assertEqual(ids, {"s-001", "s-002", "s-005"})
            # 已完成的批次再跑直接回放，不重复处理
            again = svc2.resume_batch("b-1", as_of="2024-03-01")
            self.assertTrue(again["completed"])
            self.assertEqual(len(again["results"]), 3)

    def test_补足批次跳过毕业生且不改历史(self):
        svc = build_world(withdrawn=True)
        svc.start_batch("supplement", ["s-003", "s-004"], SEC2023, batch_id="b-sup")
        out = svc.resume_batch("b-sup", as_of="2025-03-01")
        self.assertTrue(out["completed"])
        s3 = next(r for r in out["results"] if r["student_id"] == "s-003")
        self.assertFalse(s3["complete"])
        s4 = next(r for r in out["results"] if r["student_id"] == "s-004")
        self.assertTrue(s4["skipped"] == "graduated")


class JSON入口测试(unittest.TestCase):
    def test_health与register兼容(self):
        out = json.loads(handle('{"action":"health"}', Service(Store())))
        self.assertEqual(out["status"], "ok")

    def test_持久化与request_id幂等(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "net.db")
            payload = {"action": "register", "record_id": "r-1", "owner_id": "o-1",
                       "db": db, "request_id": "req-1"}
            raw = json.dumps(payload, ensure_ascii=False)
            first = handle(raw)
            second = handle(raw)  # 重复提交不再次写入
            self.assertEqual(first, second)
            svc = Service(Store(db))
            self.assertEqual(svc.find("r-1")["owner_id"], "o-1")

    def test_错误动作返回异常(self):
        with self.assertRaises(ValueError):
            handle('{"action":"nope"}', Service(Store()))


class 命令行测试(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "net.db")
        build_world(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_explain人类可读输出(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli_main(["explain", "--db", self.db, "--student", "s-001",
                             "--standard", SW2022, "--date", "2023-07-15"])
        text = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("软件测试综合实施", text)
        self.assertIn("全部能力已满足", text)

    def test_explain缺口返回码2(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli_main(["explain", "--db", self.db, "--student", "s-002",
                             "--standard", SW2022, "--date", "2024-03-01"])
        self.assertEqual(code, 2)
        self.assertIn("不能越权拼接", buf.getvalue())

    def test_impact与supplement与batch子命令(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli_main(["impact", "--db", self.db, "--old", SW2022,
                                       "--new", SW2024]), 0)
        impact_text = buf.getvalue()
        self.assertIn("SW101", impact_text)
        self.assertIn("王老师", impact_text)
        self.assertIn("prog-soft", impact_text)

        buf = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            self.assertEqual(cli_main(["batch", "--db", self.db, "--kind", "review",
                                       "--standard", SW2022, "--students", "s-001", "s-002",
                                       "--batch-id", "b-cli", "--max-steps", "1",
                                       "--date", "2024-03-01"]), 1)
        self.assertIn("断点已保存", err.getvalue())


if __name__ == "__main__":
    unittest.main()
