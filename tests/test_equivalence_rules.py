"""等价关系规则测试：覆盖程度、先修依赖、防重复计算、局部证明越权拼接。"""
import unittest

from scenario import (D_EARLY, D_AFTER, build_service, pass_course)
from skill_equivalence.evaluation import (
    SAT_CURRENT, SAT_EVIDENCE_READY, SAT_MISSING, SAT_PARTIAL, SAT_PREREQ,
    SAT_PROTECTED)


class 等价认定测试(unittest.TestCase):
    def setUp(self):
        self.service = build_service()

    def test_设备前提与教师授权是受理条件(self):
        # 缺设备
        rejected = pass_course(self.service, "s1", "C1", "T1")
        self.assertFalse(rejected["accepted"])
        self.assertTrue(any("设备" in r for r in rejected["reasons"]))
        # 教师无授权
        rejected2 = pass_course(self.service, "s1", "C1", "T9", equipment=["EQ-SW"])
        self.assertFalse(rejected2["accepted"])
        self.assertTrue(any("授权" in r for r in rejected2["reasons"]))
        # 授权过期日取证不被接受
        self.service.add_teacher_authorization({
            "teacher_id": "T1B", "course_id": "C1",
            "valid_from": "2024-01-01", "valid_until": "2025-12-31"})
        rejected3 = pass_course(self.service, "s1", "C1", "T1B", equipment=["EQ-SW"])
        self.assertFalse(rejected3["accepted"])
        # 全部齐备才受理
        ok = pass_course(self.service, "s1", "C1", "T1", equipment=["EQ-SW"])
        self.assertTrue(ok["accepted"], ok["reasons"])
        self.assertEqual(ok["coverage"], "full")

    def test_先修依赖缺失时不能认定(self):
        # C1 完整覆盖 N-AUTO，但先修 N-BASE 未满足
        pass_course(self.service, "s1", "C1", "T1", equipment=["EQ-SW"])
        ev = self.service.explain("s1", "N-AUTO", D_EARLY)
        self.assertFalse(ev["satisfied"])
        self.assertEqual(ev["status"], SAT_PREREQ)
        self.assertIn("N-BASE", ev["missing_prerequisites"])
        # 补齐先修
        pass_course(self.service, "s1", "C0", "T0")
        ev = self.service.explain("s1", "N-AUTO", D_EARLY)
        self.assertTrue(ev["satisfied"])
        self.assertEqual(ev["status"], SAT_EVIDENCE_READY)

    def test_同名能力跨课程不重复计算(self):
        # s2 用轨道车辆课程的两条 0.5 局部证明覆盖同一个 N-AUTO，且补了先修
        pass_course(self.service, "s2", "C0", "T0")
        self.service.submit_evidence({
            "evidence_id": "ev-c2-a", "student_id": "s2", "course_id": "C2",
            "teacher_id": "T2", "evidence_date": D_EARLY,
            "equivalence_id": "eq-c2-auto"})
        self.service.submit_evidence({
            "evidence_id": "ev-c2-b", "student_id": "s2", "course_id": "C2",
            "teacher_id": "T2", "evidence_date": D_EARLY,
            "equivalence_id": "eq-c2b-auto"})
        ev = self.service.explain("s2", "N-AUTO", D_EARLY)
        # 0.5+0.5 且授权 stack_partials -> 可拼合
        self.assertTrue(ev["satisfied"], ev["reasons"])
        award = self.service.award_credit({"student_id": "s2", "node_id": "N-AUTO"})
        self.assertTrue(award["awarded"])
        # 再次授予直接被拒（同一能力不重复计学分）
        again = self.service.award_credit({"student_id": "s2", "node_id": "N-AUTO"})
        self.assertFalse(again["awarded"])

    def test_未授权叠加的局部证明不能越权拼成资格(self):
        # C3/C4 各 0.5 覆盖 N-MON，但两条等价都不允许叠加
        pass_course(self.service, "s1", "C3", "T3", evidence_id="ev-c3")
        pass_course(self.service, "s1", "C4", "T4", evidence_id="ev-c4")
        ev = self.service.explain("s1", "N-MON", D_EARLY)
        self.assertFalse(ev["satisfied"])
        self.assertEqual(ev["status"], SAT_PARTIAL)
        self.assertTrue(any("越权" in r or "叠加" in r for r in ev["reasons"]))
        # 份额即使合计到 1，授予也被拒
        award = self.service.award_credit({"student_id": "s1", "node_id": "N-MON"})
        self.assertFalse(award["awarded"])

    def test_份额不足的局部证明仍为partial(self):
        # 只有一条 0.5 的可叠加局部证明
        self.service.submit_evidence({
            "evidence_id": "ev-c2-only", "student_id": "s3", "course_id": "C2",
            "teacher_id": "T2", "evidence_date": D_EARLY,
            "equivalence_id": "eq-c2-auto"})
        ev = self.service.explain("s3", "N-AUTO", D_EARLY)
        self.assertFalse(ev["satisfied"])
        self.assertEqual(ev["status"], SAT_PARTIAL)
        self.assertAlmostEqual(ev["share"], 0.5)

    def test_复合能力必须走完整签发通道(self):
        # 槽位都有了：N-BASE、N-AUTO（C1 full）、N-MON 只有局部证明
        pass_course(self.service, "s1", "C0", "T0")
        pass_course(self.service, "s1", "C1", "T1", equipment=["EQ-SW"])
        pass_course(self.service, "s1", "C3", "T3")
        ev = self.service.explain("s1", "C-TEST", D_EARLY)
        self.assertFalse(ev["satisfied"])
        self.assertTrue(any("拼装" in r for r in ev["reasons"]))
        # 修读授权完整签发的复合项目课
        pass_course(self.service, "s1", "C5", "T5", evidence_id="ev-c5")
        ev = self.service.explain("s1", "C-TEST", D_EARLY)
        self.assertTrue(ev["satisfied"], ev["reasons"])

    def test_槽位组装型复合能力(self):
        # C-SLOT = N-AUTO + N-SEC（slots_allowed）
        pass_course(self.service, "s3", "C0", "T0")
        pass_course(self.service, "s3", "C1", "T1", equipment=["EQ-SW"])
        # 尚缺 N-SEC
        ev = self.service.explain("s3", "C-SLOT", D_EARLY)
        self.assertFalse(ev["satisfied"])
        self.assertEqual(len(ev["missing_slots"]), 1)
        pass_course(self.service, "s3", "C-SEC1", "TS", evidence_id="ev-sec")
        ev = self.service.explain("s3", "C-SLOT", D_EARLY)
        self.assertTrue(ev["satisfied"], ev["reasons"])
        award = self.service.award_credit({"student_id": "s3", "node_id": "C-SLOT"})
        self.assertTrue(award["awarded"])

    def test_没有任何证据时给出missing与补足路径(self):
        ev = self.service.explain("s1", "N-AUTO", D_EARLY)
        self.assertEqual(ev["status"], SAT_MISSING)
        course_paths = [p for p in ev["remediation"] if p["kind"] == "course"]
        self.assertTrue(any(p["course_id"] == "C1" for p in course_paths))


class 学分授予状态测试(unittest.TestCase):
    def setUp(self):
        self.service = build_service()
        pass_course(self.service, "s1", "C0", "T0")
        pass_course(self.service, "s1", "C1", "T1", equipment=["EQ-SW"])
        self.award = self.service.award_credit({"student_id": "s1", "node_id": "N-AUTO",
                                                "on_date": D_EARLY})
        self.assertTrue(self.award["awarded"])

    def test_授予后状态为protected且保护期后变current(self):
        ev = self.service.explain("s1", "N-AUTO", D_AFTER)
        self.assertTrue(ev["satisfied"])
        self.assertEqual(ev["status"], SAT_PROTECTED)
        self.assertEqual(ev["awards"][0]["protection_until"],
                         self.award["protection_until"])

    def test_解释文本可读(self):
        text = self.service.explain_text("s1", "N-AUTO", D_AFTER)
        self.assertIn("满足", text)
        self.assertIn("保护期", text)


if __name__ == "__main__":
    unittest.main()
