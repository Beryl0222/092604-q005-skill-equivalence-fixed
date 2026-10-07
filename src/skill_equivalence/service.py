"""技能标准课程等价网的应用服务。

登记类方法写入事实；判定委托 engine。关键业务约束：

* 学分授予幂等且只追加，授予时固化依据快照；
* 标准换版只圈定受影响能力（内容指纹变化/删除/新增），未受影响的
  关系、授权、培养方案与保护期内学分不动；
* 企业撤回只翻转认可状态，新认定被引擎阻止，并为未毕业学生给出
  补足路径；往届授予记录永不修改或删除；
* 批量审查逐项落盘断点，中断后重跑只处理剩余项。
"""
from __future__ import annotations

import json
from typing import Any

from .clock import Clock, add_days
from .domain import (
    COMPLETE,
    PARTIAL,
    BatchRun,
    CompetencyNode,
    CompetencyPart,
    CourseObjective,
    CreditAward,
    CreditRule,
    EnterpriseRecognition,
    EquipmentAvailability,
    EquipmentPrerequisite,
    EquivalenceLink,
    GRANT_AWARDED,
    GRANT_DENIED,
    Prerequisite,
    Program,
    ProgramRequirement,
    Record,
    RECOGNITION_ACTIVE,
    StandardVersion,
    Student,
    StudentEvidence,
    TeacherAuthorization,
)
from .engine import Engine
from .store import Store


class ServiceError(ValueError):
    """业务规则违反。"""


def ref(standard_id: str, version: str) -> str:
    return f"{standard_id}@{version}"


class Service:
    def __init__(self, store: Store | None = None, clock: Clock | None = None) -> None:
        self.store = store or Store()
        self.clock = clock or Clock()
        self.engine = Engine(self.store)

    def _day(self, as_of: str | None) -> str:
        return as_of or self.clock.today().isoformat()

    # -- 基础健康/登记（兼容脚手架） -----------------------------------------
    def health(self) -> dict[str, str]:
        return {"service": "skill_equivalence", "status": "ok"}

    def register(self, record_id: str, owner_id: str) -> dict[str, str | int]:
        record = Record(record_id, owner_id, "draft", 1, self.clock.now())
        self.store.add(record)
        return record.__dict__.copy()

    def find(self, record_id: str) -> dict[str, str | int] | None:
        record = self.store.get(record_id)
        return record.__dict__.copy() if record else None

    # -- 标准版本 -------------------------------------------------------------
    def register_standard(self, standard_id: str, version: str, title: str,
                          effective_date: str, supersedes: str | None = None) -> dict[str, Any]:
        if self.store.get_standard(ref(standard_id, version)):
            raise ServiceError(f"标准版本 {standard_id}@{version} 已存在")
        item = StandardVersion(standard_id, version, title, effective_date, supersedes)
        self.store.add_standard(item)
        return {"standard_ref": ref(standard_id, version), **item.__dict__}

    def effective_standard(self, standard_id: str, as_of: str | None = None) -> dict[str, Any] | None:
        return self.store.effective_standard(standard_id, self._day(as_of))

    # -- 能力节点与结构 --------------------------------------------------------
    def register_competency(self, competency_id: str, standard_id: str, version: str, name: str,
                            is_composite: bool = False, content_hash: str | None = None,
                            ordinal: int = 0) -> dict[str, Any]:
        std_ref = ref(standard_id, version)
        existing = self.store.get_competency(competency_id)
        if existing and bool(existing["is_composite"]) != is_composite:
            raise ServiceError(f"能力 {competency_id} 的复合属性在不同版本间必须一致")
        if content_hash is None:
            content_hash = f"hash:{competency_id}:{name}"
        self.store.add_competency(
            CompetencyNode(competency_id, std_ref, name, is_composite, content_hash, ordinal)
        )
        return {"competency_id": competency_id, "standard_ref": std_ref,
                "name": name, "content_hash": content_hash, "is_composite": is_composite}

    def register_part(self, parent_id: str, child_id: str, standard_id: str, version: str,
                      position: int) -> None:
        self.store.add_part(CompetencyPart(parent_id, child_id, position), ref(standard_id, version))

    def register_prerequisite(self, competency_id: str, required_id: str,
                              standard_id: str, version: str) -> None:
        self.store.add_prerequisite(Prerequisite(competency_id, required_id), ref(standard_id, version))

    def register_equipment(self, competency_id: str, equipment_id: str, equipment_name: str,
                           standard_id: str, version: str) -> None:
        self.store.add_equipment_prereq(
            EquipmentPrerequisite(competency_id, equipment_id, equipment_name),
            ref(standard_id, version),
        )

    # -- 课程目标与等价关系 -----------------------------------------------------
    def register_course_objective(self, course_id: str, standard_id: str, version: str, title: str,
                                  competency_id: str, coverage: str) -> None:
        self._require_coverage(coverage)
        self.store.add_course_objective(
            CourseObjective(course_id, ref(standard_id, version), title, competency_id, coverage)
        )

    def register_link(self, link_id: str, source_type: str, source_id: str, competency_id: str,
                      coverage: str, standard_id: str, version: str,
                      prereq_ids: list[str] | None = None) -> dict[str, Any]:
        self._require_coverage(coverage)
        std_ref = ref(standard_id, version)
        # 企业已撤回认可的能力，不再接受新的等价关系登记
        for rec in self.store.recognitions_for(competency_id):
            if rec["status"] != "active" and (
                rec["standard_ref"] == std_ref
                or self.store.revision_hash(competency_id, rec["standard_ref"])
                == self.store.revision_hash(competency_id, std_ref)
            ):
                raise ServiceError(
                    f"企业认可已撤回（{rec['enterprise_name']}），阻止对能力 {competency_id} 的新等价认定"
                )
        item = EquivalenceLink(
            link_id=link_id, source_type=source_type, source_id=source_id,
            competency_id=competency_id, coverage=coverage,
            prereq_ids=tuple(prereq_ids or ()), standard_ref=std_ref,
        )
        self.store.add_link(item)
        return {"link_id": link_id, "competency_id": competency_id, "standard_ref": std_ref}

    @staticmethod
    def _require_coverage(coverage: str) -> None:
        if coverage not in (COMPLETE, PARTIAL):
            raise ServiceError(f"覆盖程度必须是 complete 或 partial，收到 {coverage}")

    def mark_equipment_used(self, source_type: str, source_id: str, equipment_id: str) -> None:
        self.store.mark_equipment_used(EquipmentAvailability(source_type, source_id, equipment_id))

    # -- 教师授权 ---------------------------------------------------------------
    def authorize_teacher(self, authorization_id: str, teacher_id: str, teacher_name: str,
                          standard_id: str, version: str, competency_id: str,
                          competency_hash: str | None = None) -> dict[str, Any]:
        std_ref = ref(standard_id, version)
        if competency_hash is None:
            # 默认按能力语义指纹授权：换版后内容未变即可延续
            competency_hash = self.store.revision_hash(competency_id, std_ref) or ""
        item = TeacherAuthorization(
            authorization_id=authorization_id, teacher_id=teacher_id, teacher_name=teacher_name,
            standard_ref=std_ref, competency_id=competency_id, competency_hash=competency_hash,
            granted_date=self.clock.now(),
        )
        self.store.add_teacher_authorization(item)
        return {"authorization_id": authorization_id, "competency_hash": competency_hash}

    def revoke_teacher_authorization(self, authorization_id: str) -> None:
        self.store.revoke_authorization(authorization_id)

    # -- 学生与证据 --------------------------------------------------------------
    def register_student(self, student_id: str, name: str, program_id: str,
                         status: str = "active", graduated_date: str | None = None) -> None:
        self.store.add_student(Student(student_id, name, program_id, status, graduated_date))

    def add_evidence(self, evidence_id: str, student_id: str, source_type: str, source_ref: str,
                     title: str, obtained_date: str, used_equipment: list[str] | None = None) -> None:
        self.store.add_evidence(
            StudentEvidence(evidence_id, student_id, source_type, source_ref, title, obtained_date)
        )
        for equipment_id in used_equipment or []:
            self.store.mark_equipment_used(EquipmentAvailability(source_type, evidence_id, equipment_id))

    # -- 企业认可 -----------------------------------------------------------------
    def add_recognition(self, recognition_id: str, enterprise_id: str, enterprise_name: str,
                        competency_id: str, standard_id: str, version: str,
                        valid_from: str, valid_until: str) -> None:
        self.store.add_recognition(
            EnterpriseRecognition(
                recognition_id=recognition_id, enterprise_id=enterprise_id,
                enterprise_name=enterprise_name, competency_id=competency_id,
                standard_ref=ref(standard_id, version), valid_from=valid_from,
                valid_until=valid_until, status=RECOGNITION_ACTIVE,
            )
        )

    def withdraw_recognition(self, recognition_id: str, as_of: str | None = None) -> dict[str, Any]:
        """撤回企业认可：阻止新认定，找出尚未毕业且失去依据的学生。"""
        day = self._day(as_of)
        record = self.store.get_recognition(recognition_id)
        if record is None:
            raise ServiceError(f"认可记录 {recognition_id} 不存在")
        if record["status"] != "active":
            raise ServiceError("该认可已撤回，不可重复撤回")
        self.store.withdraw_recognition(recognition_id, day)

        cid = record["competency_id"]
        affected_students: list[dict[str, Any]] = []
        for student in self.store.list_students(status="active"):
            program = self.store.get_program(student["program_id"])
            required = {r["competency_id"] for r in self.store.requirements(program["program_id"])}
            if cid not in required:
                continue
            evaluation = self.engine.evaluate_student(
                student["student_id"], program["standard_ref"], day, targets=[cid]
            )
            status = evaluation["competencies"][0]
            if not status["satisfied"]:
                affected_students.append({
                    "student_id": student["student_id"],
                    "name": student["name"],
                    "program_id": student["program_id"],
                    "standard_ref": program["standard_ref"],
                    "blocked_competency": cid,
                })
        return {
            "recognition_id": recognition_id,
            "competency_id": cid,
            "withdrawn_at": day,
            "new_determinations_blocked": True,
            "affected_active_students": affected_students,
        }

    # -- 学分规则与授予 -------------------------------------------------------------
    def add_credit_rule(self, rule_id: str, competency_id: str, course_id: str, credits: float,
                        standard_id: str, version: str, protection_days: int) -> None:
        self.store.add_credit_rule(
            CreditRule(rule_id, competency_id, course_id, credits,
                       ref(standard_id, version), protection_days)
        )

    def award_credit(self, student_id: str, rule_id: str, as_of: str | None = None) -> dict[str, Any]:
        day = self._day(as_of)
        rule = self.store.get_rule(rule_id)
        if rule is None:
            raise ServiceError(f"学分规则 {rule_id} 不存在")
        existing = self.store.find_award(student_id, rule_id)
        if existing:  # 幂等：同一规则不重复授予
            return {"awarded": True, "idempotent": True, "award": existing}
        for other in self.store.awards_for_student(student_id):
            if other["competency_id"] == rule["competency_id"]:
                return {
                    "awarded": False,
                    "conclusion": GRANT_DENIED,
                    "reason": f"能力 {rule['competency_id']} 的学分已由 {other['rule_id']} 计入，不得重复计算",
                    "existing_award_id": other["award_id"],
                }

        evaluation = self.engine.evaluate_student(
            student_id, rule["standard_ref"], day, targets=[rule["competency_id"]]
        )
        status = evaluation["competencies"][0]
        if not status["live_satisfied"]:
            return {"awarded": False, "conclusion": GRANT_DENIED,
                    "competency_id": rule["competency_id"], "evaluation": status}

        awarded_date = day
        award = CreditAward(
            award_id=f"award:{student_id}:{rule_id}",
            student_id=student_id,
            competency_id=rule["competency_id"],
            course_id=rule["course_id"],
            rule_id=rule_id,
            credits=rule["credits"],
            standard_ref=rule["standard_ref"],
            awarded_date=awarded_date,
            protection_until=add_days(awarded_date, rule["protection_days"]),
            conclusion=GRANT_AWARDED,
            basis_json=json.dumps(
                {"rule": rule, "status_at_award": status, "as_of": day},
                ensure_ascii=False, sort_keys=True,
            ),
        )
        self.store.add_award(award)
        return {"awarded": True, "idempotent": False,
                "award": {k: getattr(award, k) for k in award.__dataclass_fields__ if k != "basis_json"},
                "basis_snapshot": True}

    def protected_credit_status(self, student_id: str, as_of: str | None = None) -> list[dict[str, Any]]:
        day = self._day(as_of)
        result = []
        for award in self.store.awards_for_student(student_id):
            from .clock import parse_day
            protected = parse_day(day) <= parse_day(award["protection_until"])
            result.append({
                "award_id": award["award_id"], "competency_id": award["competency_id"],
                "credits": award["credits"], "standard_ref": award["standard_ref"],
                "awarded_date": award["awarded_date"], "protection_until": award["protection_until"],
                "within_protection": protected,
                "historical_record_unchanged": True,
            })
        return result

    # -- 解释与补足 -------------------------------------------------------------------
    def explain_student(self, student_id: str, standard_ref: str, as_of: str | None = None,
                        competency_id: str | None = None) -> dict[str, Any]:
        targets = [competency_id] if competency_id else None
        return self.engine.evaluate_student(student_id, standard_ref, self._day(as_of), targets)

    def supplement_path(self, student_id: str, standard_ref: str, as_of: str | None = None) -> dict[str, Any]:
        student = self.store.get_student(student_id)
        if student is None:
            raise ServiceError(f"未知学生 {student_id}")
        if student["status"] != "active":
            raise ServiceError(f"学生 {student_id} 已毕业，不生成补足路径，历史记录保持不变")
        return self.engine.supplement_plan(student_id, standard_ref, self._day(as_of))

    # -- 培养方案 ----------------------------------------------------------------------
    def register_program(self, program_id: str, name: str, standard_id: str, version: str) -> None:
        self.store.add_program(Program(program_id, name, ref(standard_id, version)))

    def register_program_requirement(self, program_id: str, competency_id: str, position: int) -> None:
        self.store.add_program_requirement(ProgramRequirement(program_id, competency_id, position))

    # -- 换版影响分析 -------------------------------------------------------------------
    def upgrade_impact(self, old_ref: str, new_ref: str) -> dict[str, Any]:
        old_nodes = {n["competency_id"]: n for n in self.store.nodes_of(old_ref)}
        new_nodes = {n["competency_id"]: n for n in self.store.nodes_of(new_ref)}
        changed = sorted(
            cid for cid, n in old_nodes.items()
            if cid in new_nodes and n["content_hash"] != new_nodes[cid]["content_hash"]
        )
        removed = sorted(cid for cid in old_nodes if cid not in new_nodes)
        added = sorted(cid for cid in new_nodes if cid not in old_nodes)
        affected = sorted(set(changed) | set(removed))

        # 受影响叶子沿复合结构向上传播：包含变更节点的复合能力同样需重审
        impacted: set[str] = set(affected)
        changed_size = -1
        while len(impacted) != changed_size:
            changed_size = len(impacted)
            for cid in list(impacted):
                for parent in old_nodes:
                    if parent in impacted:
                        continue
                    child_ids = {p["child_id"] for p in self.store.parts(old_ref, parent)}
                    if cid in child_ids:
                        impacted.add(parent)

        courses = [
            {"course_id": o["course_id"], "title": o["title"], "competency_id": o["competency_id"],
             "reason": "能力内容变更" if o["competency_id"] in changed else
                       ("能力已移除" if o["competency_id"] in removed else "包含受影响的组成能力")}
            for o in self.store.course_objectives(standard_ref=old_ref)
            if o["competency_id"] in impacted
        ]
        teachers, retained_teachers = [], []
        new_hash = {cid: new_nodes[cid]["content_hash"] for cid in new_nodes}
        for auth in self.store.all_authorizations():
            if auth["competency_id"] not in impacted:
                continue
            if auth["standard_ref"] != old_ref and not (
                auth["competency_hash"] and auth["competency_hash"]
                == self.store.revision_hash(auth["competency_id"], old_ref)
            ):
                continue
            if (auth["competency_id"] in changed
                    and auth["competency_hash"] == new_hash.get(auth["competency_id"])):
                retained_teachers.append({
                    "authorization_id": auth["authorization_id"], "teacher_id": auth["teacher_id"],
                    "teacher_name": auth["teacher_name"], "competency_id": auth["competency_id"],
                    "reason": "按能力指纹授权且新指纹一致，授权延续",
                })
            elif auth["competency_id"] not in changed and auth["competency_id"] not in removed:
                teachers.append({
                    "authorization_id": auth["authorization_id"], "teacher_id": auth["teacher_id"],
                    "teacher_name": auth["teacher_name"], "competency_id": auth["competency_id"],
                    "revoked": bool(auth["revoked"]),
                    "reason": "所认定的复合能力包含变更的组成节点",
                })
            else:
                teachers.append({
                    "authorization_id": auth["authorization_id"], "teacher_id": auth["teacher_id"],
                    "teacher_name": auth["teacher_name"], "competency_id": auth["competency_id"],
                    "revoked": bool(auth["revoked"]),
                    "reason": "能力内容变更" if auth["competency_id"] in changed else "能力已移除",
                })
        programs = []
        for prog in self.store.all_programs():
            reqs = self.store.requirements(prog["program_id"])
            hit = [r["competency_id"] for r in reqs if r["competency_id"] in impacted]
            if hit:
                programs.append({
                    "program_id": prog["program_id"], "name": prog["name"],
                    "pinned_standard_ref": prog["standard_ref"],
                    "affected_requirements": hit,
                    "must_revise": True,
                })
        links = [
            {"link_id": l["link_id"], "source_type": l["source_type"], "source_id": l["source_id"],
             "competency_id": l["competency_id"], "coverage": l["coverage"]}
            for cid in impacted for l in self.store.links_for_competency(cid)
            if l["standard_ref"] == old_ref and l["active"]
        ]
        rules = [
            {"rule_id": r["rule_id"], "course_id": r["course_id"], "competency_id": r["competency_id"]}
            for r in self.store.all_credit_rules()
            if r["standard_ref"] == old_ref and r["competency_id"] in impacted
        ]
        awards = [
            {"award_id": a["award_id"], "student_id": a["student_id"],
             "competency_id": a["competency_id"], "protection_until": a["protection_until"]}
            for a in self.store.all_awards() if a["competency_id"] in impacted
        ]
        return {
            "old_ref": old_ref,
            "new_ref": new_ref,
            "affected_competencies": affected,
            "impacted_competencies": sorted(impacted),
            "changed": changed,
            "removed": removed,
            "added": added,
            "unaffected_competencies": sorted(cid for cid in old_nodes if cid not in impacted),
            "courses_to_update": courses,
            "teacher_authorizations_to_recheck": teachers,
            "teacher_authorizations_retained": retained_teachers,
            "programs_to_revise": programs,
            "links_to_recheck": links,
            "rules_to_recheck": rules,
            "historical_awards_preserved": awards,
            "recheck_scope_only_affected": True,
        }

    # -- 批量审查（断点续跑） -----------------------------------------------------------
    def start_batch(self, kind: str, student_ids: list[str], standard_ref: str,
                    targets: list[str] | None = None, batch_id: str | None = None) -> dict[str, Any]:
        if kind not in ("review", "recheck", "supplement"):
            raise ServiceError("批量类型必须是 review / recheck / supplement")
        bid = batch_id or f"batch:{kind}:{self.clock.now()}"
        if self.store.get_batch(bid):
            return {"batch_id": bid, "resumed": True}
        now = self.clock.now()
        payload = json.dumps({"standard_ref": standard_ref, "items": student_ids,
                              "targets": targets or []}, ensure_ascii=False)
        self.store.save_batch(
            BatchRun(bid, kind, payload, now, now, "running", "[]", "[]")
        )
        return {"batch_id": bid, "resumed": False, "total": len(student_ids)}

    def resume_batch(self, batch_id: str, as_of: str | None = None,
                     max_steps: int | None = None) -> dict[str, Any]:
        """处理一批中尚未完成的项；每处理完一项立即落盘，故可随时中断。"""
        row = self.store.get_batch(batch_id)
        if row is None:
            raise ServiceError(f"批处理 {batch_id} 不存在")
        if row["status"] == "completed":
            return {"batch_id": batch_id, "completed": True, "results": json.loads(row["results_json"])}
        payload = json.loads(row["payload_json"])
        standard_ref, items = payload["standard_ref"], payload["items"]
        targets = payload.get("targets") or None
        done = json.loads(row["done_items"])
        results = json.loads(row["results_json"])
        day = self._day(as_of)
        steps = 0
        for item in items:
            if item in done:
                continue
            results.append(self._batch_item(row["kind"], item, standard_ref, day, targets))
            done.append(item)
            self.store.save_batch(
                BatchRun(batch_id, row["kind"], row["payload_json"], row["started_at"],
                         self.clock.now(), "running", json.dumps(done, ensure_ascii=False),
                         json.dumps(results, ensure_ascii=False))
            )
            steps += 1
            if max_steps is not None and steps >= max_steps:
                break
        completed = len(done) >= len(items)
        if completed:
            self.store.save_batch(
                BatchRun(batch_id, row["kind"], row["payload_json"], row["started_at"],
                         self.clock.now(), "completed", json.dumps(done, ensure_ascii=False),
                         json.dumps(results, ensure_ascii=False))
            )
        return {
            "batch_id": batch_id,
            "completed": completed,
            "processed_now": steps,
            "done": len(done),
            "total": len(items),
            "remaining": [i for i in items if i not in done],
            "results": results,
        }

    def _batch_item(self, kind: str, student_id: str, standard_ref: str, day: str,
                    targets: list[str] | None) -> dict[str, Any]:
        if kind == "review":
            ev = self.engine.evaluate_student(student_id, standard_ref, day, targets)
            return {
                "student_id": student_id,
                "all_satisfied": ev["all_satisfied"],
                "missing": [c["competency_id"] for c in ev["competencies"] if not c["satisfied"]],
            }
        if kind == "recheck":
            ev = self.engine.evaluate_student(student_id, standard_ref, day, targets)
            return {
                "student_id": student_id,
                "rechecked": [c["competency_id"] for c in ev["competencies"]],
                "still_satisfied": {c["competency_id"]: c["satisfied"] for c in ev["competencies"]},
            }
        # supplement：只面向未毕业学生
        student = self.store.get_student(student_id)
        if student["status"] != "active":
            return {"student_id": student_id, "skipped": "graduated",
                    "note": "已毕业，不生成补足路径，历史记录不变"}
        plan = self.engine.supplement_plan(student_id, standard_ref, day)
        return {"student_id": student_id, "complete": plan["complete"],
                "steps": [s["competency_id"] for s in plan["steps"]]}
