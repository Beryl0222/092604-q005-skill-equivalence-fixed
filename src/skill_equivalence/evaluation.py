"""等价判定与复合能力解释引擎。

引擎只做只读判定，所有结论都带可解释的原因链：

- 历史学分授予（仅追加）按 ``保护期 → 依据是否仍有效`` 解释为
  ``protected / current / expired``；
- 当下能否新认定取决于当前仍有效的等价通道、企业认可、先修依赖与
  复合能力组装策略；
- 多个局部证明默认不能越权拼成完整资格，必须同时满足等价关系
  ``stack_partials`` 与（复合节点的）槽位/组装策略授权。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import domain as d
from .store import Store, date_in_window

# 评估结论状态
SAT_PROTECTED = "protected"      # 历史授予在保护期内，依据保留
SAT_CURRENT = "current"          # 依据当前仍有效（历史授予或可直接认定）
SAT_EVIDENCE_READY = "evidence_ready"  # 证据齐备、可随时认定但尚未授予学分
SAT_MISSING = "missing"          # 缺少证据/通道
SAT_PARTIAL = "partial"          # 仅有局部证明，份额不足或不允许叠加
SAT_BLOCKED = "blocked"          # 企业撤回/认可失效，新认定被阻断
SAT_PREREQ = "prerequisite_missing"  # 覆盖本身够，但先修依赖未满足


@dataclass
class NodeEvaluation:
    student_id: str
    node_id: str
    on_date: str
    satisfied: bool
    status: str
    share: float = 0.0
    awards: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    missing_prerequisites: list[str] = field(default_factory=list)
    missing_slots: list[dict[str, Any]] = field(default_factory=list)
    channels: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    remediation: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "student_id": self.student_id, "node_id": self.node_id, "on_date": self.on_date,
            "satisfied": self.satisfied, "status": self.status, "share": round(self.share, 4),
            "awards": self.awards, "evidence": self.evidence,
            "missing_prerequisites": self.missing_prerequisites,
            "missing_slots": self.missing_slots, "channels": self.channels,
            "reasons": self.reasons, "remediation": self.remediation,
        }


class Evaluator:
    def __init__(self, store: Store) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # 通道（等价关系）在指定日期的有效性
    # ------------------------------------------------------------------
    def _upgrade_effective_date(self, standard_id: str, old_version: str) -> str | None:
        dates = [
            e["effective_date"] for e in self.store.list_upgrade_events(standard_id)
            if e["old_version"] == old_version
        ]
        return min(dates) if dates else None

    def channel_effective_on(self, eq: d.Equivalence, on_date: str) -> bool:
        """通道在 on_date 能否用于新认定/维持依据。"""
        if not date_in_window(on_date, eq.valid_from, eq.valid_until):
            return False
        if eq.status == d.EQUIV_ACTIVE:
            return True
        if eq.status == d.EQUIV_SUPERSEDED:
            # 换版提前登记时，在新标准生效日之前旧通道仍可过渡使用。
            if eq.standard_id and eq.version:
                effective = self._upgrade_effective_date(eq.standard_id, eq.version)
                return effective is not None and on_date < effective
            return False
        return False  # blocked：企业撤回，即时阻断新认定

    def _endorsement_ok(self, enterprise_id: str | None, node_id: str, on_date: str) -> bool:
        if not enterprise_id:
            return True  # 该通道不要求企业认可
        for row in self.store.endorsement_rows_for_node(node_id):
            if row["enterprise_id"] != enterprise_id:
                continue
            if row["withdrawn"] is not None and on_date >= row["withdrawn"]:
                return False
            if date_in_window(on_date, row["valid_from"], row["valid_until"]):
                return row["withdrawn"] is None or on_date < row["withdrawn"]
        return False

    def _endorsement_withdrawn(self, enterprise_id: str, node_id: str, on_date: str) -> bool:
        return any(
            r["enterprise_id"] == enterprise_id
            and r["withdrawn"] is not None and on_date >= r["withdrawn"]
            for r in self.store.endorsement_rows_for_node(node_id)
        )

    # ------------------------------------------------------------------
    # 历史授予解释
    # ------------------------------------------------------------------
    def explain_award(self, award: d.CreditAward, on_date: str) -> dict[str, Any]:
        protected = award.protection_until >= on_date
        entry: dict[str, Any] = {
            "award_id": award.award_id, "node_id": award.node_id,
            "credits": award.credits, "awarded_on": award.awarded_on,
            "standard_id": award.standard_id, "version": award.version,
            "equivalence_id": award.equivalence_id, "enterprise_id": award.enterprise_id,
            "protection_until": award.protection_until, "composite": award.composite,
            "revoked": award.revoked,
        }
        if award.revoked:
            entry["status"] = "revoked"
            entry["note"] = "授予记录已撤销（仅管理冻结场景）"
            return entry
        if protected:
            entry["status"] = SAT_PROTECTED
            entry["note"] = "在保护期内，保留历史依据"
            return entry
        reasons = []
        sv = self.store.get_standard_version(award.standard_id, award.version)
        if sv and sv.superseded_by:
            effective = self._upgrade_effective_date(award.standard_id, award.version)
            if effective and on_date >= effective:
                reasons.append(f"标准 {award.standard_id} 已于 {effective} 换版至 {sv.superseded_by}")
        if award.enterprise_id and self._endorsement_withdrawn(
                award.enterprise_id, award.node_id, on_date):
            reasons.append(f"企业 {award.enterprise_id} 已撤回对该能力的认可")
        elif award.enterprise_id and not self._endorsement_ok(
                award.enterprise_id, award.node_id, on_date):
            reasons.append(f"企业 {award.enterprise_id} 的认可期已过")
        if reasons:
            entry["status"] = "expired"
            entry["note"] = "已过保护期且" + "；".join(reasons)
        else:
            entry["status"] = SAT_CURRENT
            entry["note"] = "保护期已过，但授予依据仍然有效"
        return entry

    # ------------------------------------------------------------------
    # 主判定
    # ------------------------------------------------------------------
    def evaluate(self, student_id: str, node_id: str, on_date: str,
                 _stack: tuple[str, ...] = ()) -> NodeEvaluation:
        result = NodeEvaluation(student_id=student_id, node_id=node_id, on_date=on_date,
                                satisfied=False, status=SAT_MISSING)
        node = self.store.get_node(node_id)
        if node is None:
            result.reasons.append(f"能力节点 {node_id} 不存在")
            result.status = SAT_MISSING
            return result
        if node_id in _stack:
            result.reasons.append(f"先修依赖存在环：{' -> '.join((*_stack, node_id))}")
            return result
        stack = (*_stack, node_id)

        # 1) 历史授予：保护期优先，过期且依据失效的授予不满足。
        awards = [a for a in self.store.awards_for_student(student_id, node_id)
                  if not a.revoked and a.awarded_on <= on_date]
        explained_awards = [self.explain_award(a, on_date) for a in awards]
        result.awards = explained_awards
        good = [a for a in explained_awards if a["status"] in (SAT_PROTECTED, SAT_CURRENT)]
        if good:
            result.satisfied = True
            result.status = SAT_PROTECTED if any(
                a["status"] == SAT_PROTECTED for a in good) else SAT_CURRENT
            result.share = 1.0
            result.reasons.append("；".join(a["note"] for a in good))
            return result

        expired = [a for a in explained_awards if a["status"] == "expired"]

        # 2) 用已受理证据 + 当前有效通道判定当下能否认定。
        evidence = [e for e in self.store.accepted_evidence(student_id)
                    if e.node_id == node_id and e.evidence_date <= on_date]
        result.evidence = [{
            "evidence_id": e.evidence_id, "course_id": e.course_id,
            "evidence_date": e.evidence_date, "coverage": e.coverage,
            "coverage_share": e.coverage_share, "equivalence_id": e.equivalence_id,
        } for e in evidence]

        channels = self._channels_for(node_id, on_date)
        result.channels = channels

        if node.composite:
            self._evaluate_composite(result, node, evidence, channels, on_date, stack)
        else:
            self._evaluate_atomic(result, node, evidence, channels, on_date, stack)

        if expired and result.status in (SAT_MISSING, SAT_PARTIAL, SAT_BLOCKED):
            result.reasons.insert(0, "；".join(a["note"] for a in expired))

        # 3) 不满足时给补足路径。
        if not result.satisfied:
            result.remediation = self.remediation_paths(student_id, node, on_date, stack)
        return result

    # ------------------------------------------------------------------
    # 原子能力
    # ------------------------------------------------------------------
    @staticmethod
    def _channel_usable(channel: dict[str, Any]) -> bool:
        """通道当前能否承载证据：在有效期、未撤回、企业认可仍有效。"""
        return bool(channel["effective"]) and bool(channel["enterprise_ok"]) \
            and not channel["blocked"]

    def _evaluate_atomic(self, result: NodeEvaluation, node: d.CompetencyNode,
                         evidence: list[d.EvidencePack], channels: list[dict[str, Any]],
                         on_date: str, stack: tuple[str, ...]) -> None:
        by_id = {c["equivalence_id"]: c for c in channels}
        full_hits = []
        partial_hits = []
        stackable = True
        for ev in evidence:
            channel = by_id.get(ev.equivalence_id) if ev.equivalence_id else None
            if channel is None:
                result.reasons.append(
                    f"证据 {ev.evidence_id} 依据的等价关系 {ev.equivalence_id} 不存在")
                continue
            if not self._channel_usable(channel):
                why = "企业已撤回认可" if channel["blocked"] else (
                    "标准换版后已失效" if channel["status"] == d.EQUIV_SUPERSEDED
                    else "当前不在有效期或认可已失效")
                result.reasons.append(
                    f"证据 {ev.evidence_id} 依据的等价关系 {channel['equivalence_id']}：{why}，"
                    "不能再作为认定依据")
                continue
            if ev.coverage == d.FULL and channel["coverage"] == d.FULL:
                full_hits.append((ev, channel))
            else:
                partial_hits.append((ev, channel))
                stackable = stackable and bool(channel["stack_partials"])

        prereq_missing = self._missing_prereqs_for_student(
            result.student_id,
            sorted({p for _, c in (full_hits or partial_hits) for p in c["prerequisites"]}),
            on_date, stack)
        result.missing_prerequisites = prereq_missing

        if full_hits and not prereq_missing:
            result.satisfied = True
            result.status = SAT_EVIDENCE_READY
            result.share = 1.0
            ev, channel = full_hits[0]
            result.reasons.append(
                f"证据 {ev.evidence_id}（课程 {ev.course_id}）完整覆盖该能力，"
                f"通道 {channel['equivalence_id']} 有效，可认定学分")
            return
        if full_hits:
            result.status = SAT_PREREQ
            result.share = 1.0
            result.reasons.append("能力覆盖完整，但先修依赖未全部达成")
            return

        share = sum(ev.coverage_share for ev, _ in partial_hits)
        result.share = min(share, 1.0)
        if partial_hits:
            if not stackable:
                result.status = SAT_PARTIAL
                result.reasons.append(
                    "存在局部证明，但等价关系未授权局部证明叠加（不能越权拼成完整资格）")
            elif share < 1.0:
                result.status = SAT_PARTIAL
                result.reasons.append(f"可叠加的局部证明覆盖份额合计 {share:g} < 1，尚不足完整资格")
            elif prereq_missing:
                result.status = SAT_PREREQ
                result.reasons.append("局部证明已拼合完整，但先修依赖未全部达成")
            else:
                result.satisfied = True
                result.status = SAT_EVIDENCE_READY
                result.reasons.append(
                    f"{len(partial_hits)} 份局部证明在授权下拼合覆盖完整（份额 {share:g}），可认定")
            return

        usable = [c for c in channels if self._channel_usable(c)]
        if not usable and channels and any(c["blocked"] for c in channels):
            result.status = SAT_BLOCKED
            result.reasons.append("企业已撤回认可，新的等价认定被阻断")
        elif not channels:
            if self._known_blocked(node.node_id):
                result.status = SAT_BLOCKED
                result.reasons.append("企业已撤回认可，且无其他有效通道")
            else:
                result.reasons.append("没有任何覆盖该能力的有效证据或等价通道")

    # ------------------------------------------------------------------
    # 复合能力
    # ------------------------------------------------------------------
    def _evaluate_composite(self, result: NodeEvaluation, node: d.CompetencyNode,
                            evidence: list[d.EvidencePack], channels: list[dict[str, Any]],
                            on_date: str, stack: tuple[str, ...]) -> None:
        # 完整签发通道：当前可用、被授权对整项复合资格签 full 的课程。
        issuer_channels = [
            c for c in channels
            if c["coverage"] == d.FULL and c.get("issue_scope") == "composite"
            and self._channel_usable(c)
        ]
        issuer_evidence = []
        by_id = {c["equivalence_id"]: c for c in channels}
        for ev in evidence:
            ch = by_id.get(ev.equivalence_id)
            if (ch is not None and ch in issuer_channels and ev.coverage == d.FULL):
                issuer_evidence.append((ev, ch))

        slot_reports = []
        slots_met = True
        for slot_id, slot_stackable in node.slots:
            sub = self.evaluate(result.student_id, slot_id, on_date, stack)
            slot_report: dict[str, Any] = {
                "slot_node_id": slot_id, "stackable": slot_stackable,
                "satisfied": sub.satisfied, "status": sub.status, "share": sub.share,
            }
            if not sub.satisfied:
                slots_met = False
                slot_report["reasons"] = sub.reasons
                slot_report["remediation"] = sub.remediation
            slot_reports.append(slot_report)
        result.missing_slots = [s for s in slot_reports if not s["satisfied"]]

        issuer_prereq = self._missing_prereqs_for_student(
            result.student_id,
            sorted({p for _, c in issuer_evidence for p in c["prerequisites"]}),
            on_date, stack)

        if node.assembly == "forbidden":
            if issuer_evidence and not issuer_prereq:
                self._composite_ok(result, issuer_evidence[0], slot_reports,
                                   "完整签发通道认定整项复合资格")
                return
            if issuer_evidence:
                result.status = SAT_PREREQ
                result.reasons.append("完整签发证据齐备，但先修依赖未全部达成")
                return
            result.status = SAT_PARTIAL if evidence or any(
                s["status"] == SAT_PARTIAL for s in slot_reports) else (
                SAT_BLOCKED if channels and all(c["blocked"] for c in channels) else SAT_MISSING)
            result.reasons.append(
                "该复合资格不允许用局部证明拼装，必须由获得完整签发授权的通道认定")
            return

        if node.assembly == "requires_issuer":
            if slots_met and issuer_channels and not issuer_prereq and issuer_evidence:
                self._composite_ok(result, issuer_evidence[0], slot_reports,
                                   "槽位全部满足并经完整签发通道确认")
                return
            if slots_met and not issuer_channels:
                result.status = SAT_BLOCKED
                result.reasons.append(
                    "槽位已全部满足，但缺少有资格签发整项复合资格的通道，不能越权认定")
                return
            if not slots_met:
                result.status = SAT_PARTIAL if any(
                    s["status"] == SAT_PARTIAL for s in slot_reports) else SAT_MISSING
                result.reasons.append("存在未满足的槽位，且最终仍需完整签发通道确认")
                return
            result.status = SAT_PREREQ
            result.reasons.append("槽位满足，但签发通道先修依赖未达成")
            return

        # slots_allowed：槽位全部满足即可。
        if slots_met:
            result.satisfied = True
            result.status = SAT_EVIDENCE_READY
            result.share = 1.0
            result.reasons.append("复合能力的全部槽位均已满足，按组装策略可认定整项资格")
            return
        result.status = SAT_PARTIAL if any(
            s["status"] in (SAT_PARTIAL, SAT_BLOCKED) for s in slot_reports) else SAT_MISSING
        result.reasons.append(
            f"复合能力 {len(result.missing_slots)}/{len(node.slots)} 个槽位未满足，"
            "局部证明不能替代缺失槽位")

    def _composite_ok(self, result: NodeEvaluation, hit: tuple[d.EvidencePack, dict[str, Any]],
                      slot_reports: list[dict[str, Any]], note: str) -> None:
        ev, channel = hit
        result.satisfied = True
        result.status = SAT_EVIDENCE_READY
        result.share = 1.0
        result.missing_slots = []
        result.reasons.append(
            f"证据 {ev.evidence_id}（课程 {ev.course_id}）经 {note}，通道 {channel['equivalence_id']}")

    # ------------------------------------------------------------------
    # 先修依赖
    # ------------------------------------------------------------------
    # 通道清单与补足路径
    # ------------------------------------------------------------------
    def _channels_for(self, node_id: str, on_date: str) -> list[dict[str, Any]]:
        out = []
        for eq in self.store.equivalences_for_node(node_id):
            effective = self.channel_effective_on(eq, on_date)
            enterprise_ok = self._endorsement_ok(eq.enterprise_id, node_id, on_date)
            withdrawn = bool(eq.enterprise_id) and self._endorsement_withdrawn(
                eq.enterprise_id, node_id, on_date)
            course = self.store.get_course(eq.source_id) if eq.source_type == d.SOURCE_COURSE else None
            out.append({
                "equivalence_id": eq.equivalence_id,
                "source_type": eq.source_type, "source_id": eq.source_id,
                "source_name": course.name if course else eq.source_id,
                "coverage": eq.coverage, "coverage_share": eq.coverage_share,
                "stack_partials": eq.stack_partials, "issue_scope": eq.issue_scope,
                "prerequisites": list(eq.prerequisites),
                "enterprise_id": eq.enterprise_id,
                "enterprise_ok": enterprise_ok, "blocked": withdrawn,
                "effective": effective,
                "equipment_required": self.store.equipment_requirements(eq.source_id)
                    if eq.source_type == d.SOURCE_COURSE else [],
                "authorized_teachers": self.store.teachers_for_course(eq.source_id)
                    if eq.source_type == d.SOURCE_COURSE else [],
                "status": eq.status,
            })
        return out

    def _known_blocked(self, node_id: str) -> bool:
        return any(
            r["withdrawn"] is not None
            for r in self.store.endorsement_rows_for_node(node_id))

    def _missing_prereqs_for_student(self, student_id: str, prereqs: list[str], on_date: str,
                                     stack: tuple[str, ...]) -> list[str]:
        missing = []
        for prereq in prereqs:
            sub = self.evaluate(student_id, prereq, on_date, stack)
            if not sub.satisfied:
                missing.append(prereq)
        return missing

    def remediation_paths(self, student_id: str, node: d.CompetencyNode, on_date: str,
                          stack: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        channels = self._channels_for(node.node_id, on_date)
        paths: list[dict[str, Any]] = []
        if node.composite:
            for slot_id, slot_stackable in node.slots:
                sub = self.evaluate(student_id, slot_id, on_date, stack)
                if not sub.satisfied:
                    slot_node = self.store.get_node(slot_id)
                    paths.append({
                        "kind": "slot", "slot_node_id": slot_id,
                        "stackable": slot_stackable,
                        "paths": self.remediation_paths(
                            student_id, slot_node, on_date, stack) if slot_node else [],
                        "reasons": sub.reasons,
                    })
            issuers = [c for c in channels
                       if c["effective"] and c["coverage"] == d.FULL
                       and c.get("issue_scope") == "composite" and not c["blocked"]]
            if node.assembly in ("forbidden", "requires_issuer") and issuers:
                courses = sorted({c["source_id"] for c in issuers})
                paths.append({
                    "kind": "composite_issuer",
                    "message": "由获得完整签发授权的课程认定整项复合资格",
                    "courses": courses,
                })
        for c in channels:
            if c["blocked"]:
                paths.append({
                    "kind": "blocked", "equivalence_id": c["equivalence_id"],
                    "course_id": c["source_id"] if c["source_type"] == d.SOURCE_COURSE else None,
                    "enterprise_id": c["enterprise_id"],
                    "message": "企业已撤回认可：在该企业重新认可或出现其他企业背书的通道前，"
                               "不能据此新认定；往届与保护期内记录不受影响",
                })
                continue
            if not c["effective"]:
                if c["status"] == d.EQUIV_SUPERSEDED:
                    paths.append({
                        "kind": "superseded", "equivalence_id": c["equivalence_id"],
                        "course_id": c["source_id"] if c["source_type"] == d.SOURCE_COURSE else None,
                        "message": "该通道随标准换版失效，需按新版等价关系重新认定；"
                                   "保护期内的历史学分不受影响",
                    })
                continue
            missing_prereqs = self._missing_prereqs_for_student(
                student_id, c["prerequisites"], on_date, stack)
            path = {
                "kind": "course",
                "course_id": c["source_id"] if c["source_type"] == d.SOURCE_COURSE else None,
                "course_name": c["source_name"],
                "equivalence_id": c["equivalence_id"],
                "coverage": c["coverage"], "coverage_share": c["coverage_share"],
                "stack_partials": c["stack_partials"],
                "missing_prerequisites": missing_prereqs,
                "equipment_required": c["equipment_required"],
                "enterprise_id": c["enterprise_id"],
            }
            if c["coverage"] == d.FULL:
                path["message"] = "修读并通过该课程，备齐设备证据与授权教师成绩即可完整认定"
            else:
                path["message"] = (
                    f"该课程提供 {c['coverage_share']:g} 的局部覆盖；"
                    + ("可与其他授权的局部证明叠加" if c["stack_partials"]
                       else "该局部证明不允许与其他证明叠加"))
            paths.append(path)
        return paths

    # ------------------------------------------------------------------
    # 毕业核查
    # ------------------------------------------------------------------
    def graduation_check(self, student_id: str, on_date: str) -> dict[str, Any]:
        student = self.store.get_student(student_id)
        if student is None:
            return {"student_id": student_id, "error": "学生不存在"}
        program = self.store.get_program(student.program_id) if student.program_id else None
        required = list(program.required_nodes) if program else []
        details = [self.evaluate(student_id, node_id, on_date).as_dict()
                   for node_id in required]
        missing = [d_["node_id"] for d_ in details if not d_["satisfied"]]
        return {
            "student_id": student_id, "on_date": on_date,
            "program_id": student.program_id,
            "graduated": student.graduated,
            "required_nodes": required,
            "satisfied": not missing,
            "missing_nodes": missing,
            "details": details,
        }

    def explain_text(self, student_id: str, node_id: str, on_date: str) -> str:
        """生成给教务人员看的中文解释。"""
        ev = self.evaluate(student_id, node_id, on_date)
        node = self.store.get_node(node_id)
        lines = [
            f"学生 {student_id} 对能力 {node_id}"
            + (f"（{node.name}）" if node else ""),
            f"截至 {on_date}：{'满足' if ev.satisfied else '不满足'}，判定状态：{ev.status}。",
        ]
        if ev.awards:
            for a in ev.awards:
                lines.append(
                    f"- 学分授予 {a['award_id']}：{a['credits']} 学分，{a['awarded_on']} 授予，"
                    f"保护期至 {a['protection_until']}，当前解释：{a['status']}（{a.get('note', '')}）")
        if ev.evidence:
            for e in ev.evidence:
                lines.append(
                    f"- 证据 {e['evidence_id']}：课程 {e['course_id']}，{e['evidence_date']}，"
                    f"覆盖 {e['coverage']}（份额 {e['coverage_share']:g}）")
        for reason in ev.reasons:
            lines.append(f"- {reason}")
        if ev.missing_prerequisites:
            lines.append(f"- 先修依赖未达成：{', '.join(ev.missing_prerequisites)}")
        for slot in ev.missing_slots:
            lines.append(
                f"- 槽位 {slot['slot_node_id']} 未满足（{slot['status']}，份额 {slot['share']:g}）")
            for reason in slot.get("reasons", []):
                lines.append(f"    · {reason}")
        if ev.remediation:
            lines.append("补足路径：")
            for path in ev.remediation:
                if path["kind"] == "course":
                    lines.append(
                        f"  · 课程 {path['course_name']}（{path['coverage']}，"
                        f"先修缺口 {path['missing_prerequisites'] or '无'}，"
                        f"设备 {path['equipment_required'] or '无'}）：{path['message']}")
                elif path["kind"] == "blocked":
                    lines.append(f"  · 阻断：{path['message']}")
                elif path["kind"] == "superseded":
                    lines.append(f"  · 换版失效（课程 {path['course_id']}）：{path['message']}")
                elif path["kind"] == "composite_issuer":
                    lines.append(f"  · {path['message']}：{', '.join(path['courses'])}")
                elif path["kind"] == "slot":
                    lines.append(f"  · 先补槽位 {path['slot_node_id']}：{path.get('reasons', '')}")
        return "\n".join(lines)
