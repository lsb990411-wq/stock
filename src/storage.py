"""SQLite 기반 데이터 저장소."""

from __future__ import annotations

import sqlite3
from dataclasses import MISSING, fields
from datetime import date
from pathlib import Path
from typing import Any

from .models import (
    Account,
    AccountConfig,
    BrokerPartner,
    Business,
    IncomeAccountConfig,
    IncomeRecord,
    MARKET_DOMESTIC,
    Stock,
    Trade,
    normalize_market,
    now_str,
)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trades.db"


def _from_row(cls: type, row: Any) -> Any:
    """DB Row를 dataclass 생성자 인자에만 맞게 안전하게 변환한다."""
    raw = dict(row)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name in raw and raw[f.name] is not None:
            kwargs[f.name] = raw[f.name]
        elif f.default is not MISSING:
            kwargs[f.name] = f.default
        elif f.default_factory is not MISSING:  # type: ignore[misc]
            kwargs[f.name] = f.default_factory()  # type: ignore[misc]
        else:
            kwargs[f.name] = raw.get(f.name)
    return cls(**kwargs)


class Storage:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        # timeout: 다중 사용자 잠금 대기 / WAL: 동시 읽기·쓰기 안정성
        conn = sqlite3.connect(self.db_path, timeout=20.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def get_connection(self) -> sqlite3.Connection:
        """DB 연결 (update 등 외부 호출 호환용)."""
        return self._connect()

    def close(self) -> None:
        """호환용 no-op (연결은 작업 단위로 열고 닫음)."""
        return None

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS businesses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    code TEXT DEFAULT '',
                    account_no TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS stocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_id INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'domestic',
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    partner_code TEXT DEFAULT '',
                    UNIQUE(business_id, code, market),
                    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    business_id INTEGER NOT NULL,
                    stock_id INTEGER NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL', 'DIVIDEND')),
                    quantity REAL NOT NULL CHECK(quantity > 0),
                    price REAL NOT NULL CHECK(price >= 0),
                    fee REAL NOT NULL DEFAULT 0 CHECK(fee >= 0),
                    tax REAL NOT NULL DEFAULT 0 CHECK(tax >= 0),
                    settlement_amount REAL,
                    memo TEXT DEFAULT '',
                    source TEXT DEFAULT 'manual',
                    created_at TEXT NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'KRW',
                    fx_rate REAL NOT NULL DEFAULT 0,
                    price_fx REAL NOT NULL DEFAULT 0,
                    fee_fx REAL NOT NULL DEFAULT 0,
                    tax_fx REAL NOT NULL DEFAULT 0,
                    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE RESTRICT,
                    FOREIGN KEY(stock_id) REFERENCES stocks(id) ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date, id);
                CREATE INDEX IF NOT EXISTS idx_trades_biz_stock ON trades(business_id, stock_id);

                CREATE TABLE IF NOT EXISTS account_config (
                    business_id INTEGER NOT NULL,
                    market TEXT NOT NULL DEFAULT 'domestic',
                    security_code TEXT NOT NULL DEFAULT '0178',
                    fee_code TEXT NOT NULL DEFAULT '0965',
                    deposit_code TEXT NOT NULL DEFAULT '0104',
                    gain_code TEXT NOT NULL DEFAULT '0915',
                    loss_code TEXT NOT NULL DEFAULT '0953',
                    interest_code TEXT NOT NULL DEFAULT '0901',
                    prepaid_tax_code TEXT NOT NULL DEFAULT '0136',
                    bank_code TEXT NOT NULL DEFAULT '0103',
                    PRIMARY KEY (business_id, market),
                    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    code TEXT DEFAULT '',
                    account_no TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'domestic',
                    UNIQUE(business_id, name, market),
                    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS broker_partners (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_id INTEGER NOT NULL,
                    broker_name TEXT NOT NULL,
                    partner_code TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(business_id, broker_name),
                    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS income_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pay_date TEXT NOT NULL,
                    business_id INTEGER NOT NULL,
                    product_name TEXT DEFAULT '',
                    broker_name TEXT DEFAULT '',
                    income_type TEXT NOT NULL DEFAULT 'INTEREST'
                        CHECK(income_type IN ('INTEREST', 'DIVIDEND')),
                    gross_amount REAL NOT NULL DEFAULT 0 CHECK(gross_amount >= 0),
                    corp_tax REAL NOT NULL DEFAULT 0 CHECK(corp_tax >= 0),
                    local_tax REAL NOT NULL DEFAULT 0 CHECK(local_tax >= 0),
                    memo TEXT DEFAULT '',
                    source TEXT DEFAULT 'manual',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_income_date
                    ON income_records(pay_date, id);
                CREATE INDEX IF NOT EXISTS idx_income_biz
                    ON income_records(business_id);
                """
            )
            self._migrate_schema(conn)

        # market 격리: FK pragma는 트랜잭션 밖에서만 동작하므로 별도 연결로 수행
        self._migrate_market_isolation_fresh()
        self._migrate_trade_fx_columns_fresh()

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """기존 DB를 사업자별 격리 스키마로 마이그레이션한다."""

        def cols(table: str) -> set[str]:
            try:
                return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
            except sqlite3.Error:
                return set()

        def tables() -> set[str]:
            return {
                str(r[0])
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

        biz_cols = cols("businesses")
        if biz_cols:
            if "code" not in biz_cols:
                conn.execute("ALTER TABLE businesses ADD COLUMN code TEXT DEFAULT ''")
            if "account_no" not in biz_cols:
                conn.execute(
                    "ALTER TABLE businesses ADD COLUMN account_no TEXT DEFAULT ''"
                )

        trade_cols = cols("trades")
        if trade_cols and "tax" not in trade_cols:
            conn.execute("ALTER TABLE trades ADD COLUMN tax REAL DEFAULT 0")

        first_biz = conn.execute(
            "SELECT id FROM businesses ORDER BY id ASC LIMIT 1"
        ).fetchone()
        first_biz_id = int(first_biz["id"]) if first_biz else None

        # ---- stocks: business_id 추가 + UNIQUE(business_id, code) ----
        stock_cols = cols("stocks")
        if stock_cols and "business_id" not in stock_cols:
            if first_biz_id is None:
                # 사업자 없으면 임시 placeholder (이후 거래는 사업자 필요)
                conn.execute(
                    """
                    INSERT INTO businesses(name, note, created_at, code, account_no)
                    VALUES ('기본사업자', '자동 생성', ?, '', '')
                    """,
                    (now_str(),),
                )
                first_biz_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute("ALTER TABLE stocks ADD COLUMN business_id INTEGER")
            conn.execute(
                "UPDATE stocks SET business_id = ? WHERE business_id IS NULL",
                (first_biz_id,),
            )
            # UNIQUE(code) 제거를 위해 테이블 재구성
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.executescript(
                """
                CREATE TABLE stocks__new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_id INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    market TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    partner_code TEXT DEFAULT '',
                    UNIQUE(business_id, code),
                    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
                );
                INSERT INTO stocks__new(
                    id, business_id, code, name, market, note, created_at, partner_code
                )
                SELECT id, business_id, code, name, market, note, created_at,
                       COALESCE(partner_code, '')
                FROM stocks;
                DROP TABLE stocks;
                ALTER TABLE stocks__new RENAME TO stocks;
                """
            )
            conn.execute("PRAGMA foreign_keys = ON")

        # ---- account_config: id=1 → business_id PK (+ 이자 계정 컬럼) ----
        tbl = tables()
        ac_cols = cols("account_config")
        if "account_config" not in tbl:
            conn.execute(
                """
                CREATE TABLE account_config (
                    business_id INTEGER PRIMARY KEY,
                    security_code TEXT NOT NULL DEFAULT '0178',
                    fee_code TEXT NOT NULL DEFAULT '0965',
                    deposit_code TEXT NOT NULL DEFAULT '0104',
                    gain_code TEXT NOT NULL DEFAULT '0915',
                    loss_code TEXT NOT NULL DEFAULT '0953',
                    interest_code TEXT NOT NULL DEFAULT '0901',
                    prepaid_tax_code TEXT NOT NULL DEFAULT '0136',
                    bank_code TEXT NOT NULL DEFAULT '0103',
                    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
                )
                """
            )
        elif "business_id" not in ac_cols:
            old = conn.execute("SELECT * FROM account_config WHERE id = 1").fetchone()
            inc = None
            if "income_account_config" in tbl:
                inc = conn.execute(
                    "SELECT * FROM income_account_config WHERE id = 1"
                ).fetchone()
            conn.execute("DROP TABLE account_config")
            conn.execute(
                """
                CREATE TABLE account_config (
                    business_id INTEGER PRIMARY KEY,
                    security_code TEXT NOT NULL DEFAULT '0178',
                    fee_code TEXT NOT NULL DEFAULT '0965',
                    deposit_code TEXT NOT NULL DEFAULT '0104',
                    gain_code TEXT NOT NULL DEFAULT '0915',
                    loss_code TEXT NOT NULL DEFAULT '0953',
                    interest_code TEXT NOT NULL DEFAULT '0901',
                    prepaid_tax_code TEXT NOT NULL DEFAULT '0136',
                    bank_code TEXT NOT NULL DEFAULT '0103',
                    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
                )
                """
            )
            if first_biz_id is not None and old is not None:
                conn.execute(
                    """
                    INSERT INTO account_config(
                        business_id, security_code, fee_code, deposit_code,
                        gain_code, loss_code, interest_code, prepaid_tax_code, bank_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        first_biz_id,
                        str(old["security_code"] if "security_code" in old.keys() else "0178"),
                        str(old["fee_code"] if "fee_code" in old.keys() else "0965"),
                        str(old["deposit_code"] if "deposit_code" in old.keys() else "0104"),
                        str(old["gain_code"] if "gain_code" in old.keys() else "0915"),
                        str(old["loss_code"] if "loss_code" in old.keys() else "0953"),
                        str(inc["interest_code"]) if inc else "0901",
                        str(inc["prepaid_tax_code"]) if inc else "0136",
                        str(inc["bank_code"]) if inc else "0103",
                    ),
                )
        else:
            # 이미 business_id PK면 이자 컬럼만 보강
            for col, default in (
                ("interest_code", "0901"),
                ("prepaid_tax_code", "0136"),
                ("bank_code", "0103"),
            ):
                if col not in ac_cols:
                    conn.execute(
                        f"ALTER TABLE account_config ADD COLUMN {col} TEXT DEFAULT '{default}'"
                    )

        # ---- accounts 테이블 (사업자별 증권사/계좌) ----
        if "accounts" not in tables():
            conn.execute(
                """
                CREATE TABLE accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    code TEXT DEFAULT '',
                    account_no TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(business_id, name),
                    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
                )
                """
            )
            # 첫 사업자에만: 기존 businesses 행을 계좌로 복사(자기 제외는 이름만)
            if first_biz_id is not None:
                biz_row = conn.execute(
                    "SELECT * FROM businesses WHERE id = ?", (first_biz_id,)
                ).fetchone()
                if biz_row is not None:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO accounts(
                            business_id, name, code, account_no, note, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            first_biz_id,
                            str(biz_row["name"]),
                            str(biz_row["code"] or ""),
                            str(biz_row["account_no"] or ""),
                            str(biz_row["note"] or ""),
                            str(biz_row["created_at"] or now_str()),
                        ),
                    )

        # ---- broker_partners: business_id ----
        bp_cols = cols("broker_partners")
        if "broker_partners" not in tables():
            conn.execute(
                """
                CREATE TABLE broker_partners (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_id INTEGER NOT NULL,
                    broker_name TEXT NOT NULL,
                    partner_code TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(business_id, broker_name),
                    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
                )
                """
            )
        elif "business_id" not in bp_cols:
            if first_biz_id is None:
                conn.execute(
                    """
                    INSERT INTO businesses(name, note, created_at, code, account_no)
                    VALUES ('기본사업자', '자동 생성', ?, '', '')
                    """,
                    (now_str(),),
                )
                first_biz_id = int(
                    conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                )
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.executescript(
                """
                CREATE TABLE broker_partners__new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_id INTEGER NOT NULL,
                    broker_name TEXT NOT NULL,
                    partner_code TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(business_id, broker_name),
                    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE CASCADE
                );
                """
            )
            rows = conn.execute("SELECT * FROM broker_partners").fetchall()
            for r in rows:
                conn.execute(
                    """
                    INSERT INTO broker_partners__new(
                        id, business_id, broker_name, partner_code, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        int(r["id"]),
                        first_biz_id,
                        str(r["broker_name"]),
                        str(r["partner_code"] or ""),
                        str(r["created_at"] or now_str()),
                    ),
                )
            conn.execute("DROP TABLE broker_partners")
            conn.execute(
                "ALTER TABLE broker_partners__new RENAME TO broker_partners"
            )
            conn.execute("PRAGMA foreign_keys = ON")

        # income_records 테이블 보장
        if "income_records" not in tables():
            conn.execute(
                """
                CREATE TABLE income_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pay_date TEXT NOT NULL,
                    business_id INTEGER NOT NULL,
                    product_name TEXT DEFAULT '',
                    broker_name TEXT DEFAULT '',
                    income_type TEXT NOT NULL DEFAULT 'INTEREST'
                        CHECK(income_type IN ('INTEREST', 'DIVIDEND')),
                    gross_amount REAL NOT NULL DEFAULT 0 CHECK(gross_amount >= 0),
                    corp_tax REAL NOT NULL DEFAULT 0 CHECK(corp_tax >= 0),
                    local_tax REAL NOT NULL DEFAULT 0 CHECK(local_tax >= 0),
                    memo TEXT DEFAULT '',
                    source TEXT DEFAULT 'manual',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(business_id) REFERENCES businesses(id) ON DELETE RESTRICT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_income_date ON income_records(pay_date, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_income_biz ON income_records(business_id)"
            )

    def _migrate_market_isolation_fresh(self) -> None:
        """stocks/accounts/account_config에 market(domestic|overseas) 격리 적용.

        SQLite는 트랜잭션 중 PRAGMA foreign_keys 변경이 무시되므로 전용 연결에서 수행한다.
        """
        conn = self._connect()
        try:
            conn.isolation_level = None  # autocommit — pragma 적용 보장
            conn.execute("PRAGMA foreign_keys = OFF")
            self._migrate_market_isolation(conn)
            conn.execute("PRAGMA foreign_keys = ON")
        finally:
            conn.close()

    def _migrate_trade_fx_columns_fresh(self) -> None:
        """trades에 외화·환율 컬럼 추가 및 DIVIDEND side 허용."""
        conn = self._connect()
        try:
            conn.isolation_level = None
            cols = {
                str(r[1]) for r in conn.execute("PRAGMA table_info(trades)")
            }
            if not cols:
                return
            alters = [
                ("currency", "ALTER TABLE trades ADD COLUMN currency TEXT NOT NULL DEFAULT 'KRW'"),
                ("fx_rate", "ALTER TABLE trades ADD COLUMN fx_rate REAL NOT NULL DEFAULT 0"),
                ("price_fx", "ALTER TABLE trades ADD COLUMN price_fx REAL NOT NULL DEFAULT 0"),
                ("fee_fx", "ALTER TABLE trades ADD COLUMN fee_fx REAL NOT NULL DEFAULT 0"),
                ("tax_fx", "ALTER TABLE trades ADD COLUMN tax_fx REAL NOT NULL DEFAULT 0"),
            ]
            for name, sql in alters:
                if name not in cols:
                    conn.execute(sql)

            # side CHECK에 DIVIDEND 허용 (구제약 제거를 위해 테이블 재구성)
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='trades'"
            ).fetchone()
            sql = str(row[0] or "") if row else ""
            if "DIVIDEND" not in sql:
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.execute("DROP TABLE IF EXISTS trades__fx")
                conn.executescript(
                    """
                    CREATE TABLE trades__fx (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trade_date TEXT NOT NULL,
                        business_id INTEGER NOT NULL,
                        stock_id INTEGER NOT NULL,
                        side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL', 'DIVIDEND')),
                        quantity REAL NOT NULL CHECK(quantity > 0),
                        price REAL NOT NULL CHECK(price >= 0),
                        fee REAL NOT NULL DEFAULT 0 CHECK(fee >= 0),
                        tax REAL NOT NULL DEFAULT 0 CHECK(tax >= 0),
                        settlement_amount REAL,
                        memo TEXT DEFAULT '',
                        source TEXT DEFAULT 'manual',
                        created_at TEXT NOT NULL,
                        currency TEXT NOT NULL DEFAULT 'KRW',
                        fx_rate REAL NOT NULL DEFAULT 0,
                        price_fx REAL NOT NULL DEFAULT 0,
                        fee_fx REAL NOT NULL DEFAULT 0,
                        tax_fx REAL NOT NULL DEFAULT 0
                    );
                    INSERT INTO trades__fx(
                        id, trade_date, business_id, stock_id, side, quantity, price,
                        fee, tax, settlement_amount, memo, source, created_at,
                        currency, fx_rate, price_fx, fee_fx, tax_fx
                    )
                    SELECT id, trade_date, business_id, stock_id, side, quantity, price,
                           fee, tax, settlement_amount, memo, source, created_at,
                           COALESCE(currency, 'KRW'),
                           COALESCE(fx_rate, 0),
                           COALESCE(price_fx, 0),
                           COALESCE(fee_fx, 0),
                           COALESCE(tax_fx, 0)
                    FROM trades;
                    DROP TABLE trades;
                    ALTER TABLE trades__fx RENAME TO trades;
                    CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date, id);
                    CREATE INDEX IF NOT EXISTS idx_trades_biz_stock
                        ON trades(business_id, stock_id);
                    """
                )
                conn.execute("PRAGMA foreign_keys = ON")
        finally:
            conn.close()

    def _migrate_market_isolation(self, conn: sqlite3.Connection) -> None:
        """stocks/accounts/account_config에 market(domestic|overseas) 격리 적용."""

        def cols(table: str) -> set[str]:
            try:
                return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
            except sqlite3.Error:
                return set()

        def table_sql(name: str) -> str:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
            return str(row[0] or "") if row else ""

        # stocks: 값 정규화 + UNIQUE(business_id, code, market)
        stock_cols = cols("stocks")
        if stock_cols:
            conn.execute(
                """
                UPDATE stocks
                SET market = CASE
                    WHEN lower(trim(COALESCE(market, ''))) IN (
                        'overseas', '해외', '해외주식', 'foreign', 'us', 'usa', 'global'
                    ) THEN 'overseas'
                    ELSE 'domestic'
                END
                """
            )
            sql = table_sql("stocks")
            compact = sql.replace(" ", "")
            if "UNIQUE(business_id,code,market)" not in compact:
                conn.execute("DROP TABLE IF EXISTS stocks__mkt")
                conn.execute("DROP TABLE IF EXISTS stocks__new")
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.executescript(
                    """
                    CREATE TABLE stocks__mkt (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        business_id INTEGER NOT NULL,
                        code TEXT NOT NULL,
                        name TEXT NOT NULL,
                        market TEXT NOT NULL DEFAULT 'domestic',
                        note TEXT DEFAULT '',
                        created_at TEXT NOT NULL,
                        partner_code TEXT DEFAULT '',
                        UNIQUE(business_id, code, market)
                    );
                    INSERT INTO stocks__mkt(
                        id, business_id, code, name, market, note, created_at, partner_code
                    )
                    SELECT id, business_id, code, name,
                           CASE
                             WHEN lower(trim(COALESCE(market, ''))) IN (
                               'overseas', '해외', '해외주식', 'foreign', 'us', 'usa', 'global'
                             ) THEN 'overseas'
                             ELSE 'domestic'
                           END,
                           note, created_at, COALESCE(partner_code, '')
                    FROM stocks;
                    DROP TABLE stocks;
                    ALTER TABLE stocks__mkt RENAME TO stocks;
                    """
                )
                conn.execute("PRAGMA foreign_keys = ON")

        # accounts: market 컬럼 + UNIQUE(business_id, name, market)
        acc_cols = cols("accounts")
        if acc_cols:
            if "market" not in acc_cols:
                conn.execute(
                    "ALTER TABLE accounts ADD COLUMN market TEXT NOT NULL DEFAULT 'domestic'"
                )
            conn.execute(
                """
                UPDATE accounts
                SET market = CASE
                    WHEN lower(trim(COALESCE(market, ''))) IN (
                        'overseas', '해외', '해외주식', 'foreign', 'us', 'usa', 'global'
                    ) THEN 'overseas'
                    ELSE 'domestic'
                END
                """
            )
            sql = table_sql("accounts")
            compact = sql.replace(" ", "")
            if "UNIQUE(business_id,name,market)" not in compact:
                conn.execute("DROP TABLE IF EXISTS accounts__mkt")
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.executescript(
                    """
                    CREATE TABLE accounts__mkt (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        business_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        code TEXT DEFAULT '',
                        account_no TEXT DEFAULT '',
                        note TEXT DEFAULT '',
                        created_at TEXT NOT NULL,
                        market TEXT NOT NULL DEFAULT 'domestic',
                        UNIQUE(business_id, name, market)
                    );
                    INSERT INTO accounts__mkt(
                        id, business_id, name, code, account_no, note, created_at, market
                    )
                    SELECT id, business_id, name, code, account_no, note, created_at,
                           CASE
                             WHEN lower(trim(COALESCE(market, ''))) IN (
                               'overseas', '해외', '해외주식', 'foreign', 'us', 'usa', 'global'
                             ) THEN 'overseas'
                             ELSE 'domestic'
                           END
                    FROM accounts;
                    DROP TABLE accounts;
                    ALTER TABLE accounts__mkt RENAME TO accounts;
                    """
                )
                conn.execute("PRAGMA foreign_keys = ON")

        # account_config: PRIMARY KEY (business_id, market)
        ac_cols = cols("account_config")
        if ac_cols:
            sql = table_sql("account_config")
            normalized = sql.replace(" ", "").lower()
            needs_rebuild = (
                "primarykey(business_id,market)" not in normalized
                or "market" not in ac_cols
            )
            if needs_rebuild:
                conn.execute("DROP TABLE IF EXISTS account_config__mkt")
                conn.execute("PRAGMA foreign_keys = OFF")
                if "market" not in ac_cols:
                    # 구 PK(business_id) → (business_id, market)
                    conn.executescript(
                        """
                        CREATE TABLE account_config__mkt (
                            business_id INTEGER NOT NULL,
                            market TEXT NOT NULL DEFAULT 'domestic',
                            security_code TEXT NOT NULL DEFAULT '0178',
                            fee_code TEXT NOT NULL DEFAULT '0965',
                            deposit_code TEXT NOT NULL DEFAULT '0104',
                            gain_code TEXT NOT NULL DEFAULT '0915',
                            loss_code TEXT NOT NULL DEFAULT '0953',
                            interest_code TEXT NOT NULL DEFAULT '0901',
                            prepaid_tax_code TEXT NOT NULL DEFAULT '0136',
                            bank_code TEXT NOT NULL DEFAULT '0103',
                            PRIMARY KEY (business_id, market)
                        );
                        INSERT INTO account_config__mkt(
                            business_id, market, security_code, fee_code, deposit_code,
                            gain_code, loss_code, interest_code, prepaid_tax_code, bank_code
                        )
                        SELECT business_id, 'domestic',
                               security_code, fee_code, deposit_code,
                               gain_code, loss_code, interest_code, prepaid_tax_code, bank_code
                        FROM account_config;
                        DROP TABLE account_config;
                        ALTER TABLE account_config__mkt RENAME TO account_config;
                        """
                    )
                else:
                    conn.executescript(
                        """
                        CREATE TABLE account_config__mkt (
                            business_id INTEGER NOT NULL,
                            market TEXT NOT NULL DEFAULT 'domestic',
                            security_code TEXT NOT NULL DEFAULT '0178',
                            fee_code TEXT NOT NULL DEFAULT '0965',
                            deposit_code TEXT NOT NULL DEFAULT '0104',
                            gain_code TEXT NOT NULL DEFAULT '0915',
                            loss_code TEXT NOT NULL DEFAULT '0953',
                            interest_code TEXT NOT NULL DEFAULT '0901',
                            prepaid_tax_code TEXT NOT NULL DEFAULT '0136',
                            bank_code TEXT NOT NULL DEFAULT '0103',
                            PRIMARY KEY (business_id, market)
                        );
                        INSERT OR IGNORE INTO account_config__mkt(
                            business_id, market, security_code, fee_code, deposit_code,
                            gain_code, loss_code, interest_code, prepaid_tax_code, bank_code
                        )
                        SELECT business_id,
                               CASE
                                 WHEN lower(trim(COALESCE(market, ''))) IN (
                                   'overseas', '해외', '해외주식', 'foreign', 'us', 'usa', 'global'
                                 ) THEN 'overseas'
                                 ELSE 'domestic'
                               END,
                               security_code, fee_code, deposit_code,
                               gain_code, loss_code, interest_code, prepaid_tax_code, bank_code
                        FROM account_config;
                        DROP TABLE account_config;
                        ALTER TABLE account_config__mkt RENAME TO account_config;
                        """
                    )
                conn.execute("PRAGMA foreign_keys = ON")

    # ------------------------------------------------------------------
    # Account config (사업자·시장별)
    # ------------------------------------------------------------------
    def get_account_config(
        self,
        business_id: int | None,
        market: str | None = MARKET_DOMESTIC,
    ) -> AccountConfig:
        if business_id is None:
            return AccountConfig()
        mkt = normalize_market(market)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT security_code, fee_code, deposit_code, gain_code, loss_code,
                       interest_code, prepaid_tax_code, bank_code
                FROM account_config WHERE business_id = ? AND market = ?
                """,
                (int(business_id), mkt),
            ).fetchone()
        if row is None:
            return AccountConfig()
        return AccountConfig(
            security_code=str(row["security_code"] or "0178"),
            fee_code=str(row["fee_code"] or "0965"),
            deposit_code=str(row["deposit_code"] or "0104"),
            gain_code=str(row["gain_code"] or "0915"),
            loss_code=str(row["loss_code"] or "0953"),
            interest_code=str(row["interest_code"] or "0901"),
            prepaid_tax_code=str(row["prepaid_tax_code"] or "0136"),
            bank_code=str(row["bank_code"] or "0103"),
        ).normalize()

    def save_account_config(
        self,
        business_id: int | None,
        config: AccountConfig | dict[str, Any],
        market: str | None = MARKET_DOMESTIC,
    ) -> AccountConfig:
        if business_id is None:
            raise ValueError("사업자를 선택한 뒤 계정과목을 저장해 주세요.")
        mkt = normalize_market(market)
        if isinstance(config, dict):
            cfg = AccountConfig(
                security_code=str(
                    config.get("security_code")
                    or config.get("stock_code")
                    or "0178"
                ),
                fee_code=str(config.get("fee_code") or "0965"),
                deposit_code=str(config.get("deposit_code") or "0104"),
                gain_code=str(config.get("gain_code") or "0915"),
                loss_code=str(config.get("loss_code") or "0953"),
                interest_code=str(config.get("interest_code") or "0901"),
                prepaid_tax_code=str(config.get("prepaid_tax_code") or "0136"),
                bank_code=str(config.get("bank_code") or "0103"),
            ).normalize()
        else:
            cfg = config.normalize()
        for label, code in (
            ("투자유가증권", cfg.security_code),
            ("지급수수료", cfg.fee_code),
            ("기타제예금", cfg.deposit_code),
            ("투자자산처분이익", cfg.gain_code),
            ("투자자산처분손실", cfg.loss_code),
            ("이자수익", cfg.interest_code),
            ("선납세금", cfg.prepaid_tax_code),
            ("보통예금", cfg.bank_code),
        ):
            if not code:
                raise ValueError(f"{label} 계정코드는 필수입니다.")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO account_config(
                    business_id, market, security_code, fee_code, deposit_code,
                    gain_code, loss_code, interest_code, prepaid_tax_code, bank_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(business_id, market) DO UPDATE SET
                    security_code = excluded.security_code,
                    fee_code = excluded.fee_code,
                    deposit_code = excluded.deposit_code,
                    gain_code = excluded.gain_code,
                    loss_code = excluded.loss_code,
                    interest_code = excluded.interest_code,
                    prepaid_tax_code = excluded.prepaid_tax_code,
                    bank_code = excluded.bank_code
                """,
                (
                    int(business_id),
                    mkt,
                    cfg.security_code,
                    cfg.fee_code,
                    cfg.deposit_code,
                    cfg.gain_code,
                    cfg.loss_code,
                    cfg.interest_code,
                    cfg.prepaid_tax_code,
                    cfg.bank_code,
                ),
            )
        return cfg

    def update_stock_partner_codes(
        self,
        updates: dict[int, str],
        business_id: int | None = None,
    ) -> int:
        """종목 ID → 회계 거래처코드 일괄 갱신. 변경 건수 반환."""
        if not updates:
            return 0
        changed = 0
        with self._connect() as conn:
            for stock_id, partner_code in updates.items():
                if business_id is None:
                    cur = conn.execute(
                        "UPDATE stocks SET partner_code = ? WHERE id = ?",
                        (str(partner_code or "").strip(), int(stock_id)),
                    )
                else:
                    cur = conn.execute(
                        """
                        UPDATE stocks SET partner_code = ?
                        WHERE id = ? AND business_id = ?
                        """,
                        (
                            str(partner_code or "").strip(),
                            int(stock_id),
                            int(business_id),
                        ),
                    )
                changed += int(cur.rowcount or 0)
        return changed

    # ------------------------------------------------------------------
    # Business
    # ------------------------------------------------------------------
    def add_business(
        self,
        name: str,
        note: str = "",
        code: str = "",
        account_no: str = "",
    ) -> int:
        name = name.strip()
        if not name:
            raise ValueError("사업자명은 필수입니다.")
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO businesses(name, note, created_at, code, account_no)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    name,
                    note.strip(),
                    now_str(),
                    code.strip(),
                    account_no.strip(),
                ),
            )
            return int(cur.lastrowid)

    def update_business(
        self,
        business_id: int,
        name: str,
        note: str = "",
        code: str = "",
        account_no: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE businesses
                SET name = ?, note = ?, code = ?, account_no = ?
                WHERE id = ?
                """,
                (
                    name.strip(),
                    note.strip(),
                    code.strip(),
                    account_no.strip(),
                    business_id,
                ),
            )

    def delete_entity(self, business_id: int) -> None:
        """사업자 및 해당 사업자 소속 데이터 전부 삭제. (다른 사업자 데이터는 유지)"""
        with self._connect() as conn:
            conn.execute("DELETE FROM trades WHERE business_id = ?", (business_id,))
            conn.execute(
                "DELETE FROM income_records WHERE business_id = ?", (business_id,)
            )
            conn.execute("DELETE FROM stocks WHERE business_id = ?", (business_id,))
            conn.execute("DELETE FROM accounts WHERE business_id = ?", (business_id,))
            conn.execute(
                "DELETE FROM broker_partners WHERE business_id = ?", (business_id,)
            )
            conn.execute(
                "DELETE FROM account_config WHERE business_id = ?", (business_id,)
            )
            conn.execute("DELETE FROM businesses WHERE id = ?", (business_id,))

    def delete_business(self, business_id: int) -> None:
        """호환용 별칭 — delete_entity와 동일하게 거래 포함 삭제."""
        self.delete_entity(business_id)

    def count_trades_for_business(self, business_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM trades WHERE business_id = ?",
                (business_id,),
            ).fetchone()
        return int(row["c"])

    def delete_account(self, account_id: int, *, force: bool = False) -> None:
        """사업자별 증권사/계좌(accounts) 삭제. force는 호환용(무시)."""
        _ = force
        with self._connect() as conn:
            conn.execute("DELETE FROM accounts WHERE id = ?", (int(account_id),))

    def get_all_accounts(
        self,
        business_id: int | None = None,
        market: str | None = None,
    ) -> list[Account]:
        """선택한 사업자(·시장)의 증권사/계좌 목록."""
        return self.list_accounts(business_id, market=market)

    def list_accounts(
        self,
        business_id: int | None = None,
        market: str | None = None,
    ) -> list[Account]:
        if business_id is None:
            return []
        sql = """
            SELECT * FROM accounts
            WHERE business_id = ?
        """
        params: list[Any] = [int(business_id)]
        if market is not None:
            sql += " AND market = ?"
            params.append(normalize_market(market))
        sql += " ORDER BY name COLLATE NOCASE"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_from_row(Account, r) for r in rows]

    def add_account(
        self,
        business_id: int,
        name: str,
        code: str = "",
        account_no: str = "",
        note: str = "",
        market: str | None = MARKET_DOMESTIC,
    ) -> int:
        name = name.strip()
        if not name:
            raise ValueError("거래처명은 필수입니다.")
        mkt = normalize_market(market)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO accounts(
                    business_id, name, code, account_no, note, created_at, market
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(business_id),
                    name,
                    code.strip(),
                    account_no.strip(),
                    note.strip(),
                    now_str(),
                    mkt,
                ),
            )
            return int(cur.lastrowid)

    def count_trades_for_account(self, account_id: int) -> int:
        """accounts는 거래 FK가 없어 항상 0 (삭제 가능 여부 UI용)."""
        _ = account_id
        return 0

    def list_businesses(self) -> list[Business]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM businesses ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [_from_row(Business, r) for r in rows]

    def get_business_by_name(self, name: str) -> Business | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM businesses WHERE name = ?", (name.strip(),)
            ).fetchone()
        return _from_row(Business, row) if row else None

    def get_or_create_business(self, name: str) -> Business:
        existing = self.get_business_by_name(name)
        if existing:
            return existing
        bid = self.add_business(name)
        return Business(
            id=bid,
            name=name.strip(),
            note="",
            created_at=now_str(),
            code="",
            account_no="",
        )

    # ------------------------------------------------------------------
    # Stock (사업자별)
    # ------------------------------------------------------------------
    def add_stock(
        self,
        code: str,
        name: str,
        market: str = MARKET_DOMESTIC,
        note: str = "",
        partner_code: str = "",
        business_id: int | None = None,
    ) -> int:
        if business_id is None:
            raise ValueError("종목 등록 시 사업자가 필요합니다.")
        code = code.strip()
        name = name.strip()
        mkt = normalize_market(market)
        if not code or not name:
            raise ValueError("종목코드와 종목명은 필수입니다.")
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO stocks(
                    business_id, code, name, market, note, partner_code, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(business_id),
                    code,
                    name,
                    mkt,
                    note.strip(),
                    partner_code.strip(),
                    now_str(),
                ),
            )
            return int(cur.lastrowid)

    def update_stock(
        self,
        stock_id: int,
        code: str,
        name: str,
        market: str = MARKET_DOMESTIC,
        note: str = "",
        partner_code: str = "",
        business_id: int | None = None,
    ) -> None:
        mkt = normalize_market(market)
        with self._connect() as conn:
            if business_id is None:
                conn.execute(
                    """
                    UPDATE stocks
                    SET code = ?, name = ?, market = ?, note = ?, partner_code = ?
                    WHERE id = ?
                    """,
                    (
                        code.strip(),
                        name.strip(),
                        mkt,
                        note.strip(),
                        partner_code.strip(),
                        stock_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE stocks
                    SET code = ?, name = ?, market = ?, note = ?, partner_code = ?
                    WHERE id = ? AND business_id = ?
                    """,
                    (
                        code.strip(),
                        name.strip(),
                        mkt,
                        note.strip(),
                        partner_code.strip(),
                        stock_id,
                        int(business_id),
                    ),
                )

    def count_trades_for_stock(self, stock_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM trades WHERE stock_id = ?",
                (stock_id,),
            ).fetchone()
        return int(row["c"])

    def delete_stock(self, stock_id: int, *, force: bool = False) -> None:
        used = self.count_trades_for_stock(stock_id)
        if used and not force:
            raise ValueError(
                f"이 종목과 연결된 거래 내역이 {used}건 존재합니다. "
                "확인 후 강제 삭제해 주세요."
            )
        with self._connect() as conn:
            if force:
                conn.execute("DELETE FROM trades WHERE stock_id = ?", (stock_id,))
            conn.execute("DELETE FROM stocks WHERE id = ?", (stock_id,))

    def delete_stock_by_code(
        self,
        stock_code: str,
        *,
        force: bool = False,
        business_id: int | None = None,
        market: str | None = None,
    ) -> None:
        stock = self.get_stock_by_code(
            stock_code, business_id=business_id, market=market
        )
        if not stock or stock.id is None:
            raise ValueError(f"종목코드 '{stock_code}'를 찾을 수 없습니다.")
        self.delete_stock(int(stock.id), force=force)

    def list_stocks(
        self,
        business_id: int | None = None,
        market: str | None = None,
    ) -> list[Stock]:
        """business_id/market으로 종목 필터. None이면 해당 조건 미적용."""
        sql = "SELECT * FROM stocks WHERE 1=1"
        params: list[Any] = []
        if business_id is not None:
            sql += " AND business_id = ?"
            params.append(int(business_id))
        if market is not None:
            sql += " AND market = ?"
            params.append(normalize_market(market))
        sql += " ORDER BY name COLLATE NOCASE"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_from_row(Stock, r) for r in rows]

    def get_all_stocks(
        self,
        business_id: int | None = None,
        market: str | None = None,
    ) -> list[Stock]:
        """사업자·시장별 종목 조회."""
        return self.list_stocks(business_id, market=market)

    def get_stock_by_code(
        self,
        code: str,
        business_id: int | None = None,
        market: str | None = None,
    ) -> Stock | None:
        sql = "SELECT * FROM stocks WHERE code = ?"
        params: list[Any] = [code.strip()]
        if business_id is not None:
            sql += " AND business_id = ?"
            params.append(int(business_id))
        if market is not None:
            sql += " AND market = ?"
            params.append(normalize_market(market))
        sql += " LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return _from_row(Stock, row) if row else None

    def get_stock_by_name(
        self,
        name: str,
        business_id: int | None = None,
        market: str | None = None,
    ) -> Stock | None:
        sql = "SELECT * FROM stocks WHERE name = ? COLLATE NOCASE"
        params: list[Any] = [name.strip()]
        if business_id is not None:
            sql += " AND business_id = ?"
            params.append(int(business_id))
        if market is not None:
            sql += " AND market = ?"
            params.append(normalize_market(market))
        sql += " LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return _from_row(Stock, row) if row else None

    def get_or_create_stock(
        self,
        code: str,
        name: str,
        business_id: int | None = None,
        market: str | None = MARKET_DOMESTIC,
    ) -> Stock:
        if business_id is None:
            raise ValueError("종목 조회/등록 시 사업자가 필요합니다.")
        mkt = normalize_market(market)
        existing = self.get_stock_by_code(code, business_id=business_id, market=mkt)
        if existing:
            if name and existing.name != name.strip():
                self.update_stock(
                    existing.id,
                    existing.code,
                    name.strip(),
                    mkt,
                    existing.note,
                    existing.partner_code,
                    business_id=business_id,
                )
                existing.name = name.strip()
            return existing
        sid = self.add_stock(code, name, market=mkt, business_id=business_id)
        return Stock(
            id=sid,
            code=code.strip(),
            name=name.strip(),
            market=mkt,
            created_at=now_str(),
            business_id=int(business_id),
        )

    def get_or_create_stock_by_name(
        self,
        name: str,
        code: str | None = None,
        business_id: int | None = None,
        market: str | None = MARKET_DOMESTIC,
    ) -> Stock:
        if business_id is None:
            raise ValueError("종목 조회/등록 시 사업자가 필요합니다.")
        mkt = normalize_market(market)
        name = name.strip()
        if not name:
            raise ValueError("종목명은 필수입니다.")
        existing = self.get_stock_by_name(name, business_id=business_id, market=mkt)
        if existing:
            return existing
        clean_code = (code or "").strip()
        if clean_code:
            return self.get_or_create_stock(
                clean_code, name, business_id=business_id, market=mkt
            )
        n = 1
        while True:
            candidate = f"TMP{n:04d}"
            if not self.get_stock_by_code(
                candidate, business_id=business_id, market=mkt
            ):
                break
            n += 1
        sid = self.add_stock(candidate, name, market=mkt, business_id=business_id)
        return Stock(
            id=sid,
            code=candidate,
            name=name,
            market=mkt,
            created_at=now_str(),
            business_id=int(business_id),
        )

    # ------------------------------------------------------------------
    # Trade
    # ------------------------------------------------------------------
    def add_trade(self, trade: Trade) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO trades(
                    trade_date, business_id, stock_id, side, quantity, price,
                    fee, tax, settlement_amount, memo, source, created_at,
                    currency, fx_rate, price_fx, fee_fx, tax_fx
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.trade_date,
                    trade.business_id,
                    trade.stock_id,
                    trade.side,
                    trade.quantity,
                    trade.price,
                    trade.fee,
                    float(getattr(trade, "tax", 0) or 0),
                    trade.settlement_amount,
                    trade.memo,
                    trade.source,
                    trade.created_at or now_str(),
                    (getattr(trade, "currency", None) or "KRW"),
                    float(getattr(trade, "fx_rate", 0) or 0),
                    float(getattr(trade, "price_fx", 0) or 0),
                    float(getattr(trade, "fee_fx", 0) or 0),
                    float(getattr(trade, "tax_fx", 0) or 0),
                ),
            )
            return int(cur.lastrowid)

    def add_trades_bulk(self, trades: list[Trade]) -> int:
        count = 0
        with self._connect() as conn:
            for trade in trades:
                conn.execute(
                    """
                    INSERT INTO trades(
                        trade_date, business_id, stock_id, side, quantity, price,
                        fee, tax, settlement_amount, memo, source, created_at,
                        currency, fx_rate, price_fx, fee_fx, tax_fx
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade.trade_date,
                        trade.business_id,
                        trade.stock_id,
                        trade.side,
                        trade.quantity,
                        trade.price,
                        trade.fee,
                        float(getattr(trade, "tax", 0) or 0),
                        trade.settlement_amount,
                        trade.memo,
                        trade.source,
                        trade.created_at or now_str(),
                        (getattr(trade, "currency", None) or "KRW"),
                        float(getattr(trade, "fx_rate", 0) or 0),
                        float(getattr(trade, "price_fx", 0) or 0),
                        float(getattr(trade, "fee_fx", 0) or 0),
                        float(getattr(trade, "tax_fx", 0) or 0),
                    ),
                )
                count += 1
        return count

    def delete_trade(self, trade_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))

    def update_trade_date(self, trade_id: int, new_date: str) -> None:
        """특정 거래 ID의 거래일자 업데이트."""
        date_str = str(new_date or "").strip()[:10]
        if not date_str:
            raise ValueError("거래일자는 필수입니다.")
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE trades SET trade_date = ? WHERE id = ?",
                (date_str, int(trade_id)),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"거래 ID {trade_id}를 찾을 수 없습니다.")
            conn.commit()

    def list_trades(
        self,
        business_id: int | None = None,
        stock_id: int | None = None,
        market: str | None = None,
    ) -> list[Trade]:
        sql = """
            SELECT
                t.*,
                b.name AS business_name,
                s.code AS stock_code,
                s.name AS stock_name
            FROM trades t
            JOIN businesses b ON b.id = t.business_id
            JOIN stocks s ON s.id = t.stock_id
            WHERE 1=1
        """
        params: list[Any] = []
        if business_id is not None:
            sql += " AND t.business_id = ?"
            params.append(business_id)
        if stock_id is not None:
            sql += " AND t.stock_id = ?"
            params.append(stock_id)
        if market is not None:
            sql += " AND s.market = ?"
            params.append(normalize_market(market))
        sql += " ORDER BY t.trade_date ASC, t.id ASC"

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_from_row(Trade, r) for r in rows]

    def get_trades_by_period(
        self,
        business_id: int | None,
        start_date: str | date,
        end_date: str | date,
        market: str | None = None,
    ) -> list[Trade]:
        """거래일자(YYYY-MM-DD)가 start~end(포함)인 거래만 반환."""

        def _as_ymd(v: str | date) -> str:
            if isinstance(v, date):
                return v.isoformat()
            return str(v).strip()[:10]

        start = _as_ymd(start_date)
        end = _as_ymd(end_date)
        if start > end:
            start, end = end, start
        trades = self.list_trades(business_id=business_id, market=market)
        return [t for t in trades if start <= str(t.trade_date)[:10] <= end]

    def clear_all_trades(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM trades")

    def clear_trades_for_business(self, business_id: int) -> int:
        """특정 사업자의 매매 거래만 전부 삭제. 삭제 건수 반환."""
        bid = int(business_id)
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM trades WHERE business_id = ?",
                (bid,),
            )
            return int(cur.rowcount or 0)

    # ------------------------------------------------------------------
    # Income (이자·배당) account / broker / records
    # ------------------------------------------------------------------
    def get_income_account_config(
        self, business_id: int | None
    ) -> IncomeAccountConfig:
        return self.get_account_config(business_id).to_income_config()

    def save_income_account_config(
        self,
        business_id: int | None,
        config: IncomeAccountConfig,
    ) -> IncomeAccountConfig:
        if business_id is None:
            raise ValueError("사업자를 선택한 뒤 계정과목을 저장해 주세요.")
        base = self.get_account_config(business_id)
        cfg = config.normalize()
        base.interest_code = cfg.interest_code
        base.prepaid_tax_code = cfg.prepaid_tax_code
        base.bank_code = cfg.bank_code
        saved = self.save_account_config(business_id, base)
        return saved.to_income_config()

    def list_broker_partners(
        self, business_id: int | None = None
    ) -> list[BrokerPartner]:
        if business_id is None:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM broker_partners
                WHERE business_id = ?
                ORDER BY broker_name COLLATE NOCASE
                """,
                (int(business_id),),
            ).fetchall()
        return [_from_row(BrokerPartner, r) for r in rows]

    def upsert_broker_partner(
        self,
        broker_name: str,
        partner_code: str = "",
        business_id: int | None = None,
    ) -> int:
        if business_id is None:
            raise ValueError("증권사 매핑 저장 시 사업자가 필요합니다.")
        name = broker_name.strip()
        if not name:
            raise ValueError("증권사명은 필수입니다.")
        code = (partner_code or "").strip()
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM broker_partners
                WHERE business_id = ? AND broker_name = ? COLLATE NOCASE
                """,
                (int(business_id), name),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE broker_partners
                    SET partner_code = ?, broker_name = ?
                    WHERE id = ? AND business_id = ?
                    """,
                    (code, name, int(existing["id"]), int(business_id)),
                )
                return int(existing["id"])
            cur = conn.execute(
                """
                INSERT INTO broker_partners(
                    business_id, broker_name, partner_code, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (int(business_id), name, code, now_str()),
            )
            return int(cur.lastrowid)

    def update_broker_partners(
        self,
        updates: dict[int, tuple[str, str]],
        business_id: int | None = None,
    ) -> int:
        """id → (broker_name, partner_code) 일괄 갱신."""
        if not updates:
            return 0
        changed = 0
        with self._connect() as conn:
            for pid, (broker_name, partner_code) in updates.items():
                name = str(broker_name or "").strip()
                if not name:
                    continue
                if business_id is None:
                    cur = conn.execute(
                        """
                        UPDATE broker_partners
                        SET broker_name = ?, partner_code = ?
                        WHERE id = ?
                        """,
                        (name, str(partner_code or "").strip(), int(pid)),
                    )
                else:
                    cur = conn.execute(
                        """
                        UPDATE broker_partners
                        SET broker_name = ?, partner_code = ?
                        WHERE id = ? AND business_id = ?
                        """,
                        (
                            name,
                            str(partner_code or "").strip(),
                            int(pid),
                            int(business_id),
                        ),
                    )
                changed += int(cur.rowcount or 0)
        return changed

    def delete_broker_partner(
        self, partner_id: int, business_id: int | None = None
    ) -> None:
        with self._connect() as conn:
            if business_id is None:
                conn.execute(
                    "DELETE FROM broker_partners WHERE id = ?", (int(partner_id),)
                )
            else:
                conn.execute(
                    "DELETE FROM broker_partners WHERE id = ? AND business_id = ?",
                    (int(partner_id), int(business_id)),
                )

    def broker_partner_map(self, business_id: int | None = None) -> dict[str, str]:
        """증권사명(소문자 키) → 거래처코드."""
        return {
            (b.broker_name or "").strip().lower(): (b.partner_code or "").strip()
            for b in self.list_broker_partners(business_id)
            if (b.broker_name or "").strip()
        }

    def add_income_record(
        self,
        *,
        pay_date: str,
        business_id: int,
        product_name: str = "",
        broker_name: str = "",
        income_type: str = "INTEREST",
        gross_amount: float = 0.0,
        corp_tax: float = 0.0,
        local_tax: float = 0.0,
        memo: str = "",
        source: str = "manual",
    ) -> int:
        itype = str(income_type or "INTEREST").strip().upper()
        if itype not in ("INTEREST", "DIVIDEND"):
            itype = "INTEREST"
        pay = str(pay_date).strip()[:10]
        if not pay:
            raise ValueError("지급일은 필수입니다.")
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO income_records(
                    pay_date, business_id, product_name, broker_name, income_type,
                    gross_amount, corp_tax, local_tax, memo, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pay,
                    int(business_id),
                    (product_name or "").strip(),
                    (broker_name or "").strip(),
                    itype,
                    float(gross_amount or 0),
                    float(corp_tax or 0),
                    float(local_tax or 0),
                    (memo or "").strip(),
                    (source or "manual").strip(),
                    now_str(),
                ),
            )
            return int(cur.lastrowid)

    def add_income_records_bulk(self, records: list[dict[str, Any]]) -> int:
        n = 0
        for r in records:
            self.add_income_record(**r)
            n += 1
        return n

    def update_income_record(
        self,
        record_id: int,
        *,
        pay_date: str,
        product_name: str = "",
        broker_name: str = "",
        income_type: str = "INTEREST",
        gross_amount: float = 0.0,
        corp_tax: float = 0.0,
        local_tax: float = 0.0,
        memo: str = "",
    ) -> None:
        itype = str(income_type or "INTEREST").strip().upper()
        if itype not in ("INTEREST", "DIVIDEND"):
            itype = "INTEREST"
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE income_records SET
                    pay_date = ?, product_name = ?, broker_name = ?,
                    income_type = ?, gross_amount = ?, corp_tax = ?,
                    local_tax = ?, memo = ?
                WHERE id = ?
                """,
                (
                    str(pay_date).strip()[:10],
                    (product_name or "").strip(),
                    (broker_name or "").strip(),
                    itype,
                    float(gross_amount or 0),
                    float(corp_tax or 0),
                    float(local_tax or 0),
                    (memo or "").strip(),
                    int(record_id),
                ),
            )

    def delete_income_records(self, record_ids: list[int]) -> int:
        if not record_ids:
            return 0
        with self._connect() as conn:
            placeholders = ",".join("?" for _ in record_ids)
            cur = conn.execute(
                f"DELETE FROM income_records WHERE id IN ({placeholders})",
                [int(x) for x in record_ids],
            )
            return int(cur.rowcount or 0)

    def list_income_records(
        self, business_id: int | None = None
    ) -> list[IncomeRecord]:
        sql = """
            SELECT i.*, b.name AS business_name
            FROM income_records i
            JOIN businesses b ON b.id = i.business_id
            WHERE 1=1
        """
        params: list[Any] = []
        if business_id is not None:
            sql += " AND i.business_id = ?"
            params.append(business_id)
        sql += " ORDER BY i.pay_date ASC, i.id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_from_row(IncomeRecord, r) for r in rows]

    def get_income_records_by_period(
        self,
        business_id: int | None,
        start_date: str | date,
        end_date: str | date,
    ) -> list[IncomeRecord]:
        def _as_ymd(v: str | date) -> str:
            if isinstance(v, date):
                return v.isoformat()
            return str(v).strip()[:10]

        start = _as_ymd(start_date)
        end = _as_ymd(end_date)
        if start > end:
            start, end = end, start
        records = self.list_income_records(business_id=business_id)
        return [r for r in records if start <= str(r.pay_date)[:10] <= end]
