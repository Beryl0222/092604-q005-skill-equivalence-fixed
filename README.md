# 技能标准课程等价管理

面向世界技能大赛新增赛项后的教务场景，建立"技能标准课程等价网"：保存赛项标准
版本、岗位能力节点、课程目标、实训设备前提、教师授权、学生证据包、企业认可期
与学分规则，支持带覆盖程度与先修依赖的等价认定、标准换版局部重审、企业撤回与
批量审查，并能解释任意学生为何满足或缺少一项复合能力。

只使用 Python 标准库 + SQLite。

## 目录

- `src/skill_equivalence/domain.py` —— 领域对象（标准版本、能力节点、课程、等价关系、证据包、学分授予、培养方案、批次）。
- `src/skill_equivalence/store.py` —— SQLite 表结构与读写。`credit_awards` 为仅追加表，不提供更新/删除入口。
- `src/skill_equivalence/evaluation.py` —— 只读判定与解释引擎：覆盖份额、先修递归、防越权拼装、保护期解释、补足路径。
- `src/skill_equivalence/service.py` —— 应用服务：登记、证据受理、学分授予、换版、撤回、批次、毕业。
- `src/skill_equivalence/api.py` —— 进程内 JSON 请求边界（含 `request_id` 幂等）。
- `src/skill_equivalence/cli.py` —— 命令行，支持 `--db` 持久化与 `--date` 模拟日期。
- `tests/` —— 28 个测试，`scenario.py` 是覆盖软件测试/轨道车辆/智慧安防的夹具。

## 运行

```bash
python3 -m pytest -q                                   # 若环境装有 pytest
PYTHONPATH=src:tests python3 -m unittest discover -s tests   # 仅标准库时
python3 -m compileall src
```

## 核心规则

1. **能力节点是去重锚点**：软件测试、轨道车辆、智慧安防课程若覆盖同一节点
   （如"自动化测试与检测"），只计一次学分；重复授予被拒。
2. **覆盖程度**：`full` 完整覆盖；`partial` 带 `coverage_share`(0,1) 份额。
   多个局部证明只有在等价关系显式 `stack_partials=true`（复合能力还要槽位
   可拼）时才能叠加，份额合计 ≥1 才算覆盖——**默认不能越权拼成完整资格**。
3. **复合能力组装策略**：`forbidden`（默认，只认完整签发通道）、
   `requires_issuer`（槽位补齐后仍须签发通道确认）、`slots_allowed`（槽位
   全部满足即可）。
4. **先修依赖**：等价关系可声明先修节点，递归核查，未达成则不能认定。
5. **受理前置条件**：证据包取证当天，教师授权有效、实训设备前提齐备、
   等价通道在有效期、企业认可未撤回，才会被受理并冻结依据。
6. **标准换版只重审受影响能力**：登记新版本时给出 `affected_node_ids`，
   仅这些节点挂旧版本的等价关系置 `superseded`，其余不动；生效日前旧通道
   可过渡使用；自动生成"受影响能力 × 未毕业学生"的重审批次。
7. **历史依据与保护期**：学分授予仅追加。换版/撤回后，保护期内的授予解释为
   `protected` 保留毕业依据；过期且依据失效才解释为 `expired`。授予行中保存
   当时的标准版本、等价关系、企业与槽位构成快照。
8. **企业撤回**：相关等价立即 `blocked`，新认定被阻止；系统列出尚未毕业学生
   的风险状态与补足路径（其他企业背书通道/新课程/重新认可）；**往届已毕业
   学生记录完全不触碰**，毕业后也不再受理证据。
9. **批量审查断点续跑**：每项 pending/running 都可被重新领取，进程中断后
   重跑不丢项、不重复计。

## 判定状态

`current`（依据仍有效）、`protected`（保护期内保留）、`evidence_ready`
（证据齐备可认定未授予）、`missing`、`partial`（局部证明不足或不许叠加）、
`prerequisite_missing`、`blocked`（企业撤回）；历史授予另有 `expired`。

## JSON 动作

| 类别 | action |
|---|---|
| 登记 | `register_standard_version` `register_node` `register_course` `add_objective` `add_equipment` `add_teacher_authorization` `add_endorsement` `add_equivalence` `register_student` `register_credit_rule` `register_program` |
| 证据/授予/毕业 | `submit_evidence` `award_credit` `awards` `graduation_check` `graduate` |
| 解释 | `explain`（结构化） `explain_text`（中文说明） |
| 换版/撤回/批次 | `upgrade_standard` `upgrade_impact` `withdraw_endorsement` `create_review_batch` `run_batch` `batch_status` |
| 旧骨架 | `health` `register` `find` |

写请求可带 `request_id` 实现幂等重放；同键不同载荷会被拒绝。

## 命令行示例

```bash
# 模拟日期检验标准生效与保护期
printf '%s' '{"action":"explain_text","student_id":"s1","node_id":"N-AUTO"}' | \
    PYTHONPATH=src python3 -m skill_equivalence.cli --db ./data.sqlite --date 2027-02-01

# 一次换版影响哪些课程、教师、培养方案与在审学生
printf '%s' '{"action":"upgrade_impact","event_id":"upg-1"}' | \
    PYTHONPATH=src python3 -m skill_equivalence.cli --db ./data.sqlite

# 换版/撤回审查批次断点续跑（每次可限 max_items）
printf '%s' '{"action":"run_batch","batch_id":"batch-upg-upg-1","max_items":50}' | \
    PYTHONPATH=src python3 -m skill_equivalence.cli --db ./data.sqlite --date 2027-02-01
```

`explain_text` 输出学生满足/缺失原因、失效依据、先修缺口、未满足槽位与逐条
补足路径；`upgrade_impact` 输出受影响节点、课程、教师、培养方案、保护期内
授予、需重审学生与不触碰的往届名单。
