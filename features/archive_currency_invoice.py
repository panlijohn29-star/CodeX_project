from datetime import date
import re

import pymysql

from platform_config import get_db_config


ALLOWED_DB_PROFILES = ("scdbus",)
REPORT_DATE_RANGE_OPTIONS = {
    "3m": {"label": "Last 3 months", "months": 3},
    "6m": {"label": "Last 6 months", "months": 6},
    "1y": {"label": "Last 1 year", "months": 12},
}
DEFAULT_REPORT_DATE_RANGE = "1y"

FEATURE = {
    "id": "archive_currency_invoice",
    "title": "Archive Currency Invoice",
    "category": "Tools",
    "description": "Verify two currency invoices and archive them after user confirmation.",
    "supports_cancel": False,
    "output_type": "interactive",
    "input_schema": [],
    "template": "archive_currency_invoice.html",
    "db_profiles": ALLOWED_DB_PROFILES,
}


REPORT_CHECK_SQL = """
select * from
(
    select v_jobinfo.MBL_NO,v_jobinfo.HBL_NO,cf_charges.INVOICE_NO,sum(COALESCE(AMOUNT_PP,AMOUNT_CC)) as `Invoice Amount`,
    DATE_FORMAT(cf_charges.INVOICE_DATE,'%%Y-%%m-%%d') as `INVOICE_DATE`,cf_charges.CURRENCY_CODE,cf_charges.CUSTOMER_INVOICE_NO,cf_charges.CREATE_BY
    from v_jobinfo
    left join cf_charges on v_jobinfo.JOB_ID=cf_charges.JOB_ID
    left join zdy_currency_invoice ci on ci.invoice_no = cf_charges.invoice_no
    where cf_charges.CURRENCY_CODE not in('CAD','CHF','CNY','EUR','GBP','HKD','SGD','USD','RMB','THB','JPY','MXN','AUD','NZD','MYR')
    and cf_charges.CREATE_DATE >= %s and ci.invoice_no is null

    group by cf_charges.INVOICE_NO

    union all

    select v_jobinfo.MBL_NO,v_jobinfo.HBL_NO,cf_cost.INVOICE_NO,sum(COALESCE(AMOUNT_PP,AMOUNT_CC)) as `Invoice Amount`,
    DATE_FORMAT(cf_cost.INVOICE_DATE,'%%Y-%%m-%%d') as `INVOICE_DATE`,cf_cost.CURRENCY_CODE,cf_cost.CUSTOMER_INVOICE_NO,cf_cost.CREATE_BY
    from v_jobinfo
    left join cf_cost on v_jobinfo.JOB_ID=cf_cost.JOB_ID
    left join zdy_currency_invoice ci on ci.invoice_no = cf_cost.invoice_no
    where cf_cost.CURRENCY_CODE not in('CAD','CHF','CNY','EUR','GBP','HKD','SGD','USD','RMB','THB','JPY','NZD','MXN','AUD','MYR')
    and cf_cost.CREATE_DATE >= %s and ci.invoice_no is null and cf_cost.invoice_no not in ('USAAP250827539','USAAP250835654')
    group by cf_cost.INVOICE_NO
)x
"""


def normalize_db_profile(db_profile):
    profile = str(db_profile or "").strip().lower()
    if profile not in ALLOWED_DB_PROFILES:
        raise ValueError("Archive Currency Invoice only supports USA DB")
    return profile


def parse_invoice_numbers(raw_value):
    tokens = re.findall(r"[A-Za-z0-9]+", str(raw_value or ""))
    invoices = [
        token.upper()
        for token in tokens
        if re.search(r"[A-Za-z]", token) and re.search(r"\d", token)
    ]
    if len(invoices) != 2:
        raise ValueError("Please enter exactly 2 invoice# values")
    if len(set(invoices)) != 2:
        raise ValueError("The 2 invoice# values must be different")
    return invoices


def _connect(db_profile):
    config = get_db_config(db_profile)
    config["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**config)


def _public_row(row):
    return {
        "invoice_no": str(row.get("invoice_no") or ""),
        "amount_balance": row.get("amount_balance"),
    }


def _json_safe_row(row):
    return {
        str(key): "" if value is None else str(value)
        for key, value in row.items()
    }


def normalize_report_date_range(report_date_range):
    value = str(report_date_range or DEFAULT_REPORT_DATE_RANGE).strip().lower()
    if value not in REPORT_DATE_RANGE_OPTIONS:
        raise ValueError("Please select a valid report check range")
    return value


def months_ago(months, today=None):
    current = today or date.today()
    month = current.month - int(months)
    year = current.year
    if month <= 0:
        year_delta, month_remainder = divmod(abs(month), 12)
        year -= year_delta + 1
        month = 12 - month_remainder
    day = min(current.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year, month):
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - date(year, month, 1)).days


def fetch_invoice_balances(db_profile, invoice_numbers):
    profile = normalize_db_profile(db_profile)
    invoices = parse_invoice_numbers("\n".join(invoice_numbers))
    placeholders = ", ".join(["%s"] * len(invoices))
    sql = "select invoice_no, amount_balance from cf_invoice where invoice_no in ({0})".format(placeholders)
    connection = _connect(profile)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, invoices)
            rows = cursor.fetchall()
    finally:
        connection.close()

    row_map = {str(row.get("invoice_no") or "").upper(): _public_row(row) for row in rows}
    ordered_rows = [row_map[invoice] for invoice in invoices if invoice in row_map]
    missing = [invoice for invoice in invoices if invoice not in row_map]
    return ordered_rows, missing


def lookup_payload(db_profile, invoice_text):
    profile = normalize_db_profile(db_profile)
    invoices = parse_invoice_numbers(invoice_text)
    rows, missing = fetch_invoice_balances(profile, invoices)
    return {
        "ok": True,
        "db_profile": profile,
        "invoice_numbers": invoices,
        "rows": rows,
        "missing": missing,
        "can_execute": not missing and len(rows) == 2,
    }


def fetch_currency_invoice_report_check(db_profile, report_date_range=None):
    profile = normalize_db_profile(db_profile)
    range_key = normalize_report_date_range(report_date_range)
    range_config = REPORT_DATE_RANGE_OPTIONS[range_key]
    start_date = months_ago(range_config["months"]).isoformat()
    connection = _connect(profile)
    try:
        with connection.cursor() as cursor:
            cursor.execute(REPORT_CHECK_SQL, (start_date, start_date))
            rows = cursor.fetchall()
    finally:
        connection.close()

    public_rows = [_json_safe_row(row) for row in rows]
    columns = list(public_rows[0].keys()) if public_rows else [
        "MBL_NO",
        "HBL_NO",
        "INVOICE_NO",
        "Invoice Amount",
        "INVOICE_DATE",
        "CURRENCY_CODE",
        "CUSTOMER_INVOICE_NO",
        "CREATE_BY",
    ]
    return columns, public_rows, start_date, range_key, range_config["label"]


def execute_payload(db_profile, invoice_numbers, report_date_range=None):
    profile = normalize_db_profile(db_profile)
    range_key = normalize_report_date_range(report_date_range)
    invoices = parse_invoice_numbers("\n".join(invoice_numbers or []))
    rows, missing = fetch_invoice_balances(profile, invoices)
    if missing or len(rows) != 2:
        raise ValueError("Cannot archive because one or more invoice# values were not found")

    connection = _connect(profile)
    try:
        with connection.cursor() as cursor:
            cursor.executemany(
                "insert into zdy_currency_invoice values (%s)",
                [(invoice,) for invoice in invoices],
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    report_columns, report_rows, report_start_date, report_range, report_range_label = fetch_currency_invoice_report_check(
        profile,
        range_key,
    )

    return {
        "ok": True,
        "db_profile": profile,
        "invoice_numbers": invoices,
        "rows": rows,
        "report_title": "Currency invoice report check",
        "report_columns": report_columns,
        "report_rows": report_rows,
        "report_start_date": report_start_date,
        "report_date_range": report_range,
        "report_date_range_label": report_range_label,
        "message": "Currency invoices archived successfully",
    }
