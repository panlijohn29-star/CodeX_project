import json
import sqlite3
import threading
from datetime import datetime

from . import i18n
from .config import DB_PATH, DEFAULT_SETTINGS

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    page_rule TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT 'default',
    model TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    send_images INTEGER NOT NULL DEFAULT 0,
    model_note TEXT NOT NULL DEFAULT '',
    header_fields TEXT NOT NULL DEFAULT '[]',
    item_fields TEXT NOT NULL DEFAULT '[]',
    items_label TEXT NOT NULL DEFAULT '',
    unique_field TEXT NOT NULL DEFAULT '',
    checks TEXT NOT NULL DEFAULT '[]',
    auto_generated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    has_text INTEGER NOT NULL DEFAULT 0,
    template_id INTEGER,
    template_locked INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '',
    match_info TEXT NOT NULL DEFAULT '',
    pages TEXT NOT NULL DEFAULT '[]',
    data TEXT,
    original_data TEXT,
    uncertain TEXT NOT NULL DEFAULT '[]',
    issues TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    model_used TEXT NOT NULL DEFAULT '',
    verified INTEGER NOT NULL DEFAULT 0,
    batch_id INTEGER,
    exported_at TEXT,
    file_hash TEXT,
    template_stamp TEXT,
    timings TEXT,
    source TEXT NOT NULL DEFAULT '',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    legacy INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER,
    batch_id INTEGER,
    source TEXT NOT NULL DEFAULT 'pipeline',
    purpose TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_doc ON llm_usage(doc_id);
CREATE INDEX IF NOT EXISTS idx_usage_batch ON llm_usage(batch_id);
CREATE TABLE IF NOT EXISTS output_formats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    columns TEXT NOT NULL DEFAULT '[]',
    mappings TEXT NOT NULL DEFAULT '{}',
    allocate TEXT NOT NULL DEFAULT '{}',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

JSON_COLUMNS = {
    "templates": ["keywords", "header_fields", "item_fields", "checks"],
    "documents": ["pages", "data", "original_data", "uncertain", "issues", "timings"],
    "output_formats": ["columns", "mappings", "allocate"],
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _lock, connect() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}
        for col, ddl in (("batch_id", "INTEGER"), ("exported_at", "TEXT"), ("file_hash", "TEXT"),
                         ("template_stamp", "TEXT"), ("timings", "TEXT"), ("source", "TEXT NOT NULL DEFAULT ''")):
            if col not in cols:
                conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {ddl}")
        ucols = {r["name"] for r in conn.execute("PRAGMA table_info(llm_usage)")}
        if "duration_ms" not in ucols:
            conn.execute("ALTER TABLE llm_usage ADD COLUMN duration_ms INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(file_hash)")
        tcols = {r["name"] for r in conn.execute("PRAGMA table_info(templates)")}
        for col, ddl in (("send_images", "INTEGER NOT NULL DEFAULT 0"), ("model_note", "TEXT NOT NULL DEFAULT ''"),
                         ("auto_generated", "INTEGER NOT NULL DEFAULT 0")):
            if col not in tcols:
                conn.execute(f"ALTER TABLE templates ADD COLUMN {col} {ddl}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_batch ON documents(batch_id)")
        _assign_legacy_batches(conn)
        # Documents left mid-processing by a previous run will never finish.
        conn.execute(
            "UPDATE documents SET status='error', error=? WHERE status IN ('pending','classifying','extracting')",
            (i18n.enc(i18n.bi("处理被中断（服务重启），请重新抽取",
                              "Processing was interrupted (server restarted); please extract again")),),
        )


def _assign_legacy_batches(conn: sqlite3.Connection) -> None:
    """Documents uploaded before batches existed are grouped by upload date."""
    rows = conn.execute(
        "SELECT id, COALESCE(substr(created_at, 1, 10), '') AS day, created_at FROM documents "
        "WHERE batch_id IS NULL ORDER BY id"
    ).fetchall()
    batch_ids: dict[str, int] = {}
    for r in rows:
        day = r["day"].replace("-", "") or "未知日期"
        if day not in batch_ids:
            code = f"B-{day}"
            found = conn.execute("SELECT id FROM batches WHERE code=?", (code,)).fetchone()
            if found:
                batch_ids[day] = found["id"]
            else:
                ts = r["created_at"] or now()
                batch_ids[day] = conn.execute(
                    "INSERT INTO batches (code, legacy, created_at, updated_at) VALUES (?, 1, ?, ?)",
                    (code, ts, ts),
                ).lastrowid
        conn.execute("UPDATE documents SET batch_id=? WHERE id=?", (batch_ids[day], r["id"]))


def templates_stamp() -> str:
    """Changes whenever a template is added, edited or deleted; a stored result is only reusable while it is unchanged."""
    with connect() as conn:
        r = conn.execute("SELECT COUNT(1) AS n, COALESCE(MAX(updated_at), '') AS m FROM templates").fetchone()
    return f"{r['n']}:{r['m']}"


def create_batch() -> int:
    """Create a batch with a system-generated number like B20261008-001."""
    day = datetime.now().strftime("%Y%m%d")
    prefix = f"B{day}-"
    with _lock, connect() as conn:
        last = conn.execute(
            "SELECT code FROM batches WHERE code LIKE ? ORDER BY code DESC LIMIT 1", (prefix + "%",)
        ).fetchone()
        seq = int(last["code"].rsplit("-", 1)[1]) + 1 if last else 1
        ts = now()
        return conn.execute(
            "INSERT INTO batches (code, legacy, created_at, updated_at) VALUES (?, 0, ?, ?)",
            (f"{prefix}{seq:03d}", ts, ts),
        ).lastrowid


def batch_label(b: dict) -> str | dict:
    return i18n.bi(f"历史批次 {b['code']}", f"Legacy batch {b['code']}") if b["legacy"] else b["code"]


def _decode(table: str, row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for col in JSON_COLUMNS.get(table, []):
        if d.get(col) is not None:
            d[col] = json.loads(d[col])
    return d


def _encode(table: str, values: dict) -> dict:
    out = {}
    for k, v in values.items():
        if k in JSON_COLUMNS.get(table, []) and v is not None:
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = v
    return out


def get(table: str, row_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
    return _decode(table, row)


def list_rows(table: str, where: str = "", params: tuple = (), order: str = "id DESC") -> list[dict]:
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    sql += f" ORDER BY {order}"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_decode(table, r) for r in rows]


def insert(table: str, values: dict) -> int:
    values = dict(values)
    values.setdefault("created_at", now())
    values.setdefault("updated_at", now())
    enc = _encode(table, values)
    cols = ", ".join(enc)
    qs = ", ".join("?" for _ in enc)
    with _lock, connect() as conn:
        cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", tuple(enc.values()))
        return cur.lastrowid


def update(table: str, row_id: int, values: dict) -> None:
    if not values:
        return
    values = dict(values)
    values["updated_at"] = now()
    enc = _encode(table, values)
    sets = ", ".join(f"{k}=?" for k in enc)
    with _lock, connect() as conn:
        conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*enc.values(), row_id))


def delete(table: str, row_id: int) -> None:
    with _lock, connect() as conn:
        conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))


MAX_RECENT = 15


def _recent_get(key: str) -> list[str]:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else []


def _recent_save(key: str, items: list[str]) -> None:
    with _lock, connect() as conn:
        conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(items, ensure_ascii=False)))


def norm_hint(text: str) -> str:
    return " ".join((text or "").split())


def norm_lines(text: str) -> str:
    """A list typed one item per line: trim every line and drop the empty ones."""
    return "\n".join(l.strip() for l in (text or "").splitlines() if l.strip())


# Recently used inputs, newest first. They live in the settings table under their own keys, which are not settings:
# the Settings page never offers them and never overwrites them.
RECENT = {"hints": ("recent_hints", norm_hint),          # extra instructions for "AI suggest fields"
          "field_lists": ("recent_field_lists", norm_lines)}  # field lists typed for batch templating


def get_recent(kind: str) -> list[str]:
    return _recent_get(RECENT[kind][0])


def add_recent(kind: str, text: str) -> list[str]:
    key, norm = RECENT[kind]
    text = norm(text)
    items = _recent_get(key)
    if text:
        items = ([text] + [i for i in items if i != text])[:MAX_RECENT]
        _recent_save(key, items)
    return items


def remove_recent(kind: str, text: str | None = None) -> list[str]:
    """Forget one entry, or all of them when text is None."""
    key, norm = RECENT[kind]
    items = [] if text is None else [i for i in _recent_get(key) if i != norm(text)]
    _recent_save(key, items)
    return items


def get_settings() -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    s = dict(DEFAULT_SETTINGS)
    for r in rows:
        s[r["key"]] = json.loads(r["value"])
    return s


def save_settings(values: dict) -> dict:
    with _lock, connect() as conn:
        for k, v in values.items():
            if k in DEFAULT_SETTINGS:
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, json.dumps(v, ensure_ascii=False)),
                )
    return get_settings()
