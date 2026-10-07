"""技能标准课程等价网应用服务。

登记类方法保存赛项标准版本、能力节点、课程、设备前提、教师授权、企业认可、
等价关系、学生、学分规则与培养方案；业务方法处理证据受理、学分授予、标准
换版、企业撤回、断点续跑的批量审查与毕业冻结。

关键不可变约束：

* ``credit_awards`` 仅追加——换版、撤回只改变解释结论，绝不改写或删除授予；
* 已毕业学生的历史记录在撤回时完全不触碰。
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from typing import Any

from . import domain as d
from .clock import Clock
from .evaluation import Evaluator
from .store import Store, date_in_window


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Service:
    def __init__(self, store: Store | None = None, clock: Clock | None = None) -> None:
        self.store = store or Store()
        self.clock = clock or Clock()
        self.evaluator = Evaluator(self.store)

    def _date(self, on_date: str | None) -> str:
        return on_date or self.clock.today()

    # ------------------------------------------------------------------
    # 旧骨架
    # ------------------------------------------------------------------
    def health(self) -> dict[str, str]:
        return {"service": "skill_equivalence", "status": "ok"}

    def register(self, record_id: str, owner_id: str) -> dict[str, str | int]:
        record = d.Record(record_id, owner_id, "draft", 1, self.clock.now())
        self.store.add(record)
        return record.__dict__.copy()

    def find(self, record_id: str) -> dict[str, str | int] | None:
        record = self.store.get(record_id)
        return record.__dict__.copy() if record else None

    # ------------------------------------------------------------------
    # 登记：赛项标准版本 / 能力节点
    # ------------------------------------------------------------------
    def register_standard_version(self, params: dict[str, Any]) -> dict[str, Any]:
        sv = d.StandardVersion(
            standard_id=str(params["standard_id"]), version=str(params["version"]),
            title=str(params.get("title", params["standard_id"])),
            replaces=params.get("replaces"),
            effective_date=str(params["effective_date"]),
        )
        self.store.upsert_standard_version(sv)
        return {"standard_id": sv.standard_id, "version": sv.version,
                "effective_date": sv.effective_date}

    def register_node(self, params: dict[str, Any]) -> dict[str, Any]:
        slots = [(str(s[0]), bool(s[1])) for s in params.get("slots", [])]
        node = d.CompetencyNode(
            node_id=str(params["node_id"]), name=str(params.get("name", params["node_id"])),
            standard_id=str(params["standard_id"]), version=str(params["version"]),
            composite=bool(params.get("composite", False)),
            assembly=str(params.get("assembly", "forbidden")),
            slots=slots,
        )
        if node.assembly not in ("forbidden", "requires_issuer", "slots_allowed"):
            raise ValueError("assembly 只能是 forbidden / requires_issuer / slots_allowed")
        if node.slots and not node.composite:
            raise ValueError("只有复合能力节点才能配置槽位")
        self.store.upsert_node(node)
        return {"node_id": node.node_id, "composite": node.composite,
                "assembly": node.assembly, "slots": [list(s) for s in node.slots]}

    # ------------------------------------------------------------------
    # 登记：课程 / 目标 / 设备 / 教师授权
    # ------------------------------------------------------------------
    def register_course(self, params: dict[str, Any]) -> dict[str, Any]:
        course = d.Course(str(params["course_id"]), str(params.get("name", params["course_id"])),
                          params.get("program_id"))
        self.store.upsert_course(course)
        out: dict[str, Any] = {"course_id": course.course_id, "name": course.name,
                               "objectives": [], "equipment": []}
        for node_id in params.get("objectives", []):
            self.store.add_objective(course.course_id, str(node_id))
        for equipment_id in params.get("equipment_required", []):
            self.store.upsert_equipment_requirement(course.course_id, str(equipment_id))
        out["objectives"] = self.store.objectives(course.course_id)
        out["equipment"] = self.store.equipment_requirements(course.course_id)
        return out

    def add_objective(self, course_id: str, node_id: str) -> dict[str, Any]:
        if self.store.get_course(course_id) is None:
            raise ValueError(f"课程 {course_id} 不存在")
        if self.store.get_node(node_id) is None:
            raise ValueError(f"能力节点 {node_id} 不存在")
        self.store.add_objective(course_id, node_id)
        return {"course_id": course_id, "node_id": node_id}

    def add_equipment(self, course_id: str, equipment_id: str) -> dict[str, Any]:
        if self.store.get_course(course_id) is None:
            raise ValueError(f"课程 {course_id} 不存在")
        self.store.upsert_equipment_requirement(course_id, equipment_id)
        return {"course_id": course_id, "equipment_id": equipment_id,
                "equipment_required": self.store.equipment_requirements(course_id)}

    def add_teacher_authorization(self, params: dict[str, Any]) -> dict[str, Any]:
        course_id = str(params["course_id"])
        if self.store.get_course(course_id) is None:
            raise ValueError(f"课程 {course_id} 不存在")
        auth = d.TeacherAuthorization(
            teacher_id=str(params["teacher_id"]), course_id=course_id,
            valid_from=str(params["valid_from"]),
            valid_until=params.get("valid_until"),
        )
        self.store.add_authorization(auth)
        return {"teacher_id": auth.teacher_id, "course_id": course_id,
                "valid_from": auth.valid_from, "valid_until": auth.valid_until}

    # ------------------------------------------------------------------
    # 登记：企业认可
    # ------------------------------------------------------------------
    def add_endorsement(self, params: dict[str, Any]) -> dict[str, Any]:
        node_id = str(params["node_id"])
        if self.store.get_node(node_id) is None:
            raise ValueError(f"能力节点 {node_id} 不存在")
        endorsement = d.EnterpriseEndorsement(
            enterprise_id=str(params["enterprise_id"]), node_id=node_id,
            valid_from=str(params["valid_from"]), valid_until=params.get("valid_until"),
        )
        endorsement_id = self.store.add_endorsement(endorsement)
        return {"endorsement_id": endorsement_id, "enterprise_id": endorsement.enterprise_id,
                "node_id": node_id, "valid_from": endorsement.valid_from,
                "valid_until": endorsement.valid_until}

    # ------------------------------------------------------------------
    # 登记：等价关系
    # ------------------------------------------------------------------
    def add_equivalence(self, params: dict[str, Any]) -> dict[str, Any]:
        node_id = str(params["node_id"])
        node = self.store.get_node(node_id)
        if node is None:
            raise ValueError(f"能力节点 {node_id} 不存在")
        source_type = params.get("source_type", d.SOURCE_COURSE)
        source_id = str(params.get("source_id") or params.get("course_id") or "")
        if not source_id:
            raise ValueError("必须提供 course_id 或 source_id")
        if source_type == d.SOURCE_COURSE and self.store.get_course(source_id) is None:
            raise ValueError(f"课程 {source_id} 不存在")
        coverage = str(params.get("coverage", d.FULL))
        if coverage not in (d.FULL, d.PARTIAL):
            raise ValueError("coverage 必须是 full 或 partial")
        share = float(params.get("coverage_share", 1.0 if coverage == d.FULL else 0.5))
        if coverage == d.FULL:
            share = 1.0
        if not 0 < share < 1 and coverage == d.PARTIAL:
            raise ValueError("局部证明的 coverage_share 必须在 (0,1) 区间")
        issue_scope = str(params.get("issue_scope", "slot"))
        if issue_scope == "composite" and not node.composite:
            raise ValueError("issue_scope=composite 只能用于复合能力节点")
        if coverage == d.FULL and node.composite and issue_scope != "composite" \
                and node.assembly == "forbidden":
            raise ValueError("禁止拼装的复合能力只能登记完整签发（issue_scope=composite）等价关系")
        eq = d.Equivalence(
            equivalence_id=str(params.get("equivalence_id") or _new_id("eq")),
            source_type=source_type, source_id=source_id, node_id=node_id,
            coverage=coverage, prerequisites=tuple(str(p) for p in params.get("prerequisites", [])),
            stack_partials=bool(params.get("stack_partials", False)),
            issue_scope=issue_scope, coverage_share=share,
            standard_id=params.get("standard_id", node.standard_id),
            version=params.get("version", node.version),
            enterprise_id=params.get("enterprise_id"),
            valid_from=str(params.get("valid_from", "1970-01-01")),
            valid_until=params.get("valid_until"),
        )
        for prereq in eq.prerequisites:
            if self.store.get_node(prereq) is None:
                raise ValueError(f"先修依赖节点 {prereq} 不存在")
        self.store.add_equivalence(eq)
        if source_type == d.SOURCE_COURSE:
            self.store.add_objective(source_id, node_id)
        return {
            "equivalence_id": eq.equivalence_id, "source_type": eq.source_type,
            "source_id": eq.source_id, "node_id": node_id, "coverage": coverage,
            "coverage_share": share, "stack_partials": eq.stack_partials,
            "issue_scope": issue_scope, "prerequisites": list(eq.prerequisites),
            "enterprise_id": eq.enterprise_id, "valid_from": eq.valid_from,
            "valid_until": eq.valid_until, "status": d.EQUIV_ACTIVE,
        }

    # ------------------------------------------------------------------
    # 登记：学生 / 学分规则 / 培养方案
    # ------------------------------------------------------------------
    def register_student(self, params: dict[str, Any]) -> dict[str, Any]:
        program_id = params.get("program_id")
        if program_id and self.store.get_program(str(program_id)) is None:
            raise ValueError(f"培养方案 {program_id} 不存在")
        student = d.Student(str(params["student_id"]), str(params.get("name", params["student_id"])),
                            program_id)
        self.store.upsert_student(student)
        return {"student_id": student.student_id, "name": student.name,
                "program_id": student.program_id, "graduated": False}

    def register_credit_rule(self, params: dict[str, Any]) -> dict[str, Any]:
        node_id = str(params["node_id"])
        if self.store.get_node(node_id) is None:
            raise ValueError(f"能力节点 {node_id} 不存在")
        rule = d.CreditRule(
            rule_id=str(params.get("rule_id") or _new_id("rule")), node_id=node_id,
            credits=float(params["credits"]),
            protection_until=params.get("protection_until"),
            protection_days=params.get("protection_days"),
        )
        self.store.upsert_credit_rule(rule)
        return {"rule_id": rule.rule_id, "node_id": node_id, "credits": rule.credits,
                "protection_until": rule.protection_until,
                "protection_days": rule.protection_days}

    def register_program(self, params: dict[str, Any]) -> dict[str, Any]:
        required = tuple(str(n) for n in params.get("required_nodes", []))
        for node_id in required:
            if self.store.get_node(node_id) is None:
                raise ValueError(f"毕业要求节点 {node_id} 不存在")
        program = d.Program(str(params["program_id"]), str(params.get("name", params["program_id"])),
                            required, str(params.get("version", "1")))
        self.store.upsert_program(program)
        return {"program_id": program.program_id, "name": program.name,
                "required_nodes": list(required), "version": program.version}

    # ------------------------------------------------------------------
    # 证据包受理
    # ------------------------------------------------------------------
    def submit_evidence(self, params: dict[str, Any]) -> dict[str, Any]:
        evidence_id = str(params.get("evidence_id") or _new_id("ev"))
        student_id = str(params["student_id"])
        course_id = str(params["course_id"])
        evidence_date = str(params["evidence_date"])
        teacher_id = str(params["teacher_id"])
        equipment = tuple(str(e) for e in params.get("equipment", []))

        student = self.store.get_student(student_id)
        if student is None:
            raise ValueError(f"学生 {student_id} 不存在")
        if student.graduated:
            raise ValueError(f"学生 {student_id} 已毕业冻结，不再受理新证据（往届记录不可改）")
        course = self.store.get_course(course_id)
        if course is None:
            raise ValueError(f"课程 {course_id} 不存在")

        target = params.get("node_id")
        candidate_nodes = [target] if target else [
            eq.node_id for eq in self.store.equivalences_for_course(course_id)]
        candidate_nodes = sorted(set(n for n in candidate_nodes if n))

        reasons: list[str] = []
        if not self.store.authorization_valid(teacher_id, course_id, evidence_date):
            reasons.append(f"教师 {teacher_id} 在 {evidence_date} 对课程 {course_id} 无有效授权")
        needed = set(self.store.equipment_requirements(course_id))
        missing_equipment = sorted(needed - set(equipment))
        if missing_equipment:
            reasons.append(f"缺少实训设备前提证据：{', '.join(missing_equipment)}")

        chosen: d.Equivalence | None = None
        designated = params.get("equivalence_id")
        if not reasons:
            if designated:
                eq = self.store.get_equivalence(str(designated))
                if eq is None or eq.source_id != course_id:
                    reasons.append(f"等价关系 {designated} 不存在或不属于课程 {course_id}")
                else:
                    candidate_nodes = [eq.node_id]
                    if not self.evaluator.channel_effective_on(eq, evidence_date):
                        reasons.append(f"等价关系 {eq.equivalence_id} 在 {evidence_date} 未生效或已失效")
                    elif not self.evaluator._endorsement_ok(
                            eq.enterprise_id, eq.node_id, evidence_date):
                        reasons.append(
                            f"企业 {eq.enterprise_id} 对 {eq.node_id} 的认可在 {evidence_date} 无效")
                    elif not date_in_window(evidence_date, eq.valid_from, eq.valid_until):
                        reasons.append(f"等价关系 {eq.equivalence_id} 不在有效期内")
                    else:
                        chosen = eq
            else:
                for node_id in candidate_nodes:
                    for eq in self.store.equivalences_for_course(course_id):
                        if eq.node_id != node_id:
                            continue
                        if not self.evaluator.channel_effective_on(eq, evidence_date):
                            reasons.append(f"等价关系 {eq.equivalence_id} 在 {evidence_date} 未生效或已失效")
                            continue
                        if not self.evaluator._endorsement_ok(
                                eq.enterprise_id, eq.node_id, evidence_date):
                            reasons.append(
                                f"企业 {eq.enterprise_id} 对 {eq.node_id} 的认可在 {evidence_date} 无效")
                            continue
                        if not date_in_window(evidence_date, eq.valid_from, eq.valid_until):
                            reasons.append(f"等价关系 {eq.equivalence_id} 不在有效期内")
                            continue
                        chosen = eq
                        break
                    if chosen is not None:
                        break
                if chosen is None and not reasons:
                    reasons.append(f"课程 {course_id} 没有可覆盖目标能力的等价关系")

        accepted = chosen is not None
        evidence = d.EvidencePack(
            evidence_id=evidence_id, student_id=student_id, course_id=course_id,
            evidence_date=evidence_date, teacher_id=teacher_id, equipment=equipment,
            accepted=False, reasons=tuple(reasons),
        )
        self.store.add_evidence(evidence)
        if accepted and chosen is not None:
            self.store.set_evidence_decision(
                evidence_id, True, ["受理成功：教师授权、设备前提与企业认可在取证日均有效"],
                chosen.node_id, chosen.coverage, chosen.equivalence_id, chosen.coverage_share)
        else:
            self.store.set_evidence_decision(
                evidence_id, False, reasons, None, None)
        stored = self.store.get_evidence(evidence_id)
        return {
            "evidence_id": evidence_id, "student_id": student_id, "course_id": course_id,
            "accepted": accepted,
            "node_id": stored.node_id if stored else None,
            "coverage": stored.coverage if stored else None,
            "equivalence_id": stored.equivalence_id if stored else None,
            "reasons": list(stored.reasons) if stored else reasons,
        }

    # ------------------------------------------------------------------
    # 学分授予（写入仅追加的历史依据）
    # ------------------------------------------------------------------
    def award_credit(self, params: dict[str, Any]) -> dict[str, Any]:
        student_id = str(params["student_id"])
        node_id = str(params["node_id"])
        on_date = self._date(params.get("on_date"))
        if self.store.get_student(student_id) is None:
            raise ValueError(f"学生 {student_id} 不存在")
        node = self.store.get_node(node_id)
        if node is None:
            raise ValueError(f"能力节点 {node_id} 不存在")
        rule = self.store.credit_rule_for_node(node_id)
        if rule is None:
            raise ValueError(f"能力 {node_id} 没有学分规则，无法授予")

        existing = [a for a in self.store.awards_for_student(student_id, node_id) if not a.revoked]
        for award in existing:
            explained = self.evaluator.explain_award(award, on_date)
            if explained["status"] in ("protected", "current"):
                return {"awarded": False, "reason": "该能力学分已授予且依据仍然有效",
                        "award_id": award.award_id, "status": explained["status"]}

        evaluation = self.evaluator.evaluate(student_id, node_id, on_date)
        if not evaluation.satisfied:
            return {"awarded": False, "reason": "不满足认定条件",
                    "evaluation": evaluation.as_dict()}

        # 选定授予依据：完整覆盖证据 → 授权叠加的局部证明 → slots_allowed 槽位组合。
        basis_evidence = None
        eq_id: str | None = None
        standard_id = node.standard_id
        version = node.version
        enterprise_id = None
        partial_basis: list[str] = []
        for ev in self.store.accepted_evidence(student_id):
            if ev.node_id != node_id or ev.evidence_date > on_date:
                continue
            eq = self.store.get_equivalence(ev.equivalence_id) if ev.equivalence_id else None
            if eq and ev.coverage == d.FULL:
                basis_evidence, eq_id, enterprise_id = ev, eq.equivalence_id, eq.enterprise_id
                standard_id, version = eq.standard_id or standard_id, eq.version or version
                break
        if basis_evidence is None and not node.composite:
            partials = [ev for ev in self.store.accepted_evidence(student_id)
                        if ev.node_id == node_id and ev.evidence_date <= on_date
                        and ev.coverage == d.PARTIAL]
            if (partials and sum(ev.coverage_share for ev in partials) >= 1.0
                    and all((self.store.get_equivalence(ev.equivalence_id)
                             or d.Equivalence("", "", "", "", "")).stack_partials
                            for ev in partials if ev.equivalence_id)):
                partial_basis = [ev.evidence_id for ev in partials]
                first_eq = self.store.get_equivalence(partials[0].equivalence_id)
                if first_eq:
                    eq_id = "stack:" + "+".join(
                        sorted({ev.equivalence_id for ev in partials if ev.equivalence_id}))
                    enterprise_id = first_eq.enterprise_id
                    standard_id = first_eq.standard_id or standard_id
                    version = first_eq.version or version
        if basis_evidence is None and not partial_basis:
            slot_basis = []
            for slot_id, _ in node.slots:
                slot_ev = next((e for e in self.store.accepted_evidence(student_id)
                                if e.node_id == slot_id and e.evidence_date <= on_date
                                and e.coverage == d.FULL), None)
                if slot_ev is None:
                    break
                slot_basis.append(slot_ev.equivalence_id)
            if node.composite and node.assembly == "slots_allowed" and len(slot_basis) == len(node.slots):
                eq_id = f"slots:{node_id}"
            else:
                return {"awarded": False, "reason": "判定满足但缺少完整签发证据，拒绝越权授予",
                        "evaluation": evaluation.as_dict()}

        if rule.protection_until:
            protection_until = rule.protection_until
        elif rule.protection_days is not None:
            protection_until = (date.fromisoformat(on_date)
                                + timedelta(days=rule.protection_days)).isoformat()
        else:
            protection_until = on_date

        award_id = str(params.get("award_id") or _new_id("award"))
        basis = {
            "evidence_id": basis_evidence.evidence_id if basis_evidence else None,
            "course_id": basis_evidence.course_id if basis_evidence else None,
            "partial_evidence": partial_basis,
            "equivalence_id": eq_id,
            "composite": node.composite,
            "assembly": node.assembly,
            "slots": [list(s) for s in node.slots],
            "evaluation_status": evaluation.status,
        }
        award = d.CreditAward(
            award_id=award_id, student_id=student_id, node_id=node_id, rule_id=rule.rule_id,
            credits=rule.credits, awarded_on=on_date, standard_id=standard_id, version=version,
            equivalence_id=eq_id or "unknown", enterprise_id=enterprise_id,
            protection_until=protection_until, composite=node.composite,
            basis_json=json.dumps(basis, ensure_ascii=False),
        )
        self.store.add_award(award)
        return {"awarded": True, "award_id": award_id, "node_id": node_id,
                "credits": rule.credits, "awarded_on": on_date,
                "standard_id": standard_id, "version": version,
                "equivalence_id": eq_id, "enterprise_id": enterprise_id,
                "protection_until": protection_until}

    def awards(self, student_id: str, on_date: str | None = None) -> dict[str, Any]:
        on_date = self._date(on_date)
        rows = []
        for award in self.store.awards_for_student(student_id):
            entry = self.evaluator.explain_award(award, on_date)
            entry["basis"] = json.loads(award.basis_json)
            rows.append(entry)
        return {"student_id": student_id, "on_date": on_date, "awards": rows}

    # ------------------------------------------------------------------
    # 解释
    # ------------------------------------------------------------------
    def explain(self, student_id: str, node_id: str, on_date: str | None = None) -> dict[str, Any]:
        return self.evaluator.evaluate(
            student_id, node_id, self._date(on_date)).as_dict()

    def explain_text(self, student_id: str, node_id: str, on_date: str | None = None) -> str:
        return self.evaluator.explain_text(
            student_id, node_id, self._date(on_date))

    def graduation_check(self, student_id: str, on_date: str | None = None) -> dict[str, Any]:
        return self.evaluator.graduation_check(student_id, self._date(on_date))

    def graduate(self, params: dict[str, Any]) -> dict[str, Any]:
        student_id = str(params["student_id"])
        on_date = self._date(params.get("on_date"))
        student = self.store.get_student(student_id)
        if student is None:
            raise ValueError(f"学生 {student_id} 不存在")
        if student.graduated:
            return {"graduated": True, "frozen": True,
                    "graduated_at": student.graduated_at,
                    "note": "往届记录已冻结，不可重复毕业或篡改"}
        check = self.evaluator.graduation_check(student_id, on_date)
        if not check["satisfied"]:
            return {"graduated": False, "missing_nodes": check["missing_nodes"],
                    "details": check["details"]}
        self.store.mark_graduated(student_id, on_date)
        return {"graduated": True, "student_id": student_id, "graduated_at": on_date,
                "frozen": True}

    # ------------------------------------------------------------------
    # 标准换版：只重审受影响能力
    # ------------------------------------------------------------------
    def upgrade_standard(self, params: dict[str, Any]) -> dict[str, Any]:
        standard_id = str(params["standard_id"])
        new_version = str(params["new_version"])
        effective_date = str(params["effective_date"])
        old = self.store.latest_standard_version(standard_id)
        old_version = str(params.get("old_version") or (old.version if old else ""))
        if not old_version:
            raise ValueError("无法确定被替换的旧版本")
        if self.store.get_standard_version(standard_id, new_version) is not None:
            raise ValueError(f"版本 {new_version} 已登记")

        affected = sorted(str(n) for n in params.get("affected_node_ids", []))
        for node_id in affected:
            if self.store.get_node(node_id) is None:
                raise ValueError(f"受影响节点 {node_id} 不存在")

        # 影响面在失效旧等价之前收集。
        old_eqs = [eq for eq in self.store.equivalences_by_standard_version(
            standard_id, old_version) if eq.node_id in affected]
        affected_courses = sorted({eq.source_id for eq in old_eqs
                                   if eq.source_type == d.SOURCE_COURSE})
        affected_teachers = sorted({t for c in affected_courses
                                    for t in self.store.teachers_for_course(c)})
        affected_programs = [p.program_id for node_id in affected
                             for p in self.store.programs_requiring_node(node_id)]
        affected_programs = sorted(set(affected_programs))

        award_rows = [a for a in self.store.all_awards()
                      if a.node_id in affected
                      and a.standard_id == standard_id and a.version == old_version]
        protected_awards = [a for a in award_rows
                            if self.evaluator.explain_award(a, effective_date)["status"]
                            == "protected"]
        ungraduated = {s.student_id: s for s in self.store.list_students(graduated=False)}
        review_students = sorted({a.student_id for a in award_rows
                                  if a.student_id in ungraduated})
        graduates_touch = sorted({a.student_id for a in award_rows
                                  if a.student_id not in ungraduated})

        # 登记新版本与新节点（受影响节点用同一 node_id 锚点挂到新版本）。
        self.store.upsert_standard_version(d.StandardVersion(
            standard_id=standard_id, version=new_version,
            title=str(params.get("title", standard_id)), replaces=old_version,
            effective_date=effective_date))
        for node_param in params.get("nodes", []):
            node_param = dict(node_param)
            node_param.setdefault("standard_id", standard_id)
            node_param["version"] = new_version
            self.register_node(node_param)
        self.store.mark_standard_superseded(standard_id, old_version, new_version)

        # 只失效受影响能力的旧等价；未受影响能力的等价保持 active。
        superseded_ids = [eq.equivalence_id for eq in old_eqs]
        self.store.set_equivalence_status(superseded_ids, d.EQUIV_SUPERSEDED)

        # 新版本下的替代等价（可选，由教务随换版一起登记）。
        new_eq_ids = []
        for eq_param in params.get("new_equivalences", []):
            eq_param = dict(eq_param)
            eq_param.setdefault("standard_id", standard_id)
            eq_param["version"] = new_version
            new_eq_ids.append(self.add_equivalence(eq_param)["equivalence_id"])

        impact = {
            "standard_id": standard_id, "old_version": old_version,
            "new_version": new_version, "effective_date": effective_date,
            "affected_nodes": affected,
            "superseded_equivalences": superseded_ids,
            "new_equivalences": new_eq_ids,
            "affected_courses": affected_courses,
            "affected_teachers": affected_teachers,
            "affected_programs": affected_programs,
            "protected_awards": [
                {"award_id": a.award_id, "student_id": a.student_id, "node_id": a.node_id,
                 "protection_until": a.protection_until} for a in protected_awards],
            "review_students": review_students,
            "graduates_untouched": graduates_touch,
            "transition_note": f"新口径自 {effective_date} 生效；此前旧等价仍可过渡使用，"
                               "保护期内授予保留历史依据",
        }
        event_id = str(params.get("event_id") or _new_id("upg"))
        self.store.add_upgrade_event(
            event_id, standard_id, old_version, new_version, effective_date,
            affected, json.dumps(impact, ensure_ascii=False))

        # 自动建立"受影响能力 × 未毕业在审学生"的重审批次。
        items = [(sid, node_id) for sid in review_students for node_id in affected]
        batch_id = str(params.get("review_batch_id") or f"batch-upg-{event_id}")
        self.store.create_batch(
            batch_id, "upgrade_review",
            json.dumps({"event_id": event_id, "effective_date": effective_date},
                       ensure_ascii=False), items)

        return {"event_id": event_id, "impact": impact,
                "review_batch_id": batch_id, "review_items": len(items)}

    def upgrade_impact(self, event_id: str) -> dict[str, Any]:
        for event in self.store.list_upgrade_events():
            if event["event_id"] == event_id:
                return event
        raise ValueError(f"换版事件 {event_id} 不存在")

    # ------------------------------------------------------------------
    # 企业撤回认可
    # ------------------------------------------------------------------
    def withdraw_endorsement(self, params: dict[str, Any]) -> dict[str, Any]:
        enterprise_id = str(params["enterprise_id"])
        node_id = str(params["node_id"])
        on_date = self._date(params.get("on_date"))
        changed = self.store.withdraw_endorsement(enterprise_id, node_id, on_date)
        if not changed:
            raise ValueError(f"企业 {enterprise_id} 对 {node_id} 没有可撤回的有效认可")

        # 阻断依赖该企业该能力的等价关系：新认定立即停止。
        blocked = [eq for eq in self.store.equivalences_for_node(node_id)
                   if eq.enterprise_id == enterprise_id and eq.status == d.EQUIV_ACTIVE]
        self.store.set_equivalence_status(
            [eq.equivalence_id for eq in blocked], d.EQUIV_BLOCKED)

        award_rows = [a for a in self.store.all_awards()
                      if a.node_id == node_id and a.enterprise_id == enterprise_id]
        ungraduated = {s.student_id for s in self.store.list_students(graduated=False)}
        at_risk = []
        for a in award_rows:
            if a.student_id not in ungraduated:
                continue
            explained = self.evaluator.explain_award(a, on_date)
            entry = {"student_id": a.student_id, "award_id": a.award_id,
                     "status": explained["status"], "protection_until": a.protection_until}
            evaluation = self.evaluator.evaluate(a.student_id, node_id, on_date)
            entry["satisfied"] = evaluation.satisfied
            entry["remediation"] = evaluation.remediation
            entry["reasons"] = evaluation.reasons
            at_risk.append(entry)
        graduates_untouched = sorted({a.student_id for a in award_rows
                                      if a.student_id not in ungraduated})

        batch_id = str(params.get("review_batch_id") or _new_id("batch-wd"))
        items = sorted({(e["student_id"], node_id) for e in at_risk})
        self.store.create_batch(
            batch_id, "withdrawal_review",
            json.dumps({"enterprise_id": enterprise_id, "node_id": node_id,
                        "withdrawn_on": on_date}, ensure_ascii=False), items)

        return {
            "enterprise_id": enterprise_id, "node_id": node_id, "withdrawn_on": on_date,
            "blocked_equivalences": [eq.equivalence_id for eq in blocked],
            "blocked_courses": sorted({eq.source_id for eq in blocked
                                       if eq.source_type == d.SOURCE_COURSE}),
            "at_risk_students": at_risk,
            "graduates_untouched": graduates_untouched,
            "review_batch_id": batch_id,
            "note": "新的等价认定已阻断；往届记录未做任何修改；保护期内学分保留，"
                    "过期学生按补足路径补齐方可毕业",
        }

    # ------------------------------------------------------------------
    # 批量审查（断点续跑）
    # ------------------------------------------------------------------
    def create_review_batch(self, params: dict[str, Any]) -> dict[str, Any]:
        batch_id = str(params.get("batch_id") or _new_id("batch"))
        student_ids = params.get("student_ids")
        node_ids = [str(n) for n in params["node_ids"]]
        if student_ids is None:
            student_ids = [s.student_id for s in self.store.list_students(graduated=False)]
        items = [(str(sid), nid) for sid in student_ids for nid in node_ids]
        self.store.create_batch(
            batch_id, str(params.get("kind", "ad_hoc")),
            json.dumps({"on_date": self._date(params.get("on_date"))}, ensure_ascii=False),
            items)
        return {"batch_id": batch_id, "items": len(items)}

    def run_batch(self, params: dict[str, Any]) -> dict[str, Any]:
        batch_id = str(params["batch_id"])
        batch = self.store.get_batch(batch_id)
        if batch is None:
            raise ValueError(f"批次 {batch_id} 不存在")
        on_date = self._date(params.get("on_date") or batch["payload"].get("on_date"))
        limit = int(params.get("max_items", 0)) or None
        processed = 0
        while limit is None or processed < limit:
            row = self.store.claim_next_item(batch_id)
            if row is None:
                self.store.mark_batch_done(batch_id)
                break
            evaluation = self.evaluator.evaluate(
                row["student_id"], row["node_id"], on_date)
            item_result = d.BatchItemResult(
                student_id=row["student_id"], node_id=row["node_id"],
                status=evaluation.status, detail=evaluation.as_dict())
            self.store.complete_item(
                batch_id, row["item_index"], d.BATCH_DONE, item_result.detail)
            processed += 1
        progress = self.store.batch_progress(batch_id)
        return {"batch_id": batch_id, "processed_this_run": processed,
                "on_date": on_date, "progress": progress,
                "done": progress["total"] == progress["done"]}

    def batch_status(self, batch_id: str) -> dict[str, Any]:
        batch = self.store.get_batch(batch_id)
        if batch is None:
            raise ValueError(f"批次 {batch_id} 不存在")
        items = self.store.batch_items(batch_id)
        summary: dict[str, int] = {}
        for item in items:
            if item["status"] == d.BATCH_DONE and item["result"]:
                label = ("satisfied" if item["result"]["satisfied"]
                         else item["result"]["status"])
            else:
                label = item["status"]
            summary[label] = summary.get(label, 0) + 1
        return {"batch_id": batch_id, "kind": batch["kind"], "status": batch["status"],
                "payload": batch["payload"], "progress": self.store.batch_progress(batch_id),
                "summary": summary, "items": items}
