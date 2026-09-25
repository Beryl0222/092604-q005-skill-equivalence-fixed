"""技能标准课程等价管理的基础登记对象。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Record:
    record_id: str
    owner_id: str
    state: str
    revision: int
    created_at: str
