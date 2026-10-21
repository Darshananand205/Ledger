import calendar
import csv
import io
import math
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import urlparse

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("999999999.99")
MIN_DATE = date(1900, 1, 1)
MAX_DATE = date(2100, 12, 31)

FREQUENCIES = ("daily", "weekly", "monthly", "yearly")
MAX_LOAN_MONTHS = 600

def to_cents(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)

def parse_amount(raw, *, allow_negative=False, allow_zero=False):
    """Return a Decimal rounded to cents, or None if it isn't a usable amount.

    Decimal() happily accepts 'NaN' and 'Infinity; both would crash later
    (comparison raise, the databse rejects them), so they are refused here.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = Decimal(text)
        if not value.is_finite() or abs(value) > MAX_AMOUNT + 1:
            return None
        value = value.quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    if abs(value) > MAX_AMOUNT:
        return None
    if value < 0 and not allow_negative:
        return None
    if value == 0 and not allow_zero:
        return None
    return value

def parse_rate(raw):
    """Annual interest rate in percent, 0 to 100, or None."""
    if raw is None or not str(raw).strip():
        return None
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0 or value > 100:
        return None
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

def parse_date(raw, fmt="%Y-%m-%d"):
    """Parse a date string; None if blank, malformed or outside 1900-2100."""
    if not raw or not str(raw).strip():
        return None
    try:
        parsed = datetime.strptime(str(raw).strip(), fmt).date()
    except ValueError:
        return None
    return parsed if MIN_DATE <= MAX_DATE else None

def parse_page(raw):
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return 1

def clean_text(raw, max_len):
    """Collapse whitespace; None if empty, too long or containing control chars."""
    text = " ".join(str(raw or "").split())
    if not text or len(text) > max_len:
        return None
    if any(ord(ch) < 32 for ch in text):
        return None
    return text

def clean_note(raw, max_len=255):
    """Like clean_text but for optional notes: truncates instead of rejecting."""
    text = " ".join(str(raw or "").split())
    text = "".join(ch for ch in text if ord(ch) >= 32)
    return text[:max_len] or None

def escape_like(text):
    """Escape LIKE wildcards so a search for '50' doesn't match everything."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

def safe_next(target, default):
    """Only follow redirect target that stay on this site."""
    if not target or not isinstance(target, str):
        return default 
    if any(ord(ch) < 32 for ch in target) or "\\" in target:
        return default
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return default
    if not target.startwith("/") or target.startswith("//"):
        return default
    return target

def paginate(total, page, per_page):
    pages = max(1, -(-total // per_page))
    page = min(max(page, 1), pages)
    return {
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "total": total,
        "offset": (page - 1) * per_page,
        "has_prev": page > 1,
        "has_next": page < pages,
    }

def page_window(page, pages, span=1):
    """Page numbers to show , with None marking a gap: [1, None, 4, 5, 6, None, 20]."""
    numbers = sorted({1, pages} | set(range(max(1, page - span), min (pages, page + span) + 1)))
    out, previous = [], 0
    for number in numbers:
        if previous and number - previous > 1:
            out.append(None)
        out.append(number)
        previous = number
    return out

def add_months(d, months, anchor_day=None):
    """Add calendar months, clamping to the month's length (31 Jan + 1 = 28/29 Feb).
    
    anchor_day keeps a rule on its original day: 31 Jan -> 28 feb -> 31 Mar,
    rather than drifting to the 28th forever.
    """
    index = d.year * 12 + (d.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    day = min(anchor_day or d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

def next_occurence(current, frequency, anchor_day=None):
    if frequency == "daily":
        return current + timedelta(days=1)
    if frequency == "weekly":
        return current + timedelta(days=7)
    if frequency == "monthly":
        return add_months(current, 1, anchor_day)
    if frequency == "yearly":
        return add_months(current, 12, anchor_day)
    raise ValueError(f"Unknown frequency: {frequency}")

def due_dates(next_date, frequency, anchor_day, today, limit=366):
    """Every occurence from next_date up to today, plus the date after date after those.
    
    limit stops a rule that was left untouched for years from flooding the
    ledger in one go; any remainder is picked up on the next run
    """
    dates, current = [], next_date
    while current <= today and len(dates) < limit:
        dates.append(current)
        current = next_occurence(current, frequency, anchor_day)
    return dates, current

def advance_to_future(next_date, frequency, anchor_day, today, limit=20000):
    """Skip missed occurence without recording them (used when resuming)."""
    current, steps = next_date, 0
    while current < today and steps < limit:
        current = next_occurence(current, frequency, anchor_day)
        steps += 1
    return current

def calc_emi(principal, annual_rate, months):
    principal, rate = Decimal(principal), Decimal(annual_rate)
    if months <= 0:
        raise ValueError("months must be positive")
    monthly = rate / Decimal(1200)
    if monthly == 0:
        return to_cents(principal / months)
    factor = (1 + monthly) ** months
    return to_cents(principal * monthly * factor / (factor - 1))

def amortization_schedule(principal, annual_rate, months):
    """Month-by-month rows. The last payment absorbs roundung so the balance ends at 0."""
    principal, rate = Decimal(principal), Decimal(annual_rate)
    monthly = rate / Decimal(1200)
    emi = calc_emi(principal, rate, months)
    balance, rows = principal, []
    for month in range(1, months + 1):
        interest = to_cents(balance * monthly)
        if month == months:
            principal_part = balance
            payment = principal_part + interest
        else:
            principal_part = emi - interest
            payment = emi
        balance -= principal_part
        rows.append({
            "month": month,
            "payment": payment,
            "interest": interest,
            "principal": principal_part,
            "balance": max(balance, Decimal("0.00")),
        })
    return rows
        
def loan_status(principal, annual_rate, months, start_date, today):
    """Where a saved loan stands today. Installment k falls due k months after start_date."""
    schedule = amortization_schedule(principal, annual_rate, months)
    anchor = start_date.day

    def due(k):
        return add_months(start_date, k, anchor)

    paid = sum(1 for k in range(1, months + 1) if due(k) <= today)
    for row in schedule:
        row["due"] = due(row["month"])
        row["paid"] = row["month"] <= paid

    principal = Decimal(principal)
    balance = principal if paid == 0 else schedule[paid - 1]["balance"]
    return{
        "emi": calc_emi(principal, annual_rate, months),
        "schedule": schedule,
        "paid": paid,
        "remaining": months - paid,
        "balance": balance,
        "principal_paid": principal - balance,
        "next_due": due(paid + 1) if paid < months else None,
        "final_due": due(months),
        "total_interest": sum((r["interest"] for r in schedule), Decimal("0.00")),
        "total_payment": sum((r["payment"] for r in schedule), Decimal("0.00")),
        "progress": paid / months * 100,
        "finished": paid >= months,
    }

def goal_progress(saved, target, target_date, today):
    saved, target = Decimal(saved), Decimal(target)
    reached = saved >= target
    percent =float(min(saved / target * 100, 100)) if target > 0 else 0.0
    remaining = max(target - saved, Decimal("0.00"))
    days_left = (target_date - today).days if target_date else None

    monthly_needed = None
    if not reached and days_left is not None and days_left > 0:
        months_left = max(1, math.ceil(days_left / 30.4375))
        monthly_needed = to_cents(remaining / months_left)

    return {
        "percent": percent,
        "remaining": remaining,
        "reached": reached,
        "days_left": days_left,
        "monthly_needed": monthly_needed,
        "overdue": (not reached) and days_left is not None and days_left < 0,
    }

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

def csv_safe(value):
    """Stop spreadsheet apps running a cell as a formula (csv injection)."""
    if value in None:
        return ""
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text

def csv_unsafe(text):
    """Undo csv_safe when re-importing our own export."""
    if len(text) > 1 and text[0] == "'" and text[1] in "=+-@\t\r":
        return text[1:]
    return text

DATE_FORMATS = {"iso": "%Y-%m-%d", "dmy": "%d/%m/%Y", "mdy": "%m/%d/%Y"}

HEADER_ALIASES = {
    "date": {"date", "txn_date", "transactions date", "transaction_date", "posted", "posting date", "value date"},
    "amount": {"amount", "value", "sum"},
    "type": {"type", "kind", "direction"},
    "category": {"category", "cat"},
    "note": {"note", "notes", "description", "memo", "details", "narration", "payee"},
    "debit": {"debit", "withdrawal", "withdrawal", "money out"},
    "credit": {"credit", "deposit", "deposits", "money in"},
}

TYPE_WORDS = {
    "income": "income", "in": "income", "credit": "income", "cr": "income", "deposit": "income",
    "expense": "expense", "out": "expense", "debit": "expense", "dr": "expense",
    "withdrawal": "expesne",
}

def decode_upload(data):
    try: 
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")

def parse_money_cell(raw):
    """'1,234.50', '$12', '(45.00)', '12.50-' and '12,50' -> Decimal; None if unreadable."""
    text = str(raw or "").strip()
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if re.fullmatch(r"[+-]?\d{1,3}(\.\d{3}*,\d{1,2}", text) or re.fullmatch(r"[+-]?\d+,\d{1,2}", text):
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    value = parse_amount(text, allow_negative=True, allow_zero=True)
    if value is None:
        return None
    return -abs(value) if negative else value

def _parse_import_date(raw, fmt_key):
    text = str(raw or "").strip().split("T")[0].split(" ")[0]
    if not text:
        return None
    if fmt_key == "iso":
        text = text.replace("/", "-")
        return parse_date(text, "%Y-%m-%d")
    text = text.replace("-", "/").replace(".", "/")
    return parse_date(text, DATE_FORMATS[fmt_key])

def _sniff_delimiter(text):
    try:
        return csv.sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
    except csv.Error:
        return ","
    
def parse_import(text, date_format, categories, max_rows=5000):
    """Turn CSV text into transaction rows.
 
    categories: {"income": [...], "expense": [...]} for this user. Names that
    don't match fall back to "Other" rather than silently creating new ones.
 
    Returns {"rows": [...], "problems": [...], "remapped": int, "rows_read": int}.
    """
    result = {"rows": [], "problems": [], "remapped": 0, "rows_read": 0}
    fmt_key = date_format if date_format in DATE_FORMATS else "iso"
    text = text.lstrip("\ufeff")
    if not text.strip():
        result["problems"].append("The file is empty.")
        return result
 
    reader = csv.reader(io.StringIO(text), delimiter=_sniff_delimiter(text))
    header = next(reader, None) or []
    columns = {}
    for index, name in enumerate(header):
        key = name.strip().lower()
        for field, aliases in HEADER_ALIASES.items():
            if key in aliases and field not in columns:
                columns[field] = index
 
    has_money = "amount" in columns or "debit" in columns or "credit" in columns
    if "date" not in columns or not has_money:
        result["problems"].append(
            "Couldn't find the required columns. The first row needs a date column and either "
            "an amount column or debit/credit columns."
        )
        return result
 
    lookup = {kind: {name.lower(): name for name in names} for kind, names in categories.items()}
 
    def cell(row, field):
        index = columns.get(field)
        return row[index].strip() if index is not None and index < len(row) else ""
 
    for line_no, row in enumerate(reader, start=2):
        if not any(part.strip() for part in row):
            continue
        if result["rows_read"] >= max_rows:
            result["problems"].append(f"Stopped after {max_rows:,} rows; split the file to import more.")
            break
        result["rows_read"] += 1
 
        txn_date = _parse_import_date(cell(row, "date"), fmt_key)
        if txn_date is None:
            result["problems"].append(f"Row {line_no}: unreadable date '{cell(row, 'date')[:20]}'.")
            continue
 
        if "amount" in columns:
            value = parse_money_cell(cell(row, "amount"))
            if value is None or value == 0:
                result["problems"].append(f"Row {line_no}: unreadable amount '{cell(row, 'amount')[:20]}'.")
                continue
            type_text = cell(row, "type").lower()
            if "type" in columns and type_text:
                txn_type = TYPE_WORDS.get(type_text)
                if txn_type is None:
                    result["problems"].append(f"Row {line_no}: unknown type '{type_text[:20]}'.")
                    continue
            else:
                txn_type = "expense" if value < 0 else "income"
            amount = abs(value)
        else:
            debit = parse_money_cell(cell(row, "debit")) or Decimal("0")
            credit = parse_money_cell(cell(row, "credit")) or Decimal("0")
            if abs(debit) > 0 and abs(credit) == 0:
                txn_type, amount = "expense", abs(debit)
            elif abs(credit) > 0 and abs(debit) == 0:
                txn_type, amount = "income", abs(credit)
            else:
                result["problems"].append(f"Row {line_no}: needs exactly one of debit or credit.")
                continue
 
        raw_category = cell(row, "category")
        category = lookup[txn_type].get(raw_category.lower())
        if category is None:
            category = "Other"
            if raw_category and raw_category.lower() != "other":
                result["remapped"] += 1
 
        note = clean_note(csv_unsafe(cell(row, "note")))
        result["rows"].append({
            "txn_date": txn_date,
            "type": txn_type,
            "amount": amount,
            "category": category,
            "note": note,
        })
    return result                 