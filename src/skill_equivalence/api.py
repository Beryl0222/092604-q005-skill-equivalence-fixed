"""处理进程内 JSON 请求。

请求形如 {"action": "...", "request_id": "可选幂等键", "as_of": "可选模拟日期", ...}。
顶层 "db" 指定 SQLite 文件时使用持久化服务，否则用内存库（主要用于测试）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from .clock import FixedClock
from .service import Service
from .store import Store

# action -> (服务方法, 直接透传的字段名顺序；None 表示整体 body 特殊处理)
DISPATCH: dict[str, tuple[str, tuple[str, ...]]] = {
    "health": ("health", ()),
    "register": ("register", ("record_id", "owner_id")),
    "find": ("find", ("record_id",)),
    "register_standard": (
        "register_standard",
        ("standard_id", "version", "title", "effective_date", "supersedes"),
    ),
    "effective_standard": ("effective_standard", ("standard_id", "as_of")),
    "register_competency": (
        "register_competency",
        ("competency_id", "standard_id", "version", "name", "is_composite",
         "content_hash", "ordinal"),
    ),
    "register_part": ("register_part", ("parent_id", "child_id", "standard_id", "version", "position")),
    "register_prerequisite": (
        "register_prerequisite", ("competency_id", "required_id", "standard_id", "version"),
    ),
    "register_equipment": (
        "register_equipment", ("competency_id", "equipment_id", "equipment_name", "standard_id", "version"),
    ),
    "register_course_objective": (
        "register_course_objective",
        ("course_id", "standard_id", "version", "title", "competency_id", "coverage"),
    ),
    "register_link": (
        "register_link",
        ("link_id", "source_type", "source_id", "competency_id", "coverage",
         "standard_id", "version", "prereq_ids"),
    ),
    "mark_equipment_used": ("mark_equipment_used", ("source_type", "source_id", "equipment_id")),
    "authorize_teacher": (
        "authorize_teacher",
        ("authorization_id", "teacher_id", "teacher_name", "standard_id",
         "version", "competency_id", "competency_hash"),
    ),
    "revoke_teacher_authorization": ("revoke_teacher_authorization", ("authorization_id",)),
    "register_program": ("register_program", ("program_id", "name", "standard_id", "version")),
    "register_program_requirement": (
        "register_program_requirement", ("program_id", "competency_id", "position"),
    ),
    "register_student": (
        "register_student", ("student_id", "name", "program_id", "status", "graduated_date"),
    ),
    "add_evidence": (
        "add_evidence",
        ("evidence_id", "student_id", "source_type", "source_ref", "title",
         "obtained_date", "used_equipment"),
    ),
    "add_recognition": (
        "add_recognition",
        ("recognition_id", "enterprise_id", "enterprise_name", "competency_id",
         "standard_id", "version", "valid_from", "valid_until"),
    ),
    "withdraw_recognition": ("withdraw_recognition", ("recognition_id", "as_of")),
    "add_credit_rule": (
        "add_credit_rule",
        ("rule_id", "competency_id", "course_id", "credits", "standard_id", "version", "protection_days"),
    ),
    "award_credit": ("award_credit", ("student_id", "rule_id", "as_of")),
    "protected_credits": ("protected_credit_status", ("student_id", "as_of")),
    "explain_student": (
        "explain_student", ("student_id", "standard_ref", "as_of", "competency_id"),
    ),
    "supplement_path": ("supplement_path", ("student_id", "standard_ref", "as_of")),
    "upgrade_impact": ("upgrade_impact", ("old_ref", "new_ref")),
    "start_batch": (
        "start_batch", ("kind", "student_ids", "standard_ref", "targets", "batch_id"),
    ),
    "resume_batch": ("resume_batch", ("batch_id", "as_of", "max_steps")),
}


def build_service(body: dict[str, Any], service: Service | None) -> Service:
    if service is not None:
        return service
    db = body.get("db")
    store = Store(db) if db else Store()
    as_of = body.get("as_of")
    clock = FixedClock(as_of) if as_of else None
    return Service(store, clock)  # type: ignore[arg-type]


def handle(raw: str, service: Service | None = None) -> str:
    body = json.loads(raw)
    current = build_service(body, service)

    # 幂等请求：同一 request_id + 同一载荷直接回放首次响应
    request_id = body.get("request_id")
    if request_id and service is None and not body.get("db"):
        # 内存服务每次新建，回放无意义；持久化/注入服务才启用收据
        request_id = None
    if request_id:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        receipt = current.store.get_receipt(request_id)
        if receipt and receipt["payload_hash"] == digest:
            return receipt["response_json"]

    action = body.get("action")
    if action not in DISPATCH:
        raise ValueError(f"不支持的请求动作: {action}")
    method_name, fields = DISPATCH[action]
    method: Callable[..., Any] = getattr(current, method_name)
    kwargs = {}
    for name in fields:
        if name in body:
            kwargs[name] = body[name]
    result = method(**kwargs)
    output = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if request_id:
        current.store.save_receipt(request_id, digest, output)
    return output


def handle_bundle(raw: str, service: Service | None = None) -> str:
    """依次应用一批请求（JSON 数组），用于装载种子数据；返回各步结果。"""
    bodies = json.loads(raw)
    current = service or Service()
    answers = []
    for item in bodies:
        answers.append(json.loads(handle(json.dumps(item, ensure_ascii=False), current)))
    return json.dumps({"applied": len(bodies), "results": answers},
                      ensure_ascii=False, sort_keys=True)
