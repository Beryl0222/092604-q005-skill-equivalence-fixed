"""三赛项测试场景夹具。

覆盖世界技能大赛三个赛项在职业院校中的等价网：

* std_swtest 软件测试：2022 版 → 2024 版（c_test_plan 内容变更、c_ai_test 新增）
* std_rail   轨道车辆：2023 版
* std_security 智慧安防：2023 版（企业 ent-sec 后续撤回 c_cctv 认可）

c_elec_safety（电气安全）是三个赛项共享的同一项能力，用于检验去重：
学生张三持有课程、软件测试赛项、轨道车辆赛项、智慧安防赛项四份完整证明。
"""
from __future__ import annotations

from skill_equivalence.clock import FixedClock
from skill_equivalence.service import Service
from skill_equivalence.store import Store

SW2022 = "std_swtest@2022"
SW2024 = "std_swtest@2024"
RAIL2023 = "std_rail@2023"
SEC2023 = "std_security@2023"

# 能力
C_PLAN = "c_test_plan"       # 测试方案设计（2024 换版内容变更）
C_SCRIPT = "c_auto_script"   # 自动化测试脚本
C_SAFETY = "c_elec_safety"   # 电气安全（三赛项共享）
C_SW_FULL = "c_swtest_full"  # 软件测试综合实施（复合）
C_RAIL_INSPECT = "c_rail_inspect"
C_RAIL_FULL = "c_rail_full"
C_CCTV = "c_cctv"
C_AI_TEST = "c_ai_test"      # 2024 新增

HASH = {
    C_PLAN + "@2022": "h-plan-1", C_PLAN + "@2024": "h-plan-2",
    C_SCRIPT: "h-script-1", C_SAFETY: "h-safety-1",
    C_SW_FULL: "h-swfull-1",
    C_RAIL_INSPECT: "h-rail-1", C_RAIL_FULL: "h-rail-full-1",
    C_CCTV: "h-cctv-1", C_AI_TEST: "h-ai-1",
}


def build_world(path: str = ":memory:", *, withdrawn: bool = False) -> Service:
    """构建完整场景。withdrawn=True 时企业已撤回智慧安防认可。"""
    service = Service(Store(path), FixedClock("2022-09-01"))

    # -- 标准版本 ----------------------------------------------------------
    service.register_standard("std_swtest", "2022", "世界技能大赛软件测试标准", "2022-09-01")
    service.register_standard("std_rail", "2023", "世界技能大赛轨道车辆技术标准", "2023-09-01")
    service.register_standard("std_security", "2023", "世界技能大赛智慧安防标准", "2023-09-01")
    service.register_standard("std_swtest", "2024", "世界技能大赛软件测试标准", "2024-09-01",
                              supersedes=SW2022)

    # -- 2022 能力节点 ------------------------------------------------------
    service.register_competency(C_PLAN, "std_swtest", "2022", "测试方案设计",
                                content_hash=HASH[C_PLAN + "@2022"], ordinal=1)
    service.register_competency(C_SCRIPT, "std_swtest", "2022", "自动化测试脚本编写",
                                content_hash=HASH[C_SCRIPT], ordinal=2)
    service.register_competency(C_SAFETY, "std_swtest", "2022", "电气安全操作",
                                content_hash=HASH[C_SAFETY], ordinal=3)
    service.register_competency(C_SW_FULL, "std_swtest", "2022", "软件测试综合实施",
                                is_composite=True, content_hash=HASH[C_SW_FULL], ordinal=4)
    service.register_part(C_SW_FULL, C_PLAN, "std_swtest", "2022", 1)
    service.register_part(C_SW_FULL, C_SCRIPT, "std_swtest", "2022", 2)
    service.register_part(C_SW_FULL, C_SAFETY, "std_swtest", "2022", 3)
    # 自动化脚本先修电气安全
    service.register_prerequisite(C_SCRIPT, C_SAFETY, "std_swtest", "2022")

    # -- 轨道车辆 2023 ------------------------------------------------------
    service.register_competency(C_RAIL_INSPECT, "std_rail", "2023", "轨道车辆走行部检修",
                                content_hash=HASH[C_RAIL_INSPECT])
    service.register_competency(C_SAFETY, "std_rail", "2023", "电气安全操作",
                                content_hash=HASH[C_SAFETY])
    service.register_competency(C_RAIL_FULL, "std_rail", "2023", "轨道车辆综合检修",
                                is_composite=True, content_hash=HASH[C_RAIL_FULL])
    service.register_part(C_RAIL_FULL, C_RAIL_INSPECT, "std_rail", "2023", 1)
    service.register_part(C_RAIL_FULL, C_SAFETY, "std_rail", "2023", 2)

    # -- 智慧安防 2023 ------------------------------------------------------
    service.register_competency(C_CCTV, "std_security", "2023", "安防监控系统部署",
                                content_hash=HASH[C_CCTV])
    service.register_competency(C_SAFETY, "std_security", "2023", "电气安全操作",
                                content_hash=HASH[C_SAFETY])

    # -- 软件测试 2024（换版） ----------------------------------------------
    service.register_competency(C_PLAN, "std_swtest", "2024", "测试方案设计（含智能测试分析）",
                                content_hash=HASH[C_PLAN + "@2024"], ordinal=1)
    service.register_competency(C_SCRIPT, "std_swtest", "2024", "自动化测试脚本编写",
                                content_hash=HASH[C_SCRIPT], ordinal=2)
    service.register_competency(C_SAFETY, "std_swtest", "2024", "电气安全操作",
                                content_hash=HASH[C_SAFETY], ordinal=3)
    service.register_competency(C_SW_FULL, "std_swtest", "2024", "软件测试综合实施",
                                is_composite=True, content_hash=HASH[C_SW_FULL], ordinal=4)
    service.register_competency(C_AI_TEST, "std_swtest", "2024", "智能测试结果分析",
                                content_hash=HASH[C_AI_TEST], ordinal=5)
    service.register_part(C_SW_FULL, C_PLAN, "std_swtest", "2024", 1)
    service.register_part(C_SW_FULL, C_SCRIPT, "std_swtest", "2024", 2)
    service.register_part(C_SW_FULL, C_SAFETY, "std_swtest", "2024", 3)
    service.register_prerequisite(C_SCRIPT, C_SAFETY, "std_swtest", "2024")

    # -- 实训设备前提 --------------------------------------------------------
    service.register_equipment(C_SCRIPT, "eq-auto-rig", "自动化测试台", "std_swtest", "2022")
    service.register_equipment(C_SCRIPT, "eq-auto-rig", "自动化测试台", "std_swtest", "2024")
    service.register_equipment(C_CCTV, "eq-cctv-kit", "监控调试套件", "std_security", "2023")
    service.register_equipment(C_RAIL_INSPECT, "eq-bogie", "转向架检修台", "std_rail", "2023")

    # -- 课程目标 ------------------------------------------------------------
    software_courses = [
        ("SW101", "软件测试基础", C_PLAN, "complete"),
        ("SW102", "自动化测试实训", C_SCRIPT, "complete"),
        ("SW103", "电气安全规范", C_SAFETY, "complete"),
        ("SW200", "软件测试综合项目", C_SW_FULL, "complete"),
    ]
    for cid, title, comp, cov in software_courses:
        service.register_course_objective(cid, "std_swtest", "2022", title, comp, cov)
        service.register_course_objective(cid, "std_swtest", "2024", title, comp, cov)
    # 两门只给局部覆盖的工坊课（多个局部证明不能拼成完整资格）
    service.register_course_objective("SW900", "std_swtest", "2022", "测试体验工坊(上)", C_PLAN, "partial")
    service.register_course_objective("SW901", "std_swtest", "2022", "测试体验工坊(下)", C_PLAN, "partial")
    # 安防与轨道课程
    service.register_course_objective("SEC101", "std_security", "2023", "安防监控系统", C_CCTV, "complete")
    service.register_course_objective("SEC102", "std_security", "2023", "安防电气安全", C_SAFETY, "complete")
    service.register_course_objective("RAIL201", "std_rail", "2023", "走行部检修", C_RAIL_INSPECT, "complete")
    service.register_course_objective("RAIL202", "std_rail", "2023", "车辆电气安全", C_SAFETY, "complete")

    # -- 赛项证据的等价关系（同一项电气安全能力，三个赛项各一份完整证明） ----------
    service.register_link("lk-sw-safety", "evidence", "wsi-swtest-2023", C_SAFETY,
                          "complete", "std_swtest", "2022")
    service.register_link("lk-rail-safety", "evidence", "wsi-rail-2023", C_SAFETY,
                          "complete", "std_rail", "2023")
    service.register_link("lk-sec-safety", "evidence", "wsi-sec-2023", C_SAFETY,
                          "complete", "std_security", "2023")

    # -- 教师授权 -------------------------------------------------------------
    service.authorize_teacher("auth-wang-plan", "t-wang", "王老师", "std_swtest", "2022", C_PLAN)
    service.authorize_teacher("auth-li-script", "t-li", "李老师", "std_swtest", "2022", C_SCRIPT)
    service.authorize_teacher("auth-zhao-safety", "t-zhao", "赵老师", "std_swtest", "2022", C_SAFETY)
    service.authorize_teacher("auth-zhao-safety-rail", "t-zhao", "赵老师", "std_rail", "2023", C_SAFETY)
    service.authorize_teacher("auth-feng-cctv", "t-feng", "冯老师", "std_security", "2023", C_CCTV)
    service.authorize_teacher("auth-qian-rail", "t-qian", "钱老师", "std_rail", "2023", C_RAIL_INSPECT)

    # -- 企业认可与有效期 ------------------------------------------------------
    service.add_recognition("rec-soft-plan", "ent-soft", "华数软件股份公司", C_PLAN,
                            "std_swtest", "2022", "2022-09-01", "2027-12-31")
    service.add_recognition("rec-soft-script", "ent-soft", "华数软件股份公司", C_SCRIPT,
                            "std_swtest", "2022", "2022-09-01", "2027-12-31")
    service.add_recognition("rec-soft-safety", "ent-soft", "华数软件股份公司", C_SAFETY,
                            "std_swtest", "2022", "2022-09-01", "2027-12-31")
    service.add_recognition("rec-soft-plan-v2", "ent-soft", "华数软件股份公司", C_PLAN,
                            "std_swtest", "2024", "2024-09-01", "2029-12-31")
    service.add_recognition("rec-rail-inspect", "ent-rail", "中轨装备集团", C_RAIL_INSPECT,
                            "std_rail", "2023", "2023-09-01", "2028-12-31")
    service.add_recognition("rec-rail-safety", "ent-rail", "中轨装备集团", C_SAFETY,
                            "std_rail", "2023", "2023-09-01", "2028-12-31")
    service.add_recognition("rec-sec-cctv", "ent-sec", "安信科技有限公司", C_CCTV,
                            "std_security", "2023", "2023-09-01", "2027-12-31")

    # -- 培养方案 --------------------------------------------------------------
    service.register_program("prog-soft", "软件技术专业", "std_swtest", "2022")
    service.register_program_requirement("prog-soft", C_SW_FULL, 1)
    service.register_program("prog-rail", "轨道车辆专业", "std_rail", "2023")
    service.register_program_requirement("prog-rail", C_RAIL_FULL, 1)
    service.register_program("prog-sec", "智慧安防专业", "std_security", "2023")
    service.register_program_requirement("prog-sec", C_CCTV, 1)
    service.register_program_requirement("prog-sec", C_SAFETY, 2)

    # -- 学分规则 --------------------------------------------------------------
    service.add_credit_rule("rule-plan", C_PLAN, "SW101", 4.0, "std_swtest", "2022", 730)
    service.add_credit_rule("rule-script", C_SCRIPT, "SW102", 3.0, "std_swtest", "2022", 3650)
    service.add_credit_rule("rule-safety", C_SAFETY, "SW103", 2.0, "std_swtest", "2022", 3650)
    # 旧课程名下的重复学分规则：同一能力不得二次计学分
    service.add_credit_rule("rule-safety-dup", C_SAFETY, "X999", 2.0, "std_swtest", "2022", 3650)
    service.add_credit_rule("rule-cctv", C_CCTV, "SEC101", 3.0, "std_security", "2023", 3650)

    # -- 学生 ------------------------------------------------------------------
    service.register_student("s-001", "张三", "prog-soft")
    service.register_student("s-002", "李四", "prog-soft")
    service.register_student("s-003", "孙七", "prog-sec")
    service.register_student("s-004", "周八", "prog-sec", status="graduated",
                             graduated_date="2024-06-30")
    service.register_student("s-005", "吴九", "prog-rail")

    # 张三：三门课程 + 三个赛项的电气安全证明
    service.add_evidence("ev-s1-plan", "s-001", "course", "SW101", "软件测试基础成绩单", "2023-06-15")
    service.add_evidence("ev-s1-script", "s-001", "course", "SW102", "自动化测试实训成绩单",
                         "2023-07-01", used_equipment=["eq-auto-rig"])
    service.add_evidence("ev-s1-safety", "s-001", "course", "SW103", "电气安全规范成绩单", "2023-06-20")
    service.add_evidence("ev-s1-sw", "s-001", "evidence", "wsi-swtest-2023",
                         "世界技能大赛软件测试省赛电气安全模块证明", "2023-05-10")
    service.add_evidence("ev-s1-rail", "s-001", "evidence", "wsi-rail-2023",
                         "轨道车辆技术赛项电气安全模块证明", "2023-05-20")
    service.add_evidence("ev-s1-sec", "s-001", "evidence", "wsi-sec-2023",
                         "智慧安防赛项电气安全模块证明", "2023-05-25")

    # 李四：只有两份局部证明 + 缺设备核验的实训 + 无电气安全证明
    service.add_evidence("ev-s2-plan1", "s-002", "course", "SW900", "测试体验工坊(上)", "2023-10-01")
    service.add_evidence("ev-s2-plan2", "s-002", "course", "SW901", "测试体验工坊(下)", "2023-11-01")
    service.add_evidence("ev-s2-script", "s-002", "course", "SW102", "自动化测试实训成绩单（未核验设备）",
                         "2023-11-10")

    # 孙七：安防在校生，监控+电气安全齐备
    service.add_evidence("ev-s3-cctv", "s-003", "course", "SEC101", "安防监控系统成绩单",
                         "2024-05-10", used_equipment=["eq-cctv-kit"])
    service.add_evidence("ev-s3-safety", "s-003", "course", "SEC102", "安防电气安全成绩单", "2024-05-20")

    # 周八：往届毕业，持有安防学分
    service.add_evidence("ev-s4-cctv", "s-004", "course", "SEC101", "安防监控系统成绩单",
                         "2024-05-10", used_equipment=["eq-cctv-kit"])

    # 吴九：轨道车辆在校生
    service.add_evidence("ev-s5-rail", "s-005", "course", "RAIL201", "走行部检修成绩单",
                         "2024-04-10", used_equipment=["eq-bogie"])
    service.add_evidence("ev-s5-safety", "s-005", "course", "RAIL202", "车辆电气安全成绩单", "2024-04-20")

    # -- 张三的历史学分授予（2023-07-15） --------------------------------------
    service.award_credit("s-001", "rule-plan", as_of="2023-07-15")
    service.award_credit("s-001", "rule-script", as_of="2023-07-15")
    service.award_credit("s-001", "rule-safety", as_of="2023-07-15")
    # 周八的往届学分
    service.award_credit("s-004", "rule-cctv", as_of="2024-06-28")

    if withdrawn:
        service.withdraw_recognition("rec-sec-cctv", as_of="2025-02-01")

    return service
