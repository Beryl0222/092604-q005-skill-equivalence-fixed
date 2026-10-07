"""技能标准课程等价管理的领域对象。

对象全部不可变（frozen dataclass），表达"登记事实"；关系判定等业务
规则放在 engine/service 中。日期统一使用 ISO-8601 字符串（UTC），
由可替换时钟产生，便于模拟标准生效日与保护期。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 覆盖程度：complete = 完整覆盖（可直接认定），partial = 局部覆盖
# （仅可用于能力组合内部的叶子节点，多个 partial 不能越权拼成完整资格）。
COMPLETE = "complete"
PARTIAL = "partial"

# 等价关系来源。
SOURCE_EVIDENCE = "evidence"   # 学生证据包
SOURCE_COURSE = "course"       # 课程目标
SOURCE_TRANSFER = "transfer"   # 校际转学分

# 企业认可状态。
RECOGNITION_ACTIVE = "active"
RECOGNITION_WITHDRAWN = "withdrawn"

# 学分授予结论。
GRANT_AWARDED = "awarded"       # 正常授予
GRANT_PROTECTED = "protected"   # 依据已失效，但仍在保护期内，保留历史依据
GRANT_DENIED = "denied"

# 学生学习状态。
STUDENT_ACTIVE = "active"
STUDENT_GRADUATED = "graduated"


@dataclass(frozen=True)
class Record:
    """基础登记对象（保留脚手架兼容）。"""

    record_id: str
    owner_id: str
    state: str
    revision: int
    created_at: str


@dataclass(frozen=True)
class StandardVersion:
    """赛项技能标准的一个版本（如 软件测试 v2024）。"""

    standard_id: str          # 标准/赛项标识，如 std_swtest
    version: str              # 版本号，如 2024
    title: str
    effective_date: str       # 生效日（含），ISO 日期
    supersedes: str | None    # 被替代的上一版本标识 std@ver，可为空


@dataclass(frozen=True)
class CompetencyNode:
    """岗位能力节点。

    复合节点（is_composite=True）的子节点由 competency_parts 登记；
    同一能力可在多赛项/多专业间共享，依靠 competency_id 去重，
    避免软件测试、轨道车辆、智慧安防项目中同一项能力被重复计学分。
    content_hash 是能力语义指纹：换版时内容未变的节点哈希不变，
    教师授权可按哈希延续。
    """

    competency_id: str
    standard_ref: str         # std@ver
    name: str
    is_composite: bool
    content_hash: str
    ordinal: int = 0          # 版本内序号，仅用于稳定展示


@dataclass(frozen=True)
class CompetencyPart:
    """复合能力的组成节点（叶子或下级复合）。"""

    parent_id: str
    child_id: str
    position: int


@dataclass(frozen=True)
class Prerequisite:
    """能力先修依赖：拥有 competency_id 前必须先具备 required_id。"""

    competency_id: str
    required_id: str


@dataclass(frozen=True)
class CourseObjective:
    """课程目标：某门课程在某标准版本下承载的能力目标。"""

    course_id: str
    standard_ref: str         # 课程开设所依据的 std@ver
    title: str
    competency_id: str
    coverage: str             # complete / partial


@dataclass(frozen=True)
class EquivalenceLink:
    """能力等价关系：source_type+source_id 指向 competency_id。

    必须说明覆盖程度 coverage 与先修依赖（prereq_ids，判定时生效）。
    active=False 表示软失效（标准换版/企业撤回后不再支持新认定，
    但记录保留作为历史依据）。
    """

    link_id: str
    source_type: str          # evidence / course / transfer
    source_id: str
    competency_id: str
    coverage: str
    prereq_ids: tuple[str, ...] = field(default_factory=tuple)
    standard_ref: str = ""    # 认定所依据的标准版本
    active: bool = True
    reason: str = ""          # 失效原因（换版/撤回）


@dataclass(frozen=True)
class EquipmentPrerequisite:
    """实训设备前提：认定某能力前需核验设备具备。"""

    competency_id: str
    equipment_id: str
    equipment_name: str


@dataclass(frozen=True)
class EquipmentAvailability:
    """某证据/课程实际使用的设备核验记录（存在即视为该次认定已核验）。"""

    source_type: str
    source_id: str
    equipment_id: str


@dataclass(frozen=True)
class TeacherAuthorization:
    """教师授权：可在某标准版本下认定某能力。

    competency_hash 非空时为"按能力语义指纹"授权，换版后内容未变的
    能力仍可由该教师认定；否则仅在 standard_ref 精确匹配时有效。
    revoked 为撤回标记，历史授权记录保留。
    """

    authorization_id: str
    teacher_id: str
    teacher_name: str
    standard_ref: str
    competency_id: str
    competency_hash: str
    granted_date: str
    revoked: bool = False


@dataclass(frozen=True)
class StudentEvidence:
    """学生证据包：学生持有的一项证明（赛项成绩/课程修读/转学分）。

    证据本身只声明来源；它覆盖哪些能力由 EquivalenceLink 表达。
    """

    evidence_id: str
    student_id: str
    source_type: str
    source_ref: str           # 课程id / 赛项成绩id / 转出校证明id
    title: str
    obtained_date: str
    valid: bool = True


@dataclass(frozen=True)
class EnterpriseRecognition:
    """企业对"某能力在某版本下"认定的认可与有效期。

    撤回（status=withdrawn）后阻止一切新的等价认定；历史记录保留。
    """

    recognition_id: str
    enterprise_id: str
    enterprise_name: str
    competency_id: str
    standard_ref: str
    valid_from: str
    valid_until: str          # 企业认可期截止日（含）
    status: str = RECOGNITION_ACTIVE
    withdrawn_at: str | None = None


@dataclass(frozen=True)
class CreditRule:
    """学分规则：具备某能力（且课程目标达成）可授予的学分。"""

    rule_id: str
    competency_id: str
    course_id: str
    credits: float
    standard_ref: str
    protection_days: int      # 授予后的保护期天数


@dataclass(frozen=True)
class Program:
    """培养方案（专业）。"""

    program_id: str
    name: str
    standard_ref: str


@dataclass(frozen=True)
class ProgramRequirement:
    """培养方案要求的能力节点。"""

    program_id: str
    competency_id: str
    position: int


@dataclass(frozen=True)
class Student:
    """学生。未毕业（active）学生在企业撤回后需要补足路径。"""

    student_id: str
    name: str
    program_id: str
    status: str = STUDENT_ACTIVE
    graduated_date: str | None = None


@dataclass(frozen=True)
class CreditAward:
    """学分授予记录（只追加，永不更新/删除）。

    basis 保存授予时的完整依据快照（规则版本、认可、教师、证据链、
    覆盖度、先修），即使后续标准换版或企业撤回，往届记录仍可据此
    解释；保护期内的结论取 GRANT_PROTECTED 而非篡改原记录。
    """

    award_id: str
    student_id: str
    competency_id: str
    course_id: str
    rule_id: str
    credits: float
    standard_ref: str
    awarded_date: str
    protection_until: str
    conclusion: str           # awarded / protected（历史结论快照）
    basis_json: str


@dataclass(frozen=True)
class BatchRun:
    """批量审查的断点状态。

    done_items 为 JSON 数组（已处理项标识），中断后重跑只处理剩余项。
    """

    batch_id: str
    kind: str                 # review / recheck / supplement
    payload_json: str
    started_at: str
    updated_at: str
    status: str               # running / completed
    done_items: str
    results_json: str
