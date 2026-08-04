import datetime as d
import os
import zipfile as z
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import pymysql

from platform_config import BASE_DIR, env_int, get_db_config


SQL_TEMPLATE_PATH = os.path.join(BASE_DIR, "closing_report_v4.sql")
MAX_WORKERS = env_int("CLOSING_REPORT_MAX_WORKERS", 4)

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


def get_office_groups():
    return [{"label": group["label"], "offices": list(group["offices"])} for group in GROUPS]


FEATURE = {
    "id": "closing_report",
    "title": "Closing Report",
    "category": "Reports",
    "description": "Generate office closing reports as Excel files and package them into one zip.",
    "supports_cancel": True,
    "output_type": "zip",
    "input_schema": [
        {
            "name": "offices",
            "type": "office_groups",
            "label": "Office Selection",
            "groups": get_office_groups(),
            "default": "all",
            "required": True,
        }
    ],
}


def _load_sql_template():
    with open(SQL_TEMPLATE_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


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


def validate_inputs(inputs):
    selected_offices = inputs.get("offices", [])
    if not isinstance(selected_offices, list):
        raise ValueError("offices must be a list")
    known_offices = {office for group in GROUPS for office in group["offices"]}
    offices = [str(office) for office in selected_offices if str(office) in known_offices]
    if not offices:
        raise ValueError("Please select at least one office")
    return {"offices": offices}


def inputs_summary(inputs):
    offices = inputs.get("offices", [])
    if len(offices) <= 4:
        return ", ".join(offices)
    return "{0} offices selected".format(len(offices))


def total_tasks(inputs):
    return len(inputs.get("offices", []))


def fetch_dataframe(connection, query):
    with connection.cursor() as cursor:
        cursor.execute(query)
        rows = cursor.fetchall()
    return pd.DataFrame(rows)


def _connect(db_name):
    config = get_db_config(db_name)
    config["cursorclass"] = pymysql.cursors.SSDictCursor
    return pymysql.connect(**config)


def _kill_db_thread(db_name, thread_id):
    kill_connection = _connect(db_name)
    try:
        with kill_connection.cursor() as cursor:
            cursor.execute("KILL {0}".format(int(thread_id)))
    finally:
        kill_connection.close()


def cancel(run_info):
    active_connections = list(
        run_info.get("plugin_state", {}).get("active_connections", {}).values()
    )
    for connection_info in active_connections:
        try:
            _kill_db_thread(connection_info["db_name"], connection_info["thread_id"])
        except Exception:
            pass
        try:
            connection_info["connection"].close()
        except Exception:
            pass


def _create_report(context, office, db_name):
    if context.is_cancelled():
        raise RuntimeError("Run cancelled by user")

    office_sql = '"{0}"'.format(office)
    script = _load_sql_template().format(office_sql)
    excel_name = "{0} closing report.xlsx".format(office)
    excel_path = os.path.join(context.output_dir, excel_name)

    connection = _connect(db_name)
    context.register_connection(office, db_name, connection)
    try:
        if context.is_cancelled():
            raise RuntimeError("Run cancelled by user")
        dataframe = fetch_dataframe(connection, script)
    finally:
        context.unregister_connection(office)
        try:
            connection.close()
        except Exception:
            pass

    if context.is_cancelled():
        raise RuntimeError("Run cancelled by user")
    dataframe.to_excel(excel_path, index=False)
    return excel_name


def _run_group(context, label, db_name, office_list, generated_files):
    worker_count = min(MAX_WORKERS, len(office_list))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(_create_report, context, office, db_name): office
            for office in office_list
        }
        try:
            for future in as_completed(future_map):
                office = future_map[future]
                file_name = future.result()
                generated_files.append(file_name)
                context.increment_task(
                    message="Completed {0} for {1}".format(office, label),
                    files=sorted(generated_files),
                )
                if context.is_cancelled():
                    raise RuntimeError("Run cancelled by user")
        except Exception:
            for future in future_map:
                future.cancel()
            raise


def _zip_reports(output_dir, files):
    zip_name = "YYZ_NA_closing_report_{0}.zip".format(str(d.date.today()))
    zip_path = os.path.join(output_dir, zip_name)
    with z.ZipFile(zip_path, "w") as zip_file:
        for file_name in files:
            file_path = os.path.join(output_dir, file_name)
            zip_file.write(file_path, arcname=file_name, compress_type=z.ZIP_DEFLATED)
    return zip_name, zip_path


def execute(context, inputs):
    selected_groups = _build_selected_groups(inputs["offices"])
    generated_files = []

    context.update(status="running", message="Connecting to databases")
    for group in selected_groups:
        if context.is_cancelled():
            raise RuntimeError("Run cancelled by user")
        context.update(status="running", message="Running {0}".format(group["label"]))
        _run_group(context, group["label"], group["db_name"], group["offices"], generated_files)

    if context.is_cancelled():
        raise RuntimeError("Run cancelled by user")

    zip_name, zip_path = _zip_reports(context.output_dir, generated_files)
    return {
        "files": sorted(generated_files),
        "zip_name": zip_name,
        "zip_path": zip_path,
        "outputs": [
            {
                "name": zip_name,
                "path": zip_path,
                "type": "zip",
                "download_url": "/download/{0}/{1}".format(context.run_id, zip_name),
            }
        ],
    }


FEATURE.update({
    "validate_inputs": validate_inputs,
    "inputs_summary": inputs_summary,
    "total_tasks": total_tasks,
    "execute": execute,
    "cancel": cancel,
})
