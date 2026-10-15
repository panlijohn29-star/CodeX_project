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


def validate(doc_id: int | None, template: dict, data: dict, uncertain: list[str],
             check_duplicates: bool = True) -> list[dict]:
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
        for other in db.list_rows("documents", "template_id=? AND id!=? AND data IS NOT NULL",
                                  (template["id"], doc_id or -1)):
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
