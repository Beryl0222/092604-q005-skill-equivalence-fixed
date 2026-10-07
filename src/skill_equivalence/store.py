"""使用 SQLite 保存等价网的登记事实与只追加的学分授予记录。

学分授予表 credit_awards 通过触发器禁止 UPDATE/DELETE：标准换版、
企业撤回都不会篡改往届记录，保护期通过评价时计算体现。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .domain import (
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
    Prerequisite,
    Program,
    ProgramRequirement,
    Record,
    StandardVersion,
    Student,
    StudentEvidence,
    TeacherAuthorization,
)

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
-- 赛项标准版本
CREATE TABLE IF NOT EXISTS standards (
    standard_id TEXT NOT NULL,
    version TEXT NOT NULL,
    title TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    supersedes TEXT,
    PRIMARY KEY (standard_id, version)
);
-- 能力节点（全局身份）
CREATE TABLE IF NOT EXISTS competencies (
    competency_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    is_composite INTEGER NOT NULL
);
-- 节点在各标准版本下的语义快照（换版改内容 => 哈希变化）
CREATE TABLE IF NOT EXISTS competency_revisions (
    competency_id TEXT NOT NULL,
    standard_ref TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (competency_id, standard_ref)
);
-- 版本包含的节点
CREATE TABLE IF NOT EXISTS standard_nodes (
    standard_ref TEXT NOT NULL,
    competency_id TEXT NOT NULL,
    PRIMARY KEY (standard_ref, competency_id)
);
-- 复合能力组成（随版本声明）
CREATE TABLE IF NOT EXISTS competency_parts (
    standard_ref TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (standard_ref, parent_id, child_id)
);
-- 先修依赖（随版本声明）
CREATE TABLE IF NOT EXISTS prerequisites (
    standard_ref TEXT NOT NULL,
    competency_id TEXT NOT NULL,
    required_id TEXT NOT NULL,
    PRIMARY KEY (standard_ref, competency_id, required_id)
);
-- 课程目标
CREATE TABLE IF NOT EXISTS course_objectives (
    course_id TEXT NOT NULL,
    standard_ref TEXT NOT NULL,
    title TEXT NOT NULL,
    competency_id TEXT NOT NULL,
    coverage TEXT NOT NULL,
    PRIMARY KEY (course_id, standard_ref, competency_id)
);
-- 等价关系（事实登记，不删除；失效以 active/原因表达）
CREATE TABLE IF NOT EXISTS equivalence_links (
    link_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    competency_id TEXT NOT NULL,
    coverage TEXT NOT NULL,
    prereq_ids TEXT NOT NULL DEFAULT '[]',
    standard_ref TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    reason TEXT NOT NULL DEFAULT ''
);
-- 实训设备前提
CREATE TABLE IF NOT EXISTS equipment_prereqs (
    standard_ref TEXT NOT NULL,
    competency_id TEXT NOT NULL,
    equipment_id TEXT NOT NULL,
    equipment_name TEXT NOT NULL,
    PRIMARY KEY (standard_ref, competency_id, equipment_id)
);
CREATE TABLE IF NOT EXISTS equipment_used (
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    equipment_id TEXT NOT NULL,
    PRIMARY KEY (source_type, source_id, equipment_id)
);
-- 教师授权
CREATE TABLE IF NOT EXISTS teacher_authorizations (
    authorization_id TEXT PRIMARY KEY,
    teacher_id TEXT NOT NULL,
    teacher_name TEXT NOT NULL,
    standard_ref TEXT NOT NULL,
    competency_id TEXT NOT NULL,
    competency_hash TEXT NOT NULL DEFAULT '',
    granted_date TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
-- 学生证据包
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    title TEXT NOT NULL,
    obtained_date TEXT NOT NULL,
    valid INTEGER NOT NULL DEFAULT 1
);
-- 企业认可（撤回只改状态，记录保留）
CREATE TABLE IF NOT EXISTS recognitions (
    recognition_id TEXT PRIMARY KEY,
    enterprise_id TEXT NOT NULL,
    enterprise_name TEXT NOT NULL,
    competency_id TEXT NOT NULL,
    standard_ref TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    withdrawn_at TEXT
);
-- 学分规则
CREATE TABLE IF NOT EXISTS credit_rules (
    rule_id TEXT PRIMARY KEY,
    competency_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    credits REAL NOT NULL,
    standard_ref TEXT NOT NULL,
    protection_days INTEGER NOT NULL
);
-- 培养方案
CREATE TABLE IF NOT EXISTS programs (
    program_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    standard_ref TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS program_requirements (
    program_id TEXT NOT NULL,
    competency_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (program_id, competency_id)
);
CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    program_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    graduated_date TEXT
);
-- 学分授予（只追加）
CREATE TABLE IF NOT EXISTS credit_awards (
    award_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL,
    competency_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    credits REAL NOT NULL,
    standard_ref TEXT NOT NULL,
    awarded_date TEXT NOT NULL,
    protection_until TEXT NOT NULL,
    conclusion TEXT NOT NULL,
    basis_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS credit_awards_no_update
BEFORE UPDATE ON credit_awards
BEGIN SELECT RAISE(ABORT, '学分授予记录只追加，禁止修改');
END;
CREATE TRIGGER IF NOT EXISTS credit_awards_no_delete
BEFORE DELETE ON credit_awards
BEGIN SELECT RAISE(ABORT, '学分授予记录只追加，禁止删除');
END;
-- 批量审查断点
CREATE TABLE IF NOT EXISTS batch_runs (
    batch_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    done_items TEXT NOT NULL DEFAULT '[]',
    results_json TEXT NOT NULL DEFAULT '[]'
);
"""


def _dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    # -- 通用 --------------------------------------------------------------
    def _insert(self, table: str, **values: Any) -> None:
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with self.connection:
            self.connection.execute(
                f"INSERT INTO {table}({cols}) VALUES({marks})",
                tuple(values.values()),
            )

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        row = self.connection.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return _dicts(self.connection.execute(sql, params).fetchall())

    # -- 兼容基础登记 -------------------------------------------------------
    def add(self, record: Record) -> None:
        self._insert(
            "records",
            record_id=record.record_id,
            owner_id=record.owner_id,
            state=record.state,
            revision=record.revision,
            created_at=record.created_at,
        )

    def get(self, record_id: str) -> Record | None:
        row = self._one("SELECT * FROM records WHERE record_id=?", (record_id,))
        return Record(**row) if row else None

    def save_receipt(self, key: str, payload_hash: str, response_json: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO request_receipts VALUES(?,?,?)",
                (key, payload_hash, response_json),
            )

    def get_receipt(self, key: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM request_receipts WHERE request_key=?", (key,))

    # -- 标准版本 -----------------------------------------------------------
    def add_standard(self, item: StandardVersion) -> None:
        self._insert(
            "standards",
            standard_id=item.standard_id,
            version=item.version,
            title=item.title,
            effective_date=item.effective_date,
            supersedes=item.supersedes,
        )

    def get_standard(self, ref: str) -> dict[str, Any] | None:
        std_id, _, version = ref.partition("@")
        return self._one(
            "SELECT * FROM standards WHERE standard_id=? AND version=?",
            (std_id, version),
        )

    def list_standards(self, standard_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM standards WHERE standard_id=? ORDER BY effective_date",
            (standard_id,),
        )

    def effective_standard(self, standard_id: str, day: str) -> dict[str, Any] | None:
        """某模拟日当天已生效的最新版本。"""
        return self._one(
            """SELECT * FROM standards
               WHERE standard_id=? AND effective_date<=?
               ORDER BY effective_date DESC, version DESC LIMIT 1""",
            (standard_id, day),
        )

    # -- 能力节点 -----------------------------------------------------------
    def add_competency(self, item: CompetencyNode) -> None:
        with self.connection:
            # 能力身份全局唯一：跨赛项/版本复用同一条（去重基础）
            self.connection.execute(
                "INSERT OR IGNORE INTO competencies(competency_id,name,is_composite) VALUES(?,?,?)",
                (item.competency_id, item.name, 1 if item.is_composite else 0),
            )
            self.connection.execute(
                """INSERT INTO competency_revisions(competency_id,standard_ref,content_hash,ordinal)
                   VALUES(?,?,?,?)""",
                (item.competency_id, item.standard_ref, item.content_hash, item.ordinal),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO standard_nodes(standard_ref,competency_id) VALUES(?,?)",
                (item.standard_ref, item.competency_id),
            )

    def get_competency(self, competency_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM competencies WHERE competency_id=?", (competency_id,))

    def nodes_of(self, standard_ref: str) -> list[dict[str, Any]]:
        return self._all(
            """SELECT c.competency_id, c.name, c.is_composite, r.content_hash, r.ordinal
               FROM standard_nodes n
               JOIN competencies c ON c.competency_id=n.competency_id
               JOIN competency_revisions r
                 ON r.competency_id=n.competency_id AND r.standard_ref=n.standard_ref
               WHERE n.standard_ref=? ORDER BY r.ordinal, c.competency_id""",
            (standard_ref,),
        )

    def revision_hash(self, competency_id: str, standard_ref: str) -> str | None:
        row = self._one(
            "SELECT content_hash FROM competency_revisions WHERE competency_id=? AND standard_ref=?",
            (competency_id, standard_ref),
        )
        return row["content_hash"] if row else None

    def add_part(self, item: CompetencyPart, standard_ref: str) -> None:
        self._insert(
            "competency_parts",
            standard_ref=standard_ref,
            parent_id=item.parent_id,
            child_id=item.child_id,
            position=item.position,
        )

    def parts(self, standard_ref: str, parent_id: str) -> list[dict[str, Any]]:
        return self._all(
            """SELECT child_id, position FROM competency_parts
               WHERE standard_ref=? AND parent_id=? ORDER BY position""",
            (standard_ref, parent_id),
        )

    def add_prerequisite(self, item: Prerequisite, standard_ref: str) -> None:
        self._insert(
            "prerequisites",
            standard_ref=standard_ref,
            competency_id=item.competency_id,
            required_id=item.required_id,
        )

    def prerequisites(self, standard_ref: str, competency_id: str) -> list[str]:
        return [
            r["required_id"]
            for r in self._all(
                "SELECT required_id FROM prerequisites WHERE standard_ref=? AND competency_id=?",
                (standard_ref, competency_id),
            )
        ]

    # -- 课程目标 -----------------------------------------------------------
    def add_course_objective(self, item: CourseObjective) -> None:
        self._insert(
            "course_objectives",
            course_id=item.course_id,
            standard_ref=item.standard_ref,
            title=item.title,
            competency_id=item.competency_id,
            coverage=item.coverage,
        )

    def course_objectives(self, course_id: str | None = None,
                          standard_ref: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM course_objectives WHERE 1=1", []
        if course_id:
            sql += " AND course_id=?"
            params.append(course_id)
        if standard_ref:
            sql += " AND standard_ref=?"
            params.append(standard_ref)
        return self._all(sql + " ORDER BY course_id, competency_id", tuple(params))

    # -- 等价关系 -----------------------------------------------------------
    def add_link(self, item: EquivalenceLink) -> None:
        self._insert(
            "equivalence_links",
            link_id=item.link_id,
            source_type=item.source_type,
            source_id=item.source_id,
            competency_id=item.competency_id,
            coverage=item.coverage,
            prereq_ids=json.dumps(list(item.prereq_ids), ensure_ascii=False),
            standard_ref=item.standard_ref,
            active=1 if item.active else 0,
            reason=item.reason,
        )

    def get_link(self, link_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM equivalence_links WHERE link_id=?", (link_id,))

    def links_for_source(self, source_type: str, source_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM equivalence_links WHERE source_type=? AND source_id=?",
            (source_type, source_id),
        )

    def links_for_competency(self, competency_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM equivalence_links WHERE competency_id=?",
            (competency_id,),
        )

    # -- 设备 ---------------------------------------------------------------
    def add_equipment_prereq(self, item: EquipmentPrerequisite, standard_ref: str) -> None:
        self._insert(
            "equipment_prereqs",
            standard_ref=standard_ref,
            competency_id=item.competency_id,
            equipment_id=item.equipment_id,
            equipment_name=item.equipment_name,
        )

    def equipment_prereqs(self, competency_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM equipment_prereqs WHERE competency_id=?",
            (competency_id,),
        )

    def mark_equipment_used(self, item: EquipmentAvailability) -> None:
        self._insert(
            "equipment_used",
            source_type=item.source_type,
            source_id=item.source_id,
            equipment_id=item.equipment_id,
        )

    def equipment_used(self, source_type: str, source_id: str) -> set[str]:
        return {
            r["equipment_id"]
            for r in self._all(
                "SELECT equipment_id FROM equipment_used WHERE source_type=? AND source_id=?",
                (source_type, source_id),
            )
        }

    # -- 教师授权 -----------------------------------------------------------
    def add_teacher_authorization(self, item: TeacherAuthorization) -> None:
        self._insert(
            "teacher_authorizations",
            authorization_id=item.authorization_id,
            teacher_id=item.teacher_id,
            teacher_name=item.teacher_name,
            standard_ref=item.standard_ref,
            competency_id=item.competency_id,
            competency_hash=item.competency_hash,
            granted_date=item.granted_date,
            revoked=1 if item.revoked else 0,
        )

    def revoke_authorization(self, authorization_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE teacher_authorizations SET revoked=1 WHERE authorization_id=?",
                (authorization_id,),
            )

    def authorizations(self, competency_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM teacher_authorizations WHERE competency_id=?",
            (competency_id,),
        )

    def all_authorizations(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM teacher_authorizations ORDER BY authorization_id")

    # -- 证据 ---------------------------------------------------------------
    def add_evidence(self, item: StudentEvidence) -> None:
        self._insert(
            "evidence",
            evidence_id=item.evidence_id,
            student_id=item.student_id,
            source_type=item.source_type,
            source_ref=item.source_ref,
            title=item.title,
            obtained_date=item.obtained_date,
            valid=1 if item.valid else 0,
        )

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,))

    def evidence_for_student(self, student_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM evidence WHERE student_id=? ORDER BY obtained_date",
            (student_id,),
        )

    # -- 企业认可 -----------------------------------------------------------
    def add_recognition(self, item: EnterpriseRecognition) -> None:
        self._insert(
            "recognitions",
            recognition_id=item.recognition_id,
            enterprise_id=item.enterprise_id,
            enterprise_name=item.enterprise_name,
            competency_id=item.competency_id,
            standard_ref=item.standard_ref,
            valid_from=item.valid_from,
            valid_until=item.valid_until,
            status=item.status,
            withdrawn_at=item.withdrawn_at,
        )

    def get_recognition(self, recognition_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM recognitions WHERE recognition_id=?", (recognition_id,))

    def recognitions_for(self, competency_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM recognitions WHERE competency_id=? ORDER BY recognition_id",
            (competency_id,),
        )

    def all_recognitions(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM recognitions ORDER BY recognition_id")

    def withdraw_recognition(self, recognition_id: str, withdrawn_at: str) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE recognitions SET status='withdrawn', withdrawn_at=?
                   WHERE recognition_id=?""",
                (withdrawn_at, recognition_id),
            )

    # -- 学分规则 -----------------------------------------------------------
    def add_credit_rule(self, item: CreditRule) -> None:
        self._insert(
            "credit_rules",
            rule_id=item.rule_id,
            competency_id=item.competency_id,
            course_id=item.course_id,
            credits=item.credits,
            standard_ref=item.standard_ref,
            protection_days=item.protection_days,
        )

    def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM credit_rules WHERE rule_id=?", (rule_id,))

    def rules_for_competency(self, competency_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM credit_rules WHERE competency_id=?", (competency_id,))

    def all_credit_rules(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM credit_rules ORDER BY rule_id")

    # -- 培养方案与学生 ------------------------------------------------------
    def add_program(self, item: Program) -> None:
        self._insert("programs", program_id=item.program_id, name=item.name, standard_ref=item.standard_ref)

    def get_program(self, program_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM programs WHERE program_id=?", (program_id,))

    def all_programs(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM programs ORDER BY program_id")

    def add_program_requirement(self, item: ProgramRequirement) -> None:
        self._insert(
            "program_requirements",
            program_id=item.program_id,
            competency_id=item.competency_id,
            position=item.position,
        )

    def requirements(self, program_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM program_requirements WHERE program_id=? ORDER BY position",
            (program_id,),
        )

    def add_student(self, item: Student) -> None:
        self._insert(
            "students",
            student_id=item.student_id,
            name=item.name,
            program_id=item.program_id,
            status=item.status,
            graduated_date=item.graduated_date,
        )

    def get_student(self, student_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM students WHERE student_id=?", (student_id,))

    def list_students(self, status: str | None = None, program_id: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM students WHERE 1=1", []
        if status:
            sql += " AND status=?"
            params.append(status)
        if program_id:
            sql += " AND program_id=?"
            params.append(program_id)
        return self._all(sql + " ORDER BY student_id", tuple(params))

    # -- 学分授予 -----------------------------------------------------------
    def add_award(self, item: CreditAward) -> None:
        self._insert(
            "credit_awards",
            award_id=item.award_id,
            student_id=item.student_id,
            competency_id=item.competency_id,
            course_id=item.course_id,
            rule_id=item.rule_id,
            credits=item.credits,
            standard_ref=item.standard_ref,
            awarded_date=item.awarded_date,
            protection_until=item.protection_until,
            conclusion=item.conclusion,
            basis_json=item.basis_json,
        )

    def awards_for_student(self, student_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM credit_awards WHERE student_id=? ORDER BY awarded_date, award_id",
            (student_id,),
        )

    def find_award(self, student_id: str, rule_id: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM credit_awards WHERE student_id=? AND rule_id=?",
            (student_id, rule_id),
        )

    def all_awards(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM credit_awards ORDER BY award_id")

    # -- 批量审查 -----------------------------------------------------------
    def save_batch(self, item: BatchRun) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO batch_runs(batch_id,kind,payload_json,started_at,updated_at,status,done_items,results_json)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(batch_id) DO UPDATE SET
                     updated_at=excluded.updated_at,
                     status=excluded.status,
                     done_items=excluded.done_items,
                     results_json=excluded.results_json""",
                (
                    item.batch_id, item.kind, item.payload_json, item.started_at,
                    item.updated_at, item.status, item.done_items, item.results_json,
                ),
            )

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM batch_runs WHERE batch_id=?", (batch_id,))
