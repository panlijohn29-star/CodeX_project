"""Read-only invoice lookup and reconciliation. No GUI or Excel output dependencies."""
from __future__ import annotations

import os
import re
import ssl
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable

ALLOWED_CREATORS = frozenset({"USSDC.ORD", "JOHNWU.ORD", "SHILONG.ORD"})
SPECIAL_CUSTOMER = "KUEHNE & NAGEL INC - ORD"
IWT_CUSTOMERS = frozenset({"USA-IWT", "APEX LOGISTICS INTERNATIONAL (ORD), INC."})
CENT = Decimal("0.01")
DEFAULT_SINCE = "2026-09-01"
RAW_HEADERS = ["PRO", "AMT", "DO#", "AP INVOICE", "AR INVOICE", "CUSTOMER", "remark"]
LOOKUP_HEADERS = ["PRO", "Invoice_by", "Invoice_date", "invoice#", "amt", "balance", "customer"]
ALIASES = {
    "pro": {"pro", "pronumber", "vendorinvoicenumber"},
    "amount": {"amt", "amount"}, "do": {"do", "donumber"},
    "ap": {"apinvoice", "apinv"}, "ar": {"arinvoice", "arinv"},
    "customer": {"customer", "customercode"}, "remark": {"remark", "remarks"},
}


class LookupError(Exception):
    """An actionable, safe-to-display input or configuration error."""


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def key(value: Any) -> str:
    return text(value).upper()


def customer_text(value: Any) -> str:
    value = text(value)
    return "" if key(value) in {"", "0", "#N/A", "#VALUE!", "#REF!", "NULL", "NONE"} else value


def decimal_value(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(text(value).replace(",", ""))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def normalized_do(value: Any) -> str:
    value = text(value)
    if key(value).startswith("DO"):
        value = re.sub(r"\s*-\s*1\s*$", "", value)
    return value.strip()


def is_ar(pro: str) -> bool:
    return bool(re.search(r"-\s*AR\s*$", pro, re.I))


def parse_since(value: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise LookupError("查询日期格式必须为 YYYY-MM-DD。")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise LookupError("查询日期无效。") from exc


@dataclass
class RawRow:
    source_row: int
    values: list[Any]
    pro: str
    do: str
    amount: Decimal | None
    input_errors: list[str] = field(default_factory=list)
    customer: str = ""
    selected: str = ""

    @property
    def pro_key(self) -> str:
        return key(self.pro)

    @property
    def ar(self) -> bool:
        return is_ar(self.pro)

    @property
    def iwt(self) -> bool:
        return key(self.do).startswith("IWT")


@dataclass
class Source:
    path: Path
    sheet: str
    rows: list[RawRow]


@dataclass
class Snapshot:
    records: list[dict[str, Any]] = field(default_factory=list)
    succeeded: set[str] = field(default_factory=set)
    failed: dict[str, str] = field(default_factory=dict)


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    action: str
    pro: str = ""
    source_rows: tuple[int, ...] = ()
    lookup_rows: tuple[int, ...] = ()
    dos: str = ""
    invoices: str = ""
    raw_value: str = ""
    actual_value: str = ""


@dataclass
class Invoice:
    index: int
    values: list[Any]
    pro: str
    creator: str
    number: str
    amount: Decimal | None
    customer: str
    blocked: bool = False
    duplicate: bool = False
    used_by: set[int] = field(default_factory=set)

    @property
    def qualified(self) -> bool:
        return key(self.creator) in ALLOWED_CREATORS

    @property
    def special(self) -> bool:
        return key(self.customer) == SPECIAL_CUSTOMER

    @property
    def iwt(self) -> bool:
        return key(self.customer) in IWT_CUSTOMERS

    @property
    def match_group(self) -> str:
        return "IWT" if self.iwt else "K+N" if self.special else "DO"


def invoice_scope(row: RawRow, candidates: list[Invoice]) -> list[Invoice]:
    """Apply DO/IWT and customer rules before selecting an allowed creator.

    A blank invoice customer remains a possible (but blocked) candidate: it
    must not be silently discarded to make another candidate appear unique.
    """
    if row.iwt:
        return [i for i in candidates if i.iwt or not i.customer]
    ordinary_do = [i for i in candidates if not i.iwt]
    if not row.customer:
        return ordinary_do
    if key(row.customer) == SPECIAL_CUSTOMER:
        return [i for i in ordinary_do if i.special or not i.customer]
    ordinary_do = [i for i in ordinary_do if not i.special]
    exact = [i for i in ordinary_do if i.qualified and key(i.customer) == key(row.customer)]
    if exact:
        return exact + [i for i in ordinary_do if not i.customer]
    # Some ordinary invoices cover DOs of several customers (e.g. PRO 665).
    # Preserve the existing unique ordinary-invoice fallback for those rows.
    return ordinary_do


@dataclass
class Reconciliation:
    pro: str
    source_rows: tuple[int, ...]
    raw_amounts: str
    expected: Decimal | None
    actual: Decimal | None
    difference: Decimal | None
    status: str
    invoices: str


@dataclass
class Result:
    source: Source
    since: str
    xc: Snapshot
    sc: Snapshot
    invoices: list[Invoice] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    reconciliations: list[Reconciliation] = field(default_factory=list)
    started: datetime = field(default_factory=lambda: datetime.now().astimezone())
    mode: str = "LIVE DATABASE"

    @property
    def status(self) -> str:
        return "FAIL" if any(x.severity == "FAIL" for x in self.issues) else "WARNING" if self.issues else "PASS"

    def add(self, severity: str, code: str, message: str, action: str,
            rows: list[RawRow] | None = None, invoices: list[Invoice] | None = None,
            pro: str = "", raw: Any = "", actual: Any = "") -> None:
        rows, invoices = rows or [], invoices or []
        if raw == "" and rows:
            raw = "; ".join(dict.fromkeys(text(r.values[1]) for r in rows))
        if actual == "" and invoices:
            actual = "; ".join(dict.fromkeys(f"{i.number or '(missing invoice#)'}: {text(i.values[4])}" for i in invoices))
        self.issues.append(Issue(
            severity, code, message, action, pro or (rows[0].pro if rows else invoices[0].pro if invoices else ""),
            tuple(x.source_row for x in rows), tuple(x.index + 2 for x in invoices),
            "; ".join(dict.fromkeys(text(x.values[2]) for x in rows)),
            "; ".join(dict.fromkeys(x.number for x in invoices if x.number)), text(raw), text(actual),
        ))


def _header(ws) -> tuple[int, dict[str, int]] | None:
    for row_number, cells in enumerate(ws.iter_rows(max_row=min(ws.max_row, 25)), 1):
        mapping: dict[str, int] = {}
        duplicates: set[str] = set()
        for c in cells:
            name = re.sub(r"[^a-z0-9]", "", text(c.value).lower())
            for field_name, aliases in ALIASES.items():
                if name in aliases:
                    if field_name in mapping:
                        duplicates.add(field_name)
                    mapping[field_name] = c.column - 1
        if {"pro", "amount", "do"}.issubset(mapping):
            if duplicates:
                raise LookupError(f"工作表 {ws.title} 存在重复字段：{', '.join(sorted(duplicates))}。")
            return row_number, mapping
    return None


def _open_source(path: Path, data_only: bool = False):
    from openpyxl import load_workbook
    if path.suffix.lower() != ".xlsx":
        raise LookupError("请选择 .xlsx 文件。")
    try:
        # Normal mode closes the ZIP immediately, including when an invalid row
        # aborts parsing. Streaming generators otherwise retain Windows file locks.
        return load_workbook(path, read_only=False, data_only=data_only)
    except Exception as exc:
        raise LookupError("无法读取 Excel，请确认文件存在、格式正确且未加密。") from exc


def source_sheets(path: Path) -> list[str]:
    wb = _open_source(path)
    try:
        return [ws.title for ws in wb if _header(ws)]
    finally:
        wb.close()


def _identifier(cell) -> str:
    value = text(cell.value)
    # Excel stores some identifiers numerically with an explicit leading-zero format.
    if isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
        if re.fullmatch(r"0{2,}", cell.number_format or "") and re.fullmatch(r"\d+", value):
            return value.zfill(len(cell.number_format))
    return value


def read_source(path: Path, sheet: str | None = None) -> Source:
    path = path.resolve()
    names = source_sheets(path)
    if not names:
        raise LookupError("没有找到包含 PRO、AMT、DO# 的工作表。")
    if sheet is None and len(names) != 1:
        raise LookupError("多个工作表符合条件，请选择工作表（命令行使用 --sheet）。")
    sheet = sheet or names[0]
    if sheet not in names:
        raise LookupError("所选工作表不包含必要字段。")
    wb, cached = _open_source(path), _open_source(path, data_only=True)
    try:
        ws = wb[sheet]
        header_row, mapping = _header(ws)
        result = []
        cached_rows = cached[sheet].iter_rows(min_row=header_row + 1)
        for row_number, (cells, cache_cells) in enumerate(zip(ws.iter_rows(min_row=header_row + 1), cached_rows), header_row + 1):
            values, errors = [], []
            for name in ("pro", "amount", "do", "ap", "ar", "customer", "remark"):
                if name not in mapping:
                    values.append(None)
                    continue
                cell = cells[mapping[name]]
                cached_cell = cache_cells[mapping[name]]
                value = cached_cell.value if cell.data_type == "f" else cell.value
                if cell.data_type == "f" and value is None:
                    errors.append(f"{name} 公式没有缓存结果，请先在 Excel 重算保存")
                    value = cell.value
                elif name in {"pro", "do"}:
                    value = _identifier(cached_cell if cell.data_type == "f" else cell)
                if cell.data_type == "e" and name in {"pro", "amount", "do"}:
                    errors.append(f"{name} 包含 Excel 错误值")
                values.append(value)
            if not any(v is not None and text(v) != "" for v in values):
                continue
            pro, do = text(values[0]), normalized_do(values[2])
            amount = decimal_value(values[1])
            if not pro or pro.startswith("="):
                errors.append("PRO 缺失或无有效值")
            if not do or do.startswith("="):
                errors.append("DO# 缺失或无有效值")
            if amount is None:
                errors.append("AMT 缺失或不是有效数字")
            result.append(RawRow(row_number, values, pro, do, amount, errors))
        if not result:
            raise LookupError("工作表没有数据行。")
        return Source(path, sheet, result)
    finally:
        wb.close()
        cached.close()


def query_keys(source: Source) -> tuple[list[str], list[str]]:
    do_keys = sorted({key(r.do) for r in source.rows if r.do and not r.iwt and not r.input_errors})
    pro_keys = sorted({r.pro_key for r in source.rows if r.pro and not r.ar and not r.input_errors})
    return do_keys, pro_keys


def load_db_config(env_path: Path | None, prefix: str) -> dict[str, Any]:
    """Build a connection config from the Platform settings.

    XC uses the dedicated XC_DB_* variables; SC uses the platform's SCDBUS profile.
    ``env_path`` is accepted for signature compatibility and ignored.
    """
    import pymysql
    from platform_config import env_int, env_value, get_db_config
    try:
        if prefix == "XC":
            values = {
                "host": env_value("XC_DB_HOST", env_value("XC_HOST")),
                "user": env_value("XC_DB_USER", env_value("XC_USER")),
                "password": env_value("XC_DB_PASSWORD", env_value("XC_PASSWORD")),
                "db": env_value("XC_DB_NAME", env_value("XC_DATABASE", env_value("XC_DB"))),
            }
            missing = [name for name, value in values.items() if not value]
            if missing:
                raise LookupError("XC 数据库配置缺失：{0}。请在 Platform .env 中配置 XC_DB_HOST、XC_DB_USER、XC_DB_PASSWORD、XC_DB_NAME。".format(", ".join(missing)))
            config = {**values, "port": env_int("XC_DB_PORT", env_int("XC_PORT", 3306)),
                      "charset": env_value("XC_DB_CHARSET", "utf8mb4")}
            ssl_setting = str(env_value("XC_DB_SSL", "false")).strip().lower()
        else:
            config = get_db_config("scdbus")
            ssl_setting = str(env_value("SCDBUS_SSL", "false")).strip().lower()
    except RuntimeError as exc:
        raise LookupError(f"{prefix} 数据库配置不完整：{exc}") from exc
    except ValueError as exc:
        raise LookupError(f"{prefix} 数据库端口配置无效。") from exc
    if ssl_setting not in {"true", "1", "yes", "false", "0", "no"}:
        raise LookupError(f"{prefix} 的 SSL 配置必须为 true 或 false。")
    config.update(cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                  connect_timeout=20, read_timeout=180, write_timeout=180)
    if ssl_setting in {"true", "1", "yes"}:
        # Passing an SSLContext keeps certificate and hostname verification on.
        config["ssl"] = ssl.create_default_context()
    return config


def safe_db_error(exc: Exception) -> str:
    if isinstance(exc, LookupError):
        return str(exc)
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    label = {1045: "数据库身份验证失败", 1044: "数据库访问权限不足", 1142: "查询权限不足",
             1146: "数据库表不存在", 1054: "数据库字段不存在", 2003: "无法连接数据库",
             2013: "数据库连接中断或查询超时", 2006: "数据库连接已断开"}.get(code, "数据库查询失败")
    return f"{label}（错误代码 {code}）" if code is not None else label


def fetch_snapshot(prefix: str, keys: list[str], since: str, env_path: Path | None,
                   progress: Callable[[str], None] = lambda _: None,
                   connector=None, config_loader=load_db_config) -> Snapshot:
    snapshot = Snapshot()
    if not keys:
        return snapshot
    try:
        config = config_loader(env_path, prefix)
    except Exception as exc:
        snapshot.failed.update({k: safe_db_error(exc) for k in keys})
        return snapshot
    if connector is None:
        import pymysql
        connector = pymysql.connect
    for start in range(0, len(keys), 500):
        batch = keys[start:start + 500]
        placeholders = ",".join(["%s"] * len(batch))
        if prefix == "XC":
            sql = f"SELECT dispatch_no, customer_code FROM t_dispatch_head WHERE dispatch_no IN ({placeholders})"
            params = tuple(batch)
        else:
            sql = ("SELECT customer_invoice_no, invoice_by, invoice_date, invoice_no, "
                   f"amount_balance, balance, customer FROM cf_invoice WHERE customer_invoice_no IN ({placeholders}) "
                   "AND invoice_date > %s AND invoice_status <> %s")
            params = (*batch, since, "3")
        progress(f"查询 {prefix}：{min(start + 500, len(keys))}/{len(keys)} 个编号")
        connection = None
        try:
            connection = connector(**config)
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                records = list(cursor.fetchall())
            snapshot.records.extend({str(k).lower(): v for k, v in row.items()} for row in records)
            snapshot.succeeded.update(batch)
        except Exception as exc:
            snapshot.failed.update({k: safe_db_error(exc) for k in batch})
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
    return snapshot


def evaluate(source: Source, xc: Snapshot, sc: Snapshot, since: str = DEFAULT_SINCE,
             mode: str = "LIVE DATABASE") -> Result:
    parse_since(since)
    result = Result(source, since, xc, sc, mode=mode)
    raw_by_pro: dict[str, list[RawRow]] = defaultdict(list)
    for row in source.rows:
        row.customer, row.selected = "", ""
        raw_by_pro[row.pro_key].append(row)
        if row.input_errors:
            result.add("FAIL", "RAW_INVALID", "；".join(row.input_errors), "修正源数据后重新运行。", [row], raw=row.values[1])
        if row.amount is not None:
            if row.amount != money(row.amount):
                result.add("WARNING", "AMOUNT_PRECISION", "源金额超过两位小数，按分四舍五入比较。", "确认金额精度。", [row], raw=row.amount, actual=money(row.amount))
            if row.amount <= 0:
                result.add("WARNING", "NONPOSITIVE_AMOUNT", "源金额为零或负数，已按原符号保留。", "确认零金额或贷项是否正确。", [row], raw=row.amount)

    for signature, count in Counter(tuple(text(v) for v in r.values) for r in source.rows).items():
        if count > 1:
            rows = [r for r in source.rows if tuple(text(v) for v in r.values) == signature]
            result.add("WARNING", "DUPLICATE_RAW_ROW", "存在完全重复的源行，未自动删除。", "检查是否重复录入。", rows)
    by_do: dict[str, list[RawRow]] = defaultdict(list)
    for r in source.rows:
        if r.do:
            by_do[key(r.do)].append(r)
    for rows in by_do.values():
        if len(rows) > 1:
            result.add("WARNING", "REPEATED_DO", "多行使用同一个标准化 DO，记录均保留。", "确认多次费用或后缀关系。", rows)

    customers: dict[str, list[Any]] = defaultdict(list)
    for record in xc.records:
        customers[key(record.get("dispatch_no"))].append(record.get("customer_code"))
    for r in source.rows:
        # IWT has no unique XC customer. A blank customer is expected and does
        # not warrant a warning; invoice matching and reconciliation still run.
        if not r.iwt and r.do and not r.input_errors:
            do_key = key(r.do)
            if do_key not in xc.succeeded or do_key in xc.failed:
                result.add("FAIL", "XC_QUERY_INCOMPLETE", xc.failed.get(do_key, "XC 查询未完成。"), "检查配置和连接后重新运行。", [r])
            else:
                values = customers.get(do_key, [])
                names = {key(customer_text(v)): customer_text(v) for v in values}
                if not names or "" in names:
                    result.add("FAIL", "CUSTOMER_MISSING", "XC 未找到客户或客户字段为空。", "检查标准化 DO 和 XC 客户资料。", [r])
                elif len(names) > 1:
                    result.add("FAIL", "CUSTOMER_CONFLICT", "同一 DO 在 XC 返回多个不同客户。", "核实 XC 客户资料。", [r], actual="; ".join(names.values()))
                else:
                    r.customer = next(iter(names.values()))
        old_customer = customer_text(r.values[5])
        if old_customer and key(old_customer) != key(r.customer):
            result.add("WARNING", "CUSTOMER_CHANGED", "源文件客户与本次查询结果不同，不使用旧值兜底。", "核对新旧客户。", [r], raw=old_customer, actual=r.customer or "未取得")

    fields = ["customer_invoice_no", "invoice_by", "invoice_date", "invoice_no", "amount_balance", "balance", "customer"]
    by_pro: dict[str, list[Invoice]] = defaultdict(list)
    by_invoice: dict[str, list[Invoice]] = defaultdict(list)
    for index, record in enumerate(sc.records):
        values = [record.get(f) for f in fields]
        inv = Invoice(index, values, text(values[0]), text(values[1]), text(values[3]), decimal_value(values[4]), customer_text(values[6]))
        result.invoices.append(inv)
        by_pro[key(inv.pro)].append(inv)
        if inv.number:
            by_invoice[key(inv.number)].append(inv)
        invalid = not inv.pro or not inv.number or inv.amount is None or not inv.creator or not inv.customer or values[2] is None
        if invalid:
            inv.blocked = True
            result.add("FAIL", "INVOICE_INVALID", "SC 发票的编号、金额、客户、创建人或日期缺失/无效。", "补全 SC 发票信息。", raw_by_pro.get(key(inv.pro)), [inv])
        if inv.amount is not None and inv.amount != money(inv.amount):
            result.add("WARNING", "INVOICE_PRECISION", "SC 金额超过两位小数，按分四舍五入比较。", "确认发票金额精度。", raw_by_pro.get(key(inv.pro)), [inv], raw=inv.amount, actual=money(inv.amount))
        if inv.amount is not None and inv.amount <= 0:
            result.add("WARNING", "INVOICE_NONPOSITIVE", "SC 金额为零或负数，按原符号保留。", "确认贷项或零金额。", raw_by_pro.get(key(inv.pro)), [inv], actual=inv.amount)

    for group in by_invoice.values():
        if len(group) <= 1:
            continue
        signatures = {(key(i.pro), key(i.creator), text(i.values[2]), i.amount, text(i.values[5]), key(i.customer)) for i in group}
        affected = [r for p in {key(i.pro) for i in group} for r in raw_by_pro.get(p, [])]
        if len(signatures) == 1:
            for i in group[1:]:
                i.duplicate = True
            result.add("WARNING", "DUPLICATE_DB_ROW", "同一发票的重复记录完全一致，金额只计一次。", "检查数据库重复记录。", affected, group)
        else:
            for i in group:
                i.blocked = True
            code = "INVOICE_CROSS_PRO" if len({key(i.pro) for i in group}) > 1 else "INVOICE_CONFLICT"
            result.add("FAIL", code, "同一 invoice# 对应不同 PRO 或发票信息冲突，禁止自动采用。", "核对 SC 发票记录。", affected, group)

    for pro, group in by_pro.items():
        distinct = [i for i in group if not i.duplicate]
        if len(distinct) > 1:
            split = (len({i.match_group for i in distinct}) == len(distinct)
                     and all(i.qualified and not i.blocked for i in distinct))
            # A single invoice per DO/K+N/IWT group is an expected split.
            # Reconciliation still reports differences and incomplete matches.
            if not split:
                result.add("WARNING", "MULTIPLE_PRO_INVOICES", "同一 PRO 存在多张发票。",
                           "核对候选、客户分组及选择原因。", raw_by_pro.get(pro), group)
        by_amount: dict[Decimal, list[Invoice]] = defaultdict(list)
        for inv in distinct:
            if inv.amount is not None:
                by_amount[money(inv.amount)].append(inv)
        for amount, same in by_amount.items():
            if len(same) > 1 and any(not i.qualified for i in same):
                result.add("WARNING", "SUSPECT_DUPLICATE_INVOICE", "同一 PRO 存在同金额且非指定人员创建的发票，疑似重复开票。", "核查所有相关票号，确认是否重复。", raw_by_pro.get(pro), same, actual=amount)

    for row in source.rows:
        candidates = [i for i in by_pro.get(row.pro_key, []) if not i.duplicate]
        if not row.ar and not row.input_errors:
            if row.pro_key not in sc.succeeded or row.pro_key in sc.failed:
                result.add("FAIL", "SC_QUERY_INCOMPLETE", sc.failed.get(row.pro_key, "SC 查询未完成。"), "检查配置和连接后重新运行。", [row], candidates)
            else:
                scope = invoice_scope(row, candidates)
                allowed = [i for i in scope if i.qualified]
                if any(i.blocked for i in allowed):
                    result.add("FAIL", "BLOCKED_CANDIDATE", "候选票存在冲突或必填数据无效，不能自动采用。", "先修复 SC 发票异常。", [row], allowed)
                elif not allowed:
                    code = "NO_SC_INVOICE" if not candidates else "NO_CUSTOMER_INVOICE" if not scope else "NO_ALLOWED_INVOICE"
                    message = {"NO_SC_INVOICE": "SC 未找到该 PRO 的发票。", "NO_CUSTOMER_INVOICE": "未找到 IWT 专用客户的发票。" if row.iwt else "未找到对应 DO 客户组的发票。", "NO_ALLOWED_INVOICE": "候选票均非指定三位人员创建。"}[code]
                    result.add("FAIL", code, message, "检查上传结果、查询日期、客户与创建人。", [row], scope or candidates)
                elif len(allowed) > 1:
                    result.add("FAIL", "AMBIGUOUS_INVOICE", "存在多张合资格候选票，AP INVOICE 留空。", "核实客户或重复票后重新查询。", [row], allowed)
                else:
                    row.selected = allowed[0].number
                    allowed[0].used_by.add(row.source_row)
        previous = customer_text(row.values[3])
        if previous and key(previous) != key(row.selected):
            result.add("WARNING", "AP_INVOICE_CHANGED", "原 AP INVOICE 与本次选择不同，不使用旧票号兜底。", "核对旧票号和当前候选。", [row], candidates, raw=previous, actual=row.selected or "留空")

    failed_rows = {n for issue in result.issues if issue.severity == "FAIL" for n in issue.source_rows}
    for pro, rows in raw_by_pro.items():
        if not pro:
            continue
        if rows[0].ar:
            result.reconciliations.append(Reconciliation(rows[0].pro, tuple(r.source_row for r in rows), "", None, None, None, "EXCLUDED_AR", ""))
            continue
        amounts = {r.amount for r in rows if r.amount is not None}
        raw_amounts = "; ".join(str(v) for v in sorted(amounts))
        expected = money(next(iter(amounts))) if len(amounts) == 1 and all(r.amount is not None for r in rows) else None
        if len(amounts) > 1:
            result.add("FAIL", "RAW_AMOUNT_CONFLICT", "同一 PRO 的 AMT 不一致，无法确定对账源金额。", "确认该 PRO 的总金额并修正所有对应行。", rows, raw=raw_amounts)
        selected = [i for i in by_pro.get(pro, []) if i.used_by and not i.duplicate]
        actual = sum((money(i.amount) for i in selected if i.amount is not None), Decimal("0")) if selected else None
        incomplete = any(r.source_row in failed_rows or not r.selected for r in rows)
        if expected is None:
            status, difference = "UNDETERMINED", None
        elif incomplete or actual is None:
            status, difference = "INCOMPLETE", None
        else:
            difference = actual - expected
            status = "PASS" if difference == 0 else "FAIL"
            if difference:
                result.add("FAIL", "AMOUNT_MISMATCH", f"选中发票合计与 Raw 金额相差 {difference:.2f} USD。", "核对漏票、错票及账单金额。", rows, selected, raw=expected, actual=actual)
        result.reconciliations.append(Reconciliation(rows[0].pro, tuple(r.source_row for r in rows), raw_amounts, expected, actual, difference, status, "; ".join(i.number for i in selected)))
    return result


def run_lookup(input_path: Path, output_dir: Path | None = None, sheet: str | None = None,
               since: str = DEFAULT_SINCE, env_path: Path | None = None,
               progress: Callable[[str], None] = lambda _: None) -> tuple[Path, Result]:
    parse_since(since)
    progress("读取源文件")
    source = read_source(input_path, sheet)
    do_keys, pro_keys = query_keys(source)
    xc = fetch_snapshot("XC", do_keys, since, env_path, progress)
    sc = fetch_snapshot("SC", pro_keys, since, env_path, progress)
    progress("匹配发票并校验金额")
    result = evaluate(source, xc, sc, since)
    from features.invoice_lookup_excel import export_result
    progress("导出 Excel")
    output = export_result(result, output_dir or input_path.resolve().parent)
    progress(f"完成：{result.status}")
    return output, result
