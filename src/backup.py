"""Supabase → 로컬 JSON 백업."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .supabase_client import get_supabase_client

ROOT = Path(__file__).resolve().parent.parent
BACKUP_ROOT = ROOT / "data" / "backups"
LATEST_DIR = BACKUP_ROOT / "latest"

# 뷰가 아닌 원본 테이블만
BACKUP_TABLES = (
    "businesses",
    "stocks",
    "accounts",
    "broker_partners",
    "account_config",
    "trades",
    "income_records",
)

_PAGE = 1000
_KEEP_SNAPSHOTS = 30  # 타임스탬프 폴더 최대 개수
_AUTO_MIN_HOURS = 12


@dataclass
class BackupResult:
    path: Path
    counts: dict[str, int]
    created_at: str

    @property
    def total_rows(self) -> int:
        return sum(self.counts.values())


def _fetch_all(client: Any, table: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        res = (
            client.table(table)
            .select("*")
            .range(start, start + _PAGE - 1)
            .execute()
        )
        batch = list(res.data or [])
        rows.extend(batch)
        if len(batch) < _PAGE:
            break
        start += _PAGE
    return rows


def _write_json(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _prune_old_snapshots(keep: int = _KEEP_SNAPSHOTS) -> None:
    if not BACKUP_ROOT.exists():
        return
    dirs = sorted(
        (
            p
            for p in BACKUP_ROOT.iterdir()
            if p.is_dir() and p.name != "latest" and p.name[:8].isdigit()
        ),
        key=lambda p: p.name,
        reverse=True,
    )
    for old in dirs[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def create_backup(*, label: str = "") -> BackupResult:
    """전체 테이블을 data/backups/<timestamp>/ 와 latest/ 에 저장."""
    client = get_supabase_client()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if label.strip():
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label.strip())[
            :40
        ]
        stamp = f"{stamp}_{safe}" if safe else stamp

    dest = BACKUP_ROOT / stamp
    dest.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    for table in BACKUP_TABLES:
        rows = _fetch_all(client, table)
        _write_json(dest / f"{table}.json", rows)
        counts[table] = len(rows)

    meta = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "counts": counts,
        "tables": list(BACKUP_TABLES),
    }
    (dest / "_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # latest 미러
    if LATEST_DIR.exists():
        shutil.rmtree(LATEST_DIR, ignore_errors=True)
    shutil.copytree(dest, LATEST_DIR)

    _prune_old_snapshots()
    return BackupResult(path=dest, counts=counts, created_at=meta["created_at"])


def latest_backup_time() -> datetime | None:
    meta_path = LATEST_DIR / "_meta.json"
    if not meta_path.exists():
        # 타임스탬프 폴더 중 최신
        if not BACKUP_ROOT.exists():
            return None
        dirs = sorted(
            (
                p
                for p in BACKUP_ROOT.iterdir()
                if p.is_dir() and p.name != "latest" and p.name[:8].isdigit()
            ),
            key=lambda p: p.name,
            reverse=True,
        )
        if not dirs:
            return None
        meta_path = dirs[0] / "_meta.json"
        if not meta_path.exists():
            return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return datetime.fromisoformat(str(meta.get("created_at", "")))
    except Exception:  # noqa: BLE001
        return None


def maybe_auto_backup(*, min_hours: float = _AUTO_MIN_HOURS) -> BackupResult | None:
    """마지막 백업이 min_hours 이상 지났으면 자동 백업."""
    last = latest_backup_time()
    if last is not None and datetime.now() - last < timedelta(hours=min_hours):
        return None
    return create_backup(label="auto")


def list_backup_dirs(limit: int = 10) -> list[Path]:
    if not BACKUP_ROOT.exists():
        return []
    dirs = sorted(
        (
            p
            for p in BACKUP_ROOT.iterdir()
            if p.is_dir() and p.name != "latest" and p.name[:8].isdigit()
        ),
        key=lambda p: p.name,
        reverse=True,
    )
    return dirs[:limit]
