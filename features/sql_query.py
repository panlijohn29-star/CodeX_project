import datetime as d
import decimal
import hashlib
import json
import os
import re

import pandas as pd
import pymysql

from platform_config import BASE_DIR, get_db_config


SCRIPT_ROOT = os.path.join(BASE_DIR, "reports_v4", "sql_scripts")
MAX_SCRIPT_BYTES = 1024 * 1024
PREVIEW_ROW_LIMIT = 1000

FEATURE = {
    "id": "sql_query",
    "title": "SQL Query",
    "category": "Tools",
    "description": "Run SQL against an approved database, download results, and manage private SQL scripts.",
    "supports_cancel": True,
    "output_type": "files",
    "template": "sql_query.html",
}


def discover_db_profiles():
    profiles = []
    for key, database in os.environ.items():
        match = re.fullmatch(r"([A-Z0-9_]+)_(DATABASE|DB)", key)
        if not match or not str(database).strip():
            continue
        profile = match.group(1).lower()
        try:
            get_db_config(profile)
        except RuntimeError:
            continue
        profiles.append({"id": profile, "database": str(database).strip()})
    return sorted({item["id"]: item for item in profiles}.values(), key=lambda item: item["id"])


def _serialize_value(value):
    if isinstance(value, (d.datetime, d.date, d.time)):
        return value.isoformat(sep=" ") if isinstance(value, d.datetime) else value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, decimal.Decimal):
        return str(value)
    return value


def _connect(db_profile):
    config = get_db_config(db_profile)
    config["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**config)


def validate_inputs(inputs):
    profile = str(inputs.get("db_profile", "")).strip().lower()
    valid_profiles = {item["id"] for item in discover_db_profiles()}
    if profile not in valid_profiles:
        raise ValueError("Please select a configured database")
    sql = str(inputs.get("sql", "")).strip()
    if not sql:
        raise ValueError("SQL cannot be empty")
    if len(sql) > 500000:
        raise ValueError("SQL is too large")
    owner_user_id = str(inputs.get("owner_user_id", "")).strip()
    if not owner_user_id:
        raise ValueError("Missing query owner")
    return {"db_profile": profile, "sql": sql, "owner_user_id": owner_user_id}


def inputs_summary(inputs):
    return "{0}: {1}".format(inputs["db_profile"], inputs["sql"].splitlines()[0][:100])


def total_tasks(_inputs):
    return 1


def execute(context, inputs):
    profile = inputs["db_profile"]
    context.update(status="running", message="Running SQL query")
    connection = _connect(profile)
    context.register_connection("sql_query", profile, connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(inputs["sql"])
            if cursor.description:
                rows = [{key: _serialize_value(value) for key, value in row.items()} for row in cursor.fetchall()]
                columns = [item[0] for item in cursor.description]
                result_type = "query"
                affected_rows = None
            else:
                connection.commit()
                rows = []
                columns = []
                result_type = "write"
                affected_rows = cursor.rowcount
    finally:
        context.unregister_connection("sql_query")
        connection.close()

    if context.is_cancelled():
        raise RuntimeError("Run cancelled by user")

    result = {
        "result_type": result_type,
        "columns": columns,
        "rows": rows[:PREVIEW_ROW_LIMIT],
        "row_count": len(rows) if result_type == "query" else None,
        "affected_rows": affected_rows,
        "truncated": len(rows) > PREVIEW_ROW_LIMIT,
    }
    outputs = []
    if result_type == "query":
        filename = "sql_query_{0}.xlsx".format(context.run_id)
        path = os.path.join(context.output_dir, filename)
        pd.DataFrame(rows, columns=columns).to_excel(path, index=False)
        outputs.append({"name": filename, "path": path, "type": "xlsx"})
    context.update(status="running", message="Preparing results", result=result)
    return {"outputs": outputs, "files": [item["name"] for item in outputs], "result": result}


def _kill_db_thread(db_profile, thread_id):
    connection = _connect(db_profile)
    try:
        with connection.cursor() as cursor:
            cursor.execute("KILL QUERY {0}".format(int(thread_id)))
    except pymysql.MySQLError as exc:
        raise RuntimeError("Unable to terminate query. The database account needs KILL QUERY permission: {0}".format(exc))
    finally:
        connection.close()


def cancel(run_info):
    active = run_info.get("plugin_state", {}).get("active_connections", {}).get("sql_query")
    if not active:
        return
    _kill_db_thread(active["db_name"], active["thread_id"])
    try:
        active["connection"].close()
    except Exception:
        pass


def _user_dir(user_id):
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    path = os.path.join(SCRIPT_ROOT, digest)
    os.makedirs(path, exist_ok=True)
    return path


def normalize_script_name(name):
    name = os.path.basename(str(name or "").strip())
    if not name.lower().endswith(".sql"):
        name += ".sql"
    stem = name[:-4]
    if not re.fullmatch(r"[A-Za-z0-9 _.-]{1,100}", stem) or stem in {".", ".."}:
        raise ValueError("Script name may contain only letters, numbers, spaces, dot, underscore, and hyphen")
    return name


def normalize_folder_path(path):
    path = str(path or "").replace("\\", "/").strip("/")
    if not path:
        return ""
    parts = path.split("/")
    if len(parts) > 8 or any(not re.fullmatch(r"[A-Za-z0-9 _.-]{1,100}", part) or part in {".", ".."} for part in parts):
        raise ValueError("Folder path may contain only letters, numbers, spaces, dot, underscore, hyphen, and slash")
    return "/".join(parts)


def normalize_script_path(path):
    path = str(path or "").replace("\\", "/").strip("/")
    if not path:
        raise ValueError("Script name is required")
    parts = path.split("/")
    if len(parts) > 9:
        raise ValueError("Script path is too deep")
    folder = normalize_folder_path("/".join(parts[:-1]))
    name = normalize_script_name(parts[-1])
    return "/".join(item for item in (folder, name) if item)


def _script_path(user_id, script_path):
    folder = _user_dir(user_id)
    return os.path.join(folder, *normalize_script_path(script_path).split("/"))


def list_scripts(user_id):
    folder = _user_dir(user_id)
    scripts, folders = [], []
    for root, directory_names, file_names in os.walk(folder):
        directory_names.sort(key=str.lower)
        relative_root = os.path.relpath(root, folder)
        relative_root = "" if relative_root == "." else relative_root.replace("\\", "/")
        for directory_name in directory_names:
            folders.append("/".join(item for item in (relative_root, directory_name) if item))
        for file_name in sorted(file_names, key=str.lower):
            if not file_name.lower().endswith(".sql"):
                continue
            path = "/".join(item for item in (relative_root, file_name) if item)
            full_path = os.path.join(root, file_name)
            scripts.append({
                "name": file_name,
                "path": path,
                "folder": relative_root,
                "updated_at": d.datetime.fromtimestamp(os.path.getmtime(full_path)).isoformat(timespec="seconds"),
            })
    return {"scripts": scripts, "folders": folders}


def load_script(user_id, name):
    name = normalize_script_path(name)
    path = _script_path(user_id, name)
    if not os.path.isfile(path):
        raise ValueError("Script not found")
    with open(path, "r", encoding="utf-8-sig") as handle:
        return {"name": name, "sql": handle.read()}


def save_script(user_id, name, sql, overwrite=False):
    name = normalize_script_path(name)
    sql = str(sql or "")
    if not sql.strip():
        raise ValueError("SQL cannot be empty")
    if len(sql.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise ValueError("Script exceeds the 1 MB limit")
    path = _script_path(user_id, name)
    if os.path.exists(path) and not overwrite:
        return {"requires_overwrite": True, "name": name}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(sql)
    return {"ok": True, "name": name, "requires_overwrite": False}


def delete_script(user_id, name):
    name = normalize_script_path(name)
    path = _script_path(user_id, name)
    if not os.path.isfile(path):
        raise ValueError("Script not found")
    os.remove(path)
    return {"ok": True}


def create_folder(user_id, folder_path):
    folder_path = normalize_folder_path(folder_path)
    if not folder_path:
        raise ValueError("Folder name is required")
    os.makedirs(os.path.join(_user_dir(user_id), *folder_path.split("/")), exist_ok=True)
    return {"ok": True, "path": folder_path}


def delete_folder(user_id, folder_path):
    folder_path = normalize_folder_path(folder_path)
    if not folder_path:
        raise ValueError("The root folder cannot be deleted")
    path = os.path.join(_user_dir(user_id), *folder_path.split("/"))
    if not os.path.isdir(path):
        raise ValueError("Folder not found")
    if os.listdir(path):
        raise ValueError("Only empty folders can be deleted")
    os.rmdir(path)
    return {"ok": True}


FEATURE.update({
    "validate_inputs": validate_inputs,
    "inputs_summary": inputs_summary,
    "total_tasks": total_tasks,
    "execute": execute,
    "cancel": cancel,
})
