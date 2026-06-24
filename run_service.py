import datetime as d
import json
import os
import threading
import time as t
import uuid

from features import get_feature
from platform_config import BASE_DIR


REPORT_ROOT = os.path.join(BASE_DIR, "reports_v4")
RUNS = {}
RUNS_LOCK = threading.Lock()


def ensure_report_root():
    os.makedirs(REPORT_ROOT, exist_ok=True)


def _now_iso():
    return d.datetime.now().isoformat(timespec="seconds")


def _manifest_path(run_info):
    return os.path.join(run_info["output_dir"], "manifest.json")


def _snapshot(run_info):
    snapshot = dict(run_info)
    snapshot.pop("cancel_event", None)
    snapshot.pop("plugin_state", None)
    snapshot.pop("feature", None)
    return snapshot


def _write_manifest(run_info):
    manifest = _snapshot(run_info)
    with open(_manifest_path(run_info), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=True, indent=2)


def _normalize_manifest(payload):
    run_id = payload.get("run_id") or payload.get("id")
    if run_id:
        payload["run_id"] = run_id
        payload["id"] = run_id
    payload.setdefault("feature_id", "closing_report")
    payload.setdefault("feature_title", "Closing Report")
    payload.setdefault("inputs_summary", ", ".join(payload.get("selected_offices", [])))
    payload.setdefault("outputs", [])
    payload.setdefault("files", [])
    payload.setdefault("zip_name", None)
    payload.setdefault("zip_path", None)
    payload.setdefault("error", None)
    payload.setdefault("cancel_requested", False)
    return payload


def update_run(run_id, **kwargs):
    with RUNS_LOCK:
        run_info = RUNS[run_id]
        run_info.update(kwargs)
        run_info["updated_at"] = _now_iso()
        snapshot = _snapshot(run_info)
        manifest_source = dict(run_info)
    _write_manifest(manifest_source)
    return snapshot


def get_run(run_id):
    with RUNS_LOCK:
        run_info = RUNS.get(run_id)
        if run_info:
            return _snapshot(run_info)

    manifest_path = os.path.join(REPORT_ROOT, run_id, "manifest.json")
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as handle:
            return _normalize_manifest(json.load(handle))
    return None


def list_runs():
    ensure_report_root()
    runs = []
    for run_id in os.listdir(REPORT_ROOT):
        manifest_path = os.path.join(REPORT_ROOT, run_id, "manifest.json")
        if not os.path.isfile(manifest_path):
            continue
        with open(manifest_path, "r", encoding="utf-8") as handle:
            runs.append(_normalize_manifest(json.load(handle)))
    runs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return runs


class RunContext:
    def __init__(self, run_id):
        self.run_id = run_id

    @property
    def output_dir(self):
        with RUNS_LOCK:
            return RUNS[self.run_id]["output_dir"]

    def is_cancelled(self):
        with RUNS_LOCK:
            return RUNS[self.run_id]["cancel_event"].is_set()

    def update(self, **kwargs):
        return update_run(self.run_id, **kwargs)

    def increment_task(self, message, files=None):
        with RUNS_LOCK:
            run_info = RUNS[self.run_id]
            completed = run_info["completed_tasks"] + 1
        return update_run(
            self.run_id,
            status="running",
            message=message,
            completed_tasks=completed,
            files=files if files is not None else get_run(self.run_id).get("files", []),
        )

    def register_connection(self, office, db_name, connection):
        with RUNS_LOCK:
            state = RUNS[self.run_id]["plugin_state"]
            state.setdefault("active_connections", {})[office] = {
                "db_name": db_name,
                "thread_id": connection.thread_id(),
                "connection": connection,
            }

    def unregister_connection(self, office):
        with RUNS_LOCK:
            state = RUNS[self.run_id]["plugin_state"]
            state.setdefault("active_connections", {}).pop(office, None)


def create_run(feature_id, inputs):
    ensure_report_root()
    feature = get_feature(feature_id)
    if not feature:
        raise ValueError("Unknown feature: {0}".format(feature_id))

    validate_inputs = feature.get("validate_inputs")
    normalized_inputs = validate_inputs(inputs) if validate_inputs else inputs
    total_tasks_func = feature.get("total_tasks")
    total_tasks = total_tasks_func(normalized_inputs) if total_tasks_func else 1
    summary_func = feature.get("inputs_summary")
    inputs_summary = summary_func(normalized_inputs) if summary_func else ""

    run_id = uuid.uuid4().hex[:12]
    run_dir = os.path.join(REPORT_ROOT, run_id)
    os.makedirs(run_dir, exist_ok=True)
    run_info = {
        "id": run_id,
        "run_id": run_id,
        "feature_id": feature["id"],
        "feature_title": feature["title"],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "status": "queued",
        "message": "Waiting to start",
        "completed_tasks": 0,
        "total_tasks": total_tasks,
        "inputs": normalized_inputs,
        "inputs_summary": inputs_summary,
        "files": [],
        "outputs": [],
        "zip_name": None,
        "zip_path": None,
        "error": None,
        "output_dir": run_dir,
        "cancel_requested": False,
        "cancel_event": threading.Event(),
        "plugin_state": {"active_connections": {}},
        "feature": feature,
    }
    with RUNS_LOCK:
        RUNS[run_id] = run_info
    _write_manifest(run_info)
    return run_info


def _execute_run(run_id):
    started_at = t.time()
    context = RunContext(run_id)
    with RUNS_LOCK:
        run_info = RUNS[run_id]
        feature = run_info["feature"]
        inputs = dict(run_info["inputs"])

    try:
        if run_info["total_tasks"] == 0:
            raise RuntimeError("No tasks selected")
        result = feature["execute"](context, inputs) or {}
        if context.is_cancelled():
            raise RuntimeError("Run cancelled by user")
        duration = str(d.timedelta(seconds=(t.time() - started_at)))
        update_run(
            run_id,
            status="completed",
            message="Finished in {0}".format(duration),
            files=sorted(result.get("files", [])),
            outputs=result.get("outputs", []),
            zip_name=result.get("zip_name"),
            zip_path=result.get("zip_path"),
        )
    except Exception as exc:
        current = get_run(run_id)
        was_cancelled = current and current.get("cancel_requested")
        status = "cancelled" if was_cancelled or "cancelled by user" in str(exc).lower() else "failed"
        message = "Run cancelled" if status == "cancelled" else "Run failed"
        update_run(run_id, status=status, message=message, error=str(exc))


def start_run(feature_id, inputs):
    run_info = create_run(feature_id, inputs)
    thread = threading.Thread(target=_execute_run, args=(run_info["run_id"],), daemon=True)
    thread.start()
    return run_info["run_id"]


def cancel_run(run_id):
    with RUNS_LOCK:
        run_info = RUNS.get(run_id)
        if not run_info:
            return None
        run_info["cancel_requested"] = True
        run_info["cancel_event"].set()
        feature = run_info.get("feature")
        finished = run_info["status"] in ("completed", "failed", "cancelled")
        run_snapshot = _snapshot(run_info)

    if feature and feature.get("cancel"):
        try:
            feature["cancel"](run_info)
        except Exception:
            pass
    if finished:
        return run_snapshot
    return update_run(run_id, status="cancelling", message="Cancellation requested")
