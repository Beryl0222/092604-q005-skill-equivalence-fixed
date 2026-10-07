"""SQLite 持久化。

学分授予（``credit_awards``）与毕业状态是仅追加的历史依据，模块中没有提供
对这些表的更新/删除方法（毕业冻结只会把学生行标记毕业，绝不改写授予行）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from . import domain as d


def date_in_window(on_date: str, valid_from: str, valid_until: str | None) -> bool:
    return valid_from <= on_date and (valid_until is None or on_date <= valid_until)


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    # ------------------------------------------------------------------
    # 旧骨架
    # ------------------------------------------------------------------
    def add(self, record: d.Record) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO records(record_id,owner_id,state,revision,created_at) VALUES(?,?,?,?,?)",
                (record.record_id, record.owner_id, record.state, record.revision, record.created_at),
            )

    def get(self, record_id: str) -> d.Record | None:
        row = self.connection.execute(
            "SELECT record_id,owner_id,state,revision,created_at FROM records WHERE record_id=?",
            (record_id,),
        ).fetchone()
        return d.Record(**dict(row)) if row else None

    def save_receipt(self, key: str, payload_hash: str, response_json: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO request_receipts(request_key,payload_hash,response_json) VALUES(?,?,?)",
                (key, payload_hash, response_json),
            )

    def get_receipt(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT response_json FROM request_receipts WHERE request_key=?", (key,)
        ).fetchone()
        return row["response_json"] if row else None

    def get_receipt_row(self, key: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT payload_hash, response_json FROM request_receipts WHERE request_key=?",
            (key,)).fetchone()

    # ------------------------------------------------------------------
    # 赛项标准版本 / 能力节点
    # ------------------------------------------------------------------
    def upsert_standard_version(self, sv: d.StandardVersion) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO standard_versions(standard_id,version,title,replaces,effective_date,superseded_by)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(standard_id,version) DO UPDATE SET
                     title=excluded.title, replaces=excluded.replaces,
                     effective_date=excluded.effective_date, superseded_by=excluded.superseded_by""",
                (sv.standard_id, sv.version, sv.title, sv.replaces, sv.effective_date, sv.superseded_by),
            )

    def get_standard_version(self, standard_id: str, version: str) -> d.StandardVersion | None:
        row = self.connection.execute(
            "SELECT * FROM standard_versions WHERE standard_id=? AND version=?",
            (standard_id, version),
        ).fetchone()
        return d.StandardVersion(**dict(row)) if row else None

    def latest_standard_version(self, standard_id: str) -> d.StandardVersion | None:
        row = self.connection.execute(
            "SELECT * FROM standard_versions WHERE standard_id=? ORDER BY version DESC LIMIT 1",
            (standard_id,),
        ).fetchone()
        return d.StandardVersion(**dict(row)) if row else None

    def list_standard_versions(self, standard_id: str) -> list[d.StandardVersion]:
        rows = self.connection.execute(
            "SELECT * FROM standard_versions WHERE standard_id=? ORDER BY version", (standard_id,)
        ).fetchall()
        return [d.StandardVersion(**dict(r)) for r in rows]

    def mark_standard_superseded(self, standard_id: str, old_version: str, new_version: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE standard_versions SET superseded_by=? WHERE standard_id=? AND version=?",
                (new_version, standard_id, old_version),
            )

    def upsert_node(self, node: d.CompetencyNode) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO competency_nodes(node_id,name,standard_id,version,composite,assembly)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(node_id) DO UPDATE SET name=excluded.name,
                     standard_id=excluded.standard_id, version=excluded.version,
                     composite=excluded.composite, assembly=excluded.assembly""",
                (node.node_id, node.name, node.standard_id, node.version,
                 1 if node.composite else 0, node.assembly),
            )
            self.connection.execute("DELETE FROM composite_slots WHERE node_id=?", (node.node_id,))
            for ordinal, (slot_id, stackable) in enumerate(node.slots):
                self.connection.execute(
                    "INSERT INTO composite_slots(node_id,slot_node_id,stackable,ordinal) VALUES(?,?,?,?)",
                    (node.node_id, slot_id, 1 if stackable else 0, ordinal),
                )

    def get_node(self, node_id: str) -> d.CompetencyNode | None:
        row = self.connection.execute(
            "SELECT * FROM competency_nodes WHERE node_id=?", (node_id,)
        ).fetchone()
        if not row:
            return None
        slots = [
            (r["slot_node_id"], bool(r["stackable"]))
            for r in self.connection.execute(
                "SELECT slot_node_id,stackable FROM composite_slots WHERE node_id=? ORDER BY ordinal",
                (node_id,),
            )
        ]
        return d.CompetencyNode(
            node_id=row["node_id"], name=row["name"], standard_id=row["standard_id"],
            version=row["version"], composite=bool(row["composite"]),
            assembly=row["assembly"], slots=slots,
        )

    def list_nodes(self, standard_id: str | None = None, version: str | None = None) -> list[d.CompetencyNode]:
        rows = self.connection.execute(
            "SELECT node_id FROM competency_nodes ORDER BY node_id"
        ).fetchall()
        nodes = [self.get_node(r["node_id"]) for r in rows]
        return [
            n for n in nodes
            if n is not None
            and (standard_id is None or n.standard_id == standard_id)
            and (version is None or n.version == version)
        ]

    # ------------------------------------------------------------------
    # 课程 / 目标 / 设备
    # ------------------------------------------------------------------
    def upsert_course(self, course: d.Course) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO courses(course_id,name,program_id) VALUES(?,?,?)
                   ON CONFLICT(course_id) DO UPDATE SET name=excluded.name, program_id=excluded.program_id""",
                (course.course_id, course.name, course.program_id),
            )

    def get_course(self, course_id: str) -> d.Course | None:
        row = self.connection.execute("SELECT * FROM courses WHERE course_id=?", (course_id,)).fetchone()
        return d.Course(**dict(row)) if row else None

    def list_courses(self) -> list[d.Course]:
        return [d.Course(**dict(r)) for r in self.connection.execute("SELECT * FROM courses ORDER BY course_id")]

    def add_objective(self, course_id: str, node_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO course_objectives(course_id,node_id) VALUES(?,?)",
                (course_id, node_id),
            )

    def objectives(self, course_id: str) -> list[str]:
        return [r["node_id"] for r in self.connection.execute(
            "SELECT node_id FROM course_objectives WHERE course_id=? ORDER BY node_id", (course_id,))]

    def courses_for_node(self, node_id: str) -> list[str]:
        return [r["course_id"] for r in self.connection.execute(
            "SELECT course_id FROM course_objectives WHERE node_id=? ORDER BY course_id", (node_id,))]

    def upsert_equipment_requirement(self, course_id: str, equipment_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO equipment_requirements(course_id,equipment_id) VALUES(?,?)",
                (course_id, equipment_id),
            )

    def equipment_requirements(self, course_id: str) -> list[str]:
        return [r["equipment_id"] for r in self.connection.execute(
            "SELECT equipment_id FROM equipment_requirements WHERE course_id=? ORDER BY equipment_id",
            (course_id,))]

    # ------------------------------------------------------------------
    # 教师授权（按有效期追加，不覆盖历史）
    # ------------------------------------------------------------------
    def add_authorization(self, auth: d.TeacherAuthorization) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO teacher_authorizations(teacher_id,course_id,valid_from,valid_until)
                   VALUES(?,?,?,?)""",
                (auth.teacher_id, auth.course_id, auth.valid_from, auth.valid_until),
            )

    def authorization_valid(self, teacher_id: str, course_id: str, on_date: str) -> bool:
        row = self.connection.execute(
            """SELECT 1 FROM teacher_authorizations
               WHERE teacher_id=? AND course_id=? AND valid_from<=?
                 AND (valid_until IS NULL OR valid_until>=?) LIMIT 1""",
            (teacher_id, course_id, on_date, on_date),
        ).fetchone()
        return row is not None

    def teachers_for_course(self, course_id: str) -> list[str]:
        return [r["teacher_id"] for r in self.connection.execute(
            "SELECT DISTINCT teacher_id FROM teacher_authorizations WHERE course_id=? ORDER BY teacher_id",
            (course_id,))]

    def courses_for_teacher(self, teacher_id: str) -> list[str]:
        return [r["course_id"] for r in self.connection.execute(
            "SELECT DISTINCT course_id FROM teacher_authorizations WHERE teacher_id=? ORDER BY course_id",
            (teacher_id,))]

    # ------------------------------------------------------------------
    # 企业认可（撤回只置 withdrawn，不删除）
    # ------------------------------------------------------------------
    def add_endorsement(self, endorsement: d.EnterpriseEndorsement) -> str:
        endorsement_id = f"endo:{endorsement.enterprise_id}:{endorsement.node_id}:{endorsement.valid_from}"
        with self.connection:
            self.connection.execute(
                """INSERT INTO enterprise_endorsements
                   (endorsement_id,enterprise_id,node_id,valid_from,valid_until,withdrawn)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(endorsement_id) DO UPDATE SET valid_until=excluded.valid_until""",
                (endorsement_id, endorsement.enterprise_id, endorsement.node_id,
                 endorsement.valid_from, endorsement.valid_until, endorsement.withdrawn),
            )
        return endorsement_id

    def withdraw_endorsement(self, enterprise_id: str, node_id: str, on_date: str) -> int:
        with self.connection:
            cur = self.connection.execute(
                "UPDATE enterprise_endorsements SET withdrawn=? WHERE enterprise_id=? AND node_id=? AND withdrawn IS NULL",
                (on_date, enterprise_id, node_id),
            )
            return cur.rowcount

    def active_endorsement(self, node_id: str, on_date: str) -> sqlite3.Row | None:
        """返回 on_date 当日有效的认可（未撤回、窗口覆盖当日）。"""
        return self.connection.execute(
            """SELECT * FROM enterprise_endorsements
               WHERE node_id=? AND withdrawn IS NULL AND valid_from<=?
                 AND (valid_until IS NULL OR valid_until>=?)
               ORDER BY valid_from DESC LIMIT 1""",
            (node_id, on_date, on_date),
        ).fetchone()

    def active_endorsed_nodes(self, enterprise_id: str) -> list[str]:
        return [r["node_id"] for r in self.connection.execute(
            """SELECT DISTINCT node_id FROM enterprise_endorsements
               WHERE enterprise_id=? AND withdrawn IS NULL ORDER BY node_id""",
            (enterprise_id,))]

    def endorsement_rows_for_node(self, node_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM enterprise_endorsements WHERE node_id=? ORDER BY valid_from", (node_id,)
        ).fetchall()

    # ------------------------------------------------------------------
    # 等价关系
    # ------------------------------------------------------------------
    def add_equivalence(self, eq: d.Equivalence) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO equivalences
                   (equivalence_id,source_type,source_id,node_id,coverage,prerequisites_json,
                    stack_partials,issue_scope,coverage_share,standard_id,version,enterprise_id,
                    valid_from,valid_until,status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (eq.equivalence_id, eq.source_type, eq.source_id, eq.node_id, eq.coverage,
                 json.dumps(list(eq.prerequisites), ensure_ascii=False),
                 1 if eq.stack_partials else 0, eq.issue_scope, eq.coverage_share,
                 eq.standard_id, eq.version,
                 eq.enterprise_id, eq.valid_from, eq.valid_until, eq.status),
            )

    def _equivalence_from_row(self, row: sqlite3.Row) -> d.Equivalence:
        return d.Equivalence(
            equivalence_id=row["equivalence_id"], source_type=row["source_type"],
            source_id=row["source_id"], node_id=row["node_id"], coverage=row["coverage"],
            prerequisites=tuple(json.loads(row["prerequisites_json"])),
            stack_partials=bool(row["stack_partials"]), issue_scope=row["issue_scope"],
            coverage_share=row["coverage_share"],
            standard_id=row["standard_id"], version=row["version"],
            enterprise_id=row["enterprise_id"], valid_from=row["valid_from"],
            valid_until=row["valid_until"], status=row["status"],
        )

    def get_equivalence(self, equivalence_id: str) -> d.Equivalence | None:
        row = self.connection.execute(
            "SELECT * FROM equivalences WHERE equivalence_id=?", (equivalence_id,)).fetchone()
        return self._equivalence_from_row(row) if row else None

    def equivalences_for_node(self, node_id: str, status: str | None = None) -> list[d.Equivalence]:
        if status is None:
            rows = self.connection.execute(
                "SELECT * FROM equivalences WHERE node_id=? ORDER BY equivalence_id", (node_id,))
        else:
            rows = self.connection.execute(
                "SELECT * FROM equivalences WHERE node_id=? AND status=? ORDER BY equivalence_id",
                (node_id, status))
        return [self._equivalence_from_row(r) for r in rows]

    def equivalences_for_course(self, course_id: str, status: str | None = None) -> list[d.Equivalence]:
        sql = "SELECT * FROM equivalences WHERE source_type=? AND source_id=?"
        params: list[Any] = [d.SOURCE_COURSE, course_id]
        if status is not None:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY equivalence_id"
        return [self._equivalence_from_row(r) for r in self.connection.execute(sql, params)]

    def active_equivalences(self, node_id: str, on_date: str) -> list[d.Equivalence]:
        rows = self.connection.execute(
            """SELECT * FROM equivalences
               WHERE node_id=? AND status=? AND valid_from<=?
                 AND (valid_until IS NULL OR valid_until>=?)
               ORDER BY equivalence_id""",
            (node_id, d.EQUIV_ACTIVE, on_date, on_date),
        )
        return [self._equivalence_from_row(r) for r in rows]

    def set_equivalence_status(self, equivalence_ids: Iterable[str], status: str) -> int:
        ids = list(equivalence_ids)
        if not ids:
            return 0
        with self.connection:
            cur = self.connection.execute(
                f"UPDATE equivalences SET status=? WHERE equivalence_id IN ({','.join('?' * len(ids))})",
                [status, *ids],
            )
            return cur.rowcount

    def equivalences_by_standard_version(self, standard_id: str, version: str) -> list[d.Equivalence]:
        rows = self.connection.execute(
            "SELECT * FROM equivalences WHERE standard_id=? AND version=? ORDER BY equivalence_id",
            (standard_id, version),
        )
        return [self._equivalence_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # 学生 / 证据包
    # ------------------------------------------------------------------
    def upsert_student(self, student: d.Student) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO students(student_id,name,program_id,graduated,graduated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(student_id) DO UPDATE SET name=excluded.name,
                     program_id=excluded.program_id""",
                (student.student_id, student.name, student.program_id,
                 1 if student.graduated else 0, student.graduated_at),
            )

    def get_student(self, student_id: str) -> d.Student | None:
        row = self.connection.execute("SELECT * FROM students WHERE student_id=?", (student_id,)).fetchone()
        if not row:
            return None
        return d.Student(
            student_id=row["student_id"], name=row["name"], program_id=row["program_id"],
            graduated=bool(row["graduated"]), graduated_at=row["graduated_at"],
        )

    def list_students(self, graduated: bool | None = None, program_id: str | None = None) -> list[d.Student]:
        sql = "SELECT * FROM students WHERE 1=1"
        params: list[Any] = []
        if graduated is not None:
            sql += " AND graduated=?"
            params.append(1 if graduated else 0)
        if program_id is not None:
            sql += " AND program_id=?"
            params.append(program_id)
        sql += " ORDER BY student_id"
        return [
            d.Student(student_id=r["student_id"], name=r["name"], program_id=r["program_id"],
                      graduated=bool(r["graduated"]), graduated_at=r["graduated_at"])
            for r in self.connection.execute(sql, params)
        ]

    def mark_graduated(self, student_id: str, on_date: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE students SET graduated=1, graduated_at=? WHERE student_id=?",
                (on_date, student_id),
            )

    def add_evidence(self, evidence: d.EvidencePack) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO evidence_packs
                   (evidence_id,student_id,course_id,evidence_date,teacher_id,equipment_json,
                    accepted,reasons_json,node_id,coverage,equivalence_id,coverage_share)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (evidence.evidence_id, evidence.student_id, evidence.course_id,
                 evidence.evidence_date, evidence.teacher_id,
                 json.dumps(list(evidence.equipment), ensure_ascii=False),
                 1 if evidence.accepted else 0,
                 json.dumps(list(evidence.reasons), ensure_ascii=False),
                 evidence.node_id, evidence.coverage, evidence.equivalence_id,
                 evidence.coverage_share),
            )

    def set_evidence_decision(self, evidence_id: str, accepted: bool, reasons: Iterable[str],
                              node_id: str | None, coverage: str | None,
                              equivalence_id: str | None = None,
                              coverage_share: float = 1.0) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE evidence_packs SET accepted=?, reasons_json=?, node_id=?, coverage=?,
                   equivalence_id=?, coverage_share=?
                   WHERE evidence_id=?""",
                (1 if accepted else 0, json.dumps(list(reasons), ensure_ascii=False),
                 node_id, coverage, equivalence_id, coverage_share, evidence_id),
            )

    def _evidence_from_row(self, row: sqlite3.Row) -> d.EvidencePack:
        return d.EvidencePack(
            evidence_id=row["evidence_id"], student_id=row["student_id"],
            course_id=row["course_id"], evidence_date=row["evidence_date"],
            teacher_id=row["teacher_id"], equipment=tuple(json.loads(row["equipment_json"])),
            accepted=bool(row["accepted"]), reasons=tuple(json.loads(row["reasons_json"])),
            node_id=row["node_id"], coverage=row["coverage"],
            equivalence_id=row["equivalence_id"], coverage_share=row["coverage_share"],
        )

    def get_evidence(self, evidence_id: str) -> d.EvidencePack | None:
        row = self.connection.execute(
            "SELECT * FROM evidence_packs WHERE evidence_id=?", (evidence_id,)).fetchone()
        return self._evidence_from_row(row) if row else None

    def evidence_for_student(self, student_id: str) -> list[d.EvidencePack]:
        rows = self.connection.execute(
            "SELECT * FROM evidence_packs WHERE student_id=? ORDER BY evidence_date, evidence_id",
            (student_id,))
        return [self._evidence_from_row(r) for r in rows]

    def accepted_evidence(self, student_id: str) -> list[d.EvidencePack]:
        return [e for e in self.evidence_for_student(student_id) if e.accepted]

    # ------------------------------------------------------------------
    # 学分规则 / 学分授予（仅追加）
    # ------------------------------------------------------------------
    def upsert_credit_rule(self, rule: d.CreditRule) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO credit_rules(rule_id,node_id,credits,protection_until,protection_days)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(rule_id) DO UPDATE SET node_id=excluded.node_id,
                     credits=excluded.credits, protection_until=excluded.protection_until,
                     protection_days=excluded.protection_days""",
                (rule.rule_id, rule.node_id, rule.credits, rule.protection_until, rule.protection_days),
            )

    def get_credit_rule(self, rule_id: str) -> d.CreditRule | None:
        row = self.connection.execute("SELECT * FROM credit_rules WHERE rule_id=?", (rule_id,)).fetchone()
        return d.CreditRule(**dict(row)) if row else None

    def credit_rule_for_node(self, node_id: str) -> d.CreditRule | None:
        row = self.connection.execute(
            "SELECT * FROM credit_rules WHERE node_id=? ORDER BY rule_id LIMIT 1", (node_id,)).fetchone()
        return d.CreditRule(**dict(row)) if row else None

    def add_award(self, award: d.CreditAward) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO credit_awards
                   (award_id,student_id,node_id,rule_id,credits,awarded_on,standard_id,version,
                    equivalence_id,enterprise_id,protection_until,composite,basis_json,revoked)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                (award.award_id, award.student_id, award.node_id, award.rule_id, award.credits,
                 award.awarded_on, award.standard_id, award.version, award.equivalence_id,
                 award.enterprise_id, award.protection_until, 1 if award.composite else 0,
                 award.basis_json),
            )

    def get_award(self, award_id: str) -> d.CreditAward | None:
        row = self.connection.execute("SELECT * FROM credit_awards WHERE award_id=?", (award_id,)).fetchone()
        return self._award_from_row(row) if row else None

    @staticmethod
    def _award_from_row(row: sqlite3.Row) -> d.CreditAward:
        return d.CreditAward(
            award_id=row["award_id"], student_id=row["student_id"], node_id=row["node_id"],
            rule_id=row["rule_id"], credits=row["credits"], awarded_on=row["awarded_on"],
            standard_id=row["standard_id"], version=row["version"],
            equivalence_id=row["equivalence_id"], enterprise_id=row["enterprise_id"],
            protection_until=row["protection_until"], composite=bool(row["composite"]),
            basis_json=row["basis_json"], revoked=bool(row["revoked"]),
        )

    def awards_for_student(self, student_id: str, node_id: str | None = None) -> list[d.CreditAward]:
        if node_id is None:
            rows = self.connection.execute(
                "SELECT * FROM credit_awards WHERE student_id=? ORDER BY awarded_on, award_id",
                (student_id,))
        else:
            rows = self.connection.execute(
                "SELECT * FROM credit_awards WHERE student_id=? AND node_id=? ORDER BY awarded_on, award_id",
                (student_id, node_id))
        return [self._award_from_row(r) for r in rows]

    def all_awards(self) -> list[d.CreditAward]:
        rows = self.connection.execute(
            "SELECT * FROM credit_awards ORDER BY awarded_on, award_id")
        return [self._award_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # 培养方案 / 换版事件
    # ------------------------------------------------------------------
    def upsert_program(self, program: d.Program) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO programs(program_id,name,required_nodes_json,version)
                   VALUES(?,?,?,?)
                   ON CONFLICT(program_id) DO UPDATE SET name=excluded.name,
                     required_nodes_json=excluded.required_nodes_json, version=excluded.version""",
                (program.program_id, program.name,
                 json.dumps(list(program.required_nodes), ensure_ascii=False), program.version),
            )

    def get_program(self, program_id: str) -> d.Program | None:
        row = self.connection.execute("SELECT * FROM programs WHERE program_id=?", (program_id,)).fetchone()
        if not row:
            return None
        return d.Program(program_id=row["program_id"], name=row["name"],
                         required_nodes=tuple(json.loads(row["required_nodes_json"])),
                         version=row["version"])

    def list_programs(self) -> list[d.Program]:
        return [self.get_program(r["program_id"])  # type: ignore[union-attr]
                for r in self.connection.execute("SELECT program_id FROM programs ORDER BY program_id")]

    def programs_requiring_node(self, node_id: str) -> list[d.Program]:
        result = []
        for program_id in [r["program_id"] for r in self.connection.execute(
                "SELECT program_id FROM programs ORDER BY program_id")]:
            program = self.get_program(program_id)
            if program and node_id in program.required_nodes:
                result.append(program)
        return result

    def add_upgrade_event(self, event_id: str, standard_id: str, old_version: str,
                          new_version: str, effective_date: str, affected_nodes: list[str],
                          impact_json: str) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO upgrade_events
                   (event_id,standard_id,old_version,new_version,effective_date,
                    affected_nodes_json,impact_json)
                   VALUES(?,?,?,?,?,?,?)""",
                (event_id, standard_id, old_version, new_version, effective_date,
                 json.dumps(affected_nodes, ensure_ascii=False), impact_json),
            )

    def list_upgrade_events(self, standard_id: str | None = None) -> list[dict[str, Any]]:
        if standard_id is None:
            rows = self.connection.execute(
                "SELECT * FROM upgrade_events ORDER BY effective_date, event_id")
        else:
            rows = self.connection.execute(
                "SELECT * FROM upgrade_events WHERE standard_id=? ORDER BY effective_date, event_id",
                (standard_id,))
        out = []
        for r in rows:
            item = dict(r)
            item["affected_nodes"] = json.loads(item.pop("affected_nodes_json"))
            item["impact"] = json.loads(item.pop("impact_json"))
            out.append(item)
        return out

    # ------------------------------------------------------------------
    # 批量审查批次（断点续跑）
    # ------------------------------------------------------------------
    def create_batch(self, batch_id: str, kind: str, payload_json: str,
                     items: Iterable[tuple[str, str]]) -> int:
        with self.connection:
            self.connection.execute(
                "INSERT INTO batches(batch_id,kind,payload_json,status,created_at) VALUES(?,?,?,?,?)",
                (batch_id, kind, payload_json, d.BATCH_PENDING, ""),
            )
            count = 0
            for ordinal, (student_id, node_id) in enumerate(items):
                self.connection.execute(
                    """INSERT INTO batch_items(batch_id,item_index,student_id,node_id,status,result_json)
                       VALUES(?,?,?,?,?,?)""",
                    (batch_id, ordinal, student_id, node_id, d.BATCH_PENDING, None),
                )
                count += 1
            return count

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def list_batches(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM batches ORDER BY batch_id")
        return [dict(r) for r in rows]

    def claim_next_item(self, batch_id: str) -> sqlite3.Row | None:
        """领取下一个未完成项。

        pending 与上次中断遗留的 running 都可重新领取，保证断点续跑时
        不会有项目被永久跳过。
        """
        with self.connection:
            row = self.connection.execute(
                """SELECT * FROM batch_items
                   WHERE batch_id=? AND status!=?
                   ORDER BY item_index LIMIT 1""",
                (batch_id, d.BATCH_DONE),
            ).fetchone()
            if row is None:
                return None
            self.connection.execute(
                "UPDATE batch_items SET status=? WHERE batch_id=? AND item_index=?",
                (d.BATCH_RUNNING, batch_id, row["item_index"]),
            )
            self.connection.execute(
                "UPDATE batches SET status=? WHERE batch_id=?", (d.BATCH_RUNNING, batch_id))
            return row

    def complete_item(self, batch_id: str, item_index: int, status: str, result: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE batch_items SET status=?, result_json=? WHERE batch_id=? AND item_index=?",
                (status, json.dumps(result, ensure_ascii=False), batch_id, item_index),
            )

    def batch_progress(self, batch_id: str) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS n FROM batch_items WHERE batch_id=? GROUP BY status",
            (batch_id,),
        ).fetchall()
        progress = {d.BATCH_PENDING: 0, d.BATCH_RUNNING: 0, d.BATCH_DONE: 0}
        for r in rows:
            progress[r["status"]] = r["n"]
        progress["total"] = sum(progress.values())
        return progress

    def batch_items(self, batch_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM batch_items WHERE batch_id=? ORDER BY item_index", (batch_id,))
        out = []
        for r in rows:
            item = dict(r)
            item["result"] = json.loads(item["result_json"]) if item["result_json"] else None
            item.pop("result_json", None)
            out.append(item)
        return out

    def mark_batch_done(self, batch_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE batches SET status=? WHERE batch_id=?", (d.BATCH_DONE, batch_id))

    def unfinished_count(self, batch_id: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS n FROM batch_items WHERE batch_id=? AND status!=?",
            (batch_id, d.BATCH_DONE),
        ).fetchone()
        return int(row["n"])


SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    state TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS request_receipts (
    request_key TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    response_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS standard_versions (
    standard_id TEXT NOT NULL,
    version TEXT NOT NULL,
    title TEXT NOT NULL,
    replaces TEXT,
    effective_date TEXT NOT NULL,
    superseded_by TEXT,
    PRIMARY KEY (standard_id, version)
);
CREATE TABLE IF NOT EXISTS competency_nodes (
    node_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    standard_id TEXT NOT NULL,
    version TEXT NOT NULL,
    composite INTEGER NOT NULL DEFAULT 0,
    assembly TEXT NOT NULL DEFAULT 'forbidden'
);
CREATE TABLE IF NOT EXISTS composite_slots (
    node_id TEXT NOT NULL,
    slot_node_id TEXT NOT NULL,
    stackable INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY (node_id, slot_node_id)
);
CREATE TABLE IF NOT EXISTS courses (
    course_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    program_id TEXT
);
CREATE TABLE IF NOT EXISTS course_objectives (
    course_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    PRIMARY KEY (course_id, node_id)
);
CREATE TABLE IF NOT EXISTS equipment_requirements (
    course_id TEXT NOT NULL,
    equipment_id TEXT NOT NULL,
    PRIMARY KEY (course_id, equipment_id)
);
CREATE TABLE IF NOT EXISTS teacher_authorizations (
    authorization_id INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_until TEXT
);
CREATE TABLE IF NOT EXISTS enterprise_endorsements (
    endorsement_id TEXT PRIMARY KEY,
    enterprise_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    withdrawn TEXT
);
CREATE TABLE IF NOT EXISTS equivalences (
    equivalence_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    coverage TEXT NOT NULL,
    prerequisites_json TEXT NOT NULL,
    stack_partials INTEGER NOT NULL DEFAULT 0,
    issue_scope TEXT NOT NULL DEFAULT 'slot',
    coverage_share REAL NOT NULL DEFAULT 1.0,
    standard_id TEXT,
    version TEXT,
    enterprise_id TEXT,
    valid_from TEXT,
    valid_until TEXT,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    program_id TEXT,
    graduated INTEGER NOT NULL DEFAULT 0,
    graduated_at TEXT
);
CREATE TABLE IF NOT EXISTS evidence_packs (
    evidence_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    evidence_date TEXT NOT NULL,
    teacher_id TEXT NOT NULL,
    equipment_json TEXT NOT NULL,
    accepted INTEGER NOT NULL DEFAULT 0,
    reasons_json TEXT NOT NULL,
    node_id TEXT,
    coverage TEXT,
    equivalence_id TEXT,
    coverage_share REAL NOT NULL DEFAULT 1.0
);
CREATE TABLE IF NOT EXISTS credit_rules (
    rule_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    credits REAL NOT NULL,
    protection_until TEXT,
    protection_days INTEGER
);
CREATE TABLE IF NOT EXISTS credit_awards (
    award_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    credits REAL NOT NULL,
    awarded_on TEXT NOT NULL,
    standard_id TEXT NOT NULL,
    version TEXT NOT NULL,
    equivalence_id TEXT NOT NULL,
    enterprise_id TEXT,
    protection_until TEXT NOT NULL,
    composite INTEGER NOT NULL,
    basis_json TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS programs (
    program_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    required_nodes_json TEXT NOT NULL,
    version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS upgrade_events (
    event_id TEXT PRIMARY KEY,
    standard_id TEXT NOT NULL,
    old_version TEXT NOT NULL,
    new_version TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    affected_nodes_json TEXT NOT NULL,
    impact_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS batch_items (
    batch_id TEXT NOT NULL,
    item_index INTEGER NOT NULL,
    student_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    PRIMARY KEY (batch_id, item_index)
);
"""
