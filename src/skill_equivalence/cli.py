"""命令行入口：从标准输入接收 JSON 请求。

示例：

    printf '%s' '{"action":"health"}' | \
        PYTHONPATH=src python3 -m skill_equivalence.cli --db ./data.sqlite

    # 用模拟日期检验标准生效与保护期
    printf '%s' '{"action":"explain_text","student_id":"s1","node_id":"N1"}' | \
        PYTHONPATH=src python3 -m skill_equivalence.cli --db ./data.sqlite --date 2027-03-01
"""
from __future__ import annotations

import argparse
import json
import sys

from .api import handle
from .clock import FixedClock
from .service import Service
from .store import Store


def build_service(db: str | None, fixed_date: str | None) -> Service:
    store = Store(db or ":memory:")
    clock = FixedClock(fixed_date) if fixed_date else None
    return Service(store, clock)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="技能标准课程等价网命令行")
    parser.add_argument("--db", default=None, help="SQLite 数据库路径（默认内存库）")
    parser.add_argument("--date", default=None,
                        help="模拟业务日期 YYYY-MM-DD，用于检验标准生效与保护期")
    parser.add_argument("--input", default=None, help="从文件读取请求（默认读标准输入）")
    args = parser.parse_args(argv)

    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            raw = fh.read().strip()
    else:
        raw = sys.stdin.read().strip()
    raw = raw or '{"action":"health"}'

    service = build_service(args.db, args.date)
    try:
        output = handle(raw, service)
    except (ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    body = json.loads(output)
    # explain_text 直接输出中文文本，便于人工阅读；其他动作输出 JSON。
    if isinstance(body, dict) and set(body) == {"text"}:
        print(body["text"])
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
