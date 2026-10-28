"""Invoice Lookup: match Raw PRO rows to SC AP invoices and reconcile USD amounts."""

from __future__ import annotations

import os
import re
import uuid
from collections import Counter
from pathlib import Path

from platform_config import BASE_DIR
from features.invoice_lookup_core import (
    DEFAULT_SINCE,
    LookupError,
    parse_since,
    run_lookup,
    source_sheets,
)


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
OUTPUT_DIR = Path(BASE_DIR) / "reports_v4" / "invoice_lookup"

FEATURE = {
    "id": "invoice_lookup",
    "title": "ORD_Inv_lookup",
    "category": "Tools",
    "description": "Look up AP invoices in SC for Raw PRO rows and reconcile amounts (read-only).",
    "supports_cancel": False,
    "output_type": "interactive",
    "input_schema": [],
    "template": "invoice_lookup.html",
}


class SheetChoiceRequired(LookupError):
    def __init__(self, message, sheets):
        super().__init__(message)
        self.sheets = sheets


def _save_upload(uploaded):
    if not uploaded or not uploaded.filename:
        raise LookupError("请先选择 Raw Excel 文件。")
    if not uploaded.filename.lower().endswith(".xlsx") or uploaded.filename.startswith("~$"):
        raise LookupError("请选择正常的 .xlsx 文件。")
    content = uploaded.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise LookupError("文件超过 25 MB 限制。")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(uploaded.filename).name)
    if not name.lower().endswith(".xlsx"):
        name += ".xlsx"
    path = OUTPUT_DIR / (uuid.uuid4().hex + "_" + name)
    path.write_bytes(content)
    return path


def generate_payload(upload, since=None, sheet=None):
    since = (since or "").strip() or DEFAULT_SINCE
    parse_since(since)
    sheet = (sheet or "").strip() or None
    input_path = _save_upload(upload)
    try:
        names = source_sheets(input_path)
        if not names:
            raise LookupError("没有找到包含 PRO、AMT、DO# 的工作表。")
        if sheet is None and len(names) > 1:
            raise SheetChoiceRequired("多个工作表符合条件，请选择要处理的工作表。", names)
        output, result = run_lookup(input_path, OUTPUT_DIR, sheet, since)
    finally:
        input_path.unlink(missing_ok=True)
    counts = Counter(issue.severity for issue in result.issues)
    return {
        "ok": True,
        "status": result.status,
        "filename": output.name,
        "download_url": "/download/invoice-lookup/{0}".format(output.name),
        "rows": len(result.source.rows),
        "pro_total": len(result.reconciliations),
        "pro_passed": sum(item.status == "PASS" for item in result.reconciliations),
        "fail": counts["FAIL"],
        "warning": counts["WARNING"],
        "since": since,
    }


def output_path_for(filename):
    safe = os.path.basename(str(filename or ""))
    path = (OUTPUT_DIR / safe).resolve()
    if safe != filename or path.parent != OUTPUT_DIR.resolve() or path.suffix.lower() != ".xlsx":
        return None
    return path
