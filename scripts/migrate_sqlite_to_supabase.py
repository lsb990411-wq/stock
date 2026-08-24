"""로컬 SQLite JSON 덤프 → Supabase 적재."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.supabase_client import get_supabase_client  # noqa: E402


DUMP = ROOT / "data" / "_migrate"
TABLE_ORDER = [
    "businesses",
    "stocks",
    "accounts",
    "broker_partners",
    "account_config",
    "trades",
    "income_records",
]
TEXT_DEFAULTS = {
    "note",
    "code",
    "account_no",
    "memo",
    "source",
    "partner_code",
    "product_name",
    "broker_name",
    "currency",
    "market",
    "created_at",
}


def _clean_row(row: dict) -> dict:
    out: dict = {}
    for k, v in row.items():
        if v is None and k in TEXT_DEFAULTS:
            out[k] = ""
        else:
            out[k] = v
    return out


def _chunk(items: list[dict], size: int = 80) -> list[list[dict]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def main() -> None:
    client = get_supabase_client()
    for table in TABLE_ORDER:
        path = DUMP / f"{table}.json"
        if not path.exists():
            print(f"skip {table} (no dump)")
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        rows = [_clean_row(r) for r in rows]
        if not rows:
            print(f"{table}: 0")
            continue
        uploaded = 0
        for batch in _chunk(rows):
            client.table(table).upsert(batch).execute()
            uploaded += len(batch)
        print(f"{table}: {uploaded}")


if __name__ == "__main__":
    main()
