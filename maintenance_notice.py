"""Scheduled restart / maintenance notice shared by every signed-in user.

A plan stores one UTC restart time. The remaining seconds decide the phase:
scheduled -> warning (last 5 min, new long tasks blocked) -> countdown
(last 30 s, full-screen timer) -> maintenance (restart time reached).
"""
import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone

from platform_config import BASE_DIR

NOTICE_PATH = os.path.abspath(os.environ.get(
    "MAINTENANCE_NOTICE_PATH", os.path.join(BASE_DIR, "instance", "maintenance_notice.json")
))
WARNING_SECONDS = 300
FINAL_WARNING_SECONDS = 60
COUNTDOWN_SECONDS = 30
MAX_MESSAGE_LENGTH = 300
MAX_LEAD_SECONDS = 7 * 24 * 3600

_LOCK = threading.Lock()


def _now():
    return time.time()


def parse_restart_time(value):
    """Return epoch seconds for an ISO-8601 string with a UTC offset, or an epoch number."""
    if isinstance(value, bool):
        raise ValueError("Restart time is invalid")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        raise ValueError("Restart time is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Restart time is invalid")
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc).timestamp()


def _read():
    try:
        with open(NOTICE_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("restart_at"), (int, float)):
        return None
    return payload


def _write(payload):
    directory = os.path.dirname(NOTICE_PATH)
    os.makedirs(directory, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=".maintenance-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
        os.replace(temporary_path, NOTICE_PATH)
    except Exception:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise


def phase_for(remaining):
    if remaining <= 0:
        return "maintenance"
    if remaining <= COUNTDOWN_SECONDS:
        return "countdown"
    if remaining <= WARNING_SECONDS:
        return "warning"
    return "scheduled"


def _describe(plan, now):
    remaining = plan["restart_at"] - now
    return {
        "id": plan["id"],
        "restart_at": plan["restart_at"],
        "message": plan.get("message", ""),
        "created_by": plan.get("created_by", ""),
        "remaining_seconds": max(0.0, remaining),
        "phase": phase_for(remaining),
    }


def get_status():
    """Return the active plan (with server time and phase), or a payload with plan=None."""
    now = _now()
    with _LOCK:
        plan = _read()
    return {"server_time": now, "plan": _describe(plan, now) if plan else None}


def is_maintenance_active():
    plan = _read()
    return bool(plan and plan["restart_at"] - _now() <= 0)


def is_new_work_blocked():
    """True during the last five minutes and during maintenance."""
    plan = _read()
    return bool(plan and plan["restart_at"] - _now() <= WARNING_SECONDS)


def remaining_seconds():
    plan = _read()
    return max(0.0, plan["restart_at"] - _now()) if plan else None


def schedule(restart_at, message, user_id):
    restart_epoch = parse_restart_time(restart_at)
    now = _now()
    if restart_epoch <= now + 10:
        raise ValueError("Restart time must be in the future")
    if restart_epoch > now + MAX_LEAD_SECONDS:
        raise ValueError("Restart time must be within the next 7 days")
    text = str(message or "").strip()
    if len(text) > MAX_MESSAGE_LENGTH:
        raise ValueError("Message must be {0} characters or fewer".format(MAX_MESSAGE_LENGTH))
    plan = {
        "id": uuid.uuid4().hex[:12],
        "restart_at": restart_epoch,
        "message": text,
        "created_by": str(user_id or ""),
        "created_at": now,
    }
    with _LOCK:
        _write(plan)
    return get_status()


def cancel():
    with _LOCK:
        try:
            os.unlink(NOTICE_PATH)
        except FileNotFoundError:
            pass
    return get_status()


def clear_on_startup():
    """A fresh process means the restart already happened, so drop any old plan."""
    cancel()
