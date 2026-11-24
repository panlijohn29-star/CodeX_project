"""HTS Code Update: replace s_sys_dict_outside_code_hts202501 (scdbus) with an Excel list of HTS codes."""

import io
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

import pymysql

from platform_config import BASE_DIR, get_db_config


DB_PROFILE = "scdbus"
TABLE = "s_sys_dict_outside_code_hts202501"
SYS_CODE_ID = 0
SYS_CODE_TYPE_ID = 2016110115490001
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
STAGE_TTL_SECONDS = 2 * 60 * 60
STAGE_DIR = Path(BASE_DIR) / "instance" / "hts_code_update"
FIELDS = ("FREE_FIELD1", "FREE_FIELD2", "FREE_FIELD3", "FREE_FIELD4")
HEADER_ALIASES = {"HS": "FREE_FIELD1", "LALPHA": "FREE_FIELD2", "AUQ1": "FREE_FIELD3", "AUQ2": "FREE_FIELD4"}
MAX_TEXT_UPLOAD_BYTES = 20 * 1024 * 1024
# Census exp-code.txt / imp-code.txt are fixed width (latin-1): code, long description, unit 1, unit 2.
TEXT_SLICES = ((0, 10), (64, 224), (224, 232), (232, 240))
FIELD_LIMIT = 200
CODE_LENGTH = 10
SAMPLE_LIMIT = 50
PROBLEM_LIMIT = 200

FEATURE = {
    "id": "hts_code_update",
    "title": "HTS Code Update",
    "category": "Tools",
    "description": "Upload an Excel list of HTS codes and replace the whole US HTS code table with it after confirmation.",
    "supports_cancel": False,
    "output_type": "interactive",
    "input_schema": [],
    "template": "hts_code_update.html",
}


def _clean(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def normalize_code(value):
    """Return (code, note). code is None when invalid."""
    text = _clean(value)
    if not text:
        return None, "FREE_FIELD1 is empty"
    digits = re.sub(r"[.\s-]", "", text)
    if not digits.isdigit():
        return None, "FREE_FIELD1 must contain digits only: {0}".format(text)
    note = None
    if len(digits) < CODE_LENGTH:
        digits = digits.zfill(CODE_LENGTH)
        note = "padded with leading zeros"
    if len(digits) != CODE_LENGTH:
        return None, "FREE_FIELD1 must be {0} digits: {1}".format(CODE_LENGTH, text)
    return digits, note


def parse_workbook(content):
    """Read the first sheet; header row must contain FREE_FIELD1..4 (any order, IMP_EXP ignored)."""
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise ValueError("Cannot read the file. Please upload a valid .xlsx workbook")
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        header = next(rows, None)
        if not header:
            raise ValueError("The first sheet is empty")
        index = {}
        for position, name in enumerate(header):
            key = _clean(name).upper().replace(" ", "_")
            key = HEADER_ALIASES.get(key, key)
            if key in FIELDS and key not in index:
                index[key] = position
        missing = [field for field in FIELDS if field not in index]
        if missing:
            raise ValueError("Missing column(s) in the header row: " + ", ".join(missing))

        parsed = []
        for excel_row, row in enumerate(rows, start=2):
            values = [row[index[field]] if index[field] < len(row) else None for field in FIELDS]
            if not any(_clean(value) for value in values):
                continue
            parsed.append((excel_row, values))
        return parsed
    finally:
        workbook.close()


def parse_census_text(content, label):
    """Parse one Census fixed-width file into [(row label, [code, description, unit1, unit2])]."""
    parsed = []
    skipped = 0
    for number, line in enumerate(content.decode("latin-1").splitlines(), start=1):
        if not line.strip():
            continue
        code = line[TEXT_SLICES[0][0]:TEXT_SLICES[0][1]].strip()
        if not code:
            skipped += 1
            continue
        values = [code] + [line[start:end].strip() or None for start, end in TEXT_SLICES[1:]]
        parsed.append(("{0} line {1}".format(label, number), values))
    return parsed, skipped


def combine_census(exp_rows, imp_rows):
    """EXP rows plus IMP rows whose code is not in EXP, sorted by code (same as the old build_hts.py)."""
    exp_codes = {values[0] for _, values in exp_rows}
    imp_only = [row for row in imp_rows if row[1][0] not in exp_codes]
    combined = sorted(list(exp_rows) + imp_only, key=lambda row: row[1][0])
    overlap = len({values[0] for _, values in imp_rows} & exp_codes)
    return combined, len(imp_only), overlap


def validate_rows(parsed):
    """Return (valid rows, problems, padded_count). Duplicate codes in the file are rejected."""
    valid = []
    problems = []
    seen = {}
    padded = 0
    for excel_row, values in parsed:
        code, note = normalize_code(values[0])
        if code is None:
            problems.append({"row": excel_row, "code": _clean(values[0]), "message": note})
            continue
        texts = [code] + [_clean(value) for value in values[1:]]
        too_long = [FIELDS[i] for i, text in enumerate(texts) if len(text) > FIELD_LIMIT]
        if too_long:
            problems.append({"row": excel_row, "code": code,
                             "message": "Longer than {0} characters: {1}".format(FIELD_LIMIT, ", ".join(too_long))})
            continue
        if code in seen:
            problems.append({"row": excel_row, "code": code,
                             "message": "Duplicate of row {0} in the file".format(seen[code])})
            continue
        seen[code] = excel_row
        if note:
            padded += 1
        valid.append({
            "row": excel_row,
            "FREE_FIELD1": code,
            "FREE_FIELD2": texts[1] or None,
            "FREE_FIELD3": texts[2] or None,
            "FREE_FIELD4": texts[3] or None,
        })
    return valid, problems, padded


def _connect():
    config = get_db_config(DB_PROFILE)
    config["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**config)


def _purge_stale():
    if not STAGE_DIR.is_dir():
        return
    cutoff = time.time() - STAGE_TTL_SECONDS
    for path in STAGE_DIR.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def _stage(rows):
    _purge_stale()
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    (STAGE_DIR / (token + ".json")).write_text(json.dumps(rows), encoding="utf-8")
    return token


def _load_stage(token):
    if not re.fullmatch(r"[0-9a-f]{32}", str(token or "")):
        raise ValueError("Please upload the file again")
    path = STAGE_DIR / (token + ".json")
    if not path.is_file() or time.time() - path.stat().st_mtime > STAGE_TTL_SECONDS:
        raise ValueError("The uploaded data has expired. Please upload the file again")
    return json.loads(path.read_text(encoding="utf-8"))


def _read_upload(upload, extension=".xlsx", limit=MAX_UPLOAD_BYTES, name="Excel (.xlsx)"):
    if not upload or not upload.filename:
        raise ValueError("Please choose a {0} file".format(name))
    if not upload.filename.lower().endswith(extension) or upload.filename.startswith("~$"):
        raise ValueError("Only {0} files are supported here".format(extension))
    content = upload.read(limit + 1)
    if len(content) > limit:
        raise ValueError("The file is larger than {0} MB".format(limit // (1024 * 1024)))
    return content


def today_text():
    return datetime.now().strftime("%Y-%m-%d")


def fetch_current_codes(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT FREE_FIELD1 FROM {0}".format(TABLE))
        return [_clean(record["FREE_FIELD1"]) for record in cursor.fetchall()]


def preview_payload(upload=None, exp_upload=None, imp_upload=None):
    source = {"source": "excel"}
    if exp_upload or imp_upload:
        if not (exp_upload and imp_upload):
            raise ValueError("Please choose both exp-code.txt and imp-code.txt")
        exp_rows, exp_skipped = parse_census_text(
            _read_upload(exp_upload, ".txt", MAX_TEXT_UPLOAD_BYTES, "exp-code.txt"), "EXP")
        imp_rows, imp_skipped = parse_census_text(
            _read_upload(imp_upload, ".txt", MAX_TEXT_UPLOAD_BYTES, "imp-code.txt"), "IMP")
        if not exp_rows or not imp_rows:
            raise ValueError("exp-code.txt and imp-code.txt must both contain data")
        parsed, imp_only, overlap = combine_census(exp_rows, imp_rows)
        source = {"source": "census_text", "exp_rows": len(exp_rows), "imp_rows": len(imp_rows),
                  "imp_only": imp_only, "overlap": overlap, "skipped_lines": exp_skipped + imp_skipped}
    else:
        parsed = parse_workbook(_read_upload(upload))
        if not parsed:
            raise ValueError("No data rows were found in the first sheet")
    valid, problems, padded = validate_rows(parsed)
    connection = _connect()
    try:
        current = fetch_current_codes(connection)
    finally:
        connection.close()
    current_set = set(current)
    new_codes = {row["FREE_FIELD1"] for row in valid}
    payload = {
        "ok": True,
        "db_profile": DB_PROFILE,
        "table": TABLE,
        "total_rows": len(parsed),
        "to_load": len(valid),
        "current_rows": len(current),
        "new_codes": len(new_codes - current_set),
        "dropped_codes": len(current_set - new_codes),
        "invalid": len(problems),
        "padded": padded,
        "remark": today_text(),
        "samples": valid[:SAMPLE_LIMIT],
        "problems": problems[:PROBLEM_LIMIT],
        "token": None,
    }
    payload.update(source)
    if valid and not problems:
        payload["token"] = _stage(valid)
    return payload


def execute_payload(token, user_id):
    rows = _load_stage(token)
    if not rows:
        raise ValueError("Nothing to load")
    actor = (str(user_id or "").strip() or "platform")[:20]
    now = datetime.now()
    now_text = now.strftime("%Y-%m-%d %H:%M:%S")
    remark = now.strftime("%Y-%m-%d")
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM {0}".format(TABLE))
            old_rows = int(cursor.fetchone()["n"])
            # TRUNCATE commits implicitly (the table is a temp table), so it cannot be rolled back.
            cursor.execute("TRUNCATE TABLE {0}".format(TABLE))
            sql = (
                "INSERT INTO {0} (SYS_CODE_ID, SYS_CODE_TYPE_ID, FREE_FIELD1, FREE_FIELD2, FREE_FIELD3, "
                "FREE_FIELD4, REMARK, CREATE_BY, CREATE_DATE) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            ).format(TABLE)
            values = [
                (SYS_CODE_ID, SYS_CODE_TYPE_ID, r["FREE_FIELD1"], r["FREE_FIELD2"], r["FREE_FIELD3"],
                 r["FREE_FIELD4"], remark, actor, now_text)
                for r in rows
            ]
            for start in range(0, len(values), 2000):
                cursor.executemany(sql, values[start:start + 2000])
            cursor.execute("SELECT COUNT(*) AS n FROM {0}".format(TABLE))
            if int(cursor.fetchone()["n"]) != len(rows):
                raise RuntimeError("Row count check failed after loading")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    try:
        (STAGE_DIR / (token + ".json")).unlink()
    except OSError:
        pass
    return {
        "ok": True,
        "db_profile": DB_PROFILE,
        "loaded": len(rows),
        "deleted": old_rows,
        "remark": remark,
        "message": "HTS codes loaded: {0} rows (replaced {1}), REMARK = {2}".format(len(rows), old_rows, remark),
    }


REAL_TABLE = "s_sys_dict_outside_code"
B_FIELDS = ("B_OCEAN_EXPORT", "B_OCEAN_IMPORT", "B_AIR_EXPORT", "B_AIR_IMPORT")
ACTIVE_CLAUSE = "(" + " OR ".join("{0} IS NOT NULL".format(name) for name in B_FIELDS) + ")"
ID_SEQUENCE_SPAN = 10 ** 9
INSERT_CHUNK = 2000
REMOVED_LIMIT = 200


def diff_batches(old_rows, new_rows):
    """Compare the active batch with the new one by FREE_FIELD1."""
    old = {_clean(r["FREE_FIELD1"]): r for r in old_rows}
    new = {_clean(r["FREE_FIELD1"]): r for r in new_rows}
    added = [code for code in new if code not in old]
    removed = [code for code in old if code not in new]
    changed = []
    for code, row in new.items():
        before = old.get(code)
        if before is None:
            continue
        changes = {}
        for field in FIELDS[1:]:
            was = _clean(before.get(field)) or None
            now = _clean(row.get(field)) or None
            if was != now:
                changes[field] = {"old": was, "new": now}
        if changes:
            changed.append({"code": code, "changes": changes})
    return added, removed, changed


def _id_range(day):
    prefix = int(day.strftime("%y%m%d"))
    low = prefix * ID_SEQUENCE_SPAN + 1
    return low, low + ID_SEQUENCE_SPAN - 2


def _publish_plan(connection, today=None):
    """Read the temp table and the real table and work out what publishing would do."""
    today = today or datetime.now()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT FREE_FIELD1, FREE_FIELD2, FREE_FIELD3, FREE_FIELD4, REMARK FROM {0} "
            "ORDER BY FREE_FIELD1".format(TABLE))
        new_rows = cursor.fetchall()
        if not new_rows:
            raise ValueError("The HTS temp table is empty. Load an Excel file first")
        remarks = {_clean(r["REMARK"]) for r in new_rows}
        if len(remarks) != 1 or "" in remarks:
            raise ValueError("The HTS temp table must have exactly one REMARK value, found: {0}".format(
                ", ".join(sorted(remarks)) or "none"))
        remark = remarks.pop()

        cursor.execute(
            "SELECT COUNT(*) AS n FROM {0} WHERE SYS_CODE_TYPE_ID = %s AND REMARK = %s".format(REAL_TABLE),
            (SYS_CODE_TYPE_ID, remark))
        if int(cursor.fetchone()["n"]):
            raise ValueError("Batch {0} already exists in {1}. Nothing was changed".format(remark, REAL_TABLE))

        cursor.execute(
            "SELECT REMARK, COUNT(*) AS n FROM {0} WHERE SYS_CODE_TYPE_ID = %s AND {1} GROUP BY REMARK".format(
                REAL_TABLE, ACTIVE_CLAUSE), (SYS_CODE_TYPE_ID,))
        active = cursor.fetchall()
        if not active:
            raise ValueError("No active batch was found in {0}. Use the batch list to activate one first".format(
                REAL_TABLE))
        if len(active) > 1:
            raise ValueError("More than one active batch found ({0}). Fix this first".format(
                ", ".join("{0}: {1} rows".format(_clean(a["REMARK"]) or "(empty)", a["n"]) for a in active)))
        old_remark = active[0]["REMARK"]
        old_count = int(active[0]["n"])

        cursor.execute(
            "SELECT FREE_FIELD1, FREE_FIELD2, FREE_FIELD3, FREE_FIELD4 FROM {0} WHERE SYS_CODE_TYPE_ID = %s "
            "AND REMARK <=> %s AND {1}".format(REAL_TABLE, ACTIVE_CLAUSE), (SYS_CODE_TYPE_ID, old_remark))
        old_rows = cursor.fetchall()

        low, high = _id_range(today)
        cursor.execute(
            "SELECT COUNT(*) AS n FROM {0} WHERE SYS_CODE_ID BETWEEN %s AND %s".format(REAL_TABLE), (low, high))
        if int(cursor.fetchone()["n"]):
            raise ValueError("SYS_CODE_ID range {0}-{1} is already used in {2}".format(low, high, REAL_TABLE))
        if len(new_rows) >= ID_SEQUENCE_SPAN - 1:
            raise ValueError("Too many rows")

    added, removed, changed = diff_batches(old_rows, new_rows)
    return {
        "remark": remark, "new_rows": new_rows, "old_remark": old_remark, "old_count": old_count,
        "added": added, "removed": removed, "changed": changed, "id_low": low,
    }


def publish_preview_payload():
    connection = _connect()
    try:
        plan = _publish_plan(connection)
    finally:
        connection.close()
    return {
        "ok": True,
        "db_profile": DB_PROFILE,
        "real_table": REAL_TABLE,
        "temp_table": TABLE,
        "remark": plan["remark"],
        "load_rows": len(plan["new_rows"]),
        "expire_remark": plan["old_remark"],
        "expire_rows": plan["old_count"],
        "first_id": plan["id_low"],
        "added": len(plan["added"]),
        "removed": len(plan["removed"]),
        "changed": len(plan["changed"]),
        "removed_codes": plan["removed"][:REMOVED_LIMIT],
        "changed_samples": plan["changed"][:SAMPLE_LIMIT],
    }


def publish_execute_payload(remark, expire_remark, expire_rows, load_rows, user_id):
    actor = (str(user_id or "").strip() or "platform")[:20]
    now = datetime.now()
    now_text = now.strftime("%Y-%m-%d %H:%M:%S")
    connection = _connect()
    try:
        plan = _publish_plan(connection, now)
        if (plan["remark"] != _clean(remark) or plan["old_remark"] != expire_remark
                or plan["old_count"] != expire_rows or len(plan["new_rows"]) != load_rows):
            raise ValueError("The data changed since the preview. Please check again")
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE {0} SET B_OCEAN_EXPORT = NULL, B_OCEAN_IMPORT = NULL, B_AIR_EXPORT = NULL, "
                "B_AIR_IMPORT = NULL WHERE SYS_CODE_TYPE_ID = %s AND REMARK <=> %s AND {1}".format(
                    REAL_TABLE, ACTIVE_CLAUSE), (SYS_CODE_TYPE_ID, plan["old_remark"]))
            if cursor.rowcount != plan["old_count"]:
                raise RuntimeError("Expired {0} rows, expected {1}".format(cursor.rowcount, plan["old_count"]))
            sql = (
                "INSERT INTO {0} (SYS_CODE_ID, SYS_CODE_TYPE_ID, B_OCEAN_EXPORT, B_OCEAN_IMPORT, B_AIR_EXPORT, "
                "B_AIR_IMPORT, REMARK, CREATE_BY, CREATE_DATE, UPDATE_BY, UPDATE_DATE, FREE_FIELD1, FREE_FIELD2, "
                "FREE_FIELD3, FREE_FIELD4) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            ).format(REAL_TABLE)
            values = [
                (plan["id_low"] + index, SYS_CODE_TYPE_ID, 1, 1, 1, 1, plan["remark"], actor, now_text, actor, now_text,
                 row["FREE_FIELD1"], row["FREE_FIELD2"], row["FREE_FIELD3"], row["FREE_FIELD4"])
                for index, row in enumerate(plan["new_rows"])
            ]
            for start in range(0, len(values), INSERT_CHUNK):
                cursor.executemany(sql, values[start:start + INSERT_CHUNK])
            cursor.execute(
                "SELECT COUNT(*) AS n FROM {0} WHERE SYS_CODE_TYPE_ID = %s AND REMARK = %s AND {1}".format(
                    REAL_TABLE, ACTIVE_CLAUSE), (SYS_CODE_TYPE_ID, plan["remark"]))
            inserted = int(cursor.fetchone()["n"])
            if inserted != len(plan["new_rows"]):
                raise RuntimeError("Inserted {0} rows, expected {1}".format(inserted, len(plan["new_rows"])))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "ok": True,
        "db_profile": DB_PROFILE,
        "expired": plan["old_count"],
        "expired_remark": plan["old_remark"],
        "inserted": len(plan["new_rows"]),
        "remark": plan["remark"],
        "message": "Batch {0} published: {1} rows added, batch {2} expired ({3} rows)".format(
            plan["remark"], len(plan["new_rows"]), plan["old_remark"], plan["old_count"]),
    }


def list_batches_payload():
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT REMARK, COUNT(*) AS n, SUM({1}) AS active_rows FROM {0} WHERE SYS_CODE_TYPE_ID = %s "
                "GROUP BY REMARK ORDER BY REMARK DESC".format(REAL_TABLE, "CASE WHEN {0} THEN 1 ELSE 0 END".format(
                    ACTIVE_CLAUSE)), (SYS_CODE_TYPE_ID,))
            records = cursor.fetchall()
    finally:
        connection.close()
    return {"ok": True, "batches": [
        {"remark": r["REMARK"], "rows": int(r["n"]), "active_rows": int(r["active_rows"] or 0),
         "active": bool(r["active_rows"])}
        for r in records
    ]}


def activate_batch_payload(remark):
    """Make one batch the only active one (expires every other batch)."""
    target = _clean(remark)
    if not target:
        raise ValueError("Batch is required")
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS n FROM {0} WHERE SYS_CODE_TYPE_ID = %s AND REMARK = %s".format(REAL_TABLE),
                (SYS_CODE_TYPE_ID, target))
            total = int(cursor.fetchone()["n"])
            if not total:
                raise ValueError("Batch {0} was not found".format(target))
            cursor.execute(
                "UPDATE {0} SET B_OCEAN_EXPORT = NULL, B_OCEAN_IMPORT = NULL, B_AIR_EXPORT = NULL, "
                "B_AIR_IMPORT = NULL WHERE SYS_CODE_TYPE_ID = %s AND NOT (REMARK <=> %s) AND {1}".format(
                    REAL_TABLE, ACTIVE_CLAUSE), (SYS_CODE_TYPE_ID, target))
            expired = cursor.rowcount
            cursor.execute(
                "UPDATE {0} SET B_OCEAN_EXPORT = 1, B_OCEAN_IMPORT = 1, B_AIR_EXPORT = 1, B_AIR_IMPORT = 1 "
                "WHERE SYS_CODE_TYPE_ID = %s AND REMARK = %s".format(REAL_TABLE), (SYS_CODE_TYPE_ID, target))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"ok": True, "remark": target, "activated": total, "expired": expired,
            "message": "Batch {0} is now active ({1} rows); {2} other rows expired".format(target, total, expired)}
