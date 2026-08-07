"""이자·배당소득 → 엑셀자료일반전표전송 양식 분개."""

from __future__ import annotations

from io import BytesIO
from typing import Iterable, Mapping

from openpyxl import Workbook

from .models import IncomeAccountConfig, IncomeRecord
from .voucher_export import (
    CR,
    DATA_START_ROW,
    DR,
    VoucherLine,
    _clear_data_rows,
    _fill_company,
    _load_template_workbook,
    _won,
)


_ACCT_NAMES = {
    "interest": "이자수익",
    "prepaid_tax": "선납세금",
    "bank": "보통예금",
}


def _accounts(config: IncomeAccountConfig | None) -> dict[str, tuple[str, str]]:
    cfg = (config or IncomeAccountConfig()).normalize()
    return {
        "interest": (cfg.interest_code, _ACCT_NAMES["interest"]),
        "prepaid_tax": (cfg.prepaid_tax_code, _ACCT_NAMES["prepaid_tax"]),
        "bank": (cfg.bank_code, _ACCT_NAMES["bank"]),
    }


def _ymd(pay_date: str) -> str:
    return "".join(ch for ch in str(pay_date) if ch.isdigit())[:8]


def _line(
    *,
    ymd: str,
    side: int,
    acct: tuple[str, str],
    partner_code: str,
    partner_name: str,
    amount: int,
    summary: str,
) -> VoucherLine | None:
    if amount == 0:
        return None
    return VoucherLine(
        ymd=ymd,
        side=side,
        acct_code=acct[0],
        acct_name=acct[1],
        partner_code=partner_code or "",
        partner_name=(partner_name or "")[:30],
        summary_code="",
        summary=summary,
        amount=abs(amount),
    )


def _partner_for(
    record: IncomeRecord,
    broker_map: Mapping[str, str] | None,
) -> tuple[str, str]:
    name = (record.broker_name or "").strip()
    code = ""
    if broker_map and name:
        code = (broker_map.get(name.lower()) or broker_map.get(name) or "").strip()
        if not code:
            # 부분 일치
            key = name.lower()
            for k, v in broker_map.items():
                if k and (k in key or key in k):
                    code = (v or "").strip()
                    break
    return code, name


def income_to_voucher_lines(
    records: Iterable[IncomeRecord],
    *,
    account_config: IncomeAccountConfig | None = None,
    broker_partner_map: Mapping[str, str] | None = None,
) -> list[VoucherLine]:
    """
    건당:
      대변 이자수익(지급액)
      차변 선납세금(법인세) — >0
      차변 선납세금(지방소득세) — >0
      차변 보통예금(정산입금액)
    """
    accts = _accounts(account_config)
    # normalize map keys to lower
    bmap: dict[str, str] = {}
    if broker_partner_map:
        for k, v in broker_partner_map.items():
            bmap[str(k).strip().lower()] = str(v or "").strip()

    ordered = sorted(records, key=lambda r: (r.pay_date, r.id or 0))
    lines: list[VoucherLine] = []
    for rec in ordered:
        ymd = _ymd(rec.pay_date)
        if len(ymd) != 8:
            continue
        product = (rec.product_name or "").strip() or (
            "배당소득" if rec.income_type == "DIVIDEND" else "이자소득"
        )
        label = "배당소득" if rec.income_type == "DIVIDEND" else "이자소득"
        partner_code, partner_name = _partner_for(rec, bmap)

        gross = _won(rec.gross_amount)
        corp = _won(rec.corp_tax)
        local = _won(rec.local_tax)
        net = _won(rec.net_amount)
        if net < 0:
            net = max(gross - corp - local, 0)

        for item in (
            _line(
                ymd=ymd,
                side=CR,
                acct=accts["interest"],
                partner_code=partner_code,
                partner_name=partner_name,
                amount=gross,
                summary=f"{product} {label} 수입",
            ),
            _line(
                ymd=ymd,
                side=DR,
                acct=accts["prepaid_tax"],
                partner_code=partner_code,
                partner_name=partner_name,
                amount=corp,
                summary="원천징수 법인세",
            ),
            _line(
                ymd=ymd,
                side=DR,
                acct=accts["prepaid_tax"],
                partner_code=partner_code,
                partner_name=partner_name,
                amount=local,
                summary="원천징수 지방소득세",
            ),
            _line(
                ymd=ymd,
                side=DR,
                acct=accts["bank"],
                partner_code=partner_code,
                partner_name=partner_name,
                amount=net,
                summary=f"{product} {label} 정산입금액",
            ),
        ):
            if item:
                lines.append(item)
    return lines


def export_income_voucher_excel_bytes(
    records: list[IncomeRecord],
    *,
    company_name: str = "",
    biz_reg_no: str = "",
    account_config: IncomeAccountConfig | None = None,
    broker_partner_map: Mapping[str, str] | None = None,
) -> bytes:
    wb = build_income_voucher_workbook(
        records,
        company_name=company_name,
        biz_reg_no=biz_reg_no,
        account_config=account_config,
        broker_partner_map=broker_partner_map,
    )
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_income_voucher_workbook(
    records: list[IncomeRecord],
    *,
    company_name: str = "",
    biz_reg_no: str = "",
    account_config: IncomeAccountConfig | None = None,
    broker_partner_map: Mapping[str, str] | None = None,
) -> Workbook:
    wb = _load_template_workbook()
    ws = wb.active
    _clear_data_rows(ws)
    _fill_company(ws, company_name=company_name, biz_reg_no=biz_reg_no)

    lines = income_to_voucher_lines(
        records,
        account_config=account_config,
        broker_partner_map=broker_partner_map,
    )
    for offset, line in enumerate(lines):
        row_idx = DATA_START_ROW + offset
        for col_idx, value in enumerate(line.as_row(), start=1):
            if value == "" or value is None:
                continue
            ws.cell(row=row_idx, column=col_idx, value=value)
    return wb
