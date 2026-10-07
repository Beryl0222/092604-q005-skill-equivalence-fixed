"""等价判定引擎。

在给定"模拟日期 + 目标标准版本"下，解释一名学生为何满足或缺少某项
（复合）能力。判定维度：

1. 覆盖程度：叶子能力只有 complete 证明可认定；多个 partial 局部证明
   只展示为进展，不能越权拼成完整资格。复合能力可由一条 complete
   整体证明认定，或要求全部组成节点各自被完整证明。
2. 先修依赖：能力自身先修 + 等价关系声明先修，递归满足。
3. 版本兼容：换版后内容指纹（content_hash）未变的能力，旧版本下取得
   的证据、教师按指纹授权、企业认可仍然有效；指纹变化即"受影响能力"，
   旧证明不能支持新认定，必须重审。
4. 设备前提、教师授权、企业认可有效期/撤回逐一把关。
5. 去重：同一 competency_id 无论来自软件测试、轨道车辆还是智慧安防，
   只计一项能力，重复证明仅列出不重复计学分。
6. 历史依据：已授予学分在保护期内始终保留（protected），不依赖当前
   实时判定；超出保护期才回到实时判定。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .clock import parse_day
from .domain import COMPLETE, PARTIAL, STUDENT_GRADUATED


@dataclass
class Claim:
    """一项证据对某能力提出的一条覆盖主张（来自等价关系或课程目标）。"""

    evidence_id: str
    source_type: str
    source_id: str
    coverage: str
    claim_ref: str
    prereq_ids: tuple[str, ...] = ()
    active: bool = True
    inactive_reason: str = ""
    via_course_objective: bool = False


@dataclass
class _Ctx:
    student: dict[str, Any]
    target_ref: str
    as_of: str
    claims: dict[str, list[Claim]] = field(default_factory=dict)
    evidences: dict[str, dict[str, Any]] = field(default_factory=dict)
    awards: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


class Engine:
    def __init__(self, store) -> None:
        self.store = store

    # -- 主张收集 -----------------------------------------------------------
    def _collect_claims(self, student_id: str) -> tuple[dict[str, list[Claim]], dict[str, dict[str, Any]]]:
        import json

        claims: dict[str, list[Claim]] = {}
        evidences: dict[str, dict[str, Any]] = {}
        for ev in self.store.evidence_for_student(student_id):
            if not ev["valid"]:
                continue
            evidences[ev["evidence_id"]] = ev
            seen_sources: set[tuple[str, str]] = set()
            # 证据自身的等价关系 + 共享来源（如课程）上的等价关系
            for source_type, source_id in (
                (ev["source_type"], ev["evidence_id"]),
                (ev["source_type"], ev["source_ref"]),
            ):
                if (source_type, source_id) in seen_sources:
                    continue
                seen_sources.add((source_type, source_id))
                for link in self.store.links_for_source(source_type, source_id):
                    claims.setdefault(link["competency_id"], []).append(
                        Claim(
                            evidence_id=ev["evidence_id"],
                            source_type=link["source_type"],
                            source_id=link["source_id"],
                            coverage=link["coverage"],
                            claim_ref=link["standard_ref"],
                            prereq_ids=tuple(json.loads(link["prereq_ids"])),
                            active=bool(link["active"]),
                            inactive_reason=link["reason"],
                        )
                    )
            # 课程目标本身即为课程来源的覆盖主张
            if ev["source_type"] == "course":
                for obj in self.store.course_objectives(course_id=ev["source_ref"]):
                    claims.setdefault(obj["competency_id"], []).append(
                        Claim(
                            evidence_id=ev["evidence_id"],
                            source_type="course",
                            source_id=obj["course_id"],
                            coverage=obj["coverage"],
                            claim_ref=obj["standard_ref"],
                            via_course_objective=True,
                        )
                    )
        return claims, evidences

    # -- 主入口 -------------------------------------------------------------
    def evaluate_student(self, student_id: str, target_ref: str, as_of: str,
                         targets: list[str] | None = None) -> dict[str, Any]:
        student = self.store.get_student(student_id)
        if student is None:
            raise KeyError(f"未知学生 {student_id}")
        claims, evidences = self._collect_claims(student_id)
        awards: dict[str, list[dict[str, Any]]] = {}
        for award in self.store.awards_for_student(student_id):
            awards.setdefault(award["competency_id"], []).append(award)
        ctx = _Ctx(student=student, target_ref=target_ref, as_of=as_of,
                   claims=claims, evidences=evidences, awards=awards)
        if targets is None:
            program = self.store.get_program(student["program_id"])
            targets = [r["competency_id"] for r in self.store.requirements(program["program_id"])]
        results = [
            self._status(cid, ctx, ()) for cid in targets
        ]
        return {
            "student_id": student_id,
            "student_name": student["name"],
            "program_id": student["program_id"],
            "status": student["status"],
            "standard_ref": target_ref,
            "as_of": as_of,
            "graduated": student["status"] == STUDENT_GRADUATED,
            "competencies": results,
            "all_satisfied": all(r["satisfied"] for r in results),
        }

    # -- 单节点判定 ----------------------------------------------------------
    def _status(self, cid: str, ctx: _Ctx, stack: tuple[str, ...]) -> dict[str, Any]:
        if cid in stack:
            node = self.store.get_competency(cid)
            return {
                "competency_id": cid, "name": node["name"] if node else cid,
                "composite": bool(node and node["is_composite"]),
                "live_satisfied": False, "satisfied": False, "coverage": "cycle",
                "proofs": [], "partial_proofs": [], "duplicate_complete_proofs": 0,
                "cycle": True, "basis": "cycle", "award": None,
            }
        node = self.store.get_competency(cid)
        claims = [c for c in ctx.claims.get(cid, [])]
        proof_results = [self._check_claim(c, cid, ctx) for c in claims if c.coverage == COMPLETE]
        partial_results = [self._check_claim(c, cid, ctx) for c in claims if c.coverage == PARTIAL]
        live_ok = any(p["ok"] for p in proof_results)

        result: dict[str, Any] = {
            "competency_id": cid,
            "name": node["name"] if node else cid,
            "composite": bool(node and node["is_composite"]),
            "live_satisfied": live_ok,
            "satisfied": live_ok,
            "coverage": COMPLETE if live_ok else (PARTIAL if partial_results else "none"),
            "proofs": proof_results,
            "partial_proofs": partial_results,
            "duplicate_complete_proofs": max(0, len(proof_results) - 1),
        }

        if result["composite"]:
            parts = self.store.parts(ctx.target_ref, cid)
            part_results = [self._status(p["child_id"], ctx, stack + (cid,)) for p in parts]
            result["parts"] = part_results
            # 整体没有 complete 证明时，要求各组成部分全部完整证明
            if not live_ok:
                result["satisfied"] = len(part_results) > 0 and all(p["satisfied"] for p in part_results)

        # 已授予学分：保护期内保留历史依据
        award_state = self._award_state(cid, ctx, result["satisfied"])
        result["award"] = award_state
        if award_state and award_state["protected"]:
            result["satisfied"] = True
            result["coverage"] = "complete"
            result["basis"] = "protected"
        elif result["satisfied"]:
            result["basis"] = "live"
        else:
            result["basis"] = "missing"
        return result

    def _award_state(self, cid: str, ctx: _Ctx, live_ok: bool) -> dict[str, Any] | None:
        items = ctx.awards.get(cid)
        if not items:
            return None
        award = max(items, key=lambda a: a["awarded_date"])
        # 保护期只在 [授予日, 保护截止日] 内有效：审查日早于授予日时不可回溯
        protected = (parse_day(award["awarded_date"]) <= parse_day(ctx.as_of)
                     <= parse_day(award["protection_until"]))
        return {
            "award_id": award["award_id"],
            "rule_id": award["rule_id"],
            "course_id": award["course_id"],
            "credits": award["credits"],
            "standard_ref": award["standard_ref"],
            "awarded_date": award["awarded_date"],
            "protection_until": award["protection_until"],
            "protected": protected,
            "historical_conclusion": award["conclusion"],
            "retained": protected or live_ok,
        }

    # -- 一条主张的逐一把关 ---------------------------------------------------
    def _check_claim(self, claim: Claim, cid: str, ctx: _Ctx) -> dict[str, Any]:
        failures: list[dict[str, str]] = []
        ev = ctx.evidences[claim.evidence_id]

        if not claim.active:
            failures.append({"code": "link_inactive", "detail": claim.inactive_reason or "等价关系已失效"})
        if parse_day(ev["obtained_date"]) > parse_day(ctx.as_of):
            failures.append({"code": "evidence_future", "detail": "证据取得日期晚于审查日"})

        standard = self.store.get_standard(ctx.target_ref)
        if standard and parse_day(standard["effective_date"]) > parse_day(ctx.as_of):
            failures.append({
                "code": "standard_not_effective",
                "detail": f"{ctx.target_ref} 自 {standard['effective_date']} 起生效，审查日尚未生效",
            })

        target_hash = self.store.revision_hash(cid, ctx.target_ref)
        claim_hash = self.store.revision_hash(cid, claim.claim_ref)
        if target_hash is None:
            failures.append({"code": "competency_removed", "detail": f"{cid} 不在 {ctx.target_ref} 中"})
        elif claim.claim_ref != ctx.target_ref and claim_hash != target_hash:
            failures.append({"code": "version_changed",
                             "detail": f"依据版本 {claim.claim_ref} 的能力内容已在 {ctx.target_ref} 变更，需重审"})

        # 先修依赖（标准声明 ∪ 等价关系声明）
        prereq_ids = tuple(sorted(
            set(self.store.prerequisites(ctx.target_ref, cid)) | set(claim.prereq_ids)
        ))
        prereq_results = []
        for pid in prereq_ids:
            sub = self._status(pid, ctx, (cid,))
            prereq_results.append({"competency_id": pid, "satisfied": sub["satisfied"]})
            if not sub["satisfied"]:
                failures.append({"code": "prerequisite_missing", "detail": pid})

        # 实训设备前提
        used = (self.store.equipment_used(claim.source_type, claim.source_id)
                | self.store.equipment_used(claim.source_type, claim.evidence_id))
        needed_rows = self.store.equipment_prereqs(cid)
        seen_eq: set[str] = set()
        needed = []
        for e in needed_rows:
            if e["equipment_id"] not in seen_eq:
                seen_eq.add(e["equipment_id"])
                needed.append(e)
        missing_equipment = [e for e in needed if e["equipment_id"] not in used]
        for e in missing_equipment:
            failures.append({"code": "equipment_missing", "detail": f"{e['equipment_id']} {e['equipment_name']}"})

        # 教师授权：精确版本授权，或按能力指纹授权（指纹未变即可跨版本）
        teacher_ok = False
        teacher_detail = None
        for auth in self.store.authorizations(cid):
            if auth["revoked"]:
                continue
            hash_match = bool(auth["competency_hash"]) and auth["competency_hash"] == target_hash
            ref_match = auth["standard_ref"] == ctx.target_ref
            if not (ref_match or hash_match):
                continue
            if parse_day(auth["granted_date"]) > parse_day(ev["obtained_date"]):
                teacher_detail = {"code": "authorization_after_evidence", "teacher_id": auth["teacher_id"]}
                continue
            teacher_ok = True
            teacher_detail = {"teacher_id": auth["teacher_id"], "teacher_name": auth["teacher_name"],
                              "authorization_id": auth["authorization_id"],
                              "match": "hash" if hash_match and not ref_match else "version"}
            break
        if not teacher_ok:
            failures.append({"code": "teacher_unauthorized",
                             "detail": teacher_detail["code"] if teacher_detail else "无有效教师授权"})

        # 企业认可：active 且在认可期内，版本相同或能力指纹一致
        recognition = self._recognition_check(cid, ctx.target_ref, target_hash, ctx.as_of)
        if not recognition["ok"]:
            failures.append({"code": recognition["code"], "detail": recognition["detail"]})

        return {
            "evidence_id": claim.evidence_id,
            "evidence_title": ev["title"],
            "source_type": claim.source_type,
            "source_id": claim.source_id,
            "coverage": claim.coverage,
            "claim_standard_ref": claim.claim_ref,
            "obtained_date": ev["obtained_date"],
            "prerequisites": prereq_results,
            "equipment": {
                "required": [{"equipment_id": e["equipment_id"], "name": e["equipment_name"]} for e in needed],
                "missing": [{"equipment_id": e["equipment_id"], "name": e["equipment_name"]}
                            for e in missing_equipment],
            },
            "teacher": teacher_detail,
            "recognition": recognition,
            "ok": not failures,
            "failures": failures,
        }

    def _recognition_check(self, cid: str, target_ref: str, target_hash: str | None,
                           as_of: str) -> dict[str, Any]:
        records = self.store.recognitions_for(cid)
        active_window = []
        withdrawn = []
        expired = []
        for r in records:
            hash_match = r["standard_ref"] == target_ref or (
                target_hash is not None and self.store.revision_hash(cid, r["standard_ref"]) == target_hash
            )
            if not hash_match:
                continue
            if r["status"] == "withdrawn":
                withdrawn.append(r)
            elif parse_day(r["valid_from"]) <= parse_day(as_of) <= parse_day(r["valid_until"]):
                active_window.append(r)
            else:
                expired.append(r)
        if active_window:
            r = active_window[0]
            return {"ok": True, "enterprise_id": r["enterprise_id"], "enterprise_name": r["enterprise_name"],
                    "recognition_id": r["recognition_id"], "valid_until": r["valid_until"]}
        if withdrawn:
            r = withdrawn[0]
            return {"ok": False, "code": "enterprise_withdrawn",
                    "detail": f"企业 {r['enterprise_name']} 已于 {r['withdrawn_at']} 撤回认可，阻止新认定"}
        if expired:
            r = expired[0]
            return {"ok": False, "code": "recognition_expired",
                    "detail": f"企业认可已于 {r['valid_until']} 到期"}
        return {"ok": False, "code": "recognition_missing", "detail": "没有任何企业认可"}

    # -- 补足路径 -------------------------------------------------------------
    def supplement_plan(self, student_id: str, target_ref: str, as_of: str) -> dict[str, Any]:
        evaluation = self.evaluate_student(student_id, target_ref, as_of)
        plan_items: list[dict[str, Any]] = []
        for status in evaluation["competencies"]:
            if status["satisfied"]:
                continue
            item = self._gap_item(status, target_ref, as_of, ())
            if item:
                plan_items.append(item)
        # 拓扑排序：先修能力排在前面
        ordered = self._order_by_prereq(plan_items)
        return {
            "student_id": student_id,
            "standard_ref": target_ref,
            "as_of": as_of,
            "complete": not ordered,
            "steps": ordered,
        }

    def _gap_item(self, status: dict[str, Any], target_ref: str, as_of: str,
                  stack: tuple[str, ...]) -> dict[str, Any] | None:
        cid = status["competency_id"]
        if cid in stack:
            return None
        blockers: list[str] = []
        best = None
        for p in status["proofs"] + status["partial_proofs"]:
            for f in p["failures"]:
                if f["code"] in ("enterprise_withdrawn", "recognition_expired", "recognition_missing"):
                    blockers.append(f["detail"])
                if f["code"] == "version_changed":
                    blockers.append(f["detail"])
            best = p
        # 复合能力：缺口下钻到组成节点
        sub_steps: list[dict[str, Any]] = []
        if status["composite"]:
            for part in status.get("parts", []):
                if not part["satisfied"]:
                    sub = self._gap_item(part, target_ref, as_of, stack + (cid,))
                    if sub:
                        sub_steps.append(sub)
        candidates = [
            {"course_id": o["course_id"], "title": o["title"], "coverage": o["coverage"]}
            for o in self.store.course_objectives(standard_ref=target_ref)
            if o["competency_id"] == cid
        ]
        prereq_gaps = []
        if best:
            prereq_gaps = [p["competency_id"] for p in best["prerequisites"] if not p["satisfied"]]
        return {
            "competency_id": cid,
            "name": status["name"],
            "composite": status["composite"],
            "blockers": sorted(set(blockers)),
            "candidate_courses": candidates,
            "missing_prerequisites": prereq_gaps,
            "equipment_required": best["equipment"]["required"] if best else [],
            "sub_steps": sub_steps,
        }

    def _order_by_prereq(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {i["competency_id"]: i for i in items}

        def flat_ids(item: dict[str, Any]) -> list[str]:
            ids = []
            for sub in item.get("sub_steps", []):
                ids.extend(flat_ids(sub))
            ids.append(item["competency_id"])
            return ids

        ordered_ids: list[str] = []

        def visit(i: dict[str, Any]) -> None:
            for pid in i.get("missing_prerequisites", []):
                if pid in by_id and pid not in ordered_ids:
                    visit(by_id[pid])
            for sub in i.get("sub_steps", []):
                if sub["competency_id"] not in ordered_ids:
                    visit(sub)
            if i["competency_id"] not in ordered_ids:
                ordered_ids.append(i["competency_id"])

        for i in items:
            visit(i)
        return [by_id[cid] for cid in ordered_ids if cid in by_id]
