"""Template matching, page selection, extraction and AI-assisted template/mapping suggestions."""
import json
import re

from . import db, i18n, llm, pdf_utils, validate
from .i18n import bi

FIELD_TYPES = {
    "text": bi("文本", "Text"),
    "number": bi("数字", "Number"),
    "money": bi("金额", "Amount"),
    "date": bi("日期", "Date"),
    "boolean": bi("是/否", "Yes/No"),
}

EXTRACT_SYSTEM = """你是一个严谨的文档数据抽取引擎。根据用户定义的字段，从给定的文档页面中抽取数据。
规则：
1. 只抽取文档上真实存在的内容，绝不猜测或编造；找不到的字段填 null。
2. 日期统一输出为 YYYY-MM-DD；金额和数字输出为纯数字（去掉货币符号、千分位逗号），负数用负号。
3. 单号、编号等标识符按原样逐字抄写（保留连字符、斜杠、前导零）。如果原件中的值本身被截断（例如以 "..." 结尾）或看不全，按看到的原样填写，并把该字段路径加入 uncertain。
4. 明细表：每一条业务明细输出一行，按文档中的先后顺序；不要把合计/小计/表头行当作明细；跨页的表格要合并成一个列表；一行内容换行显示时合并为同一行。
5. 手写、模糊、需要推断、或与其他信息矛盾的值，也要把字段路径加入 uncertain。路径格式：header.<字段key> 或 items.<行号>.<字段key>（行号从 0 开始）。
6. 如果同时提供了页面图片和文字层，以图片中的版面为准判断每个值属于哪一列，用文字层校对字符。
7. 表格字段严格按其对应的列（表头标签）取值。字段说明里的“示例”和“通常为…”只是参考，同一供应商不同文档的列内容可能不同；不要因为内容与示例不像就把别的列的值挪过来或调换列（除非字段说明明确写了可以从多个位置取值）。"""


def _json_type(ftype: str) -> dict:
    if ftype in ("number", "money"):
        return {"type": ["number", "null"]}
    if ftype == "boolean":
        return {"type": ["boolean", "null"]}
    return {"type": ["string", "null"]}


def _field_desc(f: dict) -> str:
    desc = f.get("label") or f["key"]
    if f.get("description"):
        desc += "：" + f["description"]
    if f.get("type") == "date":
        desc += "（输出 YYYY-MM-DD）"
    if f.get("example"):
        desc += f"（示例：{f['example']}）"
    return desc


def build_schema(template: dict) -> dict:
    header_props = {}
    for f in template["header_fields"]:
        header_props[f["key"]] = {**_json_type(f.get("type", "text")), "description": _field_desc(f)}
    props = {
        "header": {
            "type": "object",
            "description": "表头字段（整份文档只出现一次的值）",
            "properties": header_props,
            "required": list(header_props),
        }
    }
    required = ["header"]
    if template["item_fields"]:
        item_props = {}
        for f in template["item_fields"]:
            item_props[f["key"]] = {**_json_type(f.get("type", "text")), "description": _field_desc(f)}
        props["items"] = {
            "type": "array",
            "description": "明细表的每一行" + (f"（{template['items_label']}）" if template.get("items_label") else ""),
            "items": {"type": "object", "properties": item_props, "required": list(item_props)},
        }
        required.append("items")
    props["uncertain"] = {
        "type": "array",
        "items": {"type": "string"},
        "description": "需要人工确认的字段路径，如 header.invoice_no、items.2.amount",
    }
    props["notes"] = {"type": "string", "description": "可选：抽取时需要提醒用户注意的问题，没有则留空"}
    required.append("uncertain")
    return {"type": "object", "properties": props, "required": required}


SNIPPET_CHARS = 600  # page-selection prompts get this much text from pages that have a text layer


def _images(path: str, pages: list[int], **kw) -> list[tuple[int, bytes]]:
    with llm.stage("render"):
        return pdf_utils.page_images(path, pages, gray=bool(db.get_settings().get("image_grayscale")), **kw)


def _doc_parts(path: str, pages: list[int], vision: bool, dpi: int, send_images: bool = True) -> list:
    """Pages with a text layer are sent as text only unless send_images is set; pages without one need the image."""
    parts = []
    with llm.stage("render"):
        texts = dict(pdf_utils.page_texts(path, pages))
    want = [n for n in pages if vision and (send_images or not texts.get(n))]
    images = dict(_images(path, want, dpi=dpi)) if want else {}
    for n in pages:
        parts.append(("text", f"===== 第 {n} 页 ====="))
        if n in images:
            parts.append(("image", images[n]))
        if texts.get(n):
            parts.append(("text", f"【第 {n} 页文字层（按版面位置排版）】\n{texts[n]}"))
        elif n not in images:
            parts.append(("text", f"【第 {n} 页没有文字层】"))
    return parts


def _examples(template_id: int, exclude_doc: int | None, limit: int) -> list[dict]:
    if limit <= 0:
        return []
    rows = db.list_rows(
        "documents", "template_id=? AND verified=1 AND id!=?",
        (template_id, exclude_doc or -1), order="updated_at DESC",
    )
    return [r["data"] for r in rows[:limit] if r.get("data")]


# ---------------------------------------------------------------- matching

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", s.lower())


def match_by_keywords(text: str, templates: list[dict]) -> tuple[dict | None, int]:
    t = _norm(text)
    best, best_score = None, 0
    for tpl in templates:
        score = sum(1 for k in tpl["keywords"] if k.strip() and _norm(k) and _norm(k) in t)
        if score > best_score:
            best, best_score = tpl, score
    return best, best_score


def match_template(doc: dict) -> tuple[dict | None, dict, list[int] | None]:
    """Returns (template, match_info, pages). match_info is a bilingual message. pages is only set when
    the AI already chose them in the same call (scanned documents); None means select_pages still has to run."""
    templates = db.list_rows("templates", order="id")
    if not templates:
        return None, bi("还没有任何模板", "No templates yet"), None
    text = pdf_utils.full_plain_text(doc["stored_path"])
    if text.strip():
        tpl, score = match_by_keywords(text, templates)
        if tpl:
            return tpl, bi(f"关键词匹配（命中 {score} 个）", f"Keyword match ({score} hit{'s' if score != 1 else ''})"), None
        if doc["has_text"]:
            return None, bi("文字层中没有匹配的关键词（电子版文档不再调用 AI 识别）",
                            "No keywords matched in the text layer (AI detection is skipped for digital documents)"), None

    provider, model = llm.resolve("claude", None, fast=True)
    if not llm.supports_vision(provider):
        return None, bi("文字层中没有匹配的关键词", "No keywords matched in the text layer"), None
    pages = list(range(1, min(doc["page_count"], 20) + 1))
    parts = []
    for n, img in _images(doc["stored_path"], pages, dpi=60, max_side=900):
        parts += [("text", f"第 {n} 页"), ("image", img)]
    catalog = "\n".join(
        f"- id={t['id']}，名称：{t['name']}，识别关键词：{', '.join(t['keywords']) or '无'}"
        + (f"，说明：{t['page_rule']}" if t["page_rule"] else "")
        for t in templates
    )
    parts.append(("text", f"""下面是已有的模板（每个模板对应一家供应商的文档格式）：
{catalog}

请判断这份 PDF 应该使用哪个模板。PDF 可能是多份文档合订的扫描包，只要其中包含某个模板对应供应商的文档（例如它开出的发票），就选该模板。
如果都不匹配，template_id 填 null。
同时在 pages 中列出所选模板需要抽取数据的页码（从 1 开始）：模板有“说明”（页面筛选规则）时，只列出符合规则的页；没有说明则列出全部页。template_id 为 null 时 pages 填空数组。"""))
    schema = {
        "type": "object",
        "properties": {
            "template_id": {"type": ["integer", "null"]},
            "reason": {"type": "string"},
            "pages": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["template_id", "reason", "pages"],
    }
    res = llm.call_structured(provider, model, "你是文档分类助手。", parts, schema, max_tokens=1000, purpose="classify")
    tid = res.get("template_id")
    tpl = next((t for t in templates if t["id"] == tid), None)
    if tpl:
        pages = sorted({p for p in res.get("pages", []) if isinstance(p, int) and 1 <= p <= doc["page_count"]})
        reason = res.get("reason", "")
        return tpl, bi(f"AI 识别：{reason}", f"AI match: {reason}"), pages or None
    reason = res.get("reason", "")
    return None, bi(f"AI 未能匹配：{reason}", f"AI found no match: {reason}"), None


def select_pages(doc: dict, template: dict, preselected: list[int] | None = None) -> list[int]:
    all_pages = list(range(1, doc["page_count"] + 1))
    rule = (template.get("page_rule") or "").strip()
    if not rule or doc["page_count"] <= 1:
        return all_pages
    if preselected:
        return preselected
    provider, model = llm.resolve("claude", None, fast=True)
    parts = []
    # Pages with a text layer are sent as their first lines of text; only scanned pages need an image.
    with llm.stage("render"):
        snippets = dict(pdf_utils.page_snippets(doc["stored_path"], all_pages, SNIPPET_CHARS))
    images = dict(_images(doc["stored_path"], [n for n in all_pages if not snippets[n]], dpi=60, max_side=900))
    for n in all_pages:
        if n in images:
            parts += [("text", f"第 {n} 页"), ("image", images[n])]
        else:
            parts.append(("text", f"第 {n} 页（文字层开头）：{snippets[n]}"))
    parts.append(("text", f"页面筛选规则：{rule}\n请列出符合规则、需要抽取数据的页码（从 1 开始）。"))
    schema = {
        "type": "object",
        "properties": {"pages": {"type": "array", "items": {"type": "integer"}}},
        "required": ["pages"],
    }
    res = llm.call_structured(provider, model, "你是文档页面分类助手。", parts, schema, max_tokens=500, purpose="select_pages")
    pages = sorted({p for p in res.get("pages", []) if 1 <= p <= doc["page_count"]})
    return pages or all_pages


# -------------------------------------------------------------- extraction

def extract(doc: dict, template: dict, pages: list[int]) -> dict:
    settings = db.get_settings()
    provider, model = llm.resolve(template.get("provider"), template.get("model") or None)
    vision = llm.supports_vision(provider, model)
    if not vision and not doc["has_text"]:
        raise llm.LLMError(bi(
            f"该 PDF 是扫描件（没有文字层），{provider}/{model} 无法读取图片，请把模板的模型改成 Claude 或 DeepSeek Flash",
            f"This PDF is scanned (no text layer) and {provider}/{model} cannot read images; "
            "change the template's model to Claude or DeepSeek Flash"))

    parts = _doc_parts(doc["stored_path"], pages, vision, settings["render_dpi"], bool(template.get("send_images")))
    instr = f"模板：{template['name']}\n请按 submit_result 的字段定义抽取数据。下面是要抽取的文档页面。"
    if template.get("instructions"):
        instr += f"\n\n额外说明：\n{template['instructions']}"
    examples = _examples(template["id"], doc.get("id"), int(settings.get("few_shot_examples", 2)))
    if examples:
        instr += "\n\n以下是此前同一供应商文档经人工核对后的正确结果，仅用于参考字段含义和格式习惯，不要照抄其中的数值：\n"
        instr += "\n".join(json.dumps(e, ensure_ascii=False) for e in examples)
    # Stable per-template instructions go first and are cache-marked, so repeated documents reuse the prefix.
    parts.insert(0, ("text", instr, "cache"))

    res = llm.call_structured(provider, model, EXTRACT_SYSTEM, parts, build_schema(template), purpose="extract")
    data = {"header": res.get("header") or {}, "items": res.get("items") or []}
    if not template["item_fields"]:
        data["items"] = []
    data = normalize(data, template)
    return {
        "data": data,
        "uncertain": [u for u in res.get("uncertain", []) if isinstance(u, str)],
        "notes": res.get("notes") or "",
        "model_used": f"{provider}/{model}",
    }


# ---------------------------------------------------------- model selection

def model_presets() -> list[dict]:
    """Models offered in the template editor, cheapest first (this is also the order auto-selection tries them)."""
    s = db.get_settings()
    return [
        {"key": "haiku", "label": bi("Claude Haiku 5.5（便宜）", "Claude Haiku 5.5 (cheap)"), "short": "Haiku",
         "provider": "claude", "model": "claude-haiku-5-5"},
        {"key": "deepseek", "label": bi("DeepSeek Flash（便宜，支持图片）", "DeepSeek Flash (cheap, reads images)"),
         "short": "DeepSeek", "provider": "deepseek", "model": s["deepseek_model"]},
        {"key": "sonnet", "label": bi("Claude Sonnet 5.5（更准、更贵）", "Claude Sonnet 5.5 (more accurate, pricier)"),
         "short": "Sonnet", "provider": "claude", "model": "claude-sonnet-5-5"},
        {"key": "custom", "label": bi("自定义 / Ollama", "Custom / Ollama"), "short": bi("自定义", "Custom"),
         "provider": "custom", "model": s["custom_model"]},
    ]


def _flat(data: dict) -> dict:
    out = {f"header.{k}": v for k, v in (data.get("header") or {}).items()}
    for n, row in enumerate(data.get("items") or []):
        out.update({f"items.{n}.{k}": v for k, v in row.items()})
    return out


def pick_model(doc: dict, template: dict) -> dict:
    """Try the cheap model first and only move to the next one if the result has problems.

    Problems = the same checks the review page shows (empty required fields, non-numbers, sum mismatch,
    model-flagged uncertainty). If the strongest model returns exactly the same data as the cheapest one, the
    problems come from the document itself, so the cheapest model is kept.
    """
    status = llm.provider_status()
    presets = {p["key"]: p for p in model_presets()}
    ladder = ["haiku"]
    ds = presets["deepseek"]
    if status["deepseek"] and (doc["has_text"] or llm.supports_vision(ds["provider"], ds["model"])):
        ladder.append("deepseek")
    ladder.append("sonnet")
    if not status["claude"]:
        ladder = [k for k in ladder if presets[k]["provider"] != "claude"]

    tpl = {"id": -1, "instructions": "", "send_images": 0, "checks": [], "unique_field": "", "items_label": "", **template}
    pages = select_pages(doc, tpl)
    tries: list[dict] = []
    for rank, key in enumerate(ladder):
        p = presets[key]
        t = {**tpl, "provider": p["provider"], "model": p["model"]}
        try:
            res = extract(doc, t, pages)
        except llm.LLMError as e:
            tries.append({"key": key, "label": p["short"], "rank": rank, "error": e.msg})
            continue
        issues = validate.validate(doc["id"], t, res["data"], res["uncertain"], check_duplicates=False)
        errors = sum(1 for i in issues if i["level"] == "error")
        warnings = len(issues) - errors
        tries.append({"key": key, "label": p["short"], "rank": rank, "errors": errors, "warnings": warnings,
                      "score": errors * 3 + warnings, "flat": _flat(res["data"])})
        if tries[-1]["score"] == 0:
            break
    ok = [x for x in tries if "score" in x]
    if not ok:
        errors = [x["error"] for x in tries if x.get("error")]
        raise llm.LLMError(i18n.join(errors) if errors else bi("没有可用的模型（请检查 .env 里的 Key）",
                                                               "No model available (check the keys in .env)"))

    def name(x, lang):
        return i18n.text(x["label"], lang)

    first = ok[0]
    if len(ok) > 1 and ok[-1]["flat"] == first["flat"]:
        chosen, why = first, bi("最强的模型给出同样的结果，问题来自文档本身，不是模型造成的，使用最便宜的",
                                "The strongest model gave the same result, so the problems come from the document "
                                "itself, not the model; using the cheapest")
    elif first["score"] == 0:
        chosen, why = first, bi("没有发现问题", "No issues found")
    else:
        chosen = min(ok, key=lambda x: (x["score"], -x["rank"]))
        why = (bi(f"{name(first, 'zh')}有问题，换用结果最好的", f"{name(first, 'en')} had issues; switched to the best result")
               if chosen is not first else bi("使用最便宜的", "Using the cheapest"))

    def desc(x, lang):
        zh = lang == "zh"
        if "score" not in x:
            return f"{name(x, lang)} " + ("调用失败" if zh else "failed")
        if not x["score"]:
            return f"{name(x, lang)} " + ("无问题" if zh else "no issues")
        return (f"{name(x, lang)} {x['errors']} 个错误 / {x['warnings']} 个提示" if zh
                else f"{name(x, lang)} {x['errors']} errors / {x['warnings']} warnings")

    day = db.now()[:10]
    note = bi(
        f"自动选择（{day}）：{name(chosen, 'zh')}。{why['zh']}。试抽取结果：{'；'.join(desc(x, 'zh') for x in tries)}",
        f"Auto-selected ({day}): {name(chosen, 'en')}. {why['en']}. Test results: {'; '.join(desc(x, 'en') for x in tries)}",
    )
    p = presets[chosen["key"]]
    return {
        "provider": p["provider"], "model": p["model"], "chosen": chosen["key"],
        "model_note": i18n.enc(note),
        "tries": [{k: v for k, v in x.items() if k != "flat"} for x in tries],
    }


# ----------------------------------------------------------- normalisation

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def to_number(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if not s:
        return None
    neg = ("(" in s and ")" in s) or s.lstrip("$¥€£ ").startswith("-")
    s = re.sub(r"[^0-9.]", "", s)
    if not s or s.count(".") > 1:
        return None
    n = float(s)
    return -n if neg else n


def to_date(v):
    if v is None:
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    m = re.fullmatch(r"(\d{4})[/.](\d{1,2})[/.](\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", s)
    if m:  # US style M/D/Y
        mo, d, y = m.groups()
        y = int(y) + (2000 if len(y) == 2 else 0)
        return f"{y:04d}-{int(mo):02d}-{int(d):02d}"
    m = re.fullmatch(r"(\d{1,2})[- ]([A-Za-z]{3})[A-Za-z]*[- ,]+(\d{2,4})", s)
    if m and m.group(2).lower() in _MONTHS:
        d, mon, y = m.groups()
        y = int(y) + (2000 if len(y) == 2 else 0)
        return f"{y:04d}-{_MONTHS[mon.lower()]:02d}-{int(d):02d}"
    return s


def _norm_value(v, ftype):
    if ftype in ("number", "money"):
        n = to_number(v)
        return n if n is not None else (None if v in (None, "") else v)
    if ftype == "date":
        return to_date(v)
    if ftype == "boolean":
        if isinstance(v, str):
            return v.strip().lower() in ("true", "yes", "y", "是", "1")
        return v
    if v is not None and not isinstance(v, str):
        v = str(v)
    return v.strip() if isinstance(v, str) else v


def normalize(data: dict, template: dict) -> dict:
    hf = {f["key"]: f.get("type", "text") for f in template["header_fields"]}
    itf = {f["key"]: f.get("type", "text") for f in template["item_fields"]}
    header = {k: _norm_value(data.get("header", {}).get(k), t) for k, t in hf.items()}
    items = []
    for row in data.get("items", []):
        if isinstance(row, dict):
            items.append({k: _norm_value(row.get(k), t) for k, t in itf.items()})
    return {"header": header, "items": items}


# ------------------------------------------------------------- suggestions

def suggest_template(doc: dict, hint: str = "", lang: str = "zh", with_items: bool = False) -> dict:
    """lang is the UI language; the human-readable parts of the template (name, labels, descriptions) use it.
    with_items: also design the item table (rows); without it the template only gets header fields."""
    en = lang == "en"
    lang_name = "英文" if en else "中文"
    name_example = "United Logistics Invoice" if en else "United Logistics 发票"
    rule_example = "Only the INVOICE pages issued by MANPOWER LLC" if en else "只处理 MANPOWER LLC 开出的 INVOICE 页"
    items_example = "AWB Details" if en else "运单明细"
    if with_items:
        items_rules = (
            "- item_fields：明细表每一行的字段；没有明细表则为空数组。\n"
            f"- items_label：明细表的名称，用{lang_name}（例如“{items_example}”）。\n"
            "- sum_check：如果明细金额之和应等于某个表头合计金额，填 "
            "{\"item_field\": 明细金额key, \"header_field\": 合计key}，否则 null。\n")
    else:
        items_rules = "- 不需要明细表：只设计表头字段，把明细表里的内容忽略掉（不要输出 item_fields、items_label、sum_check）。\n"
    provider, model = llm.resolve("claude", None)
    pages = list(range(1, min(doc["page_count"], 15) + 1))
    # A digital document is read from its position-preserving text layer; images are only needed for scans.
    parts = _doc_parts(doc["stored_path"], pages, True, 110, send_images=not doc["has_text"])
    parts.append(("text", f"""请为这类文档设计一个数据抽取模板（模板按供应商划分：同一家供应商开出的文档使用同一个模板）。
要求：
- name：模板名称，用“供应商名 + 文档类型”，用{lang_name}，例如 “{name_example}”。
- keywords：2~4 个能唯一识别这家供应商文档的关键词（如供应商公司名、地址、邮箱域名），要求在文档上原样出现；不要用付款方/收货方等其他公司的名字。
- page_rule：如果 PDF 里混有多份不同文档（如运单、证明、发票合订），用一句{lang_name}说明应该只处理哪些页（例如“{rule_example}”）；只有一份文档则留空。
- header_fields：表头字段（整份文档只出现一次的值），如发票号、日期、金额合计等。
{items_rules}- 字段 key 用英文 snake_case；label 用简洁{lang_name}；description 用{lang_name}说明在文档哪里、对应印刷的英文标签是什么、格式特点；example 填本文档里的实际值。
- type 只能是 text / number / money / date / boolean。
- unique_field：最能唯一标识这份文档的表头字段 key（通常是发票号），用于查重。
重点关注应付账款（AP）和成本对账需要的信息：发票号、日期、金额、运单号/参考号、件数、重量等。
{('用户补充要求：' + hint) if hint else ''}"""))
    field_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "label": {"type": "string"},
                "type": {"type": "string", "enum": list(FIELD_TYPES)},
                "description": {"type": "string"},
                "example": {"type": "string"},
                "required": {"type": "boolean"},
            },
            "required": ["key", "label", "type", "description"],
        },
    }
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "page_rule": {"type": "string"},
            "header_fields": field_schema,
            "unique_field": {"type": "string"},
        },
        "required": ["name", "keywords", "page_rule", "header_fields"],
    }
    if with_items:
        schema["properties"].update({
            "item_fields": field_schema,
            "items_label": {"type": "string", "description": f"明细表的名称，如“{items_example}”"},
            "sum_check": {
                "type": ["object", "null"],
                "properties": {"item_field": {"type": "string"}, "header_field": {"type": "string"}},
            },
        })
        schema["required"].append("item_fields")
    res = llm.call_structured(provider, model, "你是文档数据抽取模板设计专家。", parts, schema, max_tokens=8000,
                              purpose="suggest_template")
    checks = []
    sc = res.get("sum_check") if with_items else None
    if isinstance(sc, dict) and sc.get("item_field") and sc.get("header_field"):
        checks.append({"type": "sum", "item_field": sc["item_field"], "header_field": sc["header_field"],
                       "tolerance": 0.01})
    return {
        "name": res.get("name", ""),
        "keywords": res.get("keywords", []),
        "page_rule": res.get("page_rule", ""),
        "header_fields": clean_fields(res.get("header_fields", [])),
        "item_fields": clean_fields(res.get("item_fields", [])) if with_items else [],
        "items_label": res.get("items_label", "") if with_items else "",
        "unique_field": res.get("unique_field", ""),
        "checks": checks,
    }


def clean_fields(fields: list) -> list:
    out, seen = [], set()
    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            continue
        key = re.sub(r"[^a-zA-Z0-9_]", "_", str(f.get("key") or "")).strip("_").lower() or f"field_{i + 1}"
        base, n = key, 2
        while key in seen:
            key, n = f"{base}_{n}", n + 1
        seen.add(key)
        out.append({
            "key": key,
            "label": str(f.get("label") or key),
            "type": f.get("type") if f.get("type") in FIELD_TYPES else "text",
            "description": str(f.get("description") or ""),
            "example": str(f.get("example") or ""),
            "required": bool(f.get("required", False)),
        })
    return out


def suggest_mapping(columns: list[str], template: dict) -> dict:
    preset = next((p for p in model_presets() if p["key"] == db.get_settings().get("mapping_model")), None)
    provider, model = (preset["provider"], preset["model"]) if preset else llm.resolve("default", None)
    sources = [("meta.filename", "来源文件名"), ("meta.template", "模板名称"), ("meta.pages", "抽取页码"),
               ("meta.batch", "批次号（系统生成的上传批次）")]
    sources += [(f"header.{f['key']}", f"表头：{f['label']} - {f.get('description', '')}") for f in template["header_fields"]]
    sources += [(f"item.{f['key']}", f"明细：{f['label']} - {f.get('description', '')}") for f in template["item_fields"]]
    src_text = "\n".join(f"- {k}：{d}" for k, d in sources)
    prompt = f"""要把模板“{template['name']}”抽取出的字段，对应到统一输出格式的各列。
统一输出格式的列：{json.dumps(columns, ensure_ascii=False)}
可用的来源字段：
{src_text}

为每一列选出最合适的来源字段 key（只能从上面列表中选）；没有合适来源的列填空字符串。
- 有多个候选时，选含义最精确、最专门的字段（例如“运单号”列优先选名称就是运单号/主运单号的字段，而不是“其他”“备注”这类可能包含运单号的字段）。
- 导出时一条明细一行：明细级别的列（件数、重量、金额等）优先用明细字段；明细里没有时才用表头字段。
- 金额类的列优先对应实际费用金额或应付合计，不要对应单项附加费。
输出 mapping 数组，每个元素包含 column 和 source。"""
    schema = {
        "type": "object",
        "properties": {
            "mapping": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"column": {"type": "string"}, "source": {"type": "string"}},
                    "required": ["column", "source"],
                },
            }
        },
        "required": ["mapping"],
    }
    res = llm.call_structured(provider, model, "你是数据字段映射助手。", [("text", prompt)], schema, max_tokens=2000,
                              purpose="suggest_mapping")
    valid = {k for k, _ in sources}
    out = {c: "" for c in columns}
    for m in res.get("mapping", []):
        if m.get("column") in out and m.get("source") in valid:
            out[m["column"]] = m["source"]
    return out
