"""批量审查断点续跑测试。"""
import unittest

from scenario import D_EARLY, D_WITHDRAW, build_service, pass_course
from skill_equivalence import domain as d


class 批次断点续跑测试(unittest.TestCase):
    def setUp(self):
        self.service = build_service()
        # s1 已满足 N-AUTO；s2/s3 未满足
        pass_course(self.service, "s1", "C0", "T0")
        pass_course(self.service, "s1", "C1", "T1", equipment=["EQ-SW"])

    def test_分批执行可中断后续跑(self):
        created = self.service.create_review_batch({
            "batch_id": "b1", "node_ids": ["N-AUTO"], "on_date": D_EARLY})
        self.assertEqual(created["items"], 4)  # 4 名未毕业学生
        first = self.service.run_batch({"batch_id": "b1", "max_items": 2})
        self.assertEqual(first["processed_this_run"], 2)
        self.assertFalse(first["done"])
        self.assertEqual(first["progress"]["done"], 2)
        # 中途再跑，跳过已完成项
        second = self.service.run_batch({"batch_id": "b1", "max_items": 5})
        self.assertEqual(second["processed_this_run"], 2)
        self.assertTrue(second["done"])
        status = self.service.batch_status("b1")
        self.assertEqual(status["progress"]["done"], 4)
        results = {(i["student_id"], i["result"]["satisfied"]) for i in status["items"]}
        self.assertIn(("s1", True), results)
        self.assertIn(("s2", False), results)

    def test_崩溃遗留running项可被重新领取(self):
        self.service.create_review_batch({
            "batch_id": "b2", "student_ids": ["s1", "s2"],
            "node_ids": ["N-AUTO"], "on_date": D_EARLY})
        # 模拟领取第一项后进程崩溃：状态停留在 running
        row = self.service.store.claim_next_item("b2")
        self.assertIsNotNone(row)
        progress = self.service.store.batch_progress("b2")
        self.assertEqual(progress["running"], 1)
        # 续跑：running 项被重新领取，不丢失
        run = self.service.run_batch({"batch_id": "b2"})
        self.assertTrue(run["done"])
        self.assertEqual(run["progress"]["done"], 2)


if __name__ == "__main__":
    unittest.main()
