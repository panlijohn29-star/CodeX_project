import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import autotemplate, config, db, export, extractor, i18n, ingest, llm, pdf_utils, pipeline, usage, validate
from .i18n import bi

app = FastAPI(title="PDF 字段抽取")
db.init_db()


@app.middleware("http")
async def no_cache_frontend(request, call_next):
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


USAGE_SOURCES = {"pipeline": bi("文档处理", "Processing"), "template_test": bi("模板试抽取", "Template test"),
                 "quick_extract": bi("快速抽取", "Quick extract"),
                 "template_suggest": bi("AI 推荐字段", "AI field suggestion"), "mapping": bi("AI 字段对应", "AI field mapping"),
                 "auto_template": bi("批量建模板", "Batch templating"), "other": bi("其他", "Other")}
USAGE_PURPOSES = {"classify": bi("识别模板", "Template detection"), "select_pages": bi("筛选页面", "Page filtering"),
                  "extract": bi("抽取字段", "Field extraction"), "suggest_template": bi("推荐字段", "Field suggestion"),
                  "suggest_mapping": bi("字段对应", "Field mapping"), "identify_vendor": bi("识别供应商", "Vendor identification"),
                  "group_vendors": bi("合并供应商", "Vendor grouping"), "normalize_fields": bi("整理字段", "Field preparation"),
                  "suggest_keywords": bi("补充关键词", "Keyword suggestion"), "": bi("其他", "Other")}

DOC, TPL, BATCH, FMT = bi("文档", "Document"), bi("模板", "Template"), bi("批次", "Batch"), bi("输出格式", "Output format")


def _usage_label(source: str, purpose: str) -> dict:
    s = USAGE_SOURCES.get(source) or bi(source, source)
    p = USAGE_PURPOSES.get(purpose) or bi(purpose, purpose)
    return bi(f"{s['zh']} · {p['zh']}", f"{s['en']} · {p['en']}")


def _404(row, what=bi("记录", "Record")):
    if not row:
        raise HTTPException(404, bi(f"{what['zh']}不存在", f"{what['en']} not found"))
    return row


# ------------------------------------------------------------------ settings

def _is_admin(request: Request) -> bool:
    # Set by the platform proxy from the logged-in user; it overwrites any client-supplied value.
    return request.headers.get("x-platform-admin") != "0"


def _model_options(settings: dict) -> dict:
    """The models an administrator switched on per vendor, newest first, for every model drop-down."""
    options = {}
    for provider in ("claude", "deepseek"):
        enabled = [i for i in (settings.get("enabled_models") or {}).get(provider, []) if isinstance(i, str)]
        known = {m["id"]: m for m in (settings.get("model_catalog") or {}).get(provider, []) if isinstance(m, dict)}
        ordered = [i for i in known if i in enabled] + [i for i in enabled if i not in known]
        options[provider] = [{"id": i, "name": known.get(i, {}).get("name") or i} for i in dict.fromkeys(ordered)]
    return options


@app.get("/api/settings")
def get_settings(request: Request):
    full = db.get_settings()
    # Other pages still need the field types, model presets and default model name; the rest is admin-only.
    if not _is_admin(request):
        return {"settings": {"claude_model": full.get("claude_model", "")}, "providers": {},
                "field_types": extractor.FIELD_TYPES, "model_presets": extractor.model_presets(),
                "model_options": _model_options(full)}
    return {"settings": full, "providers": llm.provider_status(), "field_types": extractor.FIELD_TYPES,
            "model_presets": extractor.model_presets(), "model_options": _model_options(full)}


@app.get("/api/models/{provider}")
def list_provider_models(provider: str, request: Request):
    if not _is_admin(request):
        raise HTTPException(403, bi("只有管理员可以获取模型列表", "Only administrators can fetch the model list"))
    if provider not in ("claude", "deepseek"):
        raise HTTPException(404, bi("不支持的服务商", "Unsupported provider"))
    try:
        return {"models": llm.list_models(provider)}
    except llm.LLMError as e:
        raise HTTPException(502, e.msg)


def _valid_id_lists(value) -> bool:
    return isinstance(value, dict) and all(
        k in ("claude", "deepseek") and isinstance(v, list) and all(isinstance(i, str) and i for i in v)
        for k, v in value.items())


def _valid_catalog(value) -> bool:
    return isinstance(value, dict) and all(
        k in ("claude", "deepseek") and isinstance(v, list)
        and all(isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"] for m in v)
        for k, v in value.items())


@app.put("/api/settings")
def put_settings(body: dict, request: Request):
    if not _is_admin(request):
        raise HTTPException(403, bi("只有管理员可以修改设置", "Only administrators can change settings"))
    if "enabled_models" in body and not _valid_id_lists(body["enabled_models"]):
        raise HTTPException(400, bi("可用模型列表无效", "The list of enabled models is invalid"))
    if "model_catalog" in body and not _valid_catalog(body["model_catalog"]):
        raise HTTPException(400, bi("模型目录无效", "The model catalog is invalid"))
    return {"settings": db.save_settings(body)}


# ----------------------------------------------------------------- templates

class Template(BaseModel):
    name: str
    keywords: list[str] = []
    page_rule: str = ""
    provider: str = "default"
    model: str = ""
    instructions: str = ""
    suggest_hint: str = ""
    send_images: bool = False
    model_note: str = ""
    header_fields: list[dict] = []
    item_fields: list[dict] = []
    items_label: str = ""
    unique_field: str = ""
    checks: list[dict] = []


def _template_values(t: Template) -> dict:
    v = t.model_dump()
    v["keywords"] = [k.strip() for k in v["keywords"] if k.strip()]
    v["header_fields"] = extractor.clean_fields(v["header_fields"])
    v["item_fields"] = extractor.clean_fields(v["item_fields"])
    if not v["item_fields"]:  # a template without an item table carries no item settings either
        v["items_label"] = ""
        v["checks"] = [c for c in v["checks"] if c.get("type") != "sum"]
    return v


@app.get("/api/templates")
def list_templates():
    rows = db.list_rows("templates", order="name")
    counts = {}
    for d in db.list_rows("documents"):
        if d["template_id"]:
            c = counts.setdefault(d["template_id"], {"docs": 0, "verified": 0})
            c["docs"] += 1
            c["verified"] += d["verified"]
    for r in rows:
        r.update(counts.get(r["id"], {"docs": 0, "verified": 0}))
    return rows


@app.get("/api/templates/{tid}")
def get_template(tid: int):
    return _404(db.get("templates", tid), TPL)


@app.post("/api/templates")
def create_template(t: Template):
    return db.get("templates", db.insert("templates", _template_values(t)))


@app.put("/api/templates/{tid}")
def update_template(tid: int, t: Template):
    old = _404(db.get("templates", tid), TPL)
    db.update("templates", tid, _template_values(t))
    tpl = db.get("templates", tid)
    # Issues are stored per document, so re-check the finished ones when the rules changed (e.g. a field made required).
    rule_keys = ("header_fields", "item_fields", "checks", "unique_field")
    if any(old.get(k) != tpl.get(k) for k in rule_keys):
        docs = [d for d in db.list_rows("documents", "template_id=? AND data IS NOT NULL", (tid,))]
        for d in docs:
            if d["status"] == "done":
                db.update("documents", d["id"], {"issues": validate.validate(
                    d["id"], tpl, d["data"], d.get("uncertain") or [], others=docs)})
    return tpl


@app.delete("/api/templates/{tid}")
def delete_template(tid: int):
    db.delete("templates", tid)
    for d in db.list_rows("documents", "template_id=?", (tid,)):
        db.update("documents", d["id"], {"template_id": None, "template_locked": 0, "status": "unmatched",
                                         "match_info": i18n.enc(bi("模板已删除", "Template deleted"))})
    return {"ok": True}


class SuggestReq(BaseModel):
    doc_id: int
    hint: str = ""
    auto_model: bool = True
    lang: str = "en"
    with_items: bool | None = None  # True: design the item table; None: let the AI decide
    suggest_model: str | None = None  # preset key of the model that designs the template (None: Claude Sonnet)
    with_header: bool | None = None  # False: no header fields (items only); None: let the AI decide


@app.get("/api/hints")
def list_hints():
    return {"hints": db.get_recent("hints")}


@app.delete("/api/hints")
def delete_hints(text: str | None = None):
    """Forget one remembered hint (?text=...) or all of them."""
    return {"hints": db.remove_recent("hints", text)}


class FieldListReq(BaseModel):
    text: str


@app.get("/api/field-lists")
def list_field_lists():
    return {"lists": db.get_recent("field_lists")}


@app.post("/api/field-lists")
def add_field_list(req: FieldListReq):
    return {"lists": db.add_recent("field_lists", req.text)}


@app.delete("/api/field-lists")
def delete_field_lists(text: str | None = None):
    return {"lists": db.remove_recent("field_lists", text)}


@app.post("/api/templates/suggest")
def suggest_template(req: SuggestReq):
    doc = _404(db.get("documents", req.doc_id), DOC)
    db.add_recent("hints", req.hint)  # remembered even if the AI call fails, so it can be reused or edited
    try:
        with llm.usage_scope(req.doc_id, "template_suggest"):
            draft = extractor.suggest_template(doc, req.hint, req.lang, req.with_items, req.with_header,
                                               req.suggest_model)
            if req.auto_model:
                try:
                    pick = extractor.pick_model(doc, draft)
                    draft.update(provider=pick["provider"], model=pick["model"], model_note=pick["model_note"])
                except llm.LLMError as e:
                    draft["model_note"] = i18n.enc(bi(f"自动选择模型失败，保持默认：{i18n.text(e.msg, 'zh')}",
                                                      f"Automatic model selection failed; keeping the default: "
                                                      f"{i18n.text(e.msg, 'en')}"))
            return draft
    except llm.LLMError as e:
        raise HTTPException(400, e.msg)


class PickModelReq(BaseModel):
    doc_id: int
    template: Template
    template_id: int | None = None


@app.post("/api/templates/pick-model")
def pick_model(req: PickModelReq):
    """Try the models from cheap to expensive on the sample document and report which one to keep."""
    doc = _404(db.get("documents", req.doc_id), DOC)
    tpl = _template_values(req.template)
    tpl["id"] = req.template_id or -1
    if not tpl["header_fields"] and not tpl["item_fields"]:
        raise HTTPException(400, bi("模板还没有字段", "The template has no fields yet"))
    try:
        with llm.usage_scope(req.doc_id, "template_test"):
            return extractor.pick_model(doc, tpl)
    except llm.LLMError as e:
        raise HTTPException(400, e.msg)


class TestReq(BaseModel):
    doc_id: int
    template: Template
    template_id: int | None = None


@app.post("/api/templates/test")
def test_template(req: TestReq):
    """Run extraction with an unsaved template definition; nothing is stored."""
    doc = _404(db.get("documents", req.doc_id), DOC)
    tpl = _template_values(req.template)
    tpl["id"] = req.template_id or -1
    try:
        with llm.usage_scope(req.doc_id, "template_test"):
            pages = extractor.select_pages(doc, tpl)
            res = extractor.extract(doc, tpl, pages)
    except llm.LLMError as e:
        raise HTTPException(400, e.msg)
    res["pages"] = pages
    res["issues"] = validate.validate(doc["id"], tpl, res["data"], res["uncertain"])
    return res


# ------------------------------------------------------------- quick extract

def _run_quick(run_id: int, tpl: dict) -> None:
    """Background worker: runs the extraction on a temporary document row and stores the outcome on the run."""
    run = db.get("quick_runs", run_id)
    did = None
    t0 = time.perf_counter()
    try:
        info = pdf_utils.inspect(run["stored_path"])
        did = db.insert("documents", {"filename": run["filename"], "stored_path": run["stored_path"],
                                      "page_count": info["page_count"], "has_text": int(info["has_text"]),
                                      "status": "quick"})
        doc = db.get("documents", did)
        with llm.usage_scope(None, "quick_extract"):
            pages = extractor.select_pages(doc, tpl)
            res = extractor.extract(doc, tpl, pages)
        res["pages"] = pages
        res["issues"] = validate.validate(did, tpl, res["data"], res["uncertain"])
        db.update("quick_runs", run_id, {"status": "done", "result": res, "error": "",
                                         "duration_ms": round((time.perf_counter() - t0) * 1000)})
    except llm.LLMError as e:
        db.update("quick_runs", run_id, {"status": "error", "error": i18n.enc(e.msg)})
    except Exception as e:  # never leave a run "running" forever
        db.update("quick_runs", run_id, {"status": "error", "error": str(e)})
    finally:
        if did:
            db.delete("documents", did)


@app.post("/api/quick-extract")
def quick_extract(file: UploadFile = File(...), header_fields: str = Form("[]"), item_fields: str = Form("[]"),
                  instructions: str = Form(""), provider: str = Form("default"), model: str = Form("")):
    """Start an extraction without a template. It runs in the background and is kept under /api/quick-runs."""
    try:
        h, i = json.loads(header_fields), json.loads(item_fields)
    except ValueError:
        raise HTTPException(400, bi("字段格式无效", "Invalid field list"))
    h, i = extractor.clean_fields(h), extractor.clean_fields(i)
    if not h and not i:
        raise HTTPException(400, bi("请至少添加一个字段", "Add at least one field"))
    try:
        converted = ingest.convert(file.filename or "", file.file.read())
    except ingest.IngestError as e:
        raise HTTPException(400, e.msg)
    it = converted[0]
    path = config.UPLOAD_DIR / f"quick_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.pdf"
    path.write_bytes(it.pdf)
    try:
        pages = pdf_utils.inspect(str(path))["page_count"]
    except Exception:
        path.unlink(missing_ok=True)
        raise HTTPException(400, bi(f"无法打开 PDF：{it.filename}", f"Cannot open PDF: {it.filename}"))
    run_id = db.insert("quick_runs", {"filename": it.filename, "stored_path": str(path), "page_count": pages,
                                      "header_fields": h, "item_fields": i, "instructions": instructions.strip(),
                                      "provider": provider or "default", "model": model})
    tpl = {"id": -1, "name": it.filename, "keywords": [], "page_rule": "", "provider": provider or "default",
           "model": model, "instructions": instructions.strip(), "send_images": False, "header_fields": h,
           "item_fields": i, "items_label": "", "unique_field": "", "checks": []}
    threading.Thread(target=_run_quick, args=(run_id, tpl), daemon=True).start()
    return {"id": run_id}


def _quick_summary(r: dict) -> dict:
    res = r.get("result") or {}
    return {"id": r["id"], "filename": r["filename"], "page_count": r["page_count"], "status": r["status"],
            "error": r["error"], "model": (res.get("model_used") or r["model"] or ""), "duration_ms": r["duration_ms"],
            "created_at": r["created_at"], "rows": len((res.get("data") or {}).get("items") or []),
            "fields": [f["label"] for f in r["header_fields"] + r["item_fields"]]}


@app.get("/api/quick-runs")
def list_quick_runs():
    return [_quick_summary(r) for r in db.list_rows("quick_runs")]


@app.get("/api/quick-runs/{rid}")
def get_quick_run(rid: int):
    r = _404(db.get("quick_runs", rid), bi("快速抽取记录", "Quick extraction"))
    r.pop("stored_path", None)
    return r


@app.get("/api/quick-runs/{rid}/file")
def get_quick_run_file(rid: int):
    r = _404(db.get("quick_runs", rid), bi("快速抽取记录", "Quick extraction"))
    return FileResponse(r["stored_path"], media_type="application/pdf",
                        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(r['filename'])}.pdf"})


@app.delete("/api/quick-runs/{rid}")
def delete_quick_run(rid: int):
    r = _404(db.get("quick_runs", rid), bi("快速抽取记录", "Quick extraction"))
    db.delete("quick_runs", rid)
    try:
        Path(r["stored_path"]).unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True}

# ----------------------------------------------------------------- documents

def _batch_labels() -> dict:
    return {b["id"]: db.batch_label(b) for b in db.list_rows("batches")}


def _pipeline_usage(key: str) -> dict:
    """Token/cost totals of the automatic processing, grouped by doc_id or batch_id."""
    rows = [r for r in usage.load_rows("source='pipeline'") if r[key] is not None]
    return usage.group(rows, lambda r: r[key])


def _doc_summary(d: dict, names: dict, batches: dict, doc_usage: dict) -> dict:
    issues = d.get("issues") or []
    return {
        "usage": doc_usage.get(d["id"]),
        "duration_ms": (d.get("timings") or {}).get("total_ms"),
        "id": d["id"], "filename": d["filename"], "page_count": d["page_count"], "has_text": d["has_text"],
        "batch_id": d["batch_id"], "batch": batches.get(d["batch_id"], ""), "exported_at": d["exported_at"],
        "template_id": d["template_id"], "template_name": names.get(d["template_id"], ""),
        "status": d["status"], "error": d["error"], "match_info": d["match_info"], "verified": d["verified"],
        "errors": sum(1 for i in issues if i["level"] == "error"),
        "warnings": sum(1 for i in issues if i["level"] == "warning"),
        "items": len((d.get("data") or {}).get("items", [])),
        "created_at": d["created_at"], "updated_at": d["updated_at"],
    }


@app.get("/api/documents")
def list_documents():
    names = {t["id"]: t["name"] for t in db.list_rows("templates")}
    batches = _batch_labels()
    doc_usage = _pipeline_usage("doc_id")
    tpls = {t["id"]: t for t in db.list_rows("templates")}
    out = []
    for d in db.list_rows("documents"):
        s = _doc_summary(d, names, batches, doc_usage)
        c = validate.confidence(d, tpls.get(d["template_id"]))
        s["confidence"] = c
        out.append(s)
    return out


class FieldSpec(BaseModel):
    label: str
    description: str = ""
    column: str = ""  # the output-format column this field came from, if any


class AutoGroup(BaseModel):
    name: str = ""
    doc_ids: list[int]
    action: str  # new | existing | generic | skip
    template_id: int | None = None


class AutoRunReq(BaseModel):
    fields: list[FieldSpec]
    groups: list[AutoGroup]
    format_id: int | None = None
    auto_model: bool = True
    lang: str = "en"


@app.post("/api/batches/{bid}/auto-template/preview")
def auto_template_preview(bid: int):
    """Start a background job that works out the vendor of every unmatched document in the batch."""
    _404(db.get("batches", bid), BATCH)
    try:
        return {"job_id": autotemplate.start_preview(bid)}
    except ValueError:
        raise HTTPException(400, bi("这个批次里没有“未识别模板”的文档", "This batch has no documents without a template"))


@app.post("/api/batches/{bid}/auto-template/run")
def auto_template_run(bid: int, req: AutoRunReq):
    """Create templates for the confirmed groups and send their documents through extraction (background job)."""
    _404(db.get("batches", bid), BATCH)
    if not req.fields:
        raise HTTPException(400, bi("请先定义要抽取的字段", "Define the fields to extract first"))
    if len(req.fields) > autotemplate.MAX_FIELDS:
        raise HTTPException(400, bi(f"字段最多 {autotemplate.MAX_FIELDS} 个", f"At most {autotemplate.MAX_FIELDS} fields"))
    for g in req.groups:
        if g.action == "existing" and not db.get("templates", g.template_id or 0):
            raise HTTPException(400, bi(f"“{g.name}”选择了已有模板，但模板不存在", f"“{g.name}” uses an existing template that does not exist"))
    try:
        job = autotemplate.start_run(bid, [f.model_dump() for f in req.fields], [g.model_dump() for g in req.groups],
                                     req.format_id, req.auto_model, req.lang)
    except ValueError:
        raise HTTPException(400, bi("没有需要处理的分组", "No group to process"))
    return {"job_id": job}


@app.get("/api/auto-template/jobs/{job_id}")
def auto_template_job(job_id: str):
    job = autotemplate.get_job(job_id)
    if not job:
        raise HTTPException(404, bi("任务不存在（服务可能重启过）", "Job not found (the server may have restarted)"))
    return job


@app.get("/api/batches")
def list_batches():
    stats: dict[int, dict] = {}
    for d in db.list_rows("documents"):
        s = stats.setdefault(d["batch_id"], {"docs": 0, "done": 0, "error": 0, "exported": 0})
        s["docs"] += 1
        s["done"] += d["status"] == "done"
        s["error"] += d["status"] in ("error", "unmatched")
        s["exported"] += bool(d["exported_at"])
    empty = {"docs": 0, "done": 0, "error": 0, "exported": 0}
    batch_usage = _pipeline_usage("batch_id")
    return [{"id": b["id"], "code": b["code"], "label": db.batch_label(b), "legacy": b["legacy"],
             "created_at": b["created_at"], "usage": batch_usage.get(b["id"]), **stats.get(b["id"], empty)}
            for b in db.list_rows("batches", order="created_at DESC, id DESC")]


@app.post("/api/batches")
def create_batch():
    """Create an empty batch (number assigned by the system) to upload into later."""
    b = db.get("batches", db.create_batch())
    return {"id": b["id"], "code": b["code"], "label": db.batch_label(b)}


@app.delete("/api/batches/{bid}")
def delete_batch(bid: int):
    _404(db.get("batches", bid), BATCH)
    if db.list_rows("documents", "batch_id=?", (bid,)):
        raise HTTPException(409, bi("批次里还有文档，不能删除", "The batch still has documents and cannot be deleted"))
    db.delete("batches", bid)
    return {"ok": True}


@app.post("/api/documents")
async def upload_documents(files: list[UploadFile] = File(...), auto: bool = True, batch_id: int | None = None):
    # Everything is converted to PDF and checked first, so a bad file rejects the whole upload and nothing is half-saved.
    items: list[ingest.Item] = []
    for f in files:
        name = f.filename or ""
        try:
            converted = ingest.convert(name, f.file.read())
        except ingest.IngestError as e:
            raise HTTPException(400, e.msg)
        for it in converted:
            try:
                with pymupdf.open(stream=it.pdf, filetype="pdf") as d:
                    if d.page_count < 1:
                        raise ValueError("no pages")
            except Exception:
                raise HTTPException(400, bi(f"无法打开 PDF：{it.filename}", f"Cannot open PDF: {it.filename}"))
        items += converted
    if batch_id is not None:
        _404(db.get("batches", batch_id), BATCH)
    created = batch_id is None
    if created:
        batch_id = db.create_batch()
    ids = []
    try:
        for n, it in enumerate(items):
            stamp = datetime.now().strftime("%Y%m%d%H%M%S%f") + f"{n:03d}"
            safe = re.sub(r'[<>:"/\\|?*]', "_", it.filename)
            path = config.UPLOAD_DIR / f"{stamp}_{safe}{'' if safe.lower().endswith('.pdf') else '.pdf'}"
            path.write_bytes(it.pdf)
            info = pdf_utils.inspect(str(path))
            did = db.insert("documents", {
                "filename": it.filename, "stored_path": str(path), "batch_id": batch_id,
                "page_count": info["page_count"], "has_text": int(info["has_text"]),
                "file_hash": it.digest, "source": i18n.enc(it.source) if it.source else "",
                "status": "pending" if auto else "uploaded",
            })
            ids.append(did)
            if auto:
                pipeline.submit(did, reuse=True)
    except Exception:
        if created and not ids:
            db.delete("batches", batch_id)
        raise
    return {"ids": ids, "batch_id": batch_id}


@app.get("/api/documents/{did}")
def get_document(did: int):
    d = _404(db.get("documents", did), DOC)
    tpl = db.get("templates", d["template_id"]) if d["template_id"] else None
    return {**d, "confidence": validate.confidence(d, tpl)}


@app.get("/api/documents/{did}/file")
def get_document_file(did: int):
    d = _404(db.get("documents", did), DOC)
    name = d["filename"] if d["filename"].lower().endswith(".pdf") else d["filename"] + ".pdf"  # converted uploads
    return FileResponse(d["stored_path"], media_type="application/pdf",
                        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"})


_PAGE_TEXT_CACHE: dict = {}


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


@app.get("/api/documents/{did}/locate")
def locate_in_document(did: int, path: str):
    """Which PDF page a result cell (items.N.key / header.key) was read from, found by looking for the row's values
    in the page text. Returns {"page": n | null}."""
    d = _404(db.get("documents", did), DOC)
    data = d.get("data") or {}
    m = re.fullmatch(r"items\.(\d+)\.(.+)", path)
    if m:
        rows = data.get("items") or []
        row = rows[int(m.group(1))] if int(m.group(1)) < len(rows) else {}
        values = list(row.values())
    else:
        h = re.fullmatch(r"header\.(.+)", path)
        values = [(data.get("header") or {}).get(h.group(1))] if h else []
    needles = [_norm_text(str(v)) for v in values if v not in (None, "") and len(_norm_text(str(v))) >= 2]
    if not needles:
        return {"page": None}
    key = (d["stored_path"], Path(d["stored_path"]).stat().st_mtime)
    if key not in _PAGE_TEXT_CACHE:
        if len(_PAGE_TEXT_CACHE) > 8:
            _PAGE_TEXT_CACHE.clear()
        _PAGE_TEXT_CACHE[key] = [(n, _norm_text(t)) for n, t in
                                 pdf_utils.page_texts(d["stored_path"], list(range(1, d["page_count"] + 1)))]
    best, best_score = None, 0
    for n, text in _PAGE_TEXT_CACHE[key]:
        score = sum(len(x) for x in needles if x in text)
        if score > best_score:
            best, best_score = n, score
    return {"page": best}


@app.get("/api/documents/{did}/usage")
def get_document_usage(did: int):
    _404(db.get("documents", did), DOC)
    rows = usage.load_rows("doc_id=?", (did,))
    for r in rows:
        r["label"] = _usage_label(r["source"], r["purpose"])
    return {"calls": rows, "total": usage.total([r for r in rows if r["source"] == "pipeline"])}


@app.get("/api/usage")
def usage_report(days: int = 0, batch_id: int | None = None):
    """Usage overview. `days` limits to the last N days (0 = all time)."""
    where, params = [], []
    if days > 0:
        where.append("created_at >= ?")
        params.append((datetime.now() - timedelta(days=days)).isoformat(timespec="seconds"))
    if batch_id is not None:
        where.append("batch_id = ?")
        params.append(batch_id)
    rows = usage.load_rows(" AND ".join(where), tuple(params))
    auto = [r for r in rows if r["source"] == "pipeline"]
    docs = {d["id"]: d for d in db.list_rows("documents")}
    templates = {t["id"]: t["name"] for t in db.list_rows("templates")}
    batches = _batch_labels()
    doc_ids = {r["doc_id"] for r in auto if r["doc_id"]}
    n_docs = len(doc_ids)
    auto_total = usage.total(auto)
    doc_ms = [m for m in ((docs[i].get("timings") or {}).get("total_ms") for i in doc_ids if i in docs) if m]

    def listed(grouped: dict, label) -> list:
        return sorted(({"key": str(k), "label": label(k), **v} for k, v in grouped.items()),
                      key=lambda x: x["cost"], reverse=True)

    purposes = usage.group(rows, lambda r: (r["source"], r["purpose"]))
    return {
        "total": usage.total(rows),
        "auto": auto_total,
        "docs": n_docs,
        "avg_cost_per_doc": round(auto_total["cost"] / n_docs, 6) if n_docs else None,
        "avg_tokens_per_doc": round(auto_total["tokens"] / n_docs) if n_docs else None,
        "avg_ms_per_doc": round(sum(doc_ms) / len(doc_ms)) if doc_ms else None,
        "by_day": sorted(({"key": k, "label": k, **v} for k, v in usage.group(rows, lambda r: r["created_at"][:10]).items()),
                         key=lambda x: x["key"], reverse=True),
        "by_model": listed(usage.group(rows, lambda r: f"{r['provider']}/{r['model']}"), lambda k: k),
        "by_purpose": listed(purposes, lambda k: _usage_label(*k)),
        "by_batch": listed(usage.group([r for r in auto if r["batch_id"] is not None], lambda r: r["batch_id"]),
                           lambda k: batches.get(k) or bi(f"已删除批次 {k}", f"Deleted batch {k}")),
        "by_template": listed(
            usage.group(auto, lambda r: templates.get((docs.get(r["doc_id"]) or {}).get("template_id"))),
            lambda k: k if k is not None else bi("未匹配/已删除", "Unmatched / deleted")),
    }


class ProcessReq(BaseModel):
    template_id: int | None = None


@app.post("/api/documents/{did}/process")
def process_document(did: int, req: ProcessReq):
    d = _404(db.get("documents", did), DOC)
    if req.template_id is None:
        db.update("documents", did, {"template_locked": 0})
    elif not db.get("templates", req.template_id):
        raise HTTPException(404, bi("模板不存在", "Template not found"))
    if d["status"] in ("pending", "classifying", "extracting"):
        raise HTTPException(409, bi("正在处理中", "Already processing"))
    pipeline.submit(did, req.template_id)
    return {"ok": True}


class SaveReq(BaseModel):
    data: dict
    verified: bool = True
    reviewed: list[str] = []  # flagged cells the person looked at and left as they were


@app.put("/api/documents/{did}/result")
def save_result(did: int, req: SaveReq):
    d = _404(db.get("documents", did), DOC)
    tpl = _404(db.get("templates", d["template_id"]) if d["template_id"] else None, TPL)
    data = extractor.normalize(req.data, tpl)
    # Values the user edited are no longer "uncertain".
    uncertain = d.get("uncertain") or []
    if req.verified:
        uncertain = []
    issues = validate.validate(did, tpl, data, uncertain)
    values = {"data": data, "issues": issues, "uncertain": uncertain,
              "verified": int(req.verified), "exported_at": None}
    if req.verified:
        # Keep what the person fixed and what they checked and left alone; later extractions learn from the first.
        corrections = extractor.diff_corrections(d.get("original_data"), data, tpl)
        fixed = {c["path"] for c in corrections}
        confirmed = sorted(set(d.get("confirmed_flags") or []) | set(req.reviewed)) 
        values.update(corrections=corrections, confirmed_flags=[p for p in confirmed if p not in fixed])
    db.update("documents", did, values)
    return db.get("documents", did)


@app.delete("/api/documents/{did}")
def delete_document(did: int):
    d = _404(db.get("documents", did), DOC)
    db.delete("documents", did)
    try:
        Path(d["stored_path"]).unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True}


# ------------------------------------------------------------ output formats

class OutputFormat(BaseModel):
    name: str
    columns: list[str] = []
    mappings: dict = {}
    allocate: dict = {}


@app.get("/api/output-formats")
def list_formats():
    return db.list_rows("output_formats", order="name")


@app.get("/api/output-formats/{fid}")
def get_format(fid: int):
    return _404(db.get("output_formats", fid), FMT)


@app.post("/api/output-formats")
def create_format(f: OutputFormat):
    v = f.model_dump()
    v["columns"] = [c.strip() for c in v["columns"] if c.strip()]
    return db.get("output_formats", db.insert("output_formats", v))


@app.put("/api/output-formats/{fid}")
def update_format(fid: int, f: OutputFormat):
    _404(db.get("output_formats", fid), FMT)
    v = f.model_dump()
    v["columns"] = [c.strip() for c in v["columns"] if c.strip()]
    db.update("output_formats", fid, v)
    return db.get("output_formats", fid)


@app.delete("/api/output-formats/{fid}")
def delete_format(fid: int):
    db.delete("output_formats", fid)
    return {"ok": True}


class MapReq(BaseModel):
    columns: list[str]
    template_id: int


@app.post("/api/output-formats/suggest-mapping")
def suggest_mapping(req: MapReq):
    tpl = _404(db.get("templates", req.template_id), TPL)
    try:
        with llm.usage_scope(None, "mapping"):
            return extractor.suggest_mapping(req.columns, tpl)
    except llm.LLMError as e:
        raise HTTPException(400, e.msg)


# -------------------------------------------------------------------- export

class ExportReq(BaseModel):
    doc_ids: list[int]
    mode: str = "per_template"  # per_template | unified
    format_id: int | None = None
    file_type: str = "xlsx"  # xlsx | csv
    lang: str = "en"  # language of the columns and notes the export adds


@app.post("/api/export")
def do_export(req: ExportReq):
    docs = [d for d in (db.get("documents", i) for i in req.doc_ids)
            if d and d["status"] == "done" and d["template_id"]]
    if not docs:
        raise HTTPException(400, bi("没有可导出的文档（只有“已完成”的文档可以导出）",
                                    "No documents to export (only finished documents can be exported)"))
    batch_codes = {b["id"]: b["code"] for b in db.list_rows("batches")}
    for d in docs:
        d["batch_code"] = batch_codes.get(d["batch_id"], "")
    if req.mode == "unified":
        fmt = _404(db.get("output_formats", req.format_id) if req.format_id else None, FMT)
        tables = export.unified_table(docs, fmt, req.lang)
        base = fmt["name"]
    else:
        tables = export.per_template_tables(docs, req.lang)
        base = "抽取结果" if req.lang == "zh" else "Extraction_Results"
    used_batches = {d["batch_code"] for d in docs if d["batch_code"]}
    if len(used_batches) == 1:
        base += f"_{next(iter(used_batches))}"
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    if req.file_type == "csv":
        content, ext = export.to_csv(tables)
        media = "text/csv" if ext == "csv" else "application/zip"
    else:
        content, ext = export.to_xlsx(tables), "xlsx"
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    name = f"{base}_{stamp}.{ext}"
    exported_at = db.now()
    for d in docs:
        db.update("documents", d["id"], {"exported_at": exported_at})
    return Response(content, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


# ------------------------------------------------------------------ frontend

app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")


@app.get("/")
def index(request: Request):
    # The platform serves this app under a URL prefix (the ASGI root_path); point the page's assets and API calls at it.
    base = request.scope.get("root_path", "").rstrip("/")
    page = (config.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    page = page.replace('"/static/', f'"{base}/static/')
    admin = json.dumps(_is_admin(request))
    page = page.replace("</head>", f"<script>window.__PDF_EXTRACT_BASE={json.dumps(base)};"
                                   f"window.__PDF_EXTRACT_ADMIN={admin};</script></head>", 1)
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})
