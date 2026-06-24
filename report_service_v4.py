import datetime as d
import json
import os
import threading
import time as t
import uuid
import zipfile as z
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import pymysql

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_ROOT = os.path.join(BASE_DIR, "reports_v4")
SQL_TEMPLATE_PATH = os.path.join(BASE_DIR, "closing_report_v4.sql")
MAX_WORKERS = 4
RUNS = {}
RUNS_LOCK = threading.Lock()

with open(SQL_TEMPLATE_PATH, "r", encoding="utf-8") as handle:
    SCRIPT_ORI = handle.read()

DB_CONFIG = {
    "scdbca": {
        "host": "aauw-db-prod-us-sc.mysql.database.azure.com",
        "port": 3306,
        "user": "john",
        "password": "john123456#A",
        "db": "scdbca",
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.SSDictCursor,
        "connect_timeout": 10,
        "read_timeout": 600,
        "write_timeout": 600,
    },
    "scdbus": {
        "host": "aauw-db-prod-us-sc.mysql.database.azure.com",
        "port": 3306,
        "user": "john",
        "password": "john123456#A",
        "db": "scdbus",
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.SSDictCursor,
        "connect_timeout": 10,
        "read_timeout": 600,
        "write_timeout": 600,
    },
}

GROUPS = [
    {
        "label": "NA reports",
        "db_name": "scdbus",
        "offices": [
            "APEX-ORD", "APEX-USA", "APEX-LAX", "APEX-JFK", "APEX-MIA",
            "APEX-SEA", "APEX-SFO", "APEX-DFW", "APEX-ECM", "APEX-CMP", "APEX-NA",
            "APEX-LCK", "PERI-LAX", "PERI-SFO",
        ],
    },
    {
        "label": "YYZ reports",
        "db_name": "scdbca",
        "offices": ["APEX-YYZ"],
    },
]


def ensure_report_root():
    os.makedirs(REPORT_ROOT, exist_ok=True)


def fetch_dataframe(connection, query):
    with connection.cursor() as cursor:
        cursor.execute(query)
        rows = cursor.fetchall()
    return pd.DataFrame(rows)


def get_office_groups():
    return [{"label": group["label"], "offices": list(group["offices"])} for group in GROUPS]


def _now_iso():
    return d.datetime.now().isoformat(timespec="seconds")


def _write_manifest(run_info):
    manifest_path = os.path.join(run_info["output_dir"], "manifest.json")
    manifest = {
        "run_id": run_info["id"],
        "status": run_info["status"],
        "created_at": run_info["created_at"],
        "updated_at": run_info["updated_at"],
        "completed_tasks": run_info["completed_tasks"],
        "total_tasks": run_info["total_tasks"],
        "message": run_info["message"],
        "zip_name": run_info.get("zip_name"),
        "zip_path": run_info.get("zip_path"),
        "files": run_info.get("files", []),
        "error": run_info.get("error"),
        "selected_offices": run_info.get("selected_offices", []),
        "cancel_requested": run_info.get("cancel_requested", False),
    }
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=True, indent=2)


def _build_selected_groups(selected_offices):
    office_set = set(selected_offices)
    selected_groups = []
    for group in GROUPS:
        offices = [office for office in group["offices"] if office in office_set]
        if offices:
            selected_groups.append({
                "label": group["label"],
                "db_name": group["db_name"],
                "offices": offices,
            })
    return selected_groups


def create_run(selected_offices):
    ensure_report_root()
    run_id = uuid.uuid4().hex[:12]
    run_dir = os.path.join(REPORT_ROOT, run_id)
    os.makedirs(run_dir, exist_ok=True)
    selected_groups = _build_selected_groups(selected_offices)
    total_tasks = sum(len(group["offices"]) for group in selected_groups)
    run_info = {
        "id": run_id,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "status": "queued",
        "message": "Waiting to start",
        "completed_tasks": 0,
        "total_tasks": total_tasks,
        "files": [],
        "zip_name": None,
        "zip_path": None,
        "error": None,
        "output_dir": run_dir,
        "selected_groups": selected_groups,
        "selected_offices": list(selected_offices),
        "cancel_requested": False,
        "cancel_event": threading.Event(),
        "active_connections": {},
    }
    with RUNS_LOCK:
        RUNS[run_id] = run_info
    _write_manifest(run_info)
    return run_info


def _snapshot(run_info):
    snapshot = dict(run_info)
    snapshot.pop("cancel_event", None)
    snapshot.pop("active_connections", None)
    snapshot.pop("selected_groups", None)
    return snapshot


def update_run(run_id, **kwargs):
    with RUNS_LOCK:
        run_info = RUNS[run_id]
        run_info.update(kwargs)
        run_info["updated_at"] = _now_iso()
        snapshot = _snapshot(run_info)
    _write_manifest(snapshot)
    return snapshot


def get_run(run_id):
    with RUNS_LOCK:
        run_info = RUNS.get(run_id)
        return _snapshot(run_info) if run_info else None


def list_runs():
    ensure_report_root()
    runs = []
    for run_id in os.listdir(REPORT_ROOT):
        manifest_path = os.path.join(REPORT_ROOT, run_id, "manifest.json")
        if not os.path.isfile(manifest_path):
            continue
        with open(manifest_path, "r", encoding="utf-8") as handle:
            runs.append(json.load(handle))
    runs.sort(key=lambda item: item["created_at"], reverse=True)
    return runs


def _register_connection(run_id, office, db_name, connection):
    with RUNS_LOCK:
        run_info = RUNS.get(run_id)
        if run_info:
            run_info["active_connections"][office] = {
                "db_name": db_name,
                "thread_id": connection.thread_id(),
                "connection": connection,
            }


def _unregister_connection(run_id, office):
    with RUNS_LOCK:
        run_info = RUNS.get(run_id)
        if run_info:
            run_info["active_connections"].pop(office, None)


def _kill_db_thread(db_name, thread_id):
    kill_connection = pymysql.connect(**DB_CONFIG[db_name])
    try:
        with kill_connection.cursor() as cursor:
            cursor.execute("KILL {0}".format(int(thread_id)))
    finally:
        kill_connection.close()


def _should_cancel(run_id):
    with RUNS_LOCK:
        run_info = RUNS.get(run_id)
        return bool(run_info and run_info["cancel_event"].is_set())


def cancel_run(run_id):
    with RUNS_LOCK:
        run_info = RUNS.get(run_id)
        if not run_info:
            return None
        run_info["cancel_requested"] = True
        run_info["cancel_event"].set()
        active_connections = list(run_info["active_connections"].values())
        finished = run_info["status"] in ("completed", "failed", "cancelled")
    for connection_info in active_connections:
        try:
            _kill_db_thread(connection_info["db_name"], connection_info["thread_id"])
        except Exception:
            pass
        try:
            connection_info["connection"].close()
        except Exception:
            pass
    if finished:
        return get_run(run_id)
    return update_run(run_id, status="cancelling", message="Cancellation requested")


def create_report(run_id, office, db_name, output_dir):
    if _should_cancel(run_id):
        raise RuntimeError("Run cancelled by user")
    office_sql = '"{0}"'.format(office)
    script_fnl = SCRIPT_ORI.format(office_sql)
    excel_name = "{0} closing report.xlsx".format(office)
    excel_path = os.path.join(output_dir, excel_name)

    cnxn = pymysql.connect(**DB_CONFIG[db_name])
    _register_connection(run_id, office, db_name, cnxn)
    try:
        if _should_cancel(run_id):
            raise RuntimeError("Run cancelled by user")
        df = fetch_dataframe(cnxn, script_fnl)
    finally:
        _unregister_connection(run_id, office)
        try:
            cnxn.close()
        except Exception:
            pass

    if _should_cancel(run_id):
        raise RuntimeError("Run cancelled by user")
    df.to_excel(excel_path, index=False)
    return excel_name


def run_report_group(run_id, label, db_name, office_list, output_dir, generated_files):
    worker_count = min(MAX_WORKERS, len(office_list))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(create_report, run_id, office, db_name, output_dir): office
            for office in office_list
        }
        try:
            for future in as_completed(future_map):
                office = future_map[future]
                file_name = future.result()
                generated_files.append(file_name)
                current = get_run(run_id)
                message = "Completed {0} for {1}".format(office, label)
                update_run(
                    run_id,
                    status="running",
                    message=message,
                    completed_tasks=current["completed_tasks"] + 1,
                    files=sorted(generated_files),
                )
                if _should_cancel(run_id):
                    raise RuntimeError("Run cancelled by user")
        except Exception:
            for future in future_map:
                future.cancel()
            raise


def zip_reports(output_dir, files):
    zip_name = "YYZ_NA_closing_report_{0}.zip".format(str(d.date.today()))
    zip_path = os.path.join(output_dir, zip_name)
    with z.ZipFile(zip_path, "w") as zip_file:
        for file_name in files:
            file_path = os.path.join(output_dir, file_name)
            zip_file.write(file_path, arcname=file_name, compress_type=z.ZIP_DEFLATED)
    return zip_name, zip_path


def execute_run(run_id):
    started_at = t.time()
    run_info = get_run(run_id)
    output_dir = run_info["output_dir"]
    generated_files = []

    update_run(run_id, status="running", message="Connecting to databases")

    try:
        if run_info["total_tasks"] == 0:
            raise RuntimeError("Please select at least one office")

        with RUNS_LOCK:
            selected_groups = list(RUNS[run_id]["selected_groups"])

        for group in selected_groups:
            if _should_cancel(run_id):
                raise RuntimeError("Run cancelled by user")
            update_run(run_id, status="running", message="Running {0}".format(group["label"]))
            run_report_group(run_id, group["label"], group["db_name"], group["offices"], output_dir, generated_files)

        if _should_cancel(run_id):
            raise RuntimeError("Run cancelled by user")
        zip_name, zip_path = zip_reports(output_dir, generated_files)
        duration = str(d.timedelta(seconds=(t.time() - started_at)))
        update_run(
            run_id,
            status="completed",
            message="Finished in {0}".format(duration),
            files=sorted(generated_files),
            zip_name=zip_name,
            zip_path=zip_path,
        )
    except Exception as exc:
        current = get_run(run_id)
        was_cancelled = current and current.get("cancel_requested")
        status = "cancelled" if was_cancelled or "cancelled by user" in str(exc).lower() else "failed"
        message = "Run cancelled" if status == "cancelled" else "Run failed"
        update_run(
            run_id,
            status=status,
            message=message,
            error=str(exc),
        )


def start_run(selected_offices):
    run_info = create_run(selected_offices)
    thread = threading.Thread(target=execute_run, args=(run_info["id"],), daemon=True)
    thread.start()
    return run_info["id"]
