"""技能标准课程等价网的领域对象。

所有日期统一使用 ISO ``YYYY-MM-DD`` 字符串，时钟见 :mod:`skill_equivalence.clock`。
对象只承载数据，业务规则位于 :mod:`skill_equivalence.service` 与
:mod:`skill_equivalence.evaluation`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 兼容旧骨架的最小登记对象
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    record_id: str
    owner_id: str
    state: str
    revision: int
    created_at: str


# ---------------------------------------------------------------------------
# 赛项标准版本与岗位能力节点
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StandardVersion:
    """赛项标准的一个版本。

    ``replaces`` 指向被本版替换的上一版；``effective_date`` 之前旧版仍然有效。
    """

    standard_id: str
    version: str
    title: str
    replaces: str | None
    effective_date: str
    superseded_by: str | None = None


@dataclass(frozen=True)
class CompetencyNode:
    """岗位能力节点（能力单元）。

    同一节点可横跨软件测试、轨道车辆、智慧安防等不同赛项标准版本，
    它是去重与等价判定的核心锚点。
    """

    node_id: str
    name: str
    standard_id: str
    version: str
    composite: bool = False
    # 复合资格的组装策略：
    # forbidden      —— 只认被授权的完整签发，局部证明不能拼成资格（默认，防越权）
    # requires_issuer —— 槽位可补齐，但最终仍需一条完整签发通道确认
    # slots_allowed  —— 槽位全部满足即可认定整项资格
    assembly: str = "forbidden"
    # 复合能力槽位：每项为 (槽位能力节点 id, 是否允许由多个局部证明拼成)
    slots: list[tuple[str, bool]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 课程、实训设备、教师授权
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Course:
    """课程（培养方案中的一行）。"""

    course_id: str
    name: str
    program_id: str | None = None


@dataclass(frozen=True)
class CourseObjective:
    """课程目标，即课程可贡献的能力节点。"""

    course_id: str
    node_id: str


@dataclass(frozen=True)
class EquipmentRequirement:
    """实训设备前提：认定某课程前必须具备的设备。"""

    course_id: str
    equipment_id: str


@dataclass(frozen=True)
class TeacherAuthorization:
    """教师对某门课程的认定授权，带有效期。"""

    teacher_id: str
    course_id: str
    valid_from: str
    valid_until: str | None


# ---------------------------------------------------------------------------
# 企业认可
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnterpriseEndorsement:
    """企业对某个能力节点的认可，带认可期。

    ``withdrawn`` 为撤回时间，撤回后不能产生新的等价认定，
    但历史授予在保护期内保留依据。
    """

    enterprise_id: str
    node_id: str
    valid_from: str
    valid_until: str | None
    withdrawn: str | None = None


# ---------------------------------------------------------------------------
# 等价关系
# ---------------------------------------------------------------------------

# 覆盖程度取值
FULL = "full"          # 完整覆盖
PARTIAL = "partial"    # 局部证明
# 等价来源
SOURCE_COURSE = "course"
SOURCE_STANDARD = "standard"
# 等价状态
EQUIV_ACTIVE = "active"
EQUIV_SUPERSEDED = "superseded"  # 标准换版后受影响、失效
EQUIV_BLOCKED = "blocked"        # 企业撤回后被阻断


@dataclass(frozen=True)
class Equivalence:
    """课程/旧标准节点 与 岗位能力节点之间的等价关系。

    - ``source_type`` 为 course 时 ``source_id`` 是课程 id；
      为 standard 时是 ``standard_id:version/node_id`` 形式的旧标准节点键。
    - ``coverage`` 必须是 full 或 partial。
    - ``prerequisites`` 为先修依赖的节点 id 列表，认定前必须全部达成。
    - ``stack_partials`` 为真才允许与其他局部证明叠加；复合节点还要看
      :data:`CompetencyNode.slots` 槽位自身的可拼标记，双重控制。
    - ``issue_scope``：仅当节点为复合能力时有意义，``composite`` 表示
      该课程被授权作为整项复合资格的完整签发，``slot`` 表示只能证明槽位。
    """

    equivalence_id: str
    source_type: str
    source_id: str
    node_id: str
    coverage: str
    prerequisites: tuple[str, ...] = ()
    stack_partials: bool = False
    issue_scope: str = "slot"
    # 局部证明的覆盖份额（0,1），仅 coverage=partial 时有意义；
    # 允许叠加时，多个局部证明份额合计必须达到 1.0 才算覆盖槽位。
    coverage_share: float = 1.0
    standard_id: str | None = None
    version: str | None = None
    enterprise_id: str | None = None
    valid_from: str = "1970-01-01"
    valid_until: str | None = None
    status: str = EQUIV_ACTIVE


# ---------------------------------------------------------------------------
# 学生证据包与学分规则
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Student:
    student_id: str
    name: str
    program_id: str | None
    graduated: bool = False
    graduated_at: str | None = None


@dataclass(frozen=True)
class EvidencePack:
    """学生提交的证据包：用某门课程的通过成绩证明某能力节点。

    ``evidence_date`` 为取得证据的日期，用于判断当时教师授权、
    设备前提与认可是否有效（历史依据按当时条件解释）。
    """

    evidence_id: str
    student_id: str
    course_id: str
    evidence_date: str
    teacher_id: str
    equipment: tuple[str, ...] = ()
    accepted: bool = False
    reasons: tuple[str, ...] = ()
    # 受理时确认覆盖的节点与覆盖程度（证据一旦受理即冻结）
    node_id: str | None = None
    coverage: str | None = None
    equivalence_id: str | None = None
    coverage_share: float = 1.0


# ---------------------------------------------------------------------------
# 学分规则与学分授予（仅追加的历史依据）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreditRule:
    """能力节点对应的学分规则。"""

    rule_id: str
    node_id: str
    credits: float
    protection_until: str | None = None  # 授予后保护期截止（按授予日另算时可空）
    protection_days: int | None = None   # 自授予日起的保护期天数


@dataclass(frozen=True)
class CreditAward:
    """学分授予记录。仅追加，不允许修改或删除。

    授予后随标准换版/企业撤回，``status`` 由解释器在读取时计算，
    表里保存授予当时的完整依据快照（标准版本、等价 id、企业、规则版本日）。
    """

    award_id: str
    student_id: str
    node_id: str
    rule_id: str
    credits: float
    awarded_on: str
    standard_id: str
    version: str
    equivalence_id: str
    enterprise_id: str | None
    protection_until: str
    composite: bool
    basis_json: str  # 授予依据快照（槽位构成等），原样保留
    revoked: bool = False  # 仅毕业冻结等极少数管理动作置位；正常换版/撤回绝不置位


# ---------------------------------------------------------------------------
# 培养方案
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Program:
    """培养方案。``required_nodes`` 为毕业要求的能力节点。"""

    program_id: str
    name: str
    required_nodes: tuple[str, ...] = ()
    version: str = "1"


# ---------------------------------------------------------------------------
# 批量审查批次（断点续跑）
# ---------------------------------------------------------------------------

BATCH_PENDING = "pending"
BATCH_RUNNING = "running"
BATCH_DONE = "done"


@dataclass(frozen=True)
class BatchItemResult:
    student_id: str
    node_id: str
    status: str
    detail: dict[str, Any]


def json_default(obj: Any) -> Any:
    """供 ``json.dumps`` 使用：把元组转为列表。"""
    if isinstance(obj, tuple):
        return list(obj)
    raise TypeError(f"不可序列化的对象: {type(obj)!r}")
