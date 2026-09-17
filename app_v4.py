from functools import wraps
import json
import os

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for

import features.related_office_modification as related_office_modification
import features.archive_currency_invoice as archive_currency_invoice
import features.ar_ap_breakdown as ar_ap_breakdown
import features.offset_invoice as offset_invoice
import features.sql_query as sql_query
import features.eason_dfw_billing as eason_dfw_billing
import features.eason_client_report as eason_client_report
from features import get_feature, list_features
from run_service import cancel_run, get_run, list_runs, start_run


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "report-platform-secret")

AUTH_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_users_v4.json")
FEATURE_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feature_settings_v4.json")


def load_auth_users():
    with open(AUTH_CONFIG_PATH, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    users = []
    for item in payload.get("users", []):
        user_id = item.get("user_id", "").strip()
        password = item.get("password", "")
        enabled = bool(item.get("enabled", True))
        favourites = item.get("favourites", [])
        if not isinstance(favourites, list):
            favourites = []
        if user_id:
            users.append({
                "user_id": user_id,
                "password": password,
                "enabled": enabled,
                "favourites": [str(feature_id) for feature_id in favourites],
                "sql_access": bool(item.get("sql_access", user_id == "admin")),
                "feature_management": bool(item.get("feature_management", user_id == "admin")),
            })
    return users


def save_auth_users(users):
    payload = {"users": users}
    with open(AUTH_CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)


def get_auth_user(user_id):
    for user in load_auth_users():
        if user["user_id"] == user_id:
            return user
    return None


def get_user_favourites(user_id):
    auth_user = get_auth_user(user_id)
    if not auth_user:
        return []
    return auth_user.get("favourites", [])


def _default_feature_settings():
    return {
        feature["id"]: {
            "active": True,
            "sort_order": index,
            "title": feature["title"],
            "remark": "",
        }
        for index, feature in enumerate(list_features())
    }


def load_feature_settings():
    defaults = _default_feature_settings()
    try:
        with open(FEATURE_SETTINGS_PATH, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        saved = {}

    if not isinstance(saved, dict):
        saved = {}
    for feature_id, default in defaults.items():
        value = saved.get(feature_id, {})
        if not isinstance(value, dict):
            value = {}
        default["active"] = bool(value.get("active", True))
        sort_order = value.get("sort_order", default["sort_order"])
        default["sort_order"] = sort_order if isinstance(sort_order, int) else default["sort_order"]
        title = value.get("title", default["title"])
        default["title"] = title.strip() if isinstance(title, str) and title.strip() else default["title"]
        remark = value.get("remark", "")
        default["remark"] = remark.strip() if isinstance(remark, str) else ""
    return defaults


def save_feature_settings(settings):
    with open(FEATURE_SETTINGS_PATH, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, ensure_ascii=True, indent=2)


def is_feature_active(feature_id):
    return bool(load_feature_settings().get(feature_id, {}).get("active", False))


def feature_management_payload():
    settings = load_feature_settings()
    return sorted([
        {
            **feature,
            "original_title": feature["title"],
            "title": settings[feature["id"]]["title"],
            "remark": settings[feature["id"]]["remark"],
            "active": settings[feature["id"]]["active"],
            "sort_order": settings[feature["id"]]["sort_order"],
        }
        for feature in list_features()
    ], key=lambda item: (item["category"].lower(), item["sort_order"], item["title"].lower()))


def list_features_for_user(user_id):
    favourites = set(get_user_favourites(user_id))
    features = []
    settings = load_feature_settings()
    for feature in list_features():
        if not settings[feature["id"]]["active"]:
            continue
        if feature["id"] == "sql_query" and not has_sql_access(user_id):
            continue
        item = dict(feature)
        item["title"] = settings[item["id"]]["title"]
        item["is_favourite"] = item["id"] in favourites
        item["sort_order"] = settings[item["id"]]["sort_order"]
        features.append(item)
    return sorted(features, key=lambda item: (item["category"].lower(), item["sort_order"], item["title"].lower()))


def group_features_by_category(features):
    reports = [feature for feature in features if feature["category"].lower() == "reports"]
    tools = [feature for feature in features if feature["category"].lower() == "tools"]
    return reports, tools


def is_admin_user():
    return session.get("user_id") == "admin"


def can_manage_features():
    user = get_auth_user(session.get("user_id", ""))
    return bool(user and user.get("feature_management", False))


FEATURE_ENDPOINTS = {
    "related_office_lookup": "related_office_modification",
    "related_office_company": "related_office_modification",
    "related_office_execute": "related_office_modification",
    "archive_currency_invoice_lookup": "archive_currency_invoice",
    "archive_currency_invoice_execute": "archive_currency_invoice",
    "ar_ap_breakdown_search": "ar_ap_breakdown",
    "ar_ap_breakdown_preview": "ar_ap_breakdown",
    "offset_invoice_generate": "offset_invoice",
    "eason_dfw_billing_mappings": "eason_dfw_billing",
    "eason_dfw_billing_generate": "eason_dfw_billing",
    "eason_client_report_search": "eason_client_report",
    "eason_client_report_preview": "eason_client_report",
    "sql_query_start": "sql_query",
    "sql_query_status": "sql_query",
    "sql_query_cancel": "sql_query",
    "sql_query_list_scripts": "sql_query",
    "sql_query_load_script": "sql_query",
    "sql_query_save_script": "sql_query",
    "sql_query_delete_script": "sql_query",
    "sql_query_create_folder": "sql_query",
    "sql_query_delete_folder": "sql_query",
    "sql_query_upload_script": "sql_query",
}


@app.before_request
def block_inactive_feature_endpoints():
    feature_id = FEATURE_ENDPOINTS.get(request.endpoint)
    if feature_id and session.get("authenticated") and not is_feature_active(feature_id):
        abort(404)


def has_sql_access(user_id=None):
    user = get_auth_user(user_id or session.get("user_id", ""))
    return bool(user and user.get("sql_access", user.get("user_id") == "admin"))


def require_sql_access(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not has_sql_access():
            return jsonify({"error": "SQL Query access is not enabled for this account"}), 403
        return view_func(*args, **kwargs)
    return wrapper


def can_access_sql_run(run_info):
    if not run_info or run_info.get("feature_id") != "sql_query":
        return True
    return run_info.get("inputs", {}).get("owner_user_id") == session.get("user_id")


def require_login(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapper


def require_feature_management(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not can_manage_features():
            return jsonify({"error": "Feature management access is not enabled for this account"}), 403
        return view_func(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user_id = request.form.get("user_id", "")
        password = request.form.get("password", "")
        auth_user = get_auth_user(user_id)
        if auth_user and auth_user["enabled"] and auth_user["password"] == password:
            session["authenticated"] = True
            session["user_id"] = user_id
            return redirect(url_for("index_v4"))
        error = "Invalid ID or password"
    return render_template("login_v4.html", error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@require_login
def index_v4():
    user_id = session.get("user_id", "admin")
    features = list_features_for_user(user_id)
    reports, tools = group_features_by_category(features)
    return render_template(
        "dashboard.html",
        reports=reports,
        tools=tools,
        user_id=user_id,
        auth_users=load_auth_users(),
        is_admin=is_admin_user(),
        can_manage_features=can_manage_features(),
    )


@app.get("/features/<feature_id>")
@require_login
def feature_page(feature_id):
    feature = get_feature(feature_id)
    if not feature or not is_feature_active(feature_id):
        abort(404)
    feature = dict(feature)
    feature["title"] = load_feature_settings()[feature_id]["title"]
    if feature_id == "sql_query" and not has_sql_access():
        abort(403)
    if feature.get("template"):
        return render_template(
            feature["template"],
            feature={
                "id": feature["id"],
                "title": feature["title"],
                "category": feature["category"],
                "description": feature["description"],
            },
            db_profiles=feature.get("db_profiles", related_office_modification.ALLOWED_DB_PROFILES),
            sql_profiles=sql_query.discover_db_profiles() if feature["id"] == "sql_query" else [],
            user_id=session.get("user_id", "admin"),
        )
    return render_template(
        "feature_run.html",
        feature={
            "id": feature["id"],
            "title": feature["title"],
            "category": feature["category"],
            "description": feature["description"],
            "supports_cancel": feature.get("supports_cancel", False),
            "output_type": feature.get("output_type", "files"),
            "input_schema": feature.get("input_schema", []),
        },
        user_id=session.get("user_id", "admin"),
    )


@app.post("/api/account/password")
@require_login
def change_password():
    payload = request.get_json(silent=True) or {}
    current_password = payload.get("current_password", "")
    new_password = payload.get("new_password", "")
    user_id = session.get("user_id", "")
    users = load_auth_users()

    if not new_password.strip():
        return jsonify({"error": "New password cannot be empty"}), 400

    updated = False
    for user in users:
        if user["user_id"] == user_id:
            if user["password"] != current_password:
                return jsonify({"error": "Current password is incorrect"}), 400
            user["password"] = new_password
            updated = True
            break

    if not updated:
        return jsonify({"error": "User not found"}), 404

    save_auth_users(users)
    return jsonify({"ok": True})


@app.post("/api/account")
@require_login
def create_account():
    if not is_admin_user():
        return jsonify({"error": "Admin only"}), 403

    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id", "").strip()
    password = payload.get("password", "")
    enabled = bool(payload.get("enabled", True))

    if not user_id or not password:
        return jsonify({"error": "User ID and password are required"}), 400

    users = load_auth_users()
    if any(user["user_id"] == user_id for user in users):
        return jsonify({"error": "User already exists"}), 400

    users.append({"user_id": user_id, "password": password, "enabled": enabled, "favourites": [], "sql_access": False, "feature_management": False})
    save_auth_users(users)
    return jsonify({"ok": True, "users": users})


@app.post("/api/account/toggle")
@require_login
def toggle_account():
    if not is_admin_user():
        return jsonify({"error": "Admin only"}), 403

    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id", "").strip()
    enabled = bool(payload.get("enabled", True))
    users = load_auth_users()

    for user in users:
        if user["user_id"] == user_id:
            if user["user_id"] == "admin" and not enabled:
                return jsonify({"error": "Admin account cannot be disabled"}), 400
            user["enabled"] = enabled
            save_auth_users(users)
            return jsonify({"ok": True, "users": users})

    return jsonify({"error": "User not found"}), 404


@app.post("/api/account/sql-access")
@require_login
def toggle_sql_access():
    if not is_admin_user():
        return jsonify({"error": "Admin only"}), 403
    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id", "").strip()
    enabled = bool(payload.get("enabled", False))
    users = load_auth_users()
    for user in users:
        if user["user_id"] == user_id:
            user["sql_access"] = enabled
            save_auth_users(users)
            return jsonify({"ok": True, "users": users})
    return jsonify({"error": "User not found"}), 404


@app.post("/api/account/feature-management")
@require_login
def toggle_feature_management_access():
    if not is_admin_user():
        return jsonify({"error": "Admin only"}), 403
    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id", "").strip()
    enabled = bool(payload.get("enabled", False))
    users = load_auth_users()
    for user in users:
        if user["user_id"] == user_id:
            user["feature_management"] = enabled
            save_auth_users(users)
            return jsonify({"ok": True, "users": users})
    return jsonify({"error": "User not found"}), 404


@app.post("/api/favourites")
@require_login
def update_favourite():
    payload = request.get_json(silent=True) or {}
    feature_id = payload.get("feature_id", "").strip()
    favourite = bool(payload.get("favourite", True))
    user_id = session.get("user_id", "")

    if not get_feature(feature_id) or not is_feature_active(feature_id):
        return jsonify({"error": "Feature not found"}), 404

    users = load_auth_users()
    for user in users:
        if user["user_id"] == user_id:
            favourites = set(user.get("favourites", []))
            if favourite:
                favourites.add(feature_id)
            else:
                favourites.discard(feature_id)
            user["favourites"] = sorted(favourites)
            save_auth_users(users)
            features = list_features_for_user(user_id)
            reports, tools = group_features_by_category(features)
            return jsonify({"ok": True, "favourites": user["favourites"], "reports": reports, "tools": tools})

    return jsonify({"error": "User not found"}), 404


@app.get("/api/features")
@require_login
def api_features():
    return jsonify(list_features_for_user(session.get("user_id", "")))


@app.get("/management/features")
@require_login
@require_feature_management
def feature_management_page():
    return render_template("feature_management.html", features=feature_management_payload(), user_id=session.get("user_id", ""))


@app.get("/api/feature-management")
@require_login
@require_feature_management
def api_feature_management():
    return jsonify(feature_management_payload())


@app.post("/api/feature-management/<feature_id>/toggle")
@require_login
@require_feature_management
def toggle_feature(feature_id):
    if not get_feature(feature_id):
        return jsonify({"error": "Feature not found"}), 404
    payload = request.get_json(silent=True) or {}
    settings = load_feature_settings()
    settings[feature_id]["active"] = bool(payload.get("active", True))
    save_feature_settings(settings)
    return jsonify({"ok": True, "features": feature_management_payload()})


@app.post("/api/feature-management/<feature_id>/move")
@require_login
@require_feature_management
def move_feature(feature_id):
    feature = get_feature(feature_id)
    if not feature:
        return jsonify({"error": "Feature not found"}), 404
    direction = (request.get_json(silent=True) or {}).get("direction")
    if direction not in ("up", "down"):
        return jsonify({"error": "Direction must be up or down"}), 400
    settings = load_feature_settings()
    category_features = sorted(
        [item for item in list_features() if item["category"].lower() == feature["category"].lower()],
        key=lambda item: (settings[item["id"]]["sort_order"], item["title"].lower()),
    )
    current_index = next(index for index, item in enumerate(category_features) if item["id"] == feature_id)
    target_index = current_index - 1 if direction == "up" else current_index + 1
    if 0 <= target_index < len(category_features):
        other_id = category_features[target_index]["id"]
        settings[feature_id]["sort_order"], settings[other_id]["sort_order"] = (
            settings[other_id]["sort_order"], settings[feature_id]["sort_order"]
        )
        save_feature_settings(settings)
    return jsonify({"ok": True, "features": feature_management_payload()})


@app.post("/api/feature-management/<feature_id>/details")
@require_login
@require_feature_management
def update_feature_details(feature_id):
    feature = get_feature(feature_id)
    if not feature:
        return jsonify({"error": "Feature not found"}), 404
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title", "")).strip()
    remark = str(payload.get("remark", "")).strip()
    if not title:
        return jsonify({"error": "Display name cannot be empty"}), 400
    if len(title) > 100:
        return jsonify({"error": "Display name must be 100 characters or fewer"}), 400
    if len(remark) > 1000:
        return jsonify({"error": "Remark must be 1,000 characters or fewer"}), 400
    settings = load_feature_settings()
    settings[feature_id]["title"] = title
    settings[feature_id]["remark"] = remark
    save_feature_settings(settings)
    return jsonify({"ok": True, "features": feature_management_payload()})


def _related_office_response(action):
    try:
        return jsonify(action())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _interactive_tool_response(action):
    try:
        return jsonify(action())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/related-office/lookup")
@require_login
def related_office_lookup():
    payload = request.get_json(silent=True) or {}
    return _related_office_response(
        lambda: related_office_modification.lookup_payload(
            payload.get("db_profile"),
            payload.get("hawb_no"),
        )
    )


@app.post("/api/related-office/company")
@require_login
def related_office_company():
    payload = request.get_json(silent=True) or {}
    return _related_office_response(
        lambda: related_office_modification.company_payload(
            payload.get("db_profile"),
            payload.get("hawb_no"),
            payload.get("selected_job_no"),
        )
    )


@app.post("/api/related-office/execute")
@require_login
def related_office_execute():
    payload = request.get_json(silent=True) or {}
    return _related_office_response(
        lambda: related_office_modification.execute_payload(
            payload.get("db_profile"),
            payload.get("hawb_no"),
            payload.get("selected_job_no"),
            payload.get("confirmed_company_code"),
        )
    )


@app.post("/api/archive-currency-invoice/lookup")
@require_login
def archive_currency_invoice_lookup():
    payload = request.get_json(silent=True) or {}
    return _interactive_tool_response(
        lambda: archive_currency_invoice.lookup_payload(
            payload.get("db_profile"),
            payload.get("invoice_text"),
        )
    )


@app.post("/api/archive-currency-invoice/execute")
@require_login
def archive_currency_invoice_execute():
    payload = request.get_json(silent=True) or {}
    return _interactive_tool_response(
        lambda: archive_currency_invoice.execute_payload(
            payload.get("db_profile"),
            payload.get("invoice_numbers"),
            payload.get("report_date_range"),
        )
    )


@app.post("/api/ar-ap-breakdown/search")
@require_login
def ar_ap_breakdown_search():
    payload = request.get_json(silent=True) or {}
    return _interactive_tool_response(
        lambda: ar_ap_breakdown.search_payload(
            payload.get("db_profile"),
            payload.get("report_type"),
            payload.get("etd_from"),
            payload.get("etd_to"),
            payload.get("customer"),
            payload.get("job_type"),
            payload.get("billing_office"),
        )
    )


@app.post("/api/ar-ap-breakdown/preview")
@require_login
def ar_ap_breakdown_preview():
    payload = request.get_json(silent=True) or {}
    return _interactive_tool_response(
        lambda: ar_ap_breakdown.preview_payload(
            payload.get("db_profile"),
            payload.get("report_type"),
            payload.get("etd_from"),
            payload.get("etd_to"),
            payload.get("customer"),
            payload.get("job_type"),
            payload.get("billing_office"),
        )
    )


@app.post("/api/offset-invoice/generate")
@require_login
def offset_invoice_generate():
    payload = request.get_json(silent=True) or {}
    return _interactive_tool_response(
        lambda: offset_invoice.generate_payload(
            payload.get("db_profile"),
            payload.get("username"),
            payload.get("invoice_text"),
        )
    )


@app.get("/api/eason-dfw-billing/mappings")
@require_login
def eason_dfw_billing_mappings():
    return _interactive_tool_response(eason_dfw_billing.mappings_payload)


@app.post("/api/eason-dfw-billing/generate")
@require_login
def eason_dfw_billing_generate():
    try:
        return jsonify(eason_dfw_billing.generate_payload(
            request.files.get("workbook"),
            request.form.get("accounts_text"),
            request.form.get("charges_text"),
        ))
    except eason_dfw_billing.AuditError as exc:
        return jsonify({"error": str(exc), "issues": exc.issues}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/eason-client-report/search")
@require_login
def eason_client_report_search():
    payload = request.get_json(silent=True) or {}
    return _interactive_tool_response(lambda: eason_client_report.search_payload(
        payload.get("db_profile"), payload.get("search_values"), payload.get("office"),
        payload.get("report_type"), payload.get("search_mode"), payload.get("job_type"),
    ))


@app.post("/api/eason-client-report/preview")
@require_login
def eason_client_report_preview():
    payload = request.get_json(silent=True) or {}
    return _interactive_tool_response(lambda: eason_client_report.preview_payload(
        payload.get("db_profile"), payload.get("search_values"), payload.get("office"),
        payload.get("report_type"), payload.get("search_mode"), payload.get("job_type"),
    ))


@app.post("/api/sql-query/runs")
@require_login
@require_sql_access
def sql_query_start():
    payload = request.get_json(silent=True) or {}
    try:
        run_id = start_run("sql_query", {
            "db_profile": payload.get("db_profile"),
            "sql": payload.get("sql"),
            "owner_user_id": session.get("user_id", ""),
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"run_id": run_id})


@app.get("/api/sql-query/runs/<run_id>")
@require_login
@require_sql_access
def sql_query_status(run_id):
    run_info = get_run(run_id)
    if not run_info or run_info.get("feature_id") != "sql_query":
        abort(404)
    if not can_access_sql_run(run_info):
        abort(403)
    return jsonify(run_info)


@app.post("/api/sql-query/runs/<run_id>/cancel")
@require_login
@require_sql_access
def sql_query_cancel(run_id):
    run_info = get_run(run_id)
    if not run_info or run_info.get("feature_id") != "sql_query":
        abort(404)
    if not can_access_sql_run(run_info):
        abort(403)
    updated = cancel_run(run_id)
    if updated.get("cancellation_error"):
        return jsonify({"error": updated["cancellation_error"]}), 400
    return jsonify(updated)


@app.get("/api/sql-query/scripts")
@require_login
@require_sql_access
def sql_query_list_scripts():
    return jsonify(sql_query.list_scripts(session.get("user_id", "")))


@app.get("/api/sql-query/scripts/<path:name>")
@require_login
@require_sql_access
def sql_query_load_script(name):
    return _interactive_tool_response(lambda: sql_query.load_script(session.get("user_id", ""), name))


@app.post("/api/sql-query/scripts")
@require_login
@require_sql_access
def sql_query_save_script():
    payload = request.get_json(silent=True) or {}
    return _interactive_tool_response(lambda: sql_query.save_script(
        session.get("user_id", ""), payload.get("name"), payload.get("sql"), bool(payload.get("overwrite")),
    ))


@app.delete("/api/sql-query/scripts/<path:name>")
@require_login
@require_sql_access
def sql_query_delete_script(name):
    return _interactive_tool_response(lambda: sql_query.delete_script(session.get("user_id", ""), name))


@app.post("/api/sql-query/folders")
@require_login
@require_sql_access
def sql_query_create_folder():
    payload = request.get_json(silent=True) or {}
    return _interactive_tool_response(lambda: sql_query.create_folder(session.get("user_id", ""), payload.get("path")))


@app.delete("/api/sql-query/folders/<path:folder_path>")
@require_login
@require_sql_access
def sql_query_delete_folder(folder_path):
    return _interactive_tool_response(lambda: sql_query.delete_folder(session.get("user_id", ""), folder_path))


@app.post("/api/sql-query/scripts/upload")
@require_login
@require_sql_access
def sql_query_upload_script():
    uploaded_files = request.files.getlist("files") or request.files.getlist("file")
    uploaded_files = [item for item in uploaded_files if item and item.filename]
    if not uploaded_files:
        return jsonify({"error": "Please select a .sql file"}), 400
    scripts = []
    for uploaded in uploaded_files:
        if not uploaded.filename.lower().endswith(".sql"):
            return jsonify({"error": "Only .sql files can be uploaded"}), 400
        content = uploaded.read(sql_query.MAX_SCRIPT_BYTES + 1)
        if len(content) > sql_query.MAX_SCRIPT_BYTES:
            return jsonify({"error": "Script exceeds the 1 MB limit"}), 400
        try:
            sql = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return jsonify({"error": "SQL file must be UTF-8 encoded"}), 400
        scripts.append({"name": sql_query.normalize_script_name(uploaded.filename), "sql": sql})
    return jsonify({"scripts": scripts})


@app.post("/api/runs")
@require_login
def create_run():
    payload = request.get_json(silent=True) or {}
    feature_id = payload.get("feature_id", "")
    inputs = payload.get("inputs", {})
    if not get_feature(feature_id) or not is_feature_active(feature_id):
        return jsonify({"error": "Feature is inactive or not found"}), 404
    if feature_id == "sql_query":
        return jsonify({"error": "Use the SQL Query endpoint"}), 400
    try:
        run_id = start_run(feature_id, inputs)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"run_id": run_id})


@app.post("/api/runs/<run_id>/cancel")
@require_login
def cancel_run_api(run_id):
    run_info = get_run(run_id)
    if run_info and not can_access_sql_run(run_info):
        abort(403)
    run_info = cancel_run(run_id)
    if not run_info:
        abort(404)
    return jsonify(run_info)


@app.get("/api/runs/<run_id>")
@require_login
def run_status(run_id):
    run_info = get_run(run_id)
    if not run_info:
        abort(404)
    if not can_access_sql_run(run_info):
        abort(403)
    return jsonify(run_info)


@app.get("/api/runs")
@require_login
def runs_api():
    return jsonify([run for run in list_runs() if can_access_sql_run(run)])


@app.post("/api/run")
@require_login
def create_run_v4_legacy():
    if not is_feature_active("closing_report"):
        return jsonify({"error": "Feature is inactive or not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        run_id = start_run("closing_report", {"offices": payload.get("offices", [])})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"run_id": run_id})


@app.post("/api/cancel/<run_id>")
@require_login
def cancel_run_v4_legacy(run_id):
    return cancel_run_api(run_id)


@app.get("/api/status/<run_id>")
@require_login
def run_status_v4_legacy(run_id):
    return run_status(run_id)


@app.get("/download/<run_id>")
@require_login
def download_v4(run_id):
    run_info = get_run(run_id)
    if not run_info:
        abort(404)
    if not can_access_sql_run(run_info):
        abort(403)
    if run_info["status"] != "completed" or not run_info.get("zip_path"):
        abort(404)
    if not os.path.exists(run_info["zip_path"]):
        abort(404)
    return send_file(run_info["zip_path"], as_attachment=True, download_name=run_info["zip_name"])


@app.get("/download/<run_id>/<path:filename>")
@require_login
def download_output(run_id, filename):
    run_info = get_run(run_id)
    if not run_info or run_info["status"] != "completed":
        abort(404)
    if not can_access_sql_run(run_info):
        abort(403)
    for output in run_info.get("outputs", []):
        output_path = output.get("path")
        if output.get("name") == filename and output_path and os.path.exists(output_path):
            return send_file(output_path, as_attachment=True, download_name=filename)
    abort(404)


@app.get("/download/offset-invoice/<path:filename>")
@require_login
def download_offset_invoice(filename):
    output_path = offset_invoice.output_path_for(filename)
    if not output_path or not os.path.exists(output_path):
        abort(404)
    return send_file(output_path, as_attachment=True, download_name=filename)


@app.get("/download/eason-dfw-billing/<path:filename>")
@require_login
def download_eason_dfw_billing(filename):
    output_path = eason_dfw_billing.output_path_for(filename)
    if not output_path or not output_path.exists():
        abort(404)
    return send_file(output_path, as_attachment=True, download_name=filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
