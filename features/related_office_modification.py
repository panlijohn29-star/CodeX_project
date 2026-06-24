import pymysql

from platform_config import get_db_config


ALLOWED_DB_PROFILES = ("scdbus", "scdbca")

FEATURE = {
    "id": "related_office_modification",
    "title": "Related Office Modification",
    "category": "Tools",
    "description": "Create related office data for a two-job HAWB after user confirmation.",
    "supports_cancel": False,
    "output_type": "interactive",
    "input_schema": [],
    "template": "related_office_modification.html",
}


def normalize_db_profile(db_profile):
    profile = str(db_profile or "").strip().lower()
    if profile not in ALLOWED_DB_PROFILES:
        raise ValueError("Please select scdbus or scdbca")
    return profile


def normalize_hawb_no(hawb_no):
    hawb = str(hawb_no or "").strip()
    if not hawb:
        raise ValueError("HAWB# is required")
    return hawb


def normalize_job_no(job_no):
    job = str(job_no or "").strip()
    if not job:
        raise ValueError("Job# is required")
    return job


def _connect(db_profile):
    config = get_db_config(db_profile)
    config["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**config)


def _public_row(row):
    return {
        "HAWB_NO": row.get("HAWB_NO"),
        "job_no": row.get("job_no"),
        "JOB_ID": str(row.get("JOB_ID") or ""),
    }


def fetch_related_jobs(db_profile, hawb_no):
    profile = normalize_db_profile(db_profile)
    hawb = normalize_hawb_no(hawb_no)
    sql = """
        SELECT HAWB_NO, job_no, OP_AI_JOB.JOB_ID
        FROM OP_AI_JOB
        LEFT JOIN op_job ON op_ai_job.job_id = op_job.job_id
        WHERE op_ai_job.HAWB_NO IN (%s)
    """
    connection = _connect(profile)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, (hawb,))
            rows = cursor.fetchall()
    finally:
        connection.close()
    return validate_related_jobs(rows)


def validate_related_jobs(rows):
    if len(rows) != 2:
        raise ValueError("Expected exactly 2 records for this HAWB#, found {0}".format(len(rows)))

    job_numbers = [str(row.get("job_no") or "").strip() for row in rows]
    if any(not job_no for job_no in job_numbers):
        raise ValueError("Both records must have a job_no")
    if len(set(job_numbers)) != 2:
        raise ValueError("The 2 records must have different job_no values")

    return list(rows)


def fetch_op_company(db_profile, job_no):
    profile = normalize_db_profile(db_profile)
    job = normalize_job_no(job_no)
    connection = _connect(profile)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT OP_COMPANY FROM op_job WHERE job_no = %s", (job,))
            row = cursor.fetchone()
    finally:
        connection.close()

    if not row or not str(row.get("OP_COMPANY") or "").strip():
        raise ValueError("OP_COMPANY was not found for job# {0}".format(job))
    return str(row["OP_COMPANY"]).strip()


def _split_selected_job(rows, selected_job_no):
    selected_job = normalize_job_no(selected_job_no)
    selected = None
    other = None
    for row in rows:
        if str(row.get("job_no") or "").strip() == selected_job:
            selected = row
        else:
            other = row
    if not selected or not other:
        raise ValueError("Selected job# must be one of the two HAWB records")
    return selected, other


def lookup_payload(db_profile, hawb_no):
    profile = normalize_db_profile(db_profile)
    hawb = normalize_hawb_no(hawb_no)
    rows = fetch_related_jobs(profile, hawb)
    return {
        "ok": True,
        "db_profile": profile,
        "hawb_no": hawb,
        "rows": [_public_row(row) for row in rows],
    }


def company_payload(db_profile, hawb_no, selected_job_no):
    profile = normalize_db_profile(db_profile)
    hawb = normalize_hawb_no(hawb_no)
    rows = fetch_related_jobs(profile, hawb)
    selected, other = _split_selected_job(rows, selected_job_no)
    company = fetch_op_company(profile, selected["job_no"])
    return {
        "ok": True,
        "db_profile": profile,
        "hawb_no": hawb,
        "selected_job": _public_row(selected),
        "other_job": _public_row(other),
        "op_company": company,
    }


def execute_payload(db_profile, hawb_no, selected_job_no, confirmed_company_code):
    profile = normalize_db_profile(db_profile)
    hawb = normalize_hawb_no(hawb_no)
    rows = fetch_related_jobs(profile, hawb)
    selected, other = _split_selected_job(rows, selected_job_no)
    company = fetch_op_company(profile, selected["job_no"])
    confirmed_company = str(confirmed_company_code or "").strip()
    if confirmed_company != company:
        raise ValueError("Confirmed company code no longer matches OP_COMPANY")

    connection = _connect(profile)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "call zdy_update_related_ai_job(%s, %s, %s, %s)",
                (selected["job_no"], other["job_no"], other["JOB_ID"], company),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "ok": True,
        "db_profile": profile,
        "hawb_no": hawb,
        "selected_job": _public_row(selected),
        "other_job": _public_row(other),
        "op_company": company,
        "message": "Related office modification completed successfully",
    }
