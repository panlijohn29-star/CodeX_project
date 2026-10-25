"""Rule checks on extracted data. Each issue: {path, level: error|warning, msg: {zh, en}}."""
import re

from . import db
from .i18n import bi

TRUNCATED = re.compile(r"(\.\.\.|…)\s*$")


def _is_empty(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _check_value(path: str, label: dict, f: dict, v, issues: list) -> None:
    lz, le = label["zh"], label["en"]
    if _is_empty(v):
        if f.get("required"):
            issues.append({"path": path, "level": "error",
                           "msg": bi(f"必填字段“{lz}”为空", f'Required field "{le}" is empty')})
        return
    t = f.get("type", "text")
    if t in ("number", "money") and not isinstance(v, (int, float)):
        issues.append({"path": path, "level": "error",
                       "msg": bi(f"“{lz}”不是有效数字：{v}", f'"{le}" is not a valid number: {v}')})
    elif t == "date" and not (isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)):
        issues.append({"path": path, "level": "warning",
                       "msg": bi(f"“{lz}”日期格式无法识别：{v}", f'"{le}" has an unrecognized date format: {v}')})
    elif isinstance(v, str) and TRUNCATED.search(v):
        issues.append({"path": path, "level": "warning",
                       "msg": bi(f"“{lz}”在原件中被截断：{v}", f'"{le}" is cut off in the original: {v}')})


def confidence(doc: dict, template: dict | None) -> dict | None:
    """0-100 confidence score of an extraction, from its rule checks and how complete the data is.
    A human-verified result is 100. Returns {score, level: high|medium|low, reasons: [{zh, en}]}."""
    data = doc.get("data")
    if not template or not data or doc.get("status") != "done":
        return None
    if doc.get("verified"):
        return {"score": 100, "level": "high", "reasons": [bi("已人工核对", "Verified by a person")]}
    issues = doc.get("issues") or []
    score, reasons = 100.0, []

    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]
    # In a long item table a few flagged rows say little about the whole, so the penalty follows the share of rows
    # that are flagged (at least a small fixed amount, so any flag still shows). Short documents count each issue.
    rows = len((data.get("items") or []))
    long_table = rows > 10
    if errors:
        score -= min(60, max(10, 500 * len(errors) / rows)) if long_table else min(60, 20 * len(errors))
        reasons.append(bi(f"{len(errors)} 个错误（必填为空 / 数值无效）", f"{len(errors)} error(s) (empty required / invalid value)"))
    if warnings:
        score -= min(30, max(2, 300 * len(warnings) / rows)) if long_table else min(30, 7 * len(warnings))
        reasons.append(bi(f"{len(warnings)} 个提示（不确定 / 合计不符 / 重复等）"
                          + (f"，共 {rows} 行" if long_table else ""),
                          f"{len(warnings)} warning(s) (uncertain / sum mismatch / duplicate…)"
                          + (f", out of {rows} rows" if long_table else "")))

    header = data.get("header") or {}
    fields = template["header_fields"]
    if fields:
        empty = sum(1 for f in fields if _is_empty(header.get(f["key"])))
        if empty and not errors:  # empty required fields are already counted as errors
            score -= 10 * empty / len(fields)
            reasons.append(bi(f"{empty} 个表头字段为空", f"{empty} header field(s) empty"))
    if template["item_fields"] and not data.get("items"):
        score -= 10
    if not doc.get("has_text"):
        score -= 5
        reasons.append(bi("扫描件，靠图片识别", "Scanned document, read from images"))
    if any(c.get("type") == "sum" for c in template.get("checks", [])) and not any(i["path"].startswith("header.") and "≠" in str(i["msg"]) for i in issues):
        reasons.append(bi("明细合计与表头一致", "Item sum matches the header total"))

    score = max(0, min(100, round(score)))
    return {"score": score, "level": "high" if score >= 90 else "medium" if score >= 70 else "low", "reasons": reasons}


def validate(doc_id: int | None, template: dict, data: dict, uncertain: list[str],
             check_duplicates: bool = True, others: list[dict] | None = None) -> list[dict]:
    issues: list[dict] = []
    header = data.get("header", {})
    items = data.get("items", [])
    labels = {f["key"]: f.get("label") or f["key"] for f in template["header_fields"] + template["item_fields"]}

    for f in template["header_fields"]:
        name = f.get("label") or f["key"]
        _check_value(f"header.{f['key']}", bi(name, name), f, header.get(f["key"]), issues)
    for i, row in enumerate(items):
        for f in template["item_fields"]:
            name = f.get("label") or f["key"]
            _check_value(f"items.{i}.{f['key']}", bi(f"第{i + 1}行 {name}", f"Row {i + 1} {name}"), f,
                         row.get(f["key"]), issues)

    if template["item_fields"] and not items:
        issues.append({"path": "items", "level": "warning", "msg": bi("没有抽取到任何明细行", "No item rows were extracted")})

    for c in template.get("checks", []):
        if c.get("type") != "sum":
            continue
        ik, hk = c.get("item_field"), c.get("header_field")
        total = header.get(hk)
        if not isinstance(total, (int, float)):
            continue
        s = sum(r.get(ik) for r in items if isinstance(r.get(ik), (int, float)))
        if abs(s - total) > float(c.get("tolerance", 0.01)):
            il, hl = labels.get(ik, ik), labels.get(hk, hk)
            issues.append({
                "path": f"header.{hk}", "level": "warning",
                "msg": bi(f"明细“{il}”合计 {s:,.2f} ≠ “{hl}” {total:,.2f}",
                          f'Sum of item "{il}" {s:,.2f} ≠ "{hl}" {total:,.2f}'),
            })

    uf = template.get("unique_field")
    if check_duplicates and uf and not _is_empty(header.get(uf)):
        val = str(header.get(uf)).strip()
        pool = others if others is not None else db.list_rows(
            "documents", "template_id=? AND id!=? AND data IS NOT NULL", (template["id"], doc_id or -1))
        for other in pool:
            if other["id"] == doc_id:
                continue
            ov = (other.get("data") or {}).get("header", {}).get(uf)
            if ov is not None and str(ov).strip() == val:
                ul = labels.get(uf, uf)
                issues.append({
                    "path": f"header.{uf}", "level": "warning",
                    "msg": bi(f"可能重复：与文件“{other['filename']}”的{ul}相同（{val}）",
                              f'Possible duplicate: same {ul} as file "{other["filename"]}" ({val})'),
                })

    for u in uncertain:
        if not any(i["path"] == u for i in issues):
            issues.append({"path": u, "level": "warning",
                           "msg": bi("AI 标记为不确定，请核对原件", "AI flagged this as uncertain; check it against the original")})
    return issues
