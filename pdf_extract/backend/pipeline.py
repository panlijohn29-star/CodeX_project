import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import db, extractor, i18n, llm, validate

log = logging.getLogger("pipeline")
MANUAL = i18n.bi("手动指定", "Manually assigned")
_executor: ThreadPoolExecutor | None = None

# The pool is sized for the largest allowed setting; how many documents actually run at once is read from the
# settings every time a document is about to start, so changing "documents processed in parallel" applies at once.
POOL_THREADS = 32
_cond = threading.Condition()
_running = 0


def executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=POOL_THREADS)
    return _executor


AUTO_MIN = 3
_outstanding = 0  # documents submitted and not finished yet (queued + running)


def _int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _limit() -> int:
    s = db.get_settings()
    fixed = _int(s.get("max_workers"), 0)
    if fixed > 0:
        return min(POOL_THREADS, fixed)
    cap = max(AUTO_MIN, min(POOL_THREADS, _int(s.get("max_workers_cap"), 16)))
    return max(AUTO_MIN, min(cap, _outstanding))


def _acquire() -> None:
    global _running
    while True:
        limit = _limit()
        with _cond:
            if _running < limit:
                _running += 1
                return
            _cond.wait(2)


def _release() -> None:
    global _running
    with _cond:
        _running -= 1
        _cond.notify_all()


def submit(doc_id: int, template_id: int | None = None, reuse: bool = False) -> None:
    """reuse: copy the result of an identical, already processed file instead of calling the AI (uploads only)."""
    global _outstanding
    db.update("documents", doc_id, {"status": "pending", "error": ""})
    with _cond:
        _outstanding += 1
    executor().submit(process, doc_id, template_id, reuse, time.monotonic())


def process(doc_id: int, template_id: int | None = None, reuse: bool = False, queued_at: float | None = None) -> None:
    global _outstanding
    try:
        _acquire()
        try:
            started = time.monotonic()
            with llm.usage_scope(doc_id, "pipeline") as scope:
                try:
                    _process(doc_id, template_id, reuse)
                finally:
                    _save_timings(doc_id, scope, started, queued_at)
        finally:
            _release()
    finally:
        with _cond:
            _outstanding -= 1
            _cond.notify_all()


def _save_timings(doc_id: int, scope: dict, started: float, queued_at: float | None) -> None:
    """Milliseconds per stage. render_ms is included in the stage it happened in, not added on top."""
    try:
        t = {f"{k}_ms": round(v) for k, v in scope["timings"].items()}
        t["total_ms"] = round((time.monotonic() - started) * 1000)
        if queued_at is not None:
            t["queue_ms"] = round((started - queued_at) * 1000)
        db.update("documents", doc_id, {"timings": t})
    except Exception:  # noqa: BLE001 - timing bookkeeping must never break processing
        log.exception("failed to record timings")


def _find_reusable(doc: dict, stamp: str) -> dict | None:
    """A finished document with the same file content that was processed while the templates were as they are now."""
    if not doc.get("file_hash"):
        return None
    rows = db.list_rows(
        "documents",
        "file_hash=? AND id!=? AND status='done' AND template_id IS NOT NULL AND template_stamp=? AND data IS NOT NULL",
        (doc["file_hash"], doc["id"], stamp), order="updated_at DESC")
    return rows[0] if rows else None


def _reuse(doc: dict, src: dict, stamp: str) -> bool:
    template = db.get("templates", src["template_id"])
    if not template:
        return False
    issues = validate.validate(doc["id"], template, src["data"], src["uncertain"])
    note = i18n.bi(f"复用相同文件“{src['filename']}”的结果，未调用 AI",
                   f"Reused the result of the identical file \"{src['filename']}\"; no AI call")
    db.update("documents", doc["id"], {
        "status": "done", "template_id": src["template_id"], "template_locked": src["template_locked"],
        "match_info": i18n.enc(note), "pages": src["pages"], "data": src["data"],
        "original_data": src["original_data"] or src["data"], "uncertain": src["uncertain"], "issues": issues,
        "notes": src["notes"], "corrections": [], "confirmed_flags": [],
        "model_used": src["model_used"], "verified": 0, "error": "",
        "exported_at": None, "template_stamp": stamp,
    })
    return True


def _process(doc_id: int, template_id: int | None = None, reuse: bool = False) -> None:
    doc = db.get("documents", doc_id)
    if not doc:
        return
    try:
        stamp = db.templates_stamp()
        if reuse and not template_id:
            src = _find_reusable(doc, stamp)
            if src and _reuse(doc, src, stamp):
                return
        preselected = None
        if template_id:
            template = db.get("templates", template_id)
            match_info = i18n.enc(MANUAL)
        elif doc["template_locked"] and doc["template_id"]:
            template = db.get("templates", doc["template_id"])
            match_info = doc["match_info"] or i18n.enc(MANUAL)
        else:
            db.update("documents", doc_id, {"status": "classifying"})
            with llm.stage("classify"):
                template, info, preselected = extractor.match_template(doc)
            match_info = i18n.enc(info)
        if not template:
            db.update("documents", doc_id, {
                "status": "unmatched", "template_id": None, "match_info": match_info,
                "data": None, "issues": [], "uncertain": [],
            })
            return

        db.update("documents", doc_id, {
            "status": "extracting", "template_id": template["id"], "match_info": match_info,
            "template_locked": 1 if template_id else doc["template_locked"],
        })
        with llm.stage("select_pages"):
            pages = extractor.select_pages(doc, template, preselected)
        with llm.stage("extract"):
            res = extractor.extract(doc, template, pages)
        with llm.stage("validate"):
            issues = validate.validate(doc_id, template, res["data"], res["uncertain"])
        db.update("documents", doc_id, {
            "status": "done", "pages": pages, "data": res["data"], "original_data": res["data"],
            "uncertain": res["uncertain"], "issues": issues, "notes": res["notes"],
            "corrections": [], "confirmed_flags": [],
            "model_used": res["model_used"], "verified": 0, "error": "",
            "exported_at": None, "template_stamp": stamp,
        })
    except llm.LLMError as e:
        db.update("documents", doc_id, {"status": "error", "error": i18n.enc(e.msg)})
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        log.error(traceback.format_exc())
        db.update("documents", doc_id, {"status": "error", "error": f"{type(e).__name__}: {e}"})
