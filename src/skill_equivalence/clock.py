"""提供可替换的业务时钟。"""
from datetime import date, datetime, timezone


class Clock:
    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def today(self) -> str:
        """业务日期（ISO ``YYYY-MM-DD``），标准生效与保护期均按此解释。"""
        return date.today().isoformat()


class FixedClock(Clock):
    """测试/演练用固定时钟，可随时拨到任意模拟日期。"""

    def __init__(self, fixed_date: str) -> None:
        self.fixed_date = fixed_date

    def now(self) -> str:
        return f"{self.fixed_date}T00:00:00+00:00"

    def today(self) -> str:
        return self.fixed_date

    def set(self, fixed_date: str) -> None:
        self.fixed_date = fixed_date
