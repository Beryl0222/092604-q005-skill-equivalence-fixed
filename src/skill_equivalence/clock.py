"""提供可替换的业务时钟与日期工具。

生产使用系统 UTC 时钟；测试与命令行模拟日期使用 FixedClock，
以便检验标准生效日、企业认可期与学分保护期。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def today_of(instant: str) -> date:
    """把 ISO 时间戳归一到日期（按 UTC 日历日比较，消除时分秒影响）。"""
    return datetime.fromisoformat(instant.replace("Z", "+00:00")).astimezone(timezone.utc).date()


def parse_day(value: str) -> date:
    """解析 ISO 日期或日期时间。"""
    value = value.strip()
    if len(value) == 10:
        return date.fromisoformat(value)
    return today_of(value)


def add_days(day: str, days: int) -> str:
    """ISO 日期加天数，返回 ISO 日期。"""
    return (parse_day(day) + timedelta(days=days)).isoformat()


class Clock:
    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def today(self) -> date:
        return datetime.now(timezone.utc).date()


class FixedClock(Clock):
    """固定在某一模拟日期（或时刻）的时钟。"""

    def __init__(self, day: str) -> None:
        self.day = parse_day(day)

    def now(self) -> str:
        return datetime(self.day.year, self.day.month, self.day.day, tzinfo=timezone.utc).isoformat()

    def today(self) -> date:
        return self.day
