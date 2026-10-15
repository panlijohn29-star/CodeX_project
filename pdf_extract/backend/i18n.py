"""Bilingual user-facing messages.

A message is {"zh": ..., "en": ...}; the frontend shows whichever language is active, so switching language
never needs a round trip. Text columns store the message as JSON (see enc). Rows written before this existed
hold plain Chinese strings, which text() passes through unchanged.
"""
import json


def bi(zh: str, en: str) -> dict:
    return {"zh": zh, "en": en}


def enc(msg) -> str:
    """Message -> string for a text column."""
    return msg if isinstance(msg, str) else json.dumps(msg, ensure_ascii=False)


def text(msg, lang: str = "zh") -> str:
    """Message (dict, encoded JSON or plain string) -> text in one language."""
    if isinstance(msg, str):
        if not msg.startswith('{"zh"'):
            return msg
        try:
            msg = json.loads(msg)
        except ValueError:
            return msg
    if isinstance(msg, dict):
        return msg.get(lang) or msg.get("zh") or ""
    return "" if msg is None else str(msg)


def join(msgs: list, zh_sep: str = "；", en_sep: str = "; ") -> dict:
    return bi(zh_sep.join(text(m, "zh") for m in msgs), en_sep.join(text(m, "en") for m in msgs))
