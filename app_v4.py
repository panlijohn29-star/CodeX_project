from functools import wraps
import json
import os
import re
import shutil
import tempfile

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import features.related_office_modification as related_office_modification
import features.archive_currency_invoice as archive_currency_invoice
import features.ar_ap_breakdown as ar_ap_breakdown
import features.offset_invoice as offset_invoice
import features.sql_query as sql_query
import features.eason_dfw_billing as eason_dfw_billing
import features.eason_client_report as eason_client_report
from features import get_feature, list_features
from platform_config import env_value
from run_service import cancel_run, get_run, list_runs, start_run


app = Flask(__name__)
app.secret_key = env_value("FLASK_SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY must be configured before starting the report platform")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=env_value("SESSION_COOKIE_SECURE", "false").strip().lower() in ("1", "true", "yes", "on"),
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_CONFIG_PATH = os.environ.get("AUTH_CONFIG_PATH", os.path.join(BASE_DIR, "auth_users_v4.json"))
ROLE_CONFIG_PATH = os.environ.get("ROLE_CONFIG_PATH", os.path.join(BASE_DIR, "roles_v4.json"))
FEATURE_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feature_settings_v4.json")
ROLE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _write_json_atomically(path, payload):
    directory = os.path.dirname(path)
    fd, temporary_path = tempfile.mkstemp(prefix=".auth-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
        os.replace(temporary_path, path)
    except Exception:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise


def migrate_auth_users():
    """Convert the legacy plaintext account file to the RBAC-safe schema once."""
    with open(AUTH_CONFIG_PATH, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    users = payload.get("users", []) if isinstance(payload, dict) else []
    if not isinstance(users, list):
        raise ValueError("auth_users_v4.json users must be a list")

    migrated_users = []
    is_legacy = payload.get("schema_version") != 2
    changed = is_legacy
    for item in users:
        if not isinstance(item, dict):
            changed = True
            continue
        user = dict(item)
        user_id = str(user.get("user_id", "")).strip()
        if not user_id:
            changed = True
            continue
        password_hash = str(user.get("password_hash", ""))
        plaintext_password = user.pop("password", None)
        if plaintext_password is not None:
            password_hash = generate_password_hash(str(plaintext_password))
            changed = True
        if not password_hash:
            raise ValueError("Account {0} has no password hash".format(user_id))
        favourites = user.get("favourites", [])
        if not isinstance(favourites, list):
            favourites = []
            changed = True
        role_id = None if user_id == "admin" or is_legacy else user.get("role_id")
        if role_id is not None:
            role_id = str(role_id).strip() or None
        if user.get("role_id") != role_id:
            changed = True
        if "sql_access" in user or "feature_management" in user:
            changed = True
        migrated_users.append({
            "user_id": user_id,
            "password_hash": password_hash,
            "enabled": bool(user.get("enabled", True)),
            "favourites": [str(feature_id) for feature_id in favourites],
            "role_id": role_id,
        })

    if changed:
        backup_path = AUTH_CONFIG_PATH + ".pre_rbac_backup.json"
        if not os.path.exists(backup_path):
            shutil.copy2(AUTH_CONFIG_PATH, backup_path)
        _write_json_atomically(AUTH_CONFIG_PATH, {"schema_version": 2, "users": migrated_users})


def load_roles():
    try:
        with open(ROLE_CONFIG_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return []
    roles = payload.get("roles", []) if isinstance(payload, dict) else []
    valid_feature_ids = {feature["id"] for feature in list_features()}
    result = []
    seen_ids = set()
    for item in roles if isinstance(roles, list) else []:
        role_id = str(item.get("id", "")).strip()
        if not ROLE_ID_PATTERN.fullmatch(role_id) or role_id in seen_ids:
            continue
        seen_ids.add(role_id)
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        feature_ids = item.get("feature_ids", [])
        if not isinstance(feature_ids, list):
            feature_ids = []
        result.append({
            "id": role_id,
            "name": name,
            "remark": str(item.get("remark", "")).strip(),
            "feature_ids": sorted({str(feature_id) for feature_id in feature_ids} & valid_feature_ids),
        })
    return sorted(result, key=lambda role: role["name"].lower())


def save_roles(roles):
    _write_json_atomically(ROLE_CONFIG_PATH, {"schema_version": 1, "roles": roles})


def get_role(role_id):
    return next((role for role in load_roles() if role["id"] == role_id), None)


def load_auth_users():
    with open(AUTH_CONFIG_PATH, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    users = []
    for item in payload.get("users", []):
        user_id = item.get("user_id", "").strip()
        password_hash = item.get("password_hash", "")
        enabled = bool(item.get("enabled", True))
        favourites = item.get("favourites", [])
        if not isinstance(favourites, list):
            favourites = []
        if user_id:
            users.append({
                "user_id": user_id,
                "password_hash": password_hash,
                "enabled": enabled,
                "favourites": [str(feature_id) for feature_id in favourites],
                "role_id": None if user_id == "admin" else item.get("role_id"),
            })
    return users


def save_auth_users(users):
    _write_json_atomically(AUTH_CONFIG_PATH, {"schema_version": 2, "users": users})


def public_auth_user(user):
    role = get_role(user.get("role_id"))
    return {
        "user_id": user["user_id"],
        "enabled": user["enabled"],
        "role_id": user.get("role_id"),
        "role_name": role["name"] if role else None,
    }


migrate_auth_users()


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
        if not has_feature_access(feature["id"], user_id):
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


def has_feature_access(feature_id, user_id=None):
    if not get_feature(feature_id):
        return False
    selected_user_id = user_id or session.get("user_id", "")
    if selected_user_id == "admin":
        return True
    user = get_auth_user(selected_user_id)
    role = get_role(user.get("role_id")) if user else None
    return bool(role and feature_id in role["feature_ids"])


def can_manage_features():
    return is_admin_user()


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
    if feature_id and session.get("authenticated") and (
        not is_feature_active(feature_id) or not has_feature_access(feature_id)
    ):
        abort(404)


def require_feature_access(feature_id):
    """Hide an unassigned or unauthorized feature behind a 404 response."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            if not is_feature_active(feature_id) or not has_feature_access(feature_id):
                abort(404)
            return view_func(*args, **kwargs)
        return wrapper
    return decorator


def require_sql_access(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not is_feature_active("sql_query") or not has_feature_access("sql_query"):
            abort(404)
        return view_func(*args, **kwargs)
    return wrapper


def can_access_sql_run(run_info):
    if not run_info or run_info.get("feature_id") != "sql_query":
        return True
    return run_info.get("inputs", {}).get("owner_user_id") == session.get("user_id")


def can_access_run(run_info):
    if not run_info or not has_feature_access(run_info.get("feature_id", "")):
        return False
    return can_access_sql_run(run_info)


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
            abort(404)
        return view_func(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user_id = request.form.get("user_id", "")
        password = request.form.get("password", "")
        auth_user = get_auth_user(user_id)
        if auth_user and auth_user["enabled"] and check_password_hash(auth_user["password_hash"], password):
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
        auth_users=[public_auth_user(user) for user in load_auth_users()] if is_admin_user() else [],
        roles=load_roles() if is_admin_user() else [],
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
    if not has_feature_access(feature_id):
        abort(404)
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
            if not check_password_hash(user["password_hash"], current_password):
                return jsonify({"error": "Current password is incorrect"}), 400
            user["password_hash"] = generate_password_hash(new_password)
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
        abort(404)

    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id", "").strip()
    password = payload.get("password", "")
    enabled = bool(payload.get("enabled", True))

    if not user_id or not password:
        return jsonify({"error": "User ID and password are required"}), 400

    users = load_auth_users()
    if any(user["user_id"] == user_id for user in users):
        return jsonify({"error": "User already exists"}), 400

    role_id = payload.get("role_id")
    role_id = str(role_id).strip() if role_id is not None else None
    if role_id and not get_role(role_id):
        return jsonify({"error": "Role not found"}), 400
    users.append({"user_id": user_id, "password_hash": generate_password_hash(password), "enabled": enabled, "favourites": [], "role_id": role_id})
    save_auth_users(users)
    return jsonify({"ok": True, "users": [public_auth_user(user) for user in users]})


@app.post("/api/account/toggle")
@require_login
def toggle_account():
    if not is_admin_user():
        abort(404)

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
            return jsonify({"ok": True, "users": [public_auth_user(item) for item in users]})

    return jsonify({"error": "User not found"}), 404


@app.post("/api/account/role")
@require_login
def update_account_role():
    if not is_admin_user():
        abort(404)
    payload = request.get_json(silent=True) or {}
    user_id = str(payload.get("user_id", "")).strip()
    role_id = payload.get("role_id")
    role_id = str(role_id).strip() if role_id is not None else None
    if role_id and not get_role(role_id):
        return jsonify({"error": "Role not found"}), 400
    if user_id == "admin":
        return jsonify({"error": "Admin role cannot be changed"}), 400
    users = load_auth_users()
    for user in users:
        if user["user_id"] == user_id:
            user["role_id"] = role_id
            allowed_feature_ids = set(get_role(role_id)["feature_ids"]) if role_id else set()
            user["favourites"] = [feature_id for feature_id in user.get("favourites", []) if feature_id in allowed_feature_ids]
            save_auth_users(users)
            return jsonify({"ok": True, "users": [public_auth_user(item) for item in users]})
    return jsonify({"error": "User not found"}), 404


@app.post("/api/favourites")
@require_login
def update_favourite():
    payload = request.get_json(silent=True) or {}
    feature_id = payload.get("feature_id", "").strip()
    favourite = bool(payload.get("favourite", True))
    user_id = session.get("user_id", "")

    if not get_feature(feature_id) or not is_feature_active(feature_id) or not has_feature_access(feature_id):
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


def role_management_payload():
    settings = load_feature_settings()
    return {
        "roles": load_roles(),
        "features": [
            {
                "id": feature["id"],
                "title": settings[feature["id"]]["title"],
                "category": feature["category"],
                "active": settings[feature["id"]]["active"],
            }
            for feature in list_features()
        ],
    }


def validate_role_payload(payload, allow_id=False):
    role_id = str(payload.get("id", "")).strip()
    if allow_id and not ROLE_ID_PATTERN.fullmatch(role_id):
        raise ValueError("Role ID must use lowercase letters, numbers, hyphens, or underscores")
    name = str(payload.get("name", "")).strip()
    remark = str(payload.get("remark", "")).strip()
    if not name or len(name) > 100:
        raise ValueError("Role name must be 1 to 100 characters")
    if len(remark) > 1000:
        raise ValueError("Role remark must be 1,000 characters or fewer")
    feature_ids = payload.get("feature_ids", [])
    if not isinstance(feature_ids, list):
        raise ValueError("Feature IDs must be a list")
    valid_feature_ids = {feature["id"] for feature in list_features()}
    selected_ids = {str(feature_id) for feature_id in feature_ids}
    if not selected_ids <= valid_feature_ids:
        raise ValueError("One or more selected features do not exist")
    result = {"name": name, "remark": remark, "feature_ids": sorted(selected_ids)}
    if allow_id:
        result["id"] = role_id
    return result


@app.get("/management/roles")
@require_login
@require_feature_management
def role_management_page():
    return render_template("role_management.html", user_id=session.get("user_id", ""), **role_management_payload())


@app.get("/api/roles")
@require_login
@require_feature_management
def api_roles():
    return jsonify(role_management_payload())


@app.post("/api/roles")
@require_login
@require_feature_management
def create_role():
    try:
        role = validate_role_payload(request.get_json(silent=True) or {}, allow_id=True)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    roles = load_roles()
    if any(item["id"] == role["id"] for item in roles):
        return jsonify({"error": "Role ID already exists"}), 400
    roles.append(role)
    save_roles(roles)
    return jsonify({"ok": True, **role_management_payload()})


@app.put("/api/roles/<role_id>")
@require_login
@require_feature_management
def update_role(role_id):
    try:
        updates = validate_role_payload(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    roles = load_roles()
    for role in roles:
        if role["id"] == role_id:
            role.update(updates)
            save_roles(roles)
            return jsonify({"ok": True, **role_management_payload()})
    abort(404)


@app.delete("/api/roles/<role_id>")
@require_login
@require_feature_management
def delete_role(role_id):
    if any(user.get("role_id") == role_id for user in load_auth_users()):
        return jsonify({"error": "Reassign accounts before deleting this role"}), 400
    roles = load_roles()
    remaining_roles = [role for role in roles if role["id"] != role_id]
    if len(remaining_roles) == len(roles):
        abort(404)
    save_roles(remaining_roles)
    return jsonify({"ok": True, **role_management_payload()})


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
    if not get_feature(feature_id) or not is_feature_active(feature_id) or not has_feature_access(feature_id):
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
    if not run_info or not can_access_run(run_info):
        abort(404)
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
    if not can_access_run(run_info):
        abort(404)
    return jsonify(run_info)


@app.get("/api/runs")
@require_login
def runs_api():
    return jsonify([run for run in list_runs() if can_access_run(run)])


@app.post("/api/run")
@require_login
def create_run_v4_legacy():
    if not is_feature_active("closing_report") or not has_feature_access("closing_report"):
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
    if not can_access_run(run_info):
        abort(404)
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
    if not can_access_run(run_info):
        abort(404)
    for output in run_info.get("outputs", []):
        output_path = output.get("path")
        if output.get("name") == filename and output_path and os.path.exists(output_path):
            return send_file(output_path, as_attachment=True, download_name=filename)
    abort(404)


@app.get("/download/offset-invoice/<path:filename>")
@require_login
def download_offset_invoice(filename):
    if not has_feature_access("offset_invoice") or not is_feature_active("offset_invoice"):
        abort(404)
    output_path = offset_invoice.output_path_for(filename)
    if not output_path or not os.path.exists(output_path):
        abort(404)
    return send_file(output_path, as_attachment=True, download_name=filename)


@app.get("/download/eason-dfw-billing/<path:filename>")
@require_login
def download_eason_dfw_billing(filename):
    if not has_feature_access("eason_dfw_billing") or not is_feature_active("eason_dfw_billing"):
        abort(404)
    output_path = eason_dfw_billing.output_path_for(filename)
    if not output_path or not output_path.exists():
        abort(404)
    return send_file(output_path, as_attachment=True, download_name=filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
