import pymysql

from platform_config import get_db_config


ALLOWED_DB_PROFILES = ("scdbus", "scdbca")
REPORT_TYPE_OPTIONS = ("AR", "AP", "AR_AP")
JOB_TYPE_OPTIONS = ("ALL", "AE", "AI", "DO", "OE", "OI", "OT")

FEATURE = {
    "id": "ar_ap_breakdown",
    "title": "AR/AP breakdown",
    "category": "Reports",
    "description": "Search AR, AP, or combined AR/AP charge breakdown by ETD and customer.",
    "supports_cancel": False,
    "output_type": "interactive",
    "input_schema": [],
    "template": "ar_ap_breakdown.html",
    "db_profiles": ALLOWED_DB_PROFILES,
}


BASE_QUERIES = {
    "AR": """
        select 'AR' as AR_AP, v_jobinfo.job_type, v_jobinfo.customer,
        v_jobinfo.job_no, v_jobinfo.MBL_NO,
        cf_cost.company_code as billing_office, cf_cost.invoice_no, hbl_no, CHARGE_LOCAL_NAME,
        exchange_usd as AMT, cf_cost.balance as billing_party,
        (select amount_balance from cf_invoice where cf_invoice.invoice_no = cf_cost.invoice_no) as inv_amt
        from CF_CHARGES CF_COST
        left join v_jobinfo on cf_cost.job_id = v_jobinfo.job_id
    """,
    "AP": """
        select 'AP' as AR_AP, v_jobinfo.job_type, v_jobinfo.customer,
        v_jobinfo.job_no, v_jobinfo.MBL_NO,
        cf_cost.company_code as billing_office, cf_cost.invoice_no, hbl_no, CHARGE_LOCAL_NAME,
        exchange_usd as AMT, cf_cost.balance as billing_party,
        (select amount_balance from cf_invoice where cf_invoice.invoice_no = cf_cost.invoice_no) as inv_amt
        from cf_cost
        left join v_jobinfo on cf_cost.job_id = v_jobinfo.job_id
    """,
}

EMPTY_COLUMNS = [
    "AR_AP",
    "job_type",
    "customer",
    "job_no",
    "MBL_NO",
    "billing_office",
    "invoice_no",
    "hbl_no",
    "CHARGE_LOCAL_NAME",
    "AMT",
    "billing_party",
    "inv_amt",
]


def normalize_db_profile(db_profile):
    profile = str(db_profile or "").strip().lower()
    if profile not in ALLOWED_DB_PROFILES:
        raise ValueError("Please select scdbus or scdbca")
    return profile


def normalize_report_type(report_type):
    value = str(report_type or "").strip().upper().replace("&", "_").replace("/", "_")
    if value in ("ARAP", "AR_AP", "AR AND AP", "AR_AP"):
        return "AR_AP"
    if value not in REPORT_TYPE_OPTIONS:
        raise ValueError("Please select AR, AP, or AR&AP")
    return value


def normalize_job_type(job_type):
    value = str(job_type or "ALL").strip().upper()
    if value not in JOB_TYPE_OPTIONS:
        raise ValueError("Please select a valid JOB_TYPE")
    return value


def normalize_date(value, label):
    text = str(value or "").strip()
    if not text:
        return ""
    parts = text.split("-")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError("{0} must use YYYY-MM-DD format".format(label))
    year, month, day = [int(part) for part in parts]
    if year < 1900 or not 1 <= month <= 12 or not 1 <= day <= 31:
        raise ValueError("{0} must use YYYY-MM-DD format".format(label))
    return "{0:04d}-{1:02d}-{2:02d}".format(year, month, day)


def build_filters(etd_from=None, etd_to=None, customer=None, job_type=None, billing_office=None):
    clauses = []
    params = []
    start_date = normalize_date(etd_from, "ETD from")
    end_date = normalize_date(etd_to, "ETD to")
    customer_text = str(customer or "").strip()
    selected_job_type = normalize_job_type(job_type)
    billing_office_text = str(billing_office or "").strip()

    if start_date:
        clauses.append('DATE_FORMAT(v_jobinfo.ETD,"%%Y-%%m-%%d") >= %s')
        params.append(start_date)
    if end_date:
        clauses.append('DATE_FORMAT(v_jobinfo.ETD,"%%Y-%%m-%%d") <= %s')
        params.append(end_date)
    if customer_text:
        clauses.append("v_jobinfo.customer like %s")
        params.append("{0}%".format(customer_text))
    if selected_job_type != "ALL":
        clauses.append("v_jobinfo.job_type = %s")
        params.append(selected_job_type)
    if billing_office_text:
        clauses.append("cf_cost.company_code like %s")
        params.append("{0}%".format(billing_office_text))
    return clauses, params


def build_query(report_type, etd_from=None, etd_to=None, customer=None, job_type=None, billing_office=None):
    selected_type = normalize_report_type(report_type)
    clauses, params = build_filters(etd_from, etd_to, customer, job_type, billing_office)
    selected_queries = ["AR", "AP"] if selected_type == "AR_AP" else [selected_type]
    query_parts = []

    for query_type in selected_queries:
        query = BASE_QUERIES[query_type].strip()
        if clauses:
            query = "{0}\nwhere {1}".format(query, " and ".join(clauses))
        query_parts.append(query)

    sql = "\nunion all\n".join(query_parts)
    sql = "{0}\norder by AR_AP, job_type, job_no, invoice_no".format(sql)
    return sql, params * len(selected_queries), selected_type


def _connect(db_profile):
    config = get_db_config(db_profile)
    config["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**config)


def _json_safe_row(row):
    return {
        str(key): "" if value is None else str(value)
        for key, value in row.items()
    }


def _sql_literal(value):
    if value is None:
        return "null"
    return "'{0}'".format(str(value).replace("\\", "\\\\").replace("'", "''"))


def preview_query(sql, params):
    preview = sql.replace("%%", "%")
    for param in params:
        preview = preview.replace("%s", _sql_literal(param), 1)
    return preview


def search_payload(
    db_profile,
    report_type,
    etd_from=None,
    etd_to=None,
    customer=None,
    job_type=None,
    billing_office=None,
):
    profile = normalize_db_profile(db_profile)
    selected_job_type = normalize_job_type(job_type)
    sql, params, selected_type = build_query(
        report_type,
        etd_from,
        etd_to,
        customer,
        selected_job_type,
        billing_office,
    )
    connection = _connect(profile)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
    finally:
        connection.close()

    public_rows = [_json_safe_row(row) for row in rows]
    columns = list(public_rows[0].keys()) if public_rows else EMPTY_COLUMNS
    return {
        "ok": True,
        "db_profile": profile,
        "report_type": selected_type,
        "job_type": selected_job_type,
        "columns": columns,
        "rows": public_rows,
        "row_count": len(public_rows),
        "query_sql": preview_query(sql, params),
    }


def preview_payload(
    db_profile,
    report_type,
    etd_from=None,
    etd_to=None,
    customer=None,
    job_type=None,
    billing_office=None,
):
    profile = normalize_db_profile(db_profile)
    selected_job_type = normalize_job_type(job_type)
    sql, params, selected_type = build_query(
        report_type,
        etd_from,
        etd_to,
        customer,
        selected_job_type,
        billing_office,
    )
    return {
        "ok": True,
        "db_profile": profile,
        "report_type": selected_type,
        "job_type": selected_job_type,
        "query_sql": preview_query(sql, params),
    }
