"""测试夹具：搭建覆盖软件测试、轨道车辆、智慧安防三个方向的等价网。

同一岗位能力节点（如 N-AUTO）可被多个赛项方向的课程覆盖，节点是去重锚点，
因此学生不会因课程名称不同而被重复计学分。
"""
from __future__ import annotations

from skill_equivalence.clock import FixedClock
from skill_equivalence.service import Service
from skill_equivalence.store import Store

# 关键日期
D_EARLY = "2026-06-01"       # 取证 / 授予日
D_2026 = "2026-10-01"
D_PRE_EFFECTIVE = "2026-12-15"  # 新版生效前
D_EFFECTIVE = "2027-01-01"
D_AFTER = "2027-02-01"       # 换版生效后、保护期内
D_WITHDRAW = "2027-03-01"
D_FAR = "2029-06-02"         # 保护期过后


def build_service(fixed_date: str = D_2026) -> Service:
    service = Service(Store(), FixedClock(fixed_date))

    # 赛项标准 2024 版
    service.register_standard_version({
        "standard_id": "WSC", "version": "2024",
        "title": "世界技能大赛标准 2024 版", "effective_date": "2024-09-01"})

    # 能力节点：N-AUTO 横跨软件测试与轨道车辆；N-MON 横跨轨道车辆与智慧安防
    service.register_node({"node_id": "N-AUTO", "name": "自动化测试与检测",
                           "standard_id": "WSC", "version": "2024"})
    service.register_node({"node_id": "N-MON", "name": "智能监测",
                           "standard_id": "WSC", "version": "2024"})
    service.register_node({"node_id": "N-SEC", "name": "安防系统集成",
                           "standard_id": "WSC", "version": "2024"})
    service.register_node({"node_id": "N-BASE", "name": "安全规程（先修）",
                           "standard_id": "WSC", "version": "2024"})
    # 复合能力（默认 forbidden：不许局部证明越权拼装）
    service.register_node({"node_id": "C-TEST", "name": "软件测试复合运维",
                           "standard_id": "WSC", "version": "2024",
                           "composite": True, "assembly": "forbidden",
                           "slots": [["N-AUTO", False], ["N-MON", False]]})
    # 可按槽位组装的复合能力
    service.register_node({"node_id": "C-SLOT", "name": "智慧安防综合实施",
                           "standard_id": "WSC", "version": "2024",
                           "composite": True, "assembly": "slots_allowed",
                           "slots": [["N-AUTO", True], ["N-SEC", False]]})

    # 课程
    service.register_course({"course_id": "C1", "name": "软件测试实训",
                             "equipment_required": ["EQ-SW"]})
    service.register_course({"course_id": "C2", "name": "轨道车辆检测（上）"})
    service.register_course({"course_id": "C3", "name": "轨道车辆监测（半程）"})
    service.register_course({"course_id": "C4", "name": "智慧安防监测（半程）"})
    service.register_course({"course_id": "C5", "name": "软件测试复合项目课"})
    service.register_course({"course_id": "C0", "name": "安全规程基础"})
    service.register_course({"course_id": "C-SEC1", "name": "安防集成实训"})

    # 教师授权
    for tid, cid in [("T1", "C1"), ("T2", "C2"), ("T3", "C3"), ("T4", "C4"),
                     ("T5", "C5"), ("T0", "C0"), ("TS", "C-SEC1")]:
        service.add_teacher_authorization({
            "teacher_id": tid, "course_id": cid,
            "valid_from": "2024-01-01", "valid_until": None})

    # 企业认可
    service.add_endorsement({"enterprise_id": "E1", "node_id": "N-AUTO",
                             "valid_from": "2024-01-01"})
    service.add_endorsement({"enterprise_id": "E5", "node_id": "C-TEST",
                             "valid_from": "2024-01-01"})

    # 等价关系
    service.add_equivalence({  # 完整覆盖，要求企业 E1 认可与先修 N-BASE
        "equivalence_id": "eq-c1-auto", "course_id": "C1", "node_id": "N-AUTO",
        "coverage": "full", "enterprise_id": "E1",
        "prerequisites": ["N-BASE"], "valid_from": "2024-09-01"})
    service.add_equivalence({  # 轨道车辆方向也覆盖同一 N-AUTO：0.5 且允许叠加
        "equivalence_id": "eq-c2-auto", "course_id": "C2", "node_id": "N-AUTO",
        "coverage": "partial", "coverage_share": 0.5, "stack_partials": True,
        "valid_from": "2024-09-01"})
    service.add_equivalence({  # 又一条 0.5 可叠加局部证明
        "equivalence_id": "eq-c2b-auto", "course_id": "C2", "node_id": "N-AUTO",
        "coverage": "partial", "coverage_share": 0.5, "stack_partials": True,
        "valid_from": "2025-09-01"})
    # N-MON：两个半程课都是局部证明，且均不允许叠加 —— 不能越权拼合
    service.add_equivalence({
        "equivalence_id": "eq-c3-mon", "course_id": "C3", "node_id": "N-MON",
        "coverage": "partial", "coverage_share": 0.5, "stack_partials": False,
        "valid_from": "2024-09-01"})
    service.add_equivalence({
        "equivalence_id": "eq-c4-mon", "course_id": "C4", "node_id": "N-MON",
        "coverage": "partial", "coverage_share": 0.5, "stack_partials": False,
        "valid_from": "2024-09-01"})
    # 复合能力完整签发通道
    service.add_equivalence({
        "equivalence_id": "eq-c5-ctest", "course_id": "C5", "node_id": "C-TEST",
        "coverage": "full", "issue_scope": "composite", "enterprise_id": "E5",
        "valid_from": "2024-09-01"})
    # 先修课
    service.add_equivalence({
        "equivalence_id": "eq-c0-base", "course_id": "C0", "node_id": "N-BASE",
        "coverage": "full", "valid_from": "2024-09-01"})
    # 安防集成
    service.add_equivalence({
        "equivalence_id": "eq-sec1", "course_id": "C-SEC1", "node_id": "N-SEC",
        "coverage": "full", "valid_from": "2024-09-01"})

    # 学分规则：N-AUTO 保护期 730 天（长保护期场景）
    service.register_credit_rule({"rule_id": "rule-auto", "node_id": "N-AUTO",
                                  "credits": 4, "protection_days": 730})
    service.register_credit_rule({"rule_id": "rule-mon", "node_id": "N-MON",
                                  "credits": 3, "protection_days": 180})
    service.register_credit_rule({"rule_id": "rule-sec", "node_id": "N-SEC",
                                  "credits": 3, "protection_days": 730})
    service.register_credit_rule({"rule_id": "rule-base", "node_id": "N-BASE",
                                  "credits": 1, "protection_days": 730})
    service.register_credit_rule({"rule_id": "rule-ctest", "node_id": "C-TEST",
                                  "credits": 6, "protection_days": 730})
    service.register_credit_rule({"rule_id": "rule-cslot", "node_id": "C-SLOT",
                                  "credits": 5, "protection_days": 730})

    # 培养方案
    service.register_program({
        "program_id": "P1", "name": "软件技术专业培养方案",
        "required_nodes": ["N-BASE", "N-AUTO", "C-TEST"]})
    service.register_program({
        "program_id": "P2", "name": "智慧安防专业培养方案",
        "required_nodes": ["N-BASE", "N-SEC", "C-SLOT"]})

    # 学生
    for sid, name, program in [
            ("s1", "软件测试方向学生", "P1"),
            ("s2", "轨道车辆方向学生", "P1"),
            ("s3", "安防方向学生", "P2"),
            ("s4", "待毕业学生", "P1")]:
        service.register_student({"student_id": sid, "name": name, "program_id": program})

    return service


def pass_course(service: Service, student_id: str, course_id: str, teacher_id: str,
                evidence_date: str = D_EARLY, equipment: list[str] | None = None,
                evidence_id: str | None = None) -> dict:
    return service.submit_evidence({
        "evidence_id": evidence_id, "student_id": student_id,
        "course_id": course_id, "teacher_id": teacher_id,
        "evidence_date": evidence_date,
        "equipment": equipment if equipment is not None else []})
