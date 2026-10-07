"""企业撤回认可测试：阻断新认定、未毕业补足路径、往届记录不可篡改。"""
import unittest

from scenario import D_EARLY, D_WITHDRAW, D_FAR, build_service, pass_course
from skill_equivalence import domain as d
from skill_equivalence.evaluation import SAT_BLOCKED, SAT_PROTECTED


class 企业撤回测试(unittest.TestCase):
    def setUp(self):
        self.service = build_service()

    def _award_student(self, sid, on_date=D_EARLY):
        pass_course(self.service, sid, "C0", "T0")
        pass_course(self.service, sid, "C1", "T1", equipment=["EQ-SW"])
        return self.service.award_credit({"student_id": sid, "node_id": "N-AUTO",
                                          "on_date": on_date})

    def test_撤回后新等价认定被阻断(self):
        result = self.service.withdraw_endorsement({
            "enterprise_id": "E1", "node_id": "N-AUTO", "on_date": D_WITHDRAW})
        self.assertIn("eq-c1-auto", result["blocked_equivalences"])
        # 通道状态为 blocked
        eq = self.service.store.get_equivalence("eq-c1-auto")
        self.assertEqual(eq.status, d.EQUIV_BLOCKED)
        # 再受理证据被拒（企业认可无效）
        pass_course(self.service, "s3", "C0", "T0", evidence_id="ev-base")
        ev_pack = pass_course(self.service, "s3", "C1", "T1",
                              evidence_id="ev-c1-late",
                              evidence_date=D_WITHDRAW, equipment=["EQ-SW"])
        self.assertFalse(ev_pack["accepted"])
        # 解释为不满足，并明确指出被阻断的通道与替代/补足方向
        explanation = self.service.explain("s3", "N-AUTO", D_WITHDRAW)
        self.assertFalse(explanation["satisfied"])
        blocked_channels = [c for c in explanation["channels"] if c["blocked"]]
        self.assertTrue(blocked_channels)
        self.assertIn("eq-c1-auto", {c["equivalence_id"] for c in blocked_channels})
        self.assertTrue(any(p["kind"] == "blocked" for p in explanation["remediation"]))

    def test_保护期内未毕业学生仍有依据且得到补足路径(self):
        award = self._award_student("s1")
        result = self.service.withdraw_endorsement({
            "enterprise_id": "E1", "node_id": "N-AUTO", "on_date": D_WITHDRAW})
        # s1 仍在保护期内 -> satisfied，且出现在风险名单中
        s1 = next(s for s in result["at_risk_students"] if s["student_id"] == "s1")
        self.assertEqual(s1["status"], SAT_PROTECTED)
        self.assertTrue(s1["satisfied"])
        ev = self.service.explain("s1", "N-AUTO", D_WITHDRAW)
        self.assertTrue(ev["satisfied"])
        self.assertEqual(ev["status"], SAT_PROTECTED)
        # 保护期过后：无其他通道，不满足，但必须能看到补足方向
        ev_far = self.service.explain("s1", "N-AUTO", D_FAR)
        self.assertFalse(ev_far["satisfied"])
        self.assertTrue(ev_far["remediation"])
        self.assertTrue(any(p["kind"] == "blocked" for p in ev_far["remediation"]))

    def test_其他企业背书的新通道可作为补足路径(self):
        self._award_student("s1")
        self.service.withdraw_endorsement({
            "enterprise_id": "E1", "node_id": "N-AUTO", "on_date": D_WITHDRAW})
        # E2 提供新认可并开新课程通道
        self.service.add_endorsement({"enterprise_id": "E2", "node_id": "N-AUTO",
                                      "valid_from": D_WITHDRAW})
        self.service.register_course({"course_id": "C1-E2", "name": "企业E2认证测试实训"})
        self.service.add_teacher_authorization({
            "teacher_id": "T9", "course_id": "C1-E2", "valid_from": D_WITHDRAW})
        self.service.add_equivalence({
            "course_id": "C1-E2", "node_id": "N-AUTO", "coverage": "full",
            "enterprise_id": "E2", "prerequisites": ["N-BASE"],
            "valid_from": D_WITHDRAW})
        ev = self.service.explain("s1", "N-AUTO", D_FAR)
        # 旧授予过期，但补足路径中出现 E2 通道
        course_paths = [p for p in ev["remediation"] if p["kind"] == "course"]
        self.assertTrue(any(p["course_id"] == "C1-E2" for p in course_paths))

    def test_往届学生记录完全不触碰(self):
        # s4 先满足培养方案 P1 的全部要求并毕业
        self._award_student("s4")
        self.service.award_credit({"student_id": "s4", "node_id": "N-BASE",
                                   "on_date": D_EARLY})
        pass_course(self.service, "s4", "C5", "T5", evidence_id="ev-c5-s4")
        ctest = self.service.award_credit({"student_id": "s4", "node_id": "C-TEST",
                                           "on_date": D_EARLY})
        self.assertTrue(ctest["awarded"])
        check = self.service.graduation_check("s4", D_EARLY)
        self.assertTrue(check["satisfied"], check["missing_nodes"])
        graduated = self.service.graduate({"student_id": "s4", "on_date": D_EARLY})
        self.assertTrue(graduated["graduated"])
        before = self.service.store.awards_for_student("s4")
        result = self.service.withdraw_endorsement({
            "enterprise_id": "E1", "node_id": "N-AUTO", "on_date": D_WITHDRAW})
        self.assertIn("s4", result["graduates_untouched"])
        after = self.service.store.awards_for_student("s4")
        self.assertEqual([a.__dict__ for a in before], [a.__dict__ for a in after])
        # 毕业后不能再受理证据
        with self.assertRaises(ValueError):
            pass_course(self.service, "s4", "C1", "T1", equipment=["EQ-SW"])

    def test_撤回产生审查批次(self):
        self._award_student("s1")
        result = self.service.withdraw_endorsement({
            "enterprise_id": "E1", "node_id": "N-AUTO", "on_date": D_WITHDRAW})
        status = self.service.batch_status(result["review_batch_id"])
        student_ids = {i["student_id"] for i in status["items"]}
        self.assertIn("s1", student_ids)
        self.assertNotIn("s4", student_ids)


if __name__ == "__main__":
    unittest.main()
