# 技能标准课程等价网

面向世界技能大赛新增赛项后的教务场景：保存**赛项标准版本、岗位能力节点、
课程目标、实训设备前提、教师授权、学生证据包、企业认可期和学分规则**，
并在其上完成能力等价认定、学分去重、标准换版影响分析、企业撤回处置与
批量审查。只依赖 Python 标准库（SQLite）。

## 核心业务规则

- **等价关系必须说明覆盖程度与先修依赖**：覆盖程度为 `complete` / `partial`。
  叶子能力只接受 `complete` 证明；**多个 `partial` 局部证明不能越权拼成
  完整资格**，只作为进展展示。复合能力可由一条整体完整证明认定，或要求
  其全部组成节点各自完整证明（组成关系和先修依赖递归校验）。
- **同一能力全局去重**：能力以 `competency_id` 为全局身份，软件测试、
  轨道车辆、智慧安防项目中形成的同一项能力（如"电气安全操作"）只计一次
  学分；重复证明会被列出但不重复计学分。
- **版本与指纹**：每个能力节点带内容指纹 `content_hash`。标准换版后内容
  未变的节点指纹不变，旧证据、按指纹授权的教师、跨赛项企业认可继续有效；
  指纹变化的节点才进入"受影响"重审范围。教师授权既可绑定精确版本，也可
  绑定能力指纹跨版本延续。
- **逐一把关**：认定一条证明需同时通过标准生效日、证据取得日、版本兼容、
  先修依赖、实训设备核验、教师授权（授权不得晚于取证）、企业认可
  （active 且在认可期内）。
- **只重审受影响能力**：`upgrade_impact` 按指纹差异圈定变更/移除/新增节点，
  受影响叶子沿复合结构上卷，输出需要更新的课程、需要重审/可以延续的教师
  授权、需要修订的培养方案；未受影响部分直接沿用。
- **历史依据不可篡改**：`credit_awards` 表由触发器禁止 UPDATE/DELETE。
  已授予且仍在保护期（授予日 + `protection_days`）内的学分保留历史依据
  （`protected`）；保护期届满后才回到现版本实时判定。保护期不向授予日之前
  回溯。
- **企业撤回**：撤回后阻止该能力的一切新等价认定（登记新关系直接拒绝，
  既有证明实时判定失败），系统列出失去依据的**未毕业**学生并生成补足路径；
  **往届毕业生记录不变**，也不为其生成补足路径。
- **批量审查断点续跑**：每处理完一名学生立即落盘断点，中断后用同一
  `batch_id` 重跑只处理剩余项（可跨进程）。
- **模拟日期**：所有判定接受 `as_of` / `--date`，用于检验标准生效日、
  企业认可期与学分保护期。

## 目录

- `src/skill_equivalence/domain.py` — 不可变登记对象与状态常量。
- `src/skill_equivalence/clock.py` — 系统时钟与 `FixedClock`、日期工具。
- `src/skill_equivalence/store.py` — SQLite 表结构、只追加触发器与查询。
- `src/skill_equivalence/engine.py` — 等价判定引擎（覆盖度/先修/版本/设备/
  授权/认可/保护期/补足路径）。
- `src/skill_equivalence/service.py` — 应用服务（登记、授予、换版、撤回、批处理）。
- `src/skill_equivalence/api.py` — 进程内 JSON 请求边界（含 `request_id` 幂等）。
- `src/skill_equivalence/cli.py` — 命令行。
- `tests/scenario_world.py` — 三赛项（软件测试/轨道车辆/智慧安防）完整场景夹具。
- `tests/test_equivalence.py` — 26 项业务规则测试。

## 运行

```bash
python3 -m unittest discover -s tests -v   # 运行测试
python3 -m compileall src                    # 源码检查
```

## 命令行

```bash
# 解释某名学生为何满足/缺少一项（或全部）复合能力（退出码 0 满足 / 2 有缺口）
python3 -m skill_equivalence.cli explain --db net.db --student s-001 \
    --standard std_swtest@2024 --date 2025-09-01 [--competency c_test_plan]

# 一次换版具体影响哪些课程、教师和培养方案
python3 -m skill_equivalence.cli impact --db net.db \
    --old std_swtest@2022 --new std_swtest@2024

# 企业撤回后未毕业学生的补足路径
python3 -m skill_equivalence.cli supplement --db net.db --student s-003 \
    --standard std_security@2023 --date 2025-03-01

# 批量审查，断点续跑：中断后用同一 --batch-id 重跑
python3 -m skill_equivalence.cli batch --db net.db --kind review \
    --standard std_swtest@2022 --students s-001 s-002 --batch-id b-1 --max-steps 1

# 兼容旧入口：标准输入单条 JSON 请求
printf '%s' '{"action":"health"}' | python3 -m skill_equivalence.cli
```

## JSON 请求

请求形如：

```json
{"action": "explain_student", "student_id": "s-001",
 "standard_ref": "std_swtest@2024", "as_of": "2025-09-01",
 "db": "net.db", "request_id": "可选幂等键"}
```

主要动作：`register_standard`、`register_competency`、`register_part`、
`register_prerequisite`、`register_equipment`、`register_course_objective`、
`register_link`、`mark_equipment_used`、`authorize_teacher`、
`revoke_teacher_authorization`、`register_program`、
`register_program_requirement`、`register_student`、`add_evidence`、
`add_recognition`、`withdraw_recognition`、`add_credit_rule`、`award_credit`、
`protected_credits`、`explain_student`、`supplement_path`、`upgrade_impact`、
`start_batch`、`resume_batch`。

## 三赛项场景（测试夹具）

`tests/scenario_world.py` 构建了一个贯穿全部规则的世界：软件测试 2022→2024
换版（`c_test_plan` 内容变更、`c_ai_test` 新增）、三赛项共享的
`c_elec_safety` 电气安全能力、两门只给局部覆盖的工坊课、2025 年安信科技
撤回智慧安防认可、张三的 2023 年历史学分与周八的往届毕业记录。
