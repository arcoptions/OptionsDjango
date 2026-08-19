"""Read the broker P&L statements into one ledger.

Three brokers, three layouts, and the statements overlap: Sahi's 01Apr26-11May26
export is contained inside its 01Apr26-16Aug26 one, so a naive "load every file in
Downloads" double counts about six lakh. `STATEMENTS` is therefore an explicit
manifest -- each entry is a period we want counted exactly once, and adding a
fresh export means editing that list rather than dropping a file in a folder.

Symbol formats, one per broker:
    Zerodha  NATIONALUM26JUL225CE       <sym><yy><MON><strike><CE|PE>
    Sahi     NATIONALUM 30 Jul 225 Call <sym> <dd> <mon> <strike> <Call|Put>
    Dhan     OPT NATIONALUM 30 Jul 2026 225 CE
"""
import datetime as dt
import os
import re
from decimal import Decimal, InvalidOperation

import pandas as pd

from .models import BrokerPeriodSummary, BrokerPnlEntry, TrackedStock

DOWNLOADS = os.path.expanduser("~/Downloads")

INDEX_UNDERLYINGS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
    "SENSEX", "BANKEX", "SENSEX50",
}

# Dhan is the reference spelling, so LTIMindtree stays LTM rather than LTIM.
# TATAMOTORS has no entry at all: it demerged into TMPV and TMLCV, so the old
# contracts in the statements can never be re-priced.
SYMBOL_ALIASES = {"LTIM": "LTM"}
DELISTED = {"TATAMOTORS"}

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# (broker, account, label, from, to, filename, parser)
STATEMENTS = [
    ("Zerodha", "KW0546", "FY25-26", dt.date(2025, 4, 1), dt.date(2026, 3, 31),
     "pnl-KW0546.xlsx", "zerodha"),
    ("Zerodha", "KW0546", "FY26-27 to 17Aug", dt.date(2026, 4, 1), dt.date(2026, 8, 17),
     "pnl-KW0546 (1).xlsx", "zerodha"),
    ("Sahi", "4006723100", "FY25-26", dt.date(2025, 4, 1), dt.date(2026, 3, 31),
     "FO-Pnl-4006723100.xlsx", "sahi"),
    # (2) spans 01Apr26-16Aug26 and supersedes (1), which stopped at 11May26.
    ("Sahi", "4006723100", "FY26-27 to 16Aug", dt.date(2026, 4, 1), dt.date(2026, 8, 16),
     "FO-Pnl-4006723100 (2).xlsx", "sahi"),
    ("Dhan", "FUDV76212Z", "FY26-27 to 17Aug", dt.date(2026, 4, 1), dt.date(2026, 8, 17),
     "Dhan_P&L_01-04-2026_17-08-2026.csv", "dhan"),
]

ZERODHA_RE = re.compile(
    r"^(?P<sym>.+?)(?P<yy>\d{2})(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
    r"(?P<strike>\d+(?:\.\d+)?)(?P<ot>CE|PE)$"
)
SAHI_RE = re.compile(
    r"^(?P<sym>\S+)\s+(?P<dd>\d{1,2})\s+(?P<mon>[A-Za-z]{3})\s+"
    r"(?P<strike>[\d.]+)\s+(?P<ot>Call|Put)$", re.I
)
DHAN_RE = re.compile(
    r"^OPT\s+(?P<sym>\S+)\s+(?P<dd>\d{1,2})\s+(?P<mon>[A-Za-z]{3})\s+(?P<yyyy>\d{4})\s+"
    r"(?P<strike>[\d.]+)\s+(?P<ot>CE|PE)$"
)


def _decimal(value, default="0"):
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return Decimal(default)
        return Decimal(str(value).replace(",", "").strip() or default)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _canonical(symbol):
    symbol = symbol.upper().strip()
    return SYMBOL_ALIASES.get(symbol, symbol)


def _expiry(year, month, day=None):
    try:
        return dt.date(year, month, day) if day else None
    except ValueError:
        return None


def parse_zerodha_symbol(raw):
    match = ZERODHA_RE.match(raw.upper().strip())
    if not match:
        return None
    return {
        "underlying": _canonical(match.group("sym")),
        "option_type": match.group("ot"),
        "strike": _decimal(match.group("strike")),
        "expiry_date": None,  # monthly code only; the day is not in the symbol
        "instrument_kind": "OPT",
    }


def parse_sahi_symbol(raw):
    match = SAHI_RE.match(raw.strip())
    if not match:
        return None
    month = MONTHS.get(match.group("mon").upper())
    return {
        "underlying": _canonical(match.group("sym")),
        "option_type": "CE" if match.group("ot").upper() == "CALL" else "PE",
        "strike": _decimal(match.group("strike")),
        "expiry_date": None if not month else _guess_expiry(month, int(match.group("dd"))),
        "instrument_kind": "OPT",
    }


def parse_dhan_symbol(raw):
    match = DHAN_RE.match(raw.strip())
    if not match:
        return None
    month = MONTHS.get(match.group("mon").upper())
    return {
        "underlying": _canonical(match.group("sym")),
        "option_type": match.group("ot"),
        "strike": _decimal(match.group("strike")),
        "expiry_date": _expiry(int(match.group("yyyy")), month, int(match.group("dd"))) if month else None,
        "instrument_kind": "OPT",
    }


def _guess_expiry(month, day):
    """Sahi omits the year; statements span Apr25-Aug26 so pick the nearer one."""
    for year in (2026, 2025):
        candidate = _expiry(year, month, day)
        if candidate and dt.date(2025, 3, 1) <= candidate <= dt.date(2026, 12, 31):
            return candidate
    return None


def _zerodha_rows(path):
    frame = pd.read_excel(path, sheet_name="F&O", header=None)
    label = frame[1].astype(str).str.strip()

    def header_value(name):
        hits = frame.loc[label == name, 2]
        return _decimal(hits.iloc[0]) if len(hits) else Decimal("0")

    charge_rows = {}
    for index in range(len(frame)):
        text = str(frame.iat[index, 1]).strip()
        if text.endswith(" - Z") or text == "IPFT":
            charge_rows[text.replace(" - Z", "")] = float(_decimal(frame.iat[index, 2]))

    start = frame.index[label == "Symbol"][0]
    table = frame.iloc[start + 1:, 1:].copy()
    table.columns = frame.iloc[start, 1:].tolist()
    table = table[table["Symbol"].notna()]

    rows = []
    for _, row in table.iterrows():
        parsed = parse_zerodha_symbol(str(row["Symbol"]))
        rows.append((str(row["Symbol"]).strip(), parsed, {
            "quantity": int(_decimal(row.get("Quantity"))),
            "buy_value": _decimal(row.get("Buy Value")),
            "sell_value": _decimal(row.get("Sell Value")),
            "realised_pnl": _decimal(row.get("Realized P&L")),
            "unrealised_pnl": _decimal(row.get("Unrealized P&L")),
        }))
    summary = {
        "gross_realised": header_value("Realized P&L"),
        "charges": header_value("Charges"),
        "unrealised": header_value("Unrealized P&L"),
        "charge_breakdown": charge_rows,
    }
    return rows, summary


def _sahi_rows(path):
    frame = pd.read_excel(path, sheet_name=0, header=None)
    first = frame[0].astype(str).str.strip()

    totals = [index for index, value in enumerate(first) if value == "Total"]
    summary = {
        "gross_realised": _decimal(frame.iat[totals[0], 1]) if totals else Decimal("0"),
        "unrealised": _decimal(frame.iat[totals[1], 1]) if len(totals) > 1 else Decimal("0"),
        "charges": _decimal(frame.iat[totals[2], 1]) if len(totals) > 2 else Decimal("0"),
        "charge_breakdown": {},
    }
    charge_start = frame.index[first == "Charges"]
    if len(charge_start) and len(totals) > 2:
        for index in range(charge_start[0] + 1, totals[2]):
            name = str(frame.iat[index, 0]).strip()
            if name and name != "nan":
                summary["charge_breakdown"][name] = float(_decimal(frame.iat[index, 1]))

    headers = [index for index, value in enumerate(first) if value == "Symbol"]
    rows = []
    for header in headers:
        for index in range(header + 1, len(frame)):
            raw = str(frame.iat[index, 0]).strip()
            if raw in ("", "nan", "Options", "Futures", "Symbol"):
                if raw in ("Symbol",):
                    break
                continue
            parsed = parse_sahi_symbol(raw)
            if not parsed:
                continue
            rows.append((raw, parsed, {
                "quantity": int(_decimal(frame.iat[index, 1])),
                "buy_value": _decimal(frame.iat[index, 4]),
                "sell_value": _decimal(frame.iat[index, 7]),
                "realised_pnl": _decimal(frame.iat[index, 8]),
                "unrealised_pnl": _decimal(frame.iat[index, 9]),
            }))
    return rows, summary


def _dhan_rows(path):
    frame = pd.read_csv(path, skiprows=6)
    rows = []
    for _, row in frame.iterrows():
        raw = str(row.get("Scrip Name", "")).strip()
        parsed = parse_dhan_symbol(raw)
        if not parsed:
            continue
        rows.append((raw, parsed, {
            "quantity": int(_decimal(row.get("Buy Qty."))),
            "buy_value": _decimal(row.get("Buy Value")),
            "sell_value": _decimal(row.get("Sell Value")),
            "realised_pnl": _decimal(row.get("Realised P&L")),
            "unrealised_pnl": _decimal(row.get("Unrealised P&L")),
        }))

    # The totals live on a trailing "Net P&L,...,Gross P&L,...,Total Charges,..." line.
    summary = {"gross_realised": Decimal("0"), "charges": Decimal("0"),
               "unrealised": Decimal("0"), "charge_breakdown": {}}
    with open(path, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.startswith("Net P&L"):
                continue
            cells = [cell.strip() for cell in line.split(",")]
            pairs = dict(zip(cells[0::2], cells[1::2]))
            summary["gross_realised"] = _decimal(pairs.get("Gross P&L"))
            summary["charges"] = _decimal(pairs.get("Total Charges"))
            summary["charge_breakdown"] = {"Brokerage": float(_decimal(pairs.get("Brokerage")))}
    return rows, summary


PARSERS = {"zerodha": _zerodha_rows, "sahi": _sahi_rows, "dhan": _dhan_rows}


def ingest(downloads=DOWNLOADS, verbose=True):
    """Load every statement in the manifest. Returns (entries, summaries, missing)."""
    BrokerPnlEntry.objects.all().delete()
    BrokerPeriodSummary.objects.all().delete()

    entries, missing, summaries = [], [], 0
    for broker, account, label, start, end, filename, kind in STATEMENTS:
        path = os.path.join(downloads, filename)
        if not os.path.exists(path):
            missing.append(filename)
            continue
        rows, summary = PARSERS[kind](path)
        BrokerPeriodSummary.objects.create(
            broker=broker, account=account, period_label=label,
            period_from=start, period_to=end, source_file=filename,
            gross_realised=summary["gross_realised"], charges=summary["charges"],
            unrealised=summary["unrealised"], charge_breakdown=summary["charge_breakdown"],
        )
        summaries += 1
        for raw, parsed, values in rows:
            parsed = parsed or {}
            entries.append(BrokerPnlEntry(
                broker=broker, account=account, period_label=label, source_file=filename,
                raw_symbol=raw,
                underlying=parsed.get("underlying", ""),
                instrument_kind=parsed.get("instrument_kind", ""),
                option_type=parsed.get("option_type", ""),
                strike=parsed.get("strike"),
                expiry_date=parsed.get("expiry_date"),
                **values,
            ))
        if verbose:
            print(f"  {broker:8s} {label:20s} {len(rows):4d} rows  from {filename}")

    BrokerPnlEntry.objects.bulk_create(entries, batch_size=500)
    return len(entries), summaries, missing


def rebuild_universe():
    """Collapse the ledger into the unique stock list we will track."""
    from django.db.models import Count, Sum

    aggregate = (
        BrokerPnlEntry.objects
        .exclude(underlying="")
        .exclude(underlying__in=INDEX_UNDERLYINGS)
        .values("underlying")
        .annotate(
            contracts=Count("id"),
            pnl=Sum("realised_pnl"),
            buys=Sum("buy_value"),
            sells=Sum("sell_value"),
        )
    )
    brokers_by_symbol = {}
    for row in BrokerPnlEntry.objects.exclude(underlying="").values("underlying", "broker"):
        brokers_by_symbol.setdefault(row["underlying"], set()).add(row["broker"][0])

    seen = []
    for row in aggregate:
        symbol = row["underlying"]
        stock, _ = TrackedStock.objects.update_or_create(
            symbol=symbol,
            defaults={
                "contracts_traded": row["contracts"],
                "realised_pnl": row["pnl"] or 0,
                "turnover": (row["buys"] or 0) + (row["sells"] or 0),
                "brokers": "".join(sorted(brokers_by_symbol.get(symbol, ""))),
                "is_active": True,
            },
        )
        seen.append(stock.symbol)

    # Rank by how much of the account actually flowed through the name; the
    # downloader works this order so a truncated run still covers what matters.
    ordered = TrackedStock.objects.filter(symbol__in=seen).order_by("-turnover")
    for position, stock in enumerate(ordered, start=1):
        stock.priority = position
        stock.save(update_fields=["priority"])

    TrackedStock.objects.filter(symbol__in=DELISTED).update(is_active=False)
    TrackedStock.objects.exclude(symbol__in=seen).update(is_active=False)
    return TrackedStock.objects.filter(is_active=True).count()
