"""处理进程内 JSON 请求。

请求体形如 ``{"action": "...", ...参数}``；带 ``request_id`` 时按幂等键处理，
同一键重放返回首次结果，键相同但载荷不同会报冲突。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from .service import Service


def _legacy_register(service: Service, body: dict[str, Any]) -> dict[str, Any]:
    return service.register(str(body["record_id"]), str(body["owner_id"]))


def _legacy_find(service: Service, body: dict[str, Any]) -> dict[str, Any] | None:
    return service.find(str(body["record_id"]))


def _explain_text(service: Service, body: dict[str, Any]) -> dict[str, str]:
    return {"text": service.explain_text(
        str(body["student_id"]), str(body["node_id"]), body.get("on_date"))}


def _add_objective(service: Service, body: dict[str, Any]) -> dict[str, Any]:
    return service.add_objective(str(body["course_id"]), str(body["node_id"]))


def _add_equipment(service: Service, body: dict[str, Any]) -> dict[str, Any]:
    return service.add_equipment(str(body["course_id"]), str(body["equipment_id"]))


# action -> (处理函数, 是否为查询类)
DISPATCH: dict[str, tuple[Callable[[Service, dict[str, Any]], Any], bool]] = {
    # 旧骨架
    "health": (lambda s, b: s.health(), True),
    "register": (_legacy_register, False),
    "find": (_legacy_find, True),
    # 登记
    "register_standard_version": (lambda s, b: s.register_standard_version(b), False),
    "register_node": (lambda s, b: s.register_node(b), False),
    "register_course": (lambda s, b: s.register_course(b), False),
    "add_objective": (_add_objective, False),
    "add_equipment": (_add_equipment, False),
    "add_teacher_authorization": (lambda s, b: s.add_teacher_authorization(b), False),
    "add_endorsement": (lambda s, b: s.add_endorsement(b), False),
    "add_equivalence": (lambda s, b: s.add_equivalence(b), False),
    "register_student": (lambda s, b: s.register_student(b), False),
    "register_credit_rule": (lambda s, b: s.register_credit_rule(b), False),
    "register_program": (lambda s, b: s.register_program(b), False),
    # 证据 / 授予 / 毕业
    "submit_evidence": (lambda s, b: s.submit_evidence(b), False),
    "award_credit": (lambda s, b: s.award_credit(b), False),
    "awards": (lambda s, b: s.awards(str(b["student_id"]), b.get("on_date")), True),
    "graduate": (lambda s, b: s.graduate(b), False),
    "graduation_check": (lambda s, b: s.graduation_check(
        str(b["student_id"]), b.get("on_date")), True),
    # 解释
    "explain": (lambda s, b: s.explain(
        str(b["student_id"]), str(b["node_id"]), b.get("on_date")), True),
    "explain_text": (_explain_text, True),
    # 换版 / 撤回 / 批次
    "upgrade_standard": (lambda s, b: s.upgrade_standard(b), False),
    "upgrade_impact": (lambda s, b: s.upgrade_impact(str(b["event_id"])), True),
    "withdraw_endorsement": (lambda s, b: s.withdraw_endorsement(b), False),
    "create_review_batch": (lambda s, b: s.create_review_batch(b), False),
    "run_batch": (lambda s, b: s.run_batch(b), False),
    "batch_status": (lambda s, b: s.batch_status(str(b["batch_id"])), True),
}


def handle(raw: str, service: Service | None = None) -> str:
    current = service or Service()
    body = json.loads(raw)
    action = body.get("action")
    handler = DISPATCH.get(action)
    if handler is None:
        raise ValueError(f"不支持的请求动作：{action}")

    request_id = body.get("request_id")
    if request_id:
        key = str(request_id)
        payload_hash = hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        cached = current.store.get_receipt_row(key)
        if cached is not None:
            if cached["payload_hash"] != payload_hash:
                raise ValueError(f"request_id={key} 曾用于不同请求，拒绝重复提交")
            return cached["response_json"]

    func, _is_query = handler
    result = func(current, body)
    output = json.dumps(result, ensure_ascii=False, sort_keys=True)

    if request_id:
        current.store.save_receipt(str(request_id), payload_hash, output)
    return output
