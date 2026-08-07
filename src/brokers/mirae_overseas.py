"""미래에셋증권 해외주식 거래내역서 PDF 파서."""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any


def _num(text: str | None) -> float:
    if text is None:
        return 0.0
    s = str(text).strip().replace(",", "").replace(" ", "")
    if not s or s in {".", "-", "-."}:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _extract_text(file_bytes: bytes) -> str:
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t)
    return "\n".join(parts)


_HEADER = re.compile(
    r"(?P<date>\d{4}/\d{2}/\d{2})\s+"
    r"(?P<kind>해외주식매수입고|해외주식매수출금|해외주식매도출고|해외주식매도입금|배당금외화입금)\s+"
    r"(?P<ticker>\S+)"
    r"(?:\s+(?P<n1>[\d,\.]+))?"
    r"(?:\s+(?P<n2>[\d,\.]+))?"
    r"(?:\s+(?P<n3>[\d,\.]+))?"
    r"(?:\s+(?P<n4>[\d,\.]+))?"
)

_DETAIL_BUY = re.compile(
    r"^\d+\s+\d+\s+(?P<qty>[\d,\.]+)\s+(?P<price>[\d,\.]+)\s+"
    r"(?P<name>.+?)\s+(?P<hold>[\d,\.]+)\s+(?P<ccy>[A-Z]{3})\s*$"
)
_DETAIL_SELL = re.compile(
    r"^\d+\s+\d+\s+(?P<qty>[\d,\.]+)\s+(?P<price>[\d,\.]+)\s+"
    r"(?P<name>.+?)\s+(?P<ccy>[A-Z]{3})\s*$"
)
_DETAIL_DIV = re.compile(
    r"^\d+\s+\d+\s+(?P<fx>[\d,\.]+)\s+"
    r"(?:(?P<name>.+?)\s+)?"
    r"(?P<a>[\d,\.]+)\s+(?P<b>[\d,\.]+)\s+(?P<ccy>[A-Z]{3})\s*$"
)
_DETAIL_DIV_SIMPLE = re.compile(
    r"^\d+\s+\d+\s+(?P<fx>[\d,\.]+)\s+"
    r"(?:(?P<name>.+?)\s+)?"
    r"(?P<net>[\d,\.]+)\s+(?P<ccy>[A-Z]{3})\s*$"
)
_FX_LINE = re.compile(r"^(?P<fx>[\d,\.]+)\s*Direct", re.I)


def parse_mirae_overseas_pdf(file_bytes: bytes, filename: str = "") -> dict[str, Any]:
    """미래에셋 해외주식 거래내역서 → 표준 행 리스트."""
    notes: list[str] = []
    try:
        text = _extract_text(file_bytes)
    except Exception as exc:  # noqa: BLE001
        return {
            "rows": [],
            "notes": [f"PDF 읽기 실패: {exc}"],
            "source": "mirae-overseas-pdf",
        }

    if not text.strip():
        return {
            "rows": [],
            "notes": ["PDF에서 텍스트를 추출하지 못했습니다."],
            "source": "mirae-overseas-pdf",
        }

    if (
        "미래에셋" not in text
        and "해외주식" not in text
        and "mirae" not in (filename or "").lower()
    ):
        notes.append("미래에셋 해외주식 거래내역서 서식이 아닐 수 있습니다.")

    lines = text.splitlines()
    rows: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = _HEADER.match(line)
        if not m:
            i += 1
            continue

        kind = m.group("kind")
        if kind in {"해외주식매수출금", "해외주식매도입금"}:
            i += 1
            continue

        date = m.group("date").replace("/", "-")
        ticker = m.group("ticker").strip()
        n1 = _num(m.group("n1"))
        n2 = _num(m.group("n2"))

        block = [lines[j].strip() for j in range(i + 1, min(i + 8, len(lines)))]
        qty = 0.0
        price_fx = 0.0
        fee_fx = 0.0
        tax_fx = 0.0
        fx_rate = 0.0
        currency = "USD"
        name = ticker
        side = "BUY"

        if kind == "해외주식매수입고":
            side = "BUY"
            fee_fx = n1
            for bl in block:
                dm = _DETAIL_BUY.match(bl) or _DETAIL_SELL.match(bl)
                if dm:
                    qty = _num(dm.group("qty"))
                    price_fx = _num(dm.group("price"))
                    name = (dm.group("name") or ticker).strip()
                    currency = dm.group("ccy")
                    break
            for bl in block:
                fm = _FX_LINE.match(bl)
                if fm:
                    fx_rate = _num(fm.group("fx"))
                    break
            if qty <= 0 and n2 > 0 and price_fx > 0:
                qty = n2 / price_fx

        elif kind == "해외주식매도출고":
            side = "SELL"
            fee_fx = n1
            for bl in block:
                dm = _DETAIL_SELL.match(bl) or _DETAIL_BUY.match(bl)
                if dm:
                    qty = _num(dm.group("qty"))
                    price_fx = _num(dm.group("price"))
                    name = (dm.group("name") or ticker).strip()
                    currency = dm.group("ccy")
                    break
            for bl in block:
                fm = _FX_LINE.match(bl)
                if fm:
                    fx_rate = _num(fm.group("fx"))
                    break
            if qty <= 0 and n2 > 0 and price_fx > 0:
                qty = n2 / price_fx

        elif kind == "배당금외화입금":
            side = "DIVIDEND"
            gross = n1
            qty = 1.0
            price_fx = gross
            for bl in block:
                if re.match(r"^\d{4}/\d{2}/\d{2}\b", bl):
                    break
                dm = _DETAIL_DIV.match(bl)
                if dm:
                    fx_rate = _num(dm.group("fx"))
                    currency = dm.group("ccy")
                    if dm.group("name"):
                        name = dm.group("name").strip()
                    tax_fx = _num(dm.group("a"))
                    net = _num(dm.group("b"))
                    if tax_fx > gross and net <= gross:
                        # a=name-number misread — treat a as net if needed
                        pass
                    if net > 0 and gross > 0 and tax_fx >= 0 and abs(gross - tax_fx - net) < 0.2:
                        pass
                    elif net > 0 and gross > net:
                        tax_fx = max(0.0, gross - net)
                    break
                dm2 = _DETAIL_DIV_SIMPLE.match(bl)
                if dm2:
                    fx_rate = _num(dm2.group("fx"))
                    currency = dm2.group("ccy")
                    if dm2.group("name"):
                        name = dm2.group("name").strip()
                    net = _num(dm2.group("net"))
                    if net > 0 and gross > net:
                        tax_fx = max(0.0, gross - net)
                    break
            if not fx_rate:
                for bl in block:
                    if re.match(r"^\d{4}/\d{2}/\d{2}\b", bl):
                        break
                    fm = _FX_LINE.match(bl)
                    if fm:
                        fx_rate = _num(fm.group("fx"))
                        break
            if name == ticker:
                for bl in block:
                    if re.match(r"^\d{4}/\d{2}/\d{2}\b", bl):
                        break
                    if (
                        bl
                        and not re.match(r"^\d", bl)
                        and "Direct" not in bl
                        and "증권" not in bl
                    ):
                        name = bl.strip()
                        break

        if qty <= 0 or (side != "DIVIDEND" and price_fx <= 0 and n2 <= 0):
            i += 1
            continue

        if price_fx <= 0 and n2 > 0 and qty > 0:
            price_fx = n2 / qty

        rows.append(
            {
                "거래일자": date,
                "거래유형": (
                    "해외매수"
                    if side == "BUY"
                    else ("해외매도" if side == "SELL" else "외화배당")
                ),
                "side": side,
                "종목코드": ticker,
                "종목명": name,
                "수량": qty,
                "외화단가": price_fx,
                "외화수수료": fee_fx,
                "외화제세금": tax_fx,
                "통화코드": currency,
                "적용환율": fx_rate,
                "메모": f"미래에셋 {kind}",
            }
        )
        i += 1

    notes.append(
        f"미래에셋 해외주식 PDF에서 {len(rows)}건을 추출했습니다."
        + (f" ({filename})" if filename else "")
    )
    return {"rows": rows, "notes": notes, "source": "mirae-overseas-pdf"}


def mirae_rows_to_preview_df(rows: list[dict[str, Any]]):
    import pandas as pd

    cols = [
        "거래일자",
        "거래유형",
        "종목코드",
        "종목명",
        "수량",
        "외화단가",
        "외화수수료",
        "외화제세금",
        "통화코드",
        "적용환율",
        "메모",
    ]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]
