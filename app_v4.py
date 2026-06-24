from functools import wraps
import json
import os

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for

from features import get_feature, list_features
from run_service import cancel_run, get_run, list_runs, start_run


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "report-platform-secret")

AUTH_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_users_v4.json")


def load_auth_users():
    with open(AUTH_CONFIG_PATH, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    users = []
    for item in payload.get("users", []):
        user_id = item.get("user_id", "").strip()
        password = item.get("password", "")
        enabled = bool(item.get("enabled", True))
        if user_id:
            users.append({"user_id": user_id, "password": password, "enabled": enabled})
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


def is_admin_user():
    return session.get("user_id") == "admin"


def require_login(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
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
    return render_template(
        "dashboard.html",
        features=list_features(),
        runs=list_runs(),
        user_id=session.get("user_id", "admin"),
        auth_users=load_auth_users(),
        is_admin=is_admin_user(),
    )


@app.get("/features/<feature_id>")
@require_login
def feature_page(feature_id):
    feature = get_feature(feature_id)
    if not feature:
        abort(404)
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

    users.append({"user_id": user_id, "password": password, "enabled": enabled})
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


@app.get("/api/features")
@require_login
def api_features():
    return jsonify(list_features())


@app.post("/api/runs")
@require_login
def create_run():
    payload = request.get_json(silent=True) or {}
    feature_id = payload.get("feature_id", "")
    inputs = payload.get("inputs", {})
    try:
        run_id = start_run(feature_id, inputs)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"run_id": run_id})


@app.post("/api/runs/<run_id>/cancel")
@require_login
def cancel_run_api(run_id):
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
    return jsonify(run_info)


@app.get("/api/runs")
@require_login
def runs_api():
    return jsonify(list_runs())


@app.post("/api/run")
@require_login
def create_run_v4_legacy():
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
    for output in run_info.get("outputs", []):
        output_path = output.get("path")
        if output.get("name") == filename and output_path and os.path.exists(output_path):
            return send_file(output_path, as_attachment=True, download_name=filename)
    abort(404)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
