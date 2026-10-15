"""Token usage records and estimated cost (USD, from the price table in settings)."""
from datetime import datetime, timezone

from . import db

# DeepSeek peak hours, UTC, Monday-Friday. Chinese public holidays are not accounted for.
PEAK_HOURS_UTC = ((1, 4), (6, 10))


def _num(p: dict, key: str) -> float:
    try:
        return float(p.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def find_price(model: str, prices: dict) -> dict | None:
    if model in prices:
        return prices[model]
    matches = [k for k in prices if model.startswith(k)]
    return prices[max(matches, key=len)] if matches else None


def _is_peak(created_at: str) -> bool:
    try:
        dt = datetime.fromisoformat(created_at).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return False
    return dt.weekday() < 5 and any(a <= dt.hour < b for a, b in PEAK_HOURS_UTC)


def row_cost(r: dict, prices: dict) -> float | None:
    """Estimated cost of one call; None when the model has no price (local models are free)."""
    p = find_price(r["model"], prices)
    if p is None:
        return 0.0 if r["provider"] == "custom" else None
    inp, out = _num(p, "input"), _num(p, "output")
    cache_read, cache_write = _num(p, "cache_read"), _num(p, "cache_write")
    prompt = r["input_tokens"] + r["cache_read_tokens"] + r["cache_write_tokens"]
    if _num(p, "long_threshold") and prompt > _num(p, "long_threshold") and _num(p, "long_input"):
        scale = _num(p, "long_input") / inp if inp else 1
        inp, out = _num(p, "long_input"), _num(p, "long_output") or out
        cache_read, cache_write = cache_read * scale, cache_write * scale
    cost = (r["input_tokens"] * inp + r["output_tokens"] * out
            + r["cache_read_tokens"] * cache_read + r["cache_write_tokens"] * cache_write) / 1e6
    mult = _num(p, "peak_multiplier") or 1
    if mult != 1 and _is_peak(r["created_at"]):
        cost *= mult
    return cost


def load_rows(where: str = "", params: tuple = ()) -> list[dict]:
    prices = db.get_settings().get("prices") or {}
    rows = db.list_rows("llm_usage", where, params, order="id")
    for r in rows:
        r["cost"] = row_cost(r, prices)
    return rows


def total(rows: list[dict]) -> dict:
    t = {"calls": len(rows), "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0, "unpriced": 0,
         "duration_ms": 0, "timed": 0}
    for r in rows:
        # Calls recorded before durations were tracked have 0 and are left out of the average.
        if r.get("duration_ms"):
            t["duration_ms"] += r["duration_ms"]
            t["timed"] += 1
        t["input"] += r["input_tokens"]
        t["output"] += r["output_tokens"]
        t["cache_read"] += r["cache_read_tokens"]
        t["cache_write"] += r["cache_write_tokens"]
        if r["cost"] is None:
            t["unpriced"] += 1
        else:
            t["cost"] += r["cost"]
    t["tokens"] = t["input"] + t["output"] + t["cache_read"] + t["cache_write"]
    t["cost"] = round(t["cost"], 6)
    return t


def group(rows: list[dict], keyfn) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(keyfn(r), []).append(r)
    return {k: total(v) for k, v in out.items()}
