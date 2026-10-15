"""Batch auto-templating: for the unmatched documents of a batch, work out which vendor issued each one, group them,
and create one template per vendor (or one shared generic template) for a field list the user supplies.

Two background jobs, polled by the UI:
  preview - identify the vendor of every unmatched document and group them (nothing is created yet);
  run     - create the templates for the groups the user confirmed and send the documents through extraction.
"""
import json
import re
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed, wait

from . import db, extractor, llm, pdf_utils, pipeline
from .i18n import bi, enc

MAX_FIELDS = 40
IDENT_THREADS = 8
MAX_IDENT_PAGES = 10
TEMPLATE_THREADS = 4
FIELD_TYPES = list(extractor.FIELD_TYPES)

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


# ------------------------------------------------------------------- jobs

def _new_job(kind: str, total: int, stage: dict) -> dict:
    # steps: the stages the UI lists, in order (state pending | running | done | skipped | error, done / total counts and
    # an optional detail line). items: the per-vendor progress inside the "templates" step.
    job = {"id": uuid.uuid4().hex[:12], "kind": kind, "state": "running", "stage": stage, "done": 0, "total": total,
           "steps": [], "items": [], "started": time.time(), "finished": None, "result": None, "error": None, "log": []}
    with _lock:
        _jobs[job["id"]] = job
        for old in list(_jobs)[:-30]:  # only the latest few jobs are kept
            _jobs.pop(old, None)
    return job


def get_job(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return json.loads(json.dumps(job)) if job else None


def _set(job: dict, **kw) -> None:
    with _lock:
        job.update(kw)


def _tick(job: dict, stage: dict | None = None) -> None:
    with _lock:
        job["done"] += 1
        if stage:
            job["stage"] = stage


def _define(job: dict, defs: list[tuple]) -> None:
    """defs: (key, zh label, en label, total)."""
    with _lock:
        job["steps"] = [{"key": k, "label": bi(zh, en), "state": "pending", "done": 0, "total": t, "detail": None}
                        for k, zh, en, t in defs]


def _step(job: dict, key: str, **kw) -> None:
    with _lock:
        for s in job["steps"]:
            if s["key"] == key:
                s.update(kw)


def _item(job: dict, idx: int, **kw) -> None:
    with _lock:
        job["items"][idx].update(kw)


def _fail(job: dict, e: Exception) -> None:
    msg = e.msg if isinstance(e, llm.LLMError) else bi(f"{type(e).__name__}: {e}", f"{type(e).__name__}: {e}")
    _set(job, state="error", finished=time.time(), error=msg)


# ---------------------------------------------------------------- helpers

def own_companies() -> list[str]:
    raw = db.get_settings().get("own_companies") or ""
    return [s.strip() for s in re.split(r"[\n,，;；]+", raw) if s.strip()]


def _own_hint(own: list[str]) -> str:
    return ("以下是我方公司（是付款方 / 收货方，永远不是供应商）：" + "、".join(own)) if own else ""


def _norm(s: str) -> str:
    return extractor._norm(s or "")


_SUFFIX = re.compile(r"\b(inc|llc|ltd|limited|co|corp|corporation|company|usa|us|gmbh|sa|bv|pte|plc)\b\.?", re.I)


def _vendor_key(name: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _SUFFIX.sub(" ", name.lower()))


# ------------------------------------------------ step 1: who issued it?

def identify_vendor(doc: dict, own: list[str]) -> dict:
    """Ask the fast model about the first pages: business document or not, and which company issued it."""
    provider, model = llm.resolve("claude", None, fast=True)
    path = doc["stored_path"]
    # A scanned bundle can have its invoice on the last pages (after waybills and certificates), so look at all of
    # them (small images / short text keep this cheap).
    pages = list(range(1, min(doc["page_count"], MAX_IDENT_PAGES) + 1))
    snippets = dict(pdf_utils.page_snippets(path, pages, 500))
    scan = [n for n in pages if not snippets[n]]
    images = dict(extractor._images(path, scan, dpi=60, max_side=900)) if scan else {}
    parts = []
    for n in pages:
        if n in images:
            parts += [("text", f"第 {n} 页"), ("image", images[n])]
        else:
            parts.append(("text", f"第 {n} 页（文字层开头）：{snippets[n]}"))
    parts.append(("text", f"""请判断这份文档（共 {doc['page_count']} 页，上面给出了{'全部' if doc['page_count'] <= MAX_IDENT_PAGES else '前 ' + str(MAX_IDENT_PAGES)} 页）：
- is_business_document：是不是需要抽取数据的业务单据（发票、账单、对账单、运单、费用通知等）。系统通知、日报、广告、空白页等填 false。
- vendor_name：开出这份文档的公司（卖方 / 承运商 / 服务商），照文档上的写法，只写公司名，不要地址。付款方、收货方、“Bill To”都不是供应商。
- doc_type：文档类型，几个字，如“发票”“滞箱费账单”。
- reason：一句话理由。
{_own_hint(own)}
这份文档可能是多份文档合订的扫描包（例如空运单、安检证明、发票装订在一起）。如果里面有发票或账单，vendor_name 以发票 / 账单的开票公司为准，
不要用运单、证明、报关单等附属单据上的公司（如航空公司、货代）。"""))
    schema = {
        "type": "object",
        "properties": {"is_business_document": {"type": "boolean"}, "vendor_name": {"type": "string"},
                       "doc_type": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["is_business_document", "vendor_name", "doc_type"],
    }
    res = llm.call_structured(provider, model, "你是单据分类助手。", parts, schema, max_tokens=600,
                              purpose="identify_vendor")
    return {"business": bool(res.get("is_business_document", True)), "vendor": (res.get("vendor_name") or "").strip(),
            "doc_type": (res.get("doc_type") or "").strip()}


def group_vendors(names: dict[str, int], templates: list[dict]) -> list[dict]:
    """Merge the spellings of one company and match them to existing templates: [{name, members, existing_id}]."""
    def fallback() -> list[dict]:
        merged: dict[str, dict] = {}
        for n in names:
            g = merged.setdefault(_vendor_key(n) or n, {"name": n, "members": [], "existing_id": None})
            g["members"].append(n)
        return list(merged.values())

    if not names or (len(names) == 1 and not templates):
        return fallback()
    provider, model = llm.resolve("claude", None, fast=True)
    catalog = "\n".join(f"- id={t['id']}，名称：{t['name']}，识别关键词：{', '.join(t['keywords']) or '无'}"
                        for t in templates) or "（还没有模板）"
    listing = "\n".join(f"- {n}（{c} 份）" for n, c in names.items())
    prompt = f"""下面是从一批文档里识别出的供应商名称（括号里是份数）：
{listing}

已有的模板（每个模板对应一家供应商）：
{catalog}

请把指同一家公司的名称合并成一组（例如带或不带 Inc / LLC、缩写与全称、同一公司的不同部门），不同公司不要合并。
每组给出：name（最规范的名称）、members（组里的原始名称，必须逐字取自上面的列表，每个名称只能出现在一组里）、
existing_template_id（如果这组明确就是某个已有模板对应的供应商，填该模板 id，否则 null；不确定就填 null）。"""
    schema = {
        "type": "object",
        "properties": {"groups": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "members": {"type": "array", "items": {"type": "string"}},
                           "existing_template_id": {"type": ["integer", "null"]}},
            "required": ["name", "members"]}}},
        "required": ["groups"],
    }
    try:
        res = llm.call_structured(provider, model, "你是数据整理助手。", [("text", prompt)], schema, max_tokens=4000,
                                  purpose="group_vendors")
    except llm.LLMError:
        return fallback()
    valid_ids = {t["id"] for t in templates}
    out, used = [], set()
    for g in res.get("groups", []):
        members = [m for m in g.get("members", []) if m in names and m not in used]
        if not members:
            continue
        used.update(members)
        tid = g.get("existing_template_id")
        out.append({"name": (g.get("name") or members[0]).strip() or members[0], "members": members,
                    "existing_id": tid if tid in valid_ids else None})
    for n in names:  # anything the model left out stands on its own
        if n not in used:
            out.append({"name": n, "members": [n], "existing_id": None})
    return out


def start_preview(batch_id: int) -> str:
    docs = db.list_rows("documents", "batch_id=? AND status='unmatched'", (batch_id,), order="id")
    if not docs:
        raise ValueError("no unmatched documents")
    job = _new_job("preview", len(docs) + 1, bi("正在识别每份文档的供应商…", "Identifying the vendor of each document…"))
    threading.Thread(target=_run_preview, args=(job, docs), daemon=True).start()
    return job["id"]


def _run_preview(job: dict, docs: list[dict]) -> None:
    try:
        own = own_companies()
        found: dict[int, dict] = {}
        errors: list = []
        _define(job, [("identify", "识别每份文档的供应商", "Identify the vendor of each document", len(docs)),
                      ("group", "合并同一家供应商，并和已有模板比对", "Merge each vendor's spellings and match existing templates", 0)])
        _step(job, "identify", state="running")

        def work(d: dict):
            with llm.usage_scope(d["id"], "auto_template"):
                return d["id"], identify_vendor(d, own)

        with ThreadPoolExecutor(IDENT_THREADS) as ex:
            futures = {ex.submit(work, d): d for d in docs}
            for f in as_completed(futures):
                try:
                    did, info = f.result()
                    found[did] = info
                except llm.LLMError as e:
                    errors.append(e.msg)
                    found[futures[f]["id"]] = {"business": True, "vendor": "", "doc_type": ""}
                _tick(job)
                seen = len({i["vendor"] for i in found.values() if i["business"] and i["vendor"]})
                _step(job, "identify", done=len(found), detail=bi(
                    f"已发现 {seen} 个供应商名称" + (f"，{len(errors)} 份识别失败" if errors else ""),
                    f"{seen} vendor names found so far" + (f", {len(errors)} failed" if errors else "")))
        if errors and len(errors) == len(docs):
            raise llm.LLMError(errors[0])
        _step(job, "identify", state="done", done=len(docs))

        _step(job, "group", state="running")
        names = Counter(i["vendor"] for i in found.values() if i["business"] and i["vendor"])
        with llm.usage_scope(None, "auto_template"):
            groups_raw = group_vendors(dict(names), db.list_rows("templates", order="id"))
        _step(job, "group", state="done", detail=bi(f"{len(names)} 个名称合并为 {len(groups_raw)} 家供应商",
                                                    f"{len(names)} names merged into {len(groups_raw)} vendors"))
        templates = {t["id"]: t for t in db.list_rows("templates")}
        by_member = {m: g for g in groups_raw for m in g["members"]}
        buckets: dict[str, dict] = {}

        def bucket(key: str, kind: str, name: str, existing_id=None) -> dict:
            return buckets.setdefault(key, {"kind": kind, "name": name, "existing_id": existing_id, "docs": []})

        for d in docs:
            info = found[d["id"]]
            if not info["business"]:
                b = bucket("skip", "skip", "")
            elif not info["vendor"]:
                b = bucket("unknown", "unknown", "")
            else:
                g = by_member[info["vendor"]]
                b = bucket("v:" + g["name"], "vendor", g["name"], g["existing_id"])
            b["docs"].append((d, info))

        groups = []
        for b in buckets.values():
            dl = [d for d, _ in b["docs"]]
            types = Counter(i["doc_type"] for _, i in b["docs"] if i["doc_type"])
            eid = b["existing_id"]
            if b["kind"] == "skip":
                action = "skip"
            elif b["kind"] == "unknown":
                action = "generic"
            elif eid:
                action = "existing"
            else:
                action = "new" if len(dl) >= 2 else "generic"
            groups.append({
                "name": b["name"], "kind": b["kind"], "action": action, "count": len(dl),
                "doc_ids": [d["id"] for d in dl], "files": [d["filename"] for d in dl[:3]],
                "scanned": sum(1 for d in dl if not d["has_text"]), "doc_type": types.most_common(1)[0][0] if types else "",
                "template_id": eid, "template_name": templates[eid]["name"] if eid else "",
            })
        groups.sort(key=lambda g: ({"vendor": 0, "unknown": 1, "skip": 2}[g["kind"]], -g["count"], g["name"]))
        for n, g in enumerate(groups):
            g["id"] = n
        _tick(job)
        _set(job, state="done", finished=time.time(), result={"groups": groups, "own_companies": own, "documents": len(docs),
                                        "failed": len(errors)})
    except Exception as e:  # noqa: BLE001 - shown in the UI
        _fail(job, e)


# ------------------------------------------------ step 2: the field list

def normalize_fields(raw: list[dict]) -> list[dict]:
    """[{label, description?, column?}] -> fields with an English key and a type, in the same order."""
    items, seen = [], set()
    for r in raw:
        label = str(r.get("label") or "").strip()
        if label and label not in seen:
            seen.add(label)
            items.append({"label": label, "description": str(r.get("description") or "").strip(),
                          "column": str(r.get("column") or "")})
    if not items:
        raise ValueError("no fields")
    if len(items) > MAX_FIELDS:
        raise ValueError(f"at most {MAX_FIELDS} fields")
    keys = types = None
    provider, model = llm.resolve("claude", None, fast=True)
    listing = "\n".join(f"{i + 1}. {it['label']}" + (f"（{it['description']}）" if it["description"] else "")
                        for i, it in enumerate(items))
    schema = {"type": "object", "properties": {"fields": {"type": "array", "items": {
        "type": "object", "properties": {"key": {"type": "string"}, "type": {"type": "string", "enum": FIELD_TYPES}},
        "required": ["key", "type"]}}}, "required": ["fields"]}
    try:
        res = llm.call_structured(
            provider, model, "你是数据建模助手。",
            [("text", "为下面每个字段生成英文 snake_case 的 key，并判断类型（text 文本 / number 数字 / money 金额 / "
                      "date 日期 / boolean 是否）。按原顺序，每个字段对应一项，数量必须一致。\n" + listing)],
            schema, max_tokens=2000, purpose="normalize_fields")
        got = res.get("fields", [])
        if len(got) == len(items):
            keys, types = [g.get("key") for g in got], [g.get("type") for g in got]
    except llm.LLMError:
        pass
    fields = [{"key": (keys[i] if keys else "") or f"field_{i + 1}", "label": it["label"],
               "type": types[i] if types else "text", "description": it["description"], "example": "",
               "required": False} for i, it in enumerate(items)]
    cleaned = extractor.clean_fields(fields)
    for f, it in zip(cleaned, items):
        f["column"] = it["column"]
    return cleaned


def _public(fields: list[dict]) -> list[dict]:
    return [{k: v for k, v in f.items() if k != "column"} for f in fields]


# ------------------------------------------- step 3: one template a vendor

def _sample_text(doc: dict) -> str:
    return pdf_utils.full_plain_text(doc["stored_path"]) if doc["has_text"] else ""


MIN_KEYWORD_CHARS = 5
# Words printed on nearly every document; a template keyed on one of these would claim other vendors' documents.
GENERIC_KEYWORDS = {"invoice", "invoices", "bill", "statement", "receipt", "order", "tax", "total", "amount", "date",
                    "payment", "customer", "service", "services", "freight", "logistics", "shipping", "company",
                    "发票", "账单", "对账单", "收据", "金额", "合计"}


def _usable_keywords(kws: list[str], text: str, own: list[str]) -> list[str]:
    """Keep keywords that really appear on the sample (when it has text), identify the vendor rather than the kind
    of document, and are not one of our own companies."""
    out = []
    for k in kws:
        nk = _norm(k)
        if len(nk) < MIN_KEYWORD_CHARS or nk in GENERIC_KEYWORDS or any(_norm(o) and _norm(o) in nk for o in own):
            continue
        if text.strip() and nk not in _norm(text):
            continue
        if k not in out:
            out.append(k)
    return out


def suggest_for_fields(doc: dict, fields: list[dict], vendor: str, own: list[str], lang: str) -> tuple[dict, list]:
    """A template draft for one vendor whose header fields are fixed. Returns (draft, warnings)."""
    en = lang == "en"
    lang_name = "英文" if en else "中文"
    name_example = "United Logistics Invoice" if en else "United Logistics 发票"
    provider, model = llm.resolve("claude", None)
    pages = list(range(1, min(doc["page_count"], 10) + 1))
    parts = extractor._doc_parts(doc["stored_path"], pages, True, 110, send_images=not doc["has_text"])
    spec = [{"key": f["key"], "label": f["label"], "type": f["type"], **({"hint": f["description"]} if f["description"] else {})}
            for f in fields]
    parts.append(("text", f"""请为供应商“{vendor}”的文档设计一个数据抽取模板。要抽取的字段已经定好了（key、名称、类型不能改），你来补充其余内容。
要求：
- name：模板名称，用“供应商名 + 文档类型”，用{lang_name}，例如“{name_example}”。
- keywords：2~4 个能唯一识别这家供应商文档的关键词（供应商公司名、地址、邮箱域名等），要求在文档上原样出现；不要用付款方 / 收货方等其他公司的名字。{_own_hint(own)}
- page_rule：如果 PDF 里混有多份不同文档（如运单、证明、发票合订），用一句{lang_name}说明应该只处理哪些页；只有一份文档则留空。
  必须按页面内容来描述（例如“只处理 XX 公司开出的 INVOICE 页，忽略运单和证明页”），绝对不要写具体页码，因为同一家供应商的其他文档里发票所在的页码会不一样。
- fields：对下面每个字段给出 description（在文档哪里、对应印刷的什么标签、格式特点，用{lang_name}）、example（本文档里的实际值，没有就留空）、required（这家文档上一定有的值填 true）。
- unique_field：最能唯一标识这份文档的字段 key（通常是发票号），必须是下面字段里的 key；没有合适的留空。
- 只设计表头字段，不要设计明细表。
要抽取的字段：
{json.dumps(spec, ensure_ascii=False)}"""))
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}},
            "page_rule": {"type": "string"}, "unique_field": {"type": "string"},
            "fields": {"type": "array", "items": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "description": {"type": "string"}, "example": {"type": "string"},
                               "required": {"type": "boolean"}},
                "required": ["key", "description"]}},
        },
        "required": ["name", "keywords", "page_rule", "fields"],
    }
    res = llm.call_structured(provider, model, "你是文档数据抽取模板设计专家。", parts, schema, max_tokens=6000,
                              purpose="suggest_template")
    by_key = {f.get("key"): f for f in res.get("fields", [])}
    header = []
    for f in fields:
        g = by_key.get(f["key"], {})
        header.append({"key": f["key"], "label": f["label"], "type": f["type"],
                       "description": f["description"] or str(g.get("description") or ""),
                       "example": str(g.get("example") or ""), "required": bool(g.get("required", False))})
    warnings = []
    text = _sample_text(doc)
    kws = _usable_keywords([str(k) for k in res.get("keywords", [])], text, own)
    if not kws:
        kws = _usable_keywords([vendor], text, own)
    if not kws:
        warnings.append(bi(f"“{vendor}”：没有找到能在文档上原样出现的识别关键词，请到模板里补充",
                           f"“{vendor}”: no detection keyword that appears on the document was found; add one in the template"))
    uf = res.get("unique_field") or ""
    return {"name": (res.get("name") or vendor).strip() or vendor, "keywords": kws, "page_rule": res.get("page_rule", ""),
            "header_fields": header, "item_fields": [], "items_label": "",
            "unique_field": uf if uf in {f["key"] for f in header} else "", "checks": []}, warnings


def suggest_keywords(doc: dict, vendor: str, own: list[str]) -> list[str]:
    provider, model = llm.resolve("claude", None, fast=True)
    parts = extractor._doc_parts(doc["stored_path"], list(range(1, min(doc["page_count"], 5) + 1)), True, 80,
                                 send_images=not doc["has_text"])
    parts.append(("text", f"请给出 2~4 个能识别供应商“{vendor}”的文档的关键词（公司名、地址、邮箱域名等），要求在文档上原样出现；"
                          f"不要用付款方 / 收货方等其他公司的名字。{_own_hint(own)}"))
    schema = {"type": "object", "properties": {"keywords": {"type": "array", "items": {"type": "string"}}},
              "required": ["keywords"]}
    res = llm.call_structured(provider, model, "你是文档分类助手。", parts, schema, max_tokens=500,
                              purpose="suggest_keywords")
    return _usable_keywords([str(k) for k in res.get("keywords", [])], _sample_text(doc), own)


def _template_values(draft: dict, **extra) -> dict:
    v = {"name": draft["name"], "keywords": draft["keywords"], "page_rule": draft.get("page_rule", ""),
         "header_fields": draft["header_fields"], "item_fields": [], "items_label": "",
         "unique_field": draft.get("unique_field", ""), "checks": [], "auto_generated": 1}
    v.update(extra)
    return v


def _generic_draft(fields: list[dict], batch: dict, lang: str) -> dict:
    header = [{**f, "description": f["description"] or f"文档上与“{f['label']}”含义对应的值（按文档上实际印刷的标签去找）"}
              for f in fields]
    uf = next((f["key"] for f in fields if re.search(r"invoice.*(no|num)|inv.*(no|num)", f["key"], re.I)), "")
    code = batch["code"]
    return {"name": f"通用模板 · {code}" if lang == "zh" else f"Generic template · {code}", "keywords": [],
            "page_rule": "", "header_fields": header, "unique_field": uf}


GENERIC_NOTE = ("这是通用模板：文档来自不同的供应商，请按每个字段名称的含义，在文档上找对应的值（字段说明里写了的以说明为准）；"
                "找不到就填空，不要猜。")


# ------------------------------------------------------------------ run

def start_run(batch_id: int, fields: list[dict], groups: list[dict], format_id: int | None, auto_model: bool,
              lang: str) -> str:
    todo = [g for g in groups if g["action"] in ("new", "existing", "generic") and g["doc_ids"]]
    if not todo:
        raise ValueError("nothing to do")
    job = _new_job("run", len(todo) + 1, bi("正在整理字段…", "Preparing the fields…"))
    threading.Thread(target=_run_run, args=(job, batch_id, fields, todo, format_id, auto_model, lang), daemon=True).start()
    return job["id"]


def _statuses(ids: list[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in db.list_rows("documents", f"id IN ({','.join('?' * len(chunk))})", tuple(chunk)):
            out[r["id"]] = r["status"]
    return out


def _run_run(job, batch_id, raw_fields, groups, format_id, auto_model, lang) -> None:
    try:
        expected = sum(len(g["doc_ids"]) for g in groups)
        _define(job, [
            ("fields", "整理字段", "Prepare the fields", 1),
            ("templates", "为各供应商生成模板", "Create the templates", len(groups)),
            ("extract", "文档抽取", "Extract the documents", expected),
            ("check", "检查识别关键词", "Check the detection keywords", 0)])
        nameless = bi("未能识别供应商的文档", "Documents with no vendor identified")
        for i, g in enumerate(groups):
            g["_i"] = i
        _set(job, items=[{"label": {"zh": g["name"], "en": g["name"]} if g["name"] else nameless, "action": g["action"],
                          "docs": len(g["doc_ids"]), "state": "queued", "detail": None} for g in groups])

        _step(job, "fields", state="running")
        with llm.usage_scope(None, "auto_template"):
            canon = normalize_fields(raw_fields)
        _step(job, "fields", state="done", done=1, detail=bi(f"{len(canon)} 个字段", f"{len(canon)} fields"))
        _step(job, "templates", state="running")

        own = own_companies()
        batch = db.get("batches", batch_id)
        warnings: list = []
        created, template_ids, sent = [], [], []
        lock = threading.Lock()
        groups_done = [0]

        def current_docs(g: dict) -> list[dict]:
            docs = [db.get("documents", i) for i in g["doc_ids"]]
            return [d for d in docs if d and d["batch_id"] == batch_id and d["status"] == "unmatched"]

        def send(docs: list[dict], tid: int) -> None:
            for d in docs:
                pipeline.submit(d["id"], tid)  # locks the template to the document
            with lock:
                sent.extend(d["id"] for d in docs)

        def group_finished() -> None:
            with lock:
                groups_done[0] += 1
                n = groups_done[0]
            _step(job, "templates", done=n)

        def refresh_extract() -> tuple[int, int, int]:
            """(finished, failed, sent) for the documents sent to extraction so far."""
            with lock:
                ids = list(sent)
            if not ids:
                return 0, 0, 0
            st = _statuses(ids)
            finished = sum(1 for s in st.values() if s in ("done", "error", "unmatched"))
            failed = sum(1 for s in st.values() if s in ("error", "unmatched"))
            _step(job, "extract", state="running", done=finished, detail=bi(
                f"已发送 {len(ids)} 份，{finished} 份完成" + (f"（{failed} 份出错）" if failed else ""),
                f"{len(ids)} sent, {finished} finished" + (f" ({failed} failed)" if failed else "")))
            return finished, failed, len(ids)

        def do_new(g: dict) -> None:
            i = g["_i"]
            docs = current_docs(g)
            if not docs:
                _item(job, i, state="done", detail=bi("这些文档已经不是“未识别”，已跳过", "These documents are no longer unmatched; skipped"))
                return
            sample = docs[0]
            _item(job, i, state="working", detail=bi("读取样本，设计模板（关键词、页面规则、字段说明）…",
                                                      "Reading the sample and designing the template (keywords, page rule, field notes)…"))
            with llm.usage_scope(sample["id"], "auto_template"):
                draft, warn = suggest_for_fields(sample, _public(canon), g["name"], own, lang)
                extra = {}
                if auto_model:
                    _item(job, i, detail=bi("自动选择模型（用样本逐个试抽取）…", "Picking the model (trial extractions on the sample)…"))
                    try:
                        pick = extractor.pick_model(sample, draft)
                        extra = {"provider": pick["provider"], "model": pick["model"], "model_note": enc(pick["model_note"])}
                    except llm.LLMError:
                        pass
            tid = db.insert("templates", _template_values(draft, **extra))
            send(docs, tid)
            with lock:
                warnings.extend(warn)
                template_ids.append(tid)
                created.append({"kind": "new", "group": g["name"], "template_id": tid, "template_name": draft["name"],
                                "docs": len(docs), "model": extra.get("model") or ""})
            _item(job, i, state="done", detail=bi(f"已建模板“{draft['name']}”，{len(docs)} 份文档送去抽取",
                                                  f"Template “{draft['name']}” created; {len(docs)} documents sent for extraction"))

        def do_existing(g: dict) -> None:
            i = g["_i"]
            docs = current_docs(g)
            tpl = db.get("templates", g.get("template_id") or 0)
            if not docs or not tpl:
                _item(job, i, state="done", detail=bi("没有需要处理的文档", "Nothing to process"))
                return
            sample = docs[0]
            added = []
            text = _sample_text(sample)
            if not (text.strip() and extractor.match_by_keywords(text, [tpl])[1] > 0):
                _item(job, i, state="working", detail=bi("为已有模板补充识别关键词…", "Adding detection keywords to the existing template…"))
                try:
                    with llm.usage_scope(sample["id"], "auto_template"):
                        kws = suggest_keywords(sample, g["name"] or tpl["name"], own)
                    have = {_norm(k) for k in tpl["keywords"]}
                    added = [k for k in kws if _norm(k) not in have]
                    if added:
                        db.update("templates", tpl["id"], {"keywords": tpl["keywords"] + added})
                except llm.LLMError:
                    pass
            send(docs, tpl["id"])
            with lock:
                template_ids.append(tpl["id"])
                created.append({"kind": "existing", "group": g["name"], "template_id": tpl["id"],
                                "template_name": tpl["name"], "docs": len(docs), "keywords_added": added})
            _item(job, i, state="done", detail=bi(f"使用已有模板“{tpl['name']}”，{len(docs)} 份文档送去抽取",
                                                  f"Using the existing template “{tpl['name']}”; {len(docs)} documents sent for extraction"))

        generic_groups = [g for g in groups if g["action"] == "generic"]
        if generic_groups:
            for g in generic_groups:
                _item(job, g["_i"], state="working", detail=bi("创建通用模板…", "Creating the generic template…"))
            draft = _generic_draft(_public(canon), batch, lang)
            generic_id = db.insert("templates", _template_values(draft, instructions=GENERIC_NOTE))
            template_ids.append(generic_id)
            n = 0
            for g in generic_groups:
                docs = current_docs(g)
                send(docs, generic_id)
                n += len(docs)
                _item(job, g["_i"], state="done", detail=bi(f"{len(docs)} 份文档送去抽取", f"{len(docs)} documents sent for extraction"))
                group_finished()
            created.append({"kind": "generic", "group": "", "template_id": generic_id, "template_name": draft["name"],
                            "docs": n})

        work = [g for g in groups if g["action"] in ("new", "existing")]
        with ThreadPoolExecutor(TEMPLATE_THREADS) as ex:
            futures = {ex.submit(do_new if g["action"] == "new" else do_existing, g): g for g in work}
            pending = set(futures)  # a group shows as "working" only once a worker picks it up
            while pending:
                finished, pending = wait(pending, timeout=1)
                for f in finished:
                    g = futures[f]
                    try:
                        f.result()
                    except Exception as e:  # noqa: BLE001 - one vendor failing must not stop the others
                        msg = e.msg if isinstance(e, llm.LLMError) else bi(str(e), str(e))
                        warnings.append(bi(f"“{g['name']}”没能生成模板：{msg['zh']}（这些文档仍是“未识别模板”）",
                                           f"“{g['name']}”: the template could not be created: {msg['en']} (those documents stay unmatched)"))
                        _item(job, g["_i"], state="error", detail=bi(f"失败：{msg['zh']}", f"Failed: {msg['en']}"))
                    group_finished()
                refresh_extract()  # extraction of the groups that are ready runs while the others are still being designed
        _step(job, "templates", state="done", done=len(groups))

        if format_id:
            _map_format(format_id, canon, template_ids)

        # Wait for the documents that were sent, so the user sees the real end of the work.
        with lock:
            n_sent = len(sent)
        if not n_sent:
            _step(job, "extract", state="skipped", total=0, detail=bi("没有文档需要抽取", "No documents to extract"))
            fin = bad = 0
        else:
            _step(job, "extract", total=n_sent)
            deadline = time.time() + 30 * 60
            while True:
                fin, bad, total_sent = refresh_extract()
                if fin >= total_sent or time.time() > deadline:
                    break
                time.sleep(1)
            _step(job, "extract", state="done", done=fin)

        _step(job, "check", state="running")
        # Would the keywords find these documents again next time? (Digital documents only; scans go by AI.)
        templates = db.list_rows("templates", order="id")
        total = hit = 0
        for c in created:
            if c["kind"] == "generic":
                continue
            for g in groups:
                if g["action"] in ("new", "existing") and g["name"] == c["group"]:
                    for did in g["doc_ids"]:
                        d = db.get("documents", did)
                        if d and d["has_text"]:
                            total += 1
                            m, _ = extractor.match_by_keywords(pdf_utils.full_plain_text(d["stored_path"]), templates)
                            hit += bool(m and m["id"] == c["template_id"])
        if total and hit < total:
            warnings.append(bi(
                f"{total} 份电子版文档里，有 {total - hit} 份按识别关键词匹配不到各自的模板。这次已强制使用，但下次上传同类文档时可能识别不到，请检查这些模板的关键词",
                f"Of {total} digital documents, {total - hit} would not be matched to their template by keywords. They were sent through with the template forced, "
                "but similar documents uploaded later may not be recognised; check these templates' keywords"))
        _step(job, "check", state="done" if total else "skipped", done=hit, total=total, detail=bi(
            f"{hit}/{total} 份电子版文档能按关键词匹配回各自的模板" if total else "没有电子版文档可检查",
            f"{hit}/{total} digital documents match their template by keywords" if total else "No digital documents to check"))
        _set(job, state="done", finished=time.time(), result={
            "created": created, "warnings": warnings, "documents": sum(c["docs"] for c in created),
            "extraction": {"finished": fin, "failed": bad, "sent": n_sent},
            "keyword_check": {"total": total, "matched": hit}})
    except Exception as e:  # noqa: BLE001
        with _lock:
            for s in job["steps"]:
                if s["state"] == "running":
                    s["state"] = "error"
        _fail(job, e)


def _map_format(format_id: int, canon: list[dict], template_ids: list[int]) -> None:
    """Point the output format's columns at the new templates' fields, so unified export works at once."""
    fmt = db.get("output_formats", format_id)
    if not fmt:
        return
    cols = fmt["columns"]
    maps = fmt.get("mappings") or {}
    for tid in template_ids:
        m = maps.setdefault(str(tid), {})
        for f in canon:
            if f.get("column") in cols:
                m[f["column"]] = f"header.{f['key']}"
        for c in cols:
            if c in ("来源文件", "Source File") and not m.get(c):
                m[c] = "meta.filename"
    db.update("output_formats", format_id, {"mappings": maps})
