"""命令行端到端测试：持久化数据库、模拟日期、解释输出与换版影响查询。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from skill_equivalence.cli import build_service  # noqa: F401  确认入口可导入

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
ENV = {**os.environ, "PYTHONPATH": SRC}


def cli(db_path: str, payload: dict, date: str | None = None) -> tuple[int, str, str]:
    args = [sys.executable, "-m", "skill_equivalence.cli", "--db", db_path]
    if date:
        args += ["--date", date]
    proc = subprocess.run(args, input=json.dumps(payload), capture_output=True,
                          text=True, env=ENV, cwd=ROOT)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


class 命令行端到端测试(unittest.TestCase):
    def test_health与旧动作兼容(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "data.sqlite")
            code, out, err = cli(db, {"action": "health"})
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["status"], "ok")
            code, out, err = cli(db, {"action": "register",
                                      "record_id": "r1", "owner_id": "o1"})
            self.assertEqual(code, 0, err)
            code, out, err = cli(db, {"action": "find", "record_id": "r1"})
            self.assertEqual(json.loads(out)["state"], "draft")

    def test_模拟日期检验保护期与文本解释(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "data.sqlite")
            from skill_equivalence.service import Service
            from skill_equivalence.store import Store
            from skill_equivalence.clock import FixedClock
            svc = Service(Store(db), FixedClock("2026-06-01"))
            # 复用场景夹具（夹具自建内存库，这里改为手动最小注册）
            svc.register_standard_version({
                "standard_id": "WSC", "version": "2024",
                "title": "标准", "effective_date": "2024-09-01"})
            svc.register_node({"node_id": "N-AUTO", "name": "自动化测试",
                               "standard_id": "WSC", "version": "2024"})
            svc.register_node({"node_id": "N-BASE", "name": "安全规程",
                               "standard_id": "WSC", "version": "2024"})
            svc.register_course({"course_id": "C0", "name": "安全规程"})
            svc.register_course({"course_id": "C1", "name": "测试实训",
                                 "equipment_required": ["EQ-SW"]})
            svc.add_teacher_authorization({"teacher_id": "T0", "course_id": "C0",
                                           "valid_from": "2024-01-01"})
            svc.add_teacher_authorization({"teacher_id": "T1", "course_id": "C1",
                                           "valid_from": "2024-01-01"})
            svc.add_endorsement({"enterprise_id": "E1", "node_id": "N-AUTO",
                                 "valid_from": "2024-01-01"})
            svc.add_equivalence({"equivalence_id": "eq-base", "course_id": "C0",
                                 "node_id": "N-BASE", "coverage": "full",
                                 "valid_from": "2024-09-01"})
            svc.add_equivalence({"equivalence_id": "eq-auto", "course_id": "C1",
                                 "node_id": "N-AUTO", "coverage": "full",
                                 "enterprise_id": "E1", "prerequisites": ["N-BASE"],
                                 "valid_from": "2024-09-01"})
            svc.register_credit_rule({"rule_id": "r-auto", "node_id": "N-AUTO",
                                      "credits": 4, "protection_days": 30})
            svc.register_program({"program_id": "P1", "name": "方案",
                                  "required_nodes": ["N-BASE", "N-AUTO"]})
            svc.register_student({"student_id": "s1", "name": "学生", "program_id": "P1"})
            svc.submit_evidence({"evidence_id": "e0", "student_id": "s1", "course_id": "C0",
                                 "teacher_id": "T0", "evidence_date": "2026-06-01"})
            svc.submit_evidence({"evidence_id": "e1", "student_id": "s1", "course_id": "C1",
                                 "teacher_id": "T1", "evidence_date": "2026-06-01",
                                 "equipment": ["EQ-SW"]})
            svc.award_credit({"student_id": "s1", "node_id": "N-AUTO",
                              "on_date": "2026-06-01"})

            # 保护期内（2026-06-15）满足
            code, out, err = cli(db, {"action": "explain_text", "student_id": "s1",
                                      "node_id": "N-AUTO"}, date="2026-06-15")
            self.assertEqual(code, 0, err)
            self.assertIn("满足", out)
            self.assertIn("保护期", out)
            # 保护期外（撤回后）不满足，文本含补足路径
            svc.withdraw_endorsement({"enterprise_id": "E1", "node_id": "N-AUTO",
                                      "on_date": "2026-06-20"})
            code, out, err = cli(db, {"action": "explain_text", "student_id": "s1",
                                      "node_id": "N-AUTO"}, date="2026-08-01")
            self.assertEqual(code, 0, err)
            self.assertIn("不满足", out)
            self.assertIn("补足路径", out)

    def test_幂等重放(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "data.sqlite")
            payload = {"action": "register", "record_id": "r2", "owner_id": "o2",
                       "request_id": "req-1"}
            code, out1, err = cli(db, payload)
            self.assertEqual(code, 0, err)
            code, out2, err = cli(db, payload)  # 重放
            self.assertEqual(code, 0, err)
            self.assertEqual(out1, out2)
            changed = dict(payload, owner_id="other")
            code, out, err = cli(db, changed)
            self.assertEqual(code, 2)
            self.assertIn("request_id", err)


if __name__ == "__main__":
    unittest.main()
