"""标准换版测试：只重审受影响能力、保护期保留、影响面分析。"""
import json
import unittest

from scenario import (D_EARLY, D_AFTER, D_PRE_EFFECTIVE, D_EFFECTIVE, D_FAR,
                      build_service, pass_course)
from skill_equivalence.evaluation import SAT_PROTECTED
from skill_equivalence import domain as d


class 标准换版测试(unittest.TestCase):
    def setUp(self):
        self.service = build_service()
        # s1 在旧版下取得 N-AUTO 学分（保护期 730 天，至 2028-06 左右）
        pass_course(self.service, "s1", "C0", "T0")
        pass_course(self.service, "s1", "C1", "T1", equipment=["EQ-SW"])
        self.award = self.service.award_credit({
            "student_id": "s1", "node_id": "N-AUTO", "on_date": D_EARLY})
        self.assertTrue(self.award["awarded"])

    def _upgrade(self, **extra):
        params = {
            "standard_id": "WSC", "new_version": "2026",
            "title": "世界技能大赛标准 2026 版",
            "effective_date": D_EFFECTIVE,
            "affected_node_ids": ["N-AUTO"],   # 只影响 N-AUTO
            "event_id": "upg-1",
            # 新版节点：node_id 不变（同一能力锚点），挂到新版本
            "nodes": [{"node_id": "N-AUTO", "name": "自动化测试与检测（新版）",
                       "standard_id": "WSC", "version": "2026"}],
        }
        params.update(extra)
        return self.service.upgrade_standard(params)

    def test_影响面包含课程教师与培养方案(self):
        result = self._upgrade()
        impact = result["impact"]
        self.assertIn("C1", impact["affected_courses"])
        self.assertIn("C2", impact["affected_courses"])
        self.assertIn("T1", impact["affected_teachers"])
        self.assertIn("P1", impact["affected_programs"])
        # 未受影响能力 N-MON 的等价仍 active
        self.assertTrue(all(
            eq.status == d.EQUIV_ACTIVE
            for eq in self.service.store.equivalences_for_node("N-MON")))
        # 受影响旧等价已失效
        self.assertTrue(all(
            eq.status == d.EQUIV_SUPERSEDED
            for eq in self.service.store.equivalences_for_node("N-AUTO")
            if eq.version == "2024"))

    def test_生效日前旧通道仍过渡有效(self):
        self._upgrade()
        # 新版生效前，旧等价仍可用于受理
        ev = pass_course(self.service, "s4", "C0", "T0",
                         evidence_date=D_PRE_EFFECTIVE)
        self.assertTrue(ev["accepted"])
        ev2 = pass_course(self.service, "s4", "C1", "T1",
                          evidence_date=D_PRE_EFFECTIVE, equipment=["EQ-SW"])
        self.assertTrue(ev2["accepted"], ev2["reasons"])
        # 生效日后旧通道不再可用
        ev3 = pass_course(self.service, "s4", "C1", "T1",
                          evidence_date=D_AFTER, equipment=["EQ-SW"])
        self.assertFalse(ev3["accepted"])

    def test_保护期内历史学分保留依据(self):
        self._upgrade()
        ev = self.service.explain("s1", "N-AUTO", D_AFTER)
        self.assertTrue(ev["satisfied"])
        self.assertEqual(ev["status"], SAT_PROTECTED)
        # 授予行原样保留旧版本依据
        award_row = self.service.store.awards_for_student("s1", "N-AUTO")[0]
        self.assertEqual(award_row.version, "2024")
        self.assertEqual(award_row.equivalence_id, "eq-c1-auto")

    def test_换版自动建立重审批次且只含未毕业在审学生(self):
        result = self._upgrade()
        status = self.service.batch_status(result["review_batch_id"])
        targets = {(i["student_id"], i["node_id"]) for i in status["items"]}
        self.assertIn(("s1", "N-AUTO"), targets)
        self.assertNotIn(("s1", "N-MON"), targets)

    def test_保护期过后且无新通道时失去依据(self):
        self._upgrade()
        ev = self.service.explain("s1", "N-AUTO", D_FAR)
        self.assertFalse(ev["satisfied"])
        self.assertEqual(ev["awards"][0]["status"], "expired")
        self.assertTrue(any("换版" in r for r in ev["reasons"]))

    def test_登记新等价后可按新版补足(self):
        # 换版同时给出新通道：新课程 C1N 完整覆盖
        self.service.register_course({"course_id": "C1N", "name": "智能测试新版实训",
                                      "equipment_required": ["EQ-SW"]})
        self.service.add_teacher_authorization({
            "teacher_id": "T1", "course_id": "C1N",
            "valid_from": "2024-01-01"})
        self.service.add_endorsement({"enterprise_id": "E1", "node_id": "N-AUTO",
                                      "valid_from": "2024-01-01"})
        result = self._upgrade(new_equivalences=[{
            "course_id": "C1N", "node_id": "N-AUTO", "coverage": "full",
            "enterprise_id": "E1", "prerequisites": ["N-BASE"],
            "valid_from": D_EFFECTIVE}])
        self.assertEqual(len(result["impact"]["new_equivalences"]), 1)
        impact = self.service.upgrade_impact("upg-1")
        self.assertEqual(impact["new_version"], "2026")


if __name__ == "__main__":
    unittest.main()
