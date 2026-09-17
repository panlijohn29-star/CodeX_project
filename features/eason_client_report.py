import datetime as d

import pymysql

from platform_config import get_db_config


ALLOWED_DB_PROFILES = ("scdbus",)
REPORT_TYPE_OPTIONS = ("ALL", "AP", "AR")
HBL_JOB_TYPES = ("AI", "AE", "DO", "OE", "OI")
MAX_INPUT_VALUES = 300
MAX_PREVIEW_ROWS = 5000
EMPTY_COLUMNS = [
    "report_type", "job_no", "MBL_NO", "HBL_NO", "billing_office", "invoice_no",
    "invoice_status", "invoice_edi_status", "CHARGE_LOCAL_NAME", "cost_amt",
    "billing_party", "inv_amt", "CUSTOMER", "CREATE_BY", "HBL_TERMS", "HBL_PAYMENT_TERMS",
]

FEATURE = {
    "id": "eason_client_report",
    "title": "Eason Client Report",
    "category": "Reports",
    "description": "Search AP and AR client charges by JOB_NO or HBL_NO for one billing office.",
    "supports_cancel": False,
    "output_type": "interactive",
    "input_schema": [],
    "template": "eason_client_report.html",
    "db_profiles": ALLOWED_DB_PROFILES,
}


def parse_values(raw_text):
    values = []
    seen = set()
    for item in str(raw_text or "").replace("\n", ",").split(","):
        value = item.strip()
        key = value.casefold()
        if value and key not in seen:
            values.append(value)
            seen.add(key)
    if len(values) > MAX_INPUT_VALUES:
        raise ValueError("A maximum of {0} unique Job/HBL numbers can be queried at once".format(MAX_INPUT_VALUES))
    return values


def normalize_db_profile(db_profile):
    profile = str(db_profile or "").strip().lower()
    if profile not in ALLOWED_DB_PROFILES:
        raise ValueError("Please select scdbus")
    return profile


def normalize_report_type(report_type):
    value = str(report_type or "").strip().upper()
    if value not in REPORT_TYPE_OPTIONS:
        raise ValueError("Please select All, AP, or AR")
    return value


def normalize_search_mode(search_mode):
    value = str(search_mode or "").strip().upper()
    if value not in {"JOB_NO", "HBL_NO"}:
        raise ValueError("Please select JOB_NO or HBL_NO")
    return value


def normalize_job_type(job_type):
    value = str(job_type or "").strip().upper()
    if value not in HBL_JOB_TYPES:
        raise ValueError("Please select AI, AE, DO, OE, or OI before searching by HBL_NO")
    return value


def _hbl_field(job_type):
    fields = {
        "AI": "op_ai_job.HAWB_NO",
        "AE": "op_ae_job.HAWB_NO",
        "OE": "TRIM(REPLACE(REPLACE(op_oe_job.HBL_NO, CHAR(13), ''), CHAR(10), ''))",
        "OI": "op_oi_job.HBL_NO",
        "DO": "op_oi_job.HBL_NO",
    }
    return fields[job_type]


def _search_filter(search_mode, search_values, job_type):
    if not search_values:
        raise ValueError("Please enter at least one {0}".format("HBL number" if search_mode == "HBL_NO" else "job number"))
    placeholders = ", ".join(["%s"] * len(search_values))
    if search_mode == "JOB_NO":
        return "v_jobinfo.JOB_NO in ({0})".format(placeholders), list(search_values), ""
    normalized_job_type = normalize_job_type(job_type)
    return "v_jobinfo.JOB_TYPE = %s and {0} in ({1})".format(_hbl_field(normalized_job_type), placeholders), [normalized_job_type, *search_values], normalized_job_type


def _select_sql(source_table, report_type, search_filter):
    return """
select %s as report_type,
v_jobinfo.job_no, v_jobinfo.MBL_NO, v_jobinfo.HBL_NO,
cf_cost.company_code as billing_office, cf_cost.invoice_no,
CASE WHEN ci.INVOICE_STATUS = 0 THEN 'DRAFT'
WHEN ci.INVOICE_STATUS = 1 THEN 'CHECKED'
WHEN ci.INVOICE_STATUS = 2 THEN 'CONFIRMED'
WHEN ci.INVOICE_STATUS = 3 THEN 'CANCELLED' ELSE 'WARNING' END AS invoice_status,
CASE WHEN ci.INTERNAL_EDI_INVOICE_STATUS = 6 THEN 'confirm success'
WHEN ci.INTERNAL_EDI_INVOICE_STATUS = 2 THEN 'to be confirmed'
WHEN ci.INTERNAL_EDI_INVOICE_STATUS = 4 THEN 'no matching'
WHEN ci.INTERNAL_EDI_INVOICE_STATUS = 5 THEN 'confirm'
WHEN ci.INTERNAL_EDI_INVOICE_STATUS = 7 THEN 'confirm success'
WHEN ci.INTERNAL_EDI_INVOICE_STATUS = 9 THEN 'call back success'
WHEN ci.INTERNAL_EDI_INVOICE_STATUS = 0 OR ci.INTERNAL_EDI_INVOICE_STATUS IS NULL THEN 'new invoice'
WHEN ci.INTERNAL_EDI_INVOICE_STATUS = 3 THEN 'rejected' END AS invoice_edi_status,
cf_cost.CHARGE_LOCAL_NAME, cf_cost.exchange_usd as cost_amt, cf_cost.balance as billing_party,
(select amount_balance from cf_invoice where cf_invoice.invoice_no = cf_cost.invoice_no) as inv_amt,
v_jobinfo.customer as CUSTOMER, cf_cost.create_by as CREATE_BY, v_jobinfo.HBL_TERMS, v_jobinfo.HBL_PAYMENT_TERMS
from {0} cf_cost
left join CF_INVOICE ci on cf_cost.invoice_no = ci.invoice_no
left join v_jobinfo on cf_cost.job_id = v_jobinfo.job_id
left join op_oe_job on op_oe_job.JOB_ID = cf_cost.job_id
left join op_oi_job on op_oi_job.JOB_ID = cf_cost.job_id
left join op_ae_job on op_ae_job.JOB_ID = cf_cost.job_id
left join op_ai_job on op_ai_job.JOB_ID = cf_cost.job_id
where {1} and cf_cost.COMPANY_CODE = %s
""".format(source_table, search_filter).strip(), [report_type]


def build_query(search_values, office, report_type, search_mode, job_type=""):
    office = str(office or "").strip()
    if not office:
        raise ValueError("Please enter a company code")
    selected_type = normalize_report_type(report_type)
    selected_mode = normalize_search_mode(search_mode)
    search_filter, search_params, selected_job_type = _search_filter(selected_mode, search_values, job_type)
    selected_sources = (("cf_cost", "AP"), ("CF_CHARGES", "AR")) if selected_type == "ALL" else (("cf_cost", "AP"),) if selected_type == "AP" else (("CF_CHARGES", "AR"),)
    queries, params = [], []
    for table, label in selected_sources:
        query, label_params = _select_sql(table, label, search_filter)
        queries.append(query)
        params.extend(label_params + search_params + [office])
    return "\nUNION ALL\n".join(queries) + "\norder by invoice_no\nlimit {0}".format(MAX_PREVIEW_ROWS + 1), params, selected_type, selected_mode, selected_job_type


def _connect(db_profile):
    config = get_db_config(db_profile)
    config["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**config)


def _safe_value(value):
    if value is None:
        return ""
    if isinstance(value, (d.datetime, d.date, d.time)):
        return value.isoformat(sep=" ") if isinstance(value, d.datetime) else value.isoformat()
    return str(value)


def _preview_sql(connection, sql, params):
    return connection.escape_string(sql.replace("%s", "{}", len(params))).format(*[connection.escape_string(str(value)) for value in params])


def _classify_search_values(connection, search_values, office, report_type, search_mode, job_type):
    placeholders = ", ".join(["%s"] * len(search_values))
    if search_mode == "JOB_NO":
        match_sql = "select distinct JOB_ID, JOB_NO as search_value from v_jobinfo where JOB_NO in ({0})".format(placeholders)
        match_params = list(search_values)
    else:
        field = _hbl_field(job_type)
        match_sql = """
select distinct v_jobinfo.JOB_ID, {0} as search_value from v_jobinfo
left join op_oe_job on op_oe_job.JOB_ID = v_jobinfo.JOB_ID
left join op_oi_job on op_oi_job.JOB_ID = v_jobinfo.JOB_ID
left join op_ae_job on op_ae_job.JOB_ID = v_jobinfo.JOB_ID
left join op_ai_job on op_ai_job.JOB_ID = v_jobinfo.JOB_ID
where v_jobinfo.JOB_TYPE = %s and {0} in ({1})
""".format(field, placeholders)
        match_params = [job_type, *search_values]
    with connection.cursor() as cursor:
        cursor.execute(match_sql, match_params)
        matched = {}
        for row in cursor.fetchall():
            if row["search_value"] is not None:
                matched.setdefault(str(row["search_value"]).strip().casefold(), set()).add(str(row["JOB_ID"]))
        ids = sorted({job_id for group in matched.values() for job_id in group})
        charged = set()
        if ids:
            id_placeholders = ", ".join(["%s"] * len(ids))
            sources = ["cf_cost"] if report_type == "AP" else ["CF_CHARGES"] if report_type == "AR" else ["cf_cost", "CF_CHARGES"]
            charge_sql = " UNION ".join("select distinct JOB_ID from {0} where JOB_ID in ({1}) and COMPANY_CODE = %s".format(source, id_placeholders) for source in sources)
            charge_params = []
            for _source in sources:
                charge_params.extend([*ids, office])
            cursor.execute(charge_sql, charge_params)
            charged = {str(row["JOB_ID"]) for row in cursor.fetchall()}
    no_office, not_found = [], []
    for value in search_values:
        ids = matched.get(value.strip().casefold())
        if not ids:
            not_found.append(value)
        elif ids.isdisjoint(charged):
            no_office.append(value)
    return no_office, not_found


def search_payload(db_profile, raw_search_values, office, report_type, search_mode, job_type=""):
    profile = normalize_db_profile(db_profile)
    search_values = parse_values(raw_search_values)
    sql, params, selected_type, selected_mode, selected_job_type = build_query(search_values, office, report_type, search_mode, job_type)
    connection = _connect(profile)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        no_office, not_found = _classify_search_values(connection, search_values, str(office).strip(), selected_type, selected_mode, selected_job_type)
        query_sql = _preview_sql(connection, sql, params)
    finally:
        connection.close()
    truncated = len(rows) > MAX_PREVIEW_ROWS
    public_rows = [{key: _safe_value(value) for key, value in row.items()} for row in rows[:MAX_PREVIEW_ROWS]]
    return {"ok": True, "db_profile": profile, "report_type": selected_type, "search_mode": selected_mode, "job_type": selected_job_type, "columns": list(public_rows[0]) if public_rows else EMPTY_COLUMNS, "rows": public_rows, "row_count": len(public_rows), "truncated": truncated, "max_preview_rows": MAX_PREVIEW_ROWS, "no_office_charges": no_office, "not_found": not_found, "query_sql": query_sql}


def preview_payload(db_profile, raw_search_values, office, report_type, search_mode, job_type=""):
    profile = normalize_db_profile(db_profile)
    search_values = parse_values(raw_search_values)
    sql, params, selected_type, selected_mode, selected_job_type = build_query(search_values, office, report_type, search_mode, job_type)
    connection = _connect(profile)
    try:
        query_sql = _preview_sql(connection, sql, params)
    finally:
        connection.close()
    return {"ok": True, "db_profile": profile, "report_type": selected_type, "search_mode": selected_mode, "job_type": selected_job_type, "query_sql": query_sql}
