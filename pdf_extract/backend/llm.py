"""Unified structured-output calls for Claude, DeepSeek and any OpenAI-compatible endpoint."""
import base64
import json
import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar

import anthropic
import openai

from . import config, db, i18n
from .i18n import bi

log = logging.getLogger(__name__)

TOO_LONG = bi("模型输出超出长度上限（文档内容过多），请减少页数或字段",
              "Model output exceeded the length limit (too much document content); reduce the pages or fields")


class LLMError(Exception):
    """msg is a bilingual {"zh", "en"} message (or a plain string)."""

    def __init__(self, msg):
        self.msg = msg
        super().__init__(i18n.text(msg))


_scope: ContextVar[dict | None] = ContextVar("llm_usage_scope", default=None)


@contextmanager
def usage_scope(doc_id: int | None = None, source: str = "pipeline"):
    """Attribute the token usage of every model call inside the block to a document.

    Yields the scope; scope["timings"] collects the milliseconds spent in each stage() block.
    """
    doc = db.get("documents", doc_id) if doc_id else None
    scope = {"doc_id": doc_id, "batch_id": doc["batch_id"] if doc else None, "source": source, "timings": {}}
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


@contextmanager
def stage(name: str):
    """Add the time spent in the block to the current scope's timings (stages may nest or repeat)."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        sc = _scope.get()
        if sc is not None:
            t = sc["timings"]
            t[name] = t.get(name, 0) + (time.perf_counter() - t0) * 1000


def _record_usage(provider, model, purpose, input_tokens, output_tokens, cache_read=0, cache_write=0,
                  duration_ms=0) -> None:
    sc = _scope.get() or {}
    try:
        db.insert("llm_usage", {
            "doc_id": sc.get("doc_id"), "batch_id": sc.get("batch_id"), "source": sc.get("source", "other"),
            "purpose": purpose, "provider": provider, "model": model,
            "input_tokens": int(input_tokens or 0), "output_tokens": int(output_tokens or 0),
            "cache_read_tokens": int(cache_read or 0), "cache_write_tokens": int(cache_write or 0),
            "duration_ms": int(duration_ms or 0),
        })
    except Exception:  # noqa: BLE001 - usage bookkeeping must never break an extraction
        log.exception("failed to record token usage")


# A "part" is ("text", str) or ("image", bytes) — images are JPEG.
Part = tuple

_NO_FORCED_TOOL: set[str] = set()


def resolve(provider: str | None, model: str | None, fast: bool = False) -> tuple[str, str]:
    s = db.get_settings()
    if not provider or provider == "default":
        provider = s["default_provider"]
    if not model:
        if provider == "claude":
            model = s["claude_fast_model"] if fast else s["claude_model"]
        elif provider == "deepseek":
            model = s["deepseek_model"]
        else:
            model = s["custom_model"]
    return provider, model


# DeepSeek V4.1-Flash is natively multimodal; V4-Pro is not (the API silently drops images for it).
DEEPSEEK_VISION_MODELS = {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}


def supports_vision(provider: str, model: str | None = None) -> bool:
    if provider == "claude":
        return True
    if provider == "deepseek":
        return (model or db.get_settings()["deepseek_model"]) in DEEPSEEK_VISION_MODELS
    if provider == "custom":
        return bool(db.get_settings().get("custom_vision"))
    return False


def provider_status() -> dict:
    return {
        "claude": bool(config.ANTHROPIC_API_KEY),
        "deepseek": bool(config.DEEPSEEK_API_KEY),
        "custom": True,
    }


def _timestamp(value) -> int:
    """Release time as epoch seconds; 0 when the API does not say."""
    if hasattr(value, "timestamp"):
        return int(value.timestamp())
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _newest_first(models: list[dict]) -> list[dict]:
    return sorted(models, key=lambda m: (m["created"], m["id"]), reverse=True)


def list_models(provider: str) -> list[dict]:
    """Models the provider's API currently offers for the configured key: [{"id", "name", "created"}], newest first."""
    try:
        if provider == "claude":
            if not config.ANTHROPIC_API_KEY:
                raise LLMError(bi("未配置 Claude API Key", "Claude API key is not configured"))
            client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=1, timeout=30)
            found = [{"id": m.id, "name": getattr(m, "display_name", "") or m.id,
                      "created": _timestamp(getattr(m, "created_at", None))} for m in client.models.list(limit=100)]
            return _newest_first(found)
        if provider == "deepseek":
            if not config.DEEPSEEK_API_KEY:
                raise LLMError(bi("未配置 DeepSeek API Key", "DeepSeek API key is not configured"))
            client = openai.OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL,
                                   max_retries=1, timeout=30)
            found = [{"id": m.id, "name": m.id, "created": _timestamp(getattr(m, "created", None))}
                     for m in client.models.list()]
            return _newest_first(found)
    except LLMError:
        raise
    except Exception as e:  # noqa: BLE001 - surface the provider's own message to the settings page
        log.warning("listing %s models failed: %s", provider, e)
        raise LLMError(bi(f"获取模型列表失败：{e}", f"Could not fetch the model list: {e}")) from e
    raise LLMError(bi("不支持的服务商", "Unsupported provider"))


def call_structured(
    provider: str,
    model: str,
    system: str,
    parts: list[Part],
    schema: dict,
    max_tokens: int = 16000,
    purpose: str = "",
) -> dict:
    if provider == "claude":
        return _call_claude(model, system, parts, schema, max_tokens, purpose)
    return _call_openai_compat(provider, model, system, parts, schema, max_tokens, purpose)


def _call_claude(model, system, parts, schema, max_tokens, purpose="") -> dict:
    if not config.ANTHROPIC_API_KEY:
        raise LLMError(bi("未配置 ANTHROPIC_API_KEY（.env）", "ANTHROPIC_API_KEY is not set (.env)"))
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=3, timeout=600)
    content = []
    for p in parts:
        if p[0] == "text":
            content.append({"type": "text", "text": p[1]})
            if len(p) > 2 and p[2] == "cache":
                content[-1]["cache_control"] = {"type": "ephemeral"}
        else:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg",
                           "data": base64.standard_b64encode(p[1]).decode()},
            })
    tool = {
        "name": "submit_result",
        "description": "提交结构化的抽取结果。",
        "input_schema": schema,
    }
    system = system + "\n\n完成后必须调用 submit_result 工具提交结果，不要用普通文本回答。"
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        tools=[tool],
    )
    t0 = time.perf_counter()
    try:
        if model in _NO_FORCED_TOOL:
            resp = client.messages.create(tool_choice={"type": "auto"}, **kwargs)
        else:
            try:
                resp = client.messages.create(tool_choice={"type": "tool", "name": "submit_result"}, **kwargs)
            except anthropic.BadRequestError as e:
                # Newer models reject forced tool choice; fall back to auto + instruction.
                if "tool_choice" not in str(e):
                    raise
                _NO_FORCED_TOOL.add(model)
                resp = client.messages.create(tool_choice={"type": "auto"}, **kwargs)
    except anthropic.APIError as e:
        raise LLMError(bi(f"Claude 调用失败：{e}", f"Claude call failed: {e}")) from e
    u = resp.usage
    _record_usage("claude", model, purpose, u.input_tokens, u.output_tokens,
                  getattr(u, "cache_read_input_tokens", 0), getattr(u, "cache_creation_input_tokens", 0),
                  (time.perf_counter() - t0) * 1000)
    if resp.stop_reason == "max_tokens":
        raise LLMError(TOO_LONG)
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    text = "".join(b.text for b in resp.content if b.type == "text")
    if text.strip():
        return parse_json(text)
    raise LLMError(bi("Claude 没有返回结构化结果", "Claude returned no structured result"))


def _call_openai_compat(provider, model, system, parts, schema, max_tokens, purpose="") -> dict:
    s = db.get_settings()
    if provider == "deepseek":
        if not config.DEEPSEEK_API_KEY:
            raise LLMError(bi("未配置 DEEPSEEK_API_KEY（.env）", "DEEPSEEK_API_KEY is not set (.env)"))
        client = openai.OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL,
                               max_retries=3, timeout=600)
    else:
        client = openai.OpenAI(api_key=s["custom_api_key"] or "none", base_url=s["custom_base_url"],
                               max_retries=2, timeout=900)

    has_images = any(p[0] == "image" for p in parts)
    if has_images and not supports_vision(provider, model):
        raise LLMError(bi(f"{provider}/{model} 不支持图片输入，扫描件请改用 Claude 或 DeepSeek Flash",
                          f"{provider}/{model} does not accept images; use Claude or DeepSeek Flash for scanned documents"))

    # The schema comes first so the identical prefix can be served from the provider's prompt cache.
    content = [{
        "type": "text",
        "text": "请只输出一个 JSON 对象，必须符合下面的 JSON Schema，不要输出其他任何文字：\n"
                + json.dumps(schema, ensure_ascii=False),
    }]
    for p in parts:
        if p[0] == "text":
            content.append({"type": "text", "text": p[1]})
        else:
            b64 = base64.standard_b64encode(p[1]).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    content.append({"type": "text", "text": "请按上面的 JSON Schema，只输出一个 JSON 对象。"})
    if not has_images:
        # Plain string content is the most widely supported form for text-only models.
        content = "\n\n".join(c["text"] for c in content)

    kwargs = dict(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
        # Reasoning models spend part of the budget on thinking before the JSON.
        max_tokens=max(max_tokens, 32000),
        response_format={"type": "json_object"},
    )
    t0 = time.perf_counter()
    try:
        resp = client.chat.completions.create(temperature=0, **kwargs)
    except openai.BadRequestError:
        # Some endpoints/models reject temperature or response_format; retry minimal.
        kwargs.pop("response_format", None)
        try:
            resp = client.chat.completions.create(**kwargs)
        except openai.APIError as e:
            raise LLMError(bi(f"{provider} 调用失败：{e}", f"{provider} call failed: {e}")) from e
    except openai.APIError as e:
        raise LLMError(bi(f"{provider} 调用失败：{e}", f"{provider} call failed: {e}")) from e

    u = getattr(resp, "usage", None)
    if u:
        # prompt_tokens includes cached tokens; report them separately so they are priced separately.
        hit = getattr(u, "prompt_cache_hit_tokens", None)
        if hit is None:
            hit = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0)
        hit = hit or 0
        _record_usage(provider, model, purpose, max((u.prompt_tokens or 0) - hit, 0), u.completion_tokens, hit,
                      duration_ms=(time.perf_counter() - t0) * 1000)
    choice = resp.choices[0]
    if choice.finish_reason == "length":
        raise LLMError(TOO_LONG)
    return parse_json(choice.message.content or "")


def parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError(bi("模型返回的不是合法 JSON", "The model did not return valid JSON"))
