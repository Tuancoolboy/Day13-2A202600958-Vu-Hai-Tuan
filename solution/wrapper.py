"""Mitigation and observability layer for the opaque checkout agent.

The wrapper keeps the legal boundary: it only calls call_next(question, config).
It adds practical production controls around that boundary: prompt routing,
input cleanup, retry, cache, PII redaction, and telemetry.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
import time
import unicodedata

from telemetry.cost import cost_from_usage
from telemetry.logger import logger, new_correlation_id, set_correlation_id
from telemetry.redact import redact


_PROMPT_CACHE: str | None = None
_NOTE_MARKER_RE = re.compile(
    r"(?:^|[,;|\n])\s*(?:ghi\s*chu|loi\s*nhan|order\s*note|customer\s*note|note)\b\s*(?::|\uff1a|-)?",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(r"\b\d[\d.,]*\s*(?:VND|VNĐ|dong|đ)\b", re.IGNORECASE)
_ZERO_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
_PATHS_EXTENDED = False


def _extend_import_paths() -> None:
    """Let the PyInstaller simulator see packages installed in the user's env."""
    global _PATHS_EXTENDED
    if _PATHS_EXTENDED:
        return

    repo_root = os.path.dirname(os.path.dirname(__file__))
    brew_py312 = "/opt/homebrew/opt/python@3.12/Frameworks/Python.framework/Versions/3.12/lib/python3.12"
    candidates: list[str] = [
        os.path.join(repo_root, ".py312-deps"),
        brew_py312,
        os.path.join(brew_py312, "lib-dynload"),
    ]
    for base in (os.getenv("CONDA_PREFIX"), os.getenv("VIRTUAL_ENV")):
        if base:
            candidates.extend(_site_packages_under(base))

    home = os.path.expanduser("~")
    candidates.extend(_site_packages_under(os.path.join(home, "miniconda3")))
    candidates.extend(_site_packages_under(os.path.join(home, "anaconda3")))

    for path in reversed(candidates):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)

    _PATHS_EXTENDED = True
    _log("WRAPPER_ENV", {"python": sys.version.split()[0], "added_paths": candidates[:5]})


def _site_packages_under(base: str) -> list[str]:
    lib_dir = os.path.join(base, "lib")
    try:
        names = os.listdir(lib_dir)
    except OSError:
        return []

    paths: list[str] = []
    for name in names:
        if not name.startswith("python"):
            continue
        path = os.path.join(lib_dir, name, "site-packages")
        if os.path.isdir(path):
            paths.append(path)
    return paths


def _load_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is not None:
        return _PROMPT_CACHE

    here = os.path.dirname(__file__)
    path = os.path.join(here, "prompt.txt")
    with open(path, encoding="utf-8") as f:
        _PROMPT_CACHE = f.read().strip()
    return _PROMPT_CACHE


def _fold_for_search(text: str) -> str:
    chars: list[str] = []
    for ch in text:
        if ch in ("\u0111", "\u0110"):
            chars.append("d")
            continue
        decomposed = unicodedata.normalize("NFD", ch)
        base = "".join(c for c in decomposed if not unicodedata.combining(c))
        chars.append(base[0] if base else ch)
    return "".join(chars).lower()


def _sanitize_question(question: object) -> tuple[str, dict[str, object]]:
    raw = "" if question is None else str(question)
    normalized = unicodedata.normalize("NFC", raw).strip()
    folded = _fold_for_search(normalized)

    note_removed = False
    match = _NOTE_MARKER_RE.search(folded)
    if match:
        normalized = normalized[: match.start()].rstrip() + " [UNTRUSTED_ORDER_NOTE_REMOVED]"
        note_removed = True

    redacted, pii_count = redact(normalized)
    info = {
        "input_chars": len(raw),
        "sanitized_chars": len(redacted),
        "note_removed": note_removed,
        "input_pii_redactions": pii_count,
    }
    return redacted, info


def _slice_field(text: str, folded: str, pattern: str) -> str:
    match = re.search(pattern, folded, re.IGNORECASE)
    return text[match.start(1) : match.end(1)].strip(" ,.-") if match else ""


def _structure_question(question: str) -> tuple[str, dict[str, object]]:
    """Turn loose Vietnamese checkout prose into stable, explicit agent fields."""
    folded = _fold_for_search(question)
    shape = _order_shape(question)

    if shape["inventory_only"]:
        product = _slice_field(
            question,
            folded,
            r"(?:shop\s+)?con\s+(.+?)(?:\s+khong\b|\s+va\s+gia\b|[,.?]|$)",
        )
        if product:
            structured = f"Kiem tra ton kho va gia. Product: {product}."
            return structured, {**shape, "product": product, "coupon": "", "destination": ""}
        return question, {**shape, "product": "", "coupon": "", "destination": ""}

    product = _slice_field(
        question,
        folded,
        r"\bmua\s+\d+\s+(.+?)(?=\s+(?:dung\s+ma|ap\s+dung\s+ma|voi\s+coupon|ship\b|giao\b|tong\b|tinh\b)|[,?-]|$)",
    )
    coupon = _slice_field(
        question,
        folded,
        r"\b(?:dung\s+ma|ap\s+dung\s+ma|voi\s+coupon)\s+([a-z0-9_-]+)",
    )
    destination = _slice_field(
        question,
        folded,
        r"\b(?:ship|giao(?:\s+den)?)\s+(.+?)(?=\s+(?:tong\b|tinh\b|lien\s+he\b|goi\s+minh\b)|[,?-]|$)",
    )
    if not product:
        return question, {**shape, "product": "", "coupon": coupon, "destination": destination}

    parts = [f"Dat hang. Product: {product}. Quantity: {shape['quantity']}." ]
    if coupon:
        parts.append(f"Coupon: {coupon}.")
    if destination:
        parts.append(f"Destination: {destination}.")
    parts.append("Dung dung cac truong tren de goi tool; tinh tong VND.")
    return " ".join(parts), {
        **shape,
        "product": product,
        "coupon": coupon,
        "destination": destination,
    }


def _safe_config(config: dict[str, object]) -> dict[str, object]:
    conf = dict(config or {})
    conf.update(
        {
            "system_prompt": _load_prompt(),
            "temperature": min(float(conf.get("temperature", 0.2)), 0.2),
            "max_steps": min(int(conf.get("max_steps", 8)), 8),
            "loop_guard": True,
            "normalize_unicode": True,
            "redact_pii": True,
            "verbose_system": False,
            "max_completion_tokens": min(int(conf.get("max_completion_tokens", 512)), 512),
            "tool_budget": min(max(int(conf.get("tool_budget", 4)), 1), 4),
            "catalog_override": {},
            "tool_error_rate": 0.0,
            "session_drift_rate": 0.0,
        }
    )
    conf["retry"] = {"enabled": True, "max_attempts": 2, "backoff_ms": 120}
    conf["cache"] = {"enabled": True}
    return conf


def _cache_key(question: str, conf: dict[str, object]) -> str:
    model = str(conf.get("model", ""))
    provider = str(conf.get("provider", ""))
    compact_q = re.sub(r"\s+", " ", _fold_for_search(question)).strip()
    behavior = "|".join(
        str(conf.get(key, ""))
        for key in ("system_prompt", "temperature", "self_consistency", "tool_budget", "verify")
    )
    version = hashlib.sha256(behavior.encode("utf-8")).hexdigest()[:12]
    return "v4|" + version + "|" + provider + "|" + model + "|" + compact_q


def _cache_get(context: dict[str, object], key: str) -> dict[str, object] | None:
    cache = context.get("cache") if isinstance(context, dict) else None
    if cache is None:
        return None

    lock = context.get("cache_lock")
    if lock:
        with lock:
            value = cache.get(key)
    else:
        value = cache.get(key)

    if value is None:
        return None

    result = copy.deepcopy(value)
    meta = dict(result.get("meta") or {})
    meta.update({"cache_hit": True, "usage": dict(_ZERO_USAGE), "latency_ms": 0, "tools_used": []})
    result["meta"] = meta
    result["steps"] = 0
    result["trace"] = []
    return result


def _cache_put(context: dict[str, object], key: str, result: dict[str, object]) -> None:
    if result.get("status") != "ok" or not result.get("answer"):
        return

    cache = context.get("cache") if isinstance(context, dict) else None
    if cache is None:
        return

    value = copy.deepcopy(result)
    lock = context.get("cache_lock")
    if lock:
        with lock:
            cache[key] = value
    else:
        cache[key] = value


def _trace_stats(result: dict[str, object]) -> dict[str, object]:
    meta = result.get("meta") or {}
    trace = result.get("trace") or []
    tools = list(meta.get("tools_used") or [])
    actions: list[str] = []
    errors = 0

    for step in trace:
        if not isinstance(step, dict):
            continue
        action = step.get("tool") or step.get("action") or step.get("name")
        if action:
            actions.append(str(action))
        text = str(step)
        if "error" in text.lower():
            errors += 1

    if not tools:
        tools = actions

    return {
        "tools": tools,
        "tool_count": len(tools),
        "repeated_actions": max(0, len(actions) - len(set(actions))),
        "trace_errors": errors,
    }


def _tool_records(result: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    records: dict[str, list[dict[str, object]]] = {}
    for step in result.get("trace") or []:
        if not isinstance(step, dict):
            continue
        tool = step.get("tool") or step.get("name") or step.get("action")
        tool_name = str(tool or "").split("(", 1)[0].strip()
        observation = step.get("observation")
        if not isinstance(observation, dict):
            observation = step.get("result") or step.get("output")
        if isinstance(observation, str):
            try:
                parsed = json.loads(observation)
                observation = parsed if isinstance(parsed, dict) else observation
            except (TypeError, ValueError):
                pass
        if tool_name and isinstance(observation, dict):
            records.setdefault(tool_name, []).append(observation)
    return records


def _walk_dicts(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _find_value(value: object, *keys: str) -> object | None:
    wanted = {key.casefold() for key in keys}
    for mapping in _walk_dicts(value):
        for key, item in mapping.items():
            if str(key).casefold() in wanted and item is not None:
                return item
    return None


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold_for_search(str(key)))


def _find_number(value: object, aliases: tuple[str, ...], excludes: tuple[str, ...] = ()) -> float | None:
    exact = _find_value(value, *aliases)
    number = _as_number(exact)
    if number is not None:
        return number
    if isinstance(exact, (dict, list)):
        for mapping in _walk_dicts(exact):
            for item in mapping.values():
                number = _as_number(item)
                if number is not None:
                    return number

    normalized_aliases = tuple(_normalized_key(alias) for alias in aliases)
    normalized_excludes = tuple(_normalized_key(item) for item in excludes)
    for mapping in _walk_dicts(value):
        for key, item in mapping.items():
            normalized = _normalized_key(key)
            if any(blocked and blocked in normalized for blocked in normalized_excludes):
                continue
            if any(alias and alias in normalized for alias in normalized_aliases):
                number = _as_number(item)
                if number is not None:
                    return number
                if isinstance(item, (dict, list)):
                    for child in _walk_dicts(item):
                        for child_value in child.values():
                            number = _as_number(child_value)
                            if number is not None:
                                return number
    return None


def _as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    cleaned = value.strip().replace("_", "")
    if re.fullmatch(r"-?\d{1,3}(?:[.,]\d{3})+", cleaned):
        cleaned = cleaned.replace(",", "").replace(".", "")
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return float(match.group(0)) if match else None


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        folded = value.strip().casefold()
        if folded in {"true", "yes", "ok", "available", "supported", "valid", "in_stock"}:
            return True
        if folded in {"false", "no", "unavailable", "unsupported", "invalid", "out_of_stock"}:
            return False
    return None


def _order_shape(question: str) -> dict[str, object]:
    folded = _fold_for_search(question)
    qty_match = re.search(r"\b(?:mua\s+|quantity\s*:\s*)(\d+)\b", folded)
    coupon_match = re.search(
        r"\b(?:dung\s+ma|ap\s+dung\s+ma|voi\s+coupon|coupon\s*:)\s+([a-z0-9_-]+)", folded
    )
    return {
        "quantity": max(1, int(qty_match.group(1))) if qty_match else 1,
        "inventory_only": bool(
            re.search(r"\b(?:shop\s+)?con\b.*\bgia\b|\bkiem\s+tra\s+ton\s+kho\s+va\s+gia\b", folded)
        ),
        "needs_discount": coupon_match is not None,
        "needs_shipping": bool(
            re.search(r"\b(?:ship|giao)\b|\bdestination\s*:", folded)
        ),
    }


def _observation_failed(observation: object, *markers: str) -> bool:
    marker_set = {_normalized_key(marker) for marker in markers}
    for mapping in _walk_dicts(observation):
        for key, value in mapping.items():
            normalized_key = _normalized_key(key)
            if normalized_key in {"error", "errorcode", "status", "reason"}:
                text = _normalized_key(value)
                if any(marker and marker in text for marker in marker_set):
                    return True
            if normalized_key in marker_set and _as_bool(value) is True:
                return True
    return False


def _tool_schema(result: dict[str, object]) -> dict[str, list[str]]:
    schema: dict[str, list[str]] = {}
    for tool, rows in _tool_records(result).items():
        keys = {str(key) for row in rows for mapping in _walk_dicts(row) for key in mapping}
        schema[tool] = sorted(keys)[:30]
    return schema


def _required_tools_present(question: str, result: dict[str, object]) -> bool:
    shape = _order_shape(question)
    records = _tool_records(result)
    if not records.get("check_stock"):
        return False
    if shape["inventory_only"]:
        return True
    if shape["needs_discount"] and not records.get("get_discount"):
        return False
    if shape["needs_shipping"] and not records.get("calc_shipping"):
        return False
    return True


def _validate_answer(question: str, result: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    result = copy.deepcopy(result)
    shape = _order_shape(question)
    records = _tool_records(result)
    info: dict[str, object] = {"guardrail": "pass", "recomputed_total": False}

    stock_rows = records.get("check_stock") or []
    if not stock_rows:
        info["guardrail"] = "missing_stock_trace"
        return result, info
    stock = stock_rows[-1]
    found = _as_bool(_find_value(stock, "found", "exists"))
    in_stock = _as_bool(_find_value(stock, "in_stock", "available"))
    stock_qty = _find_number(
        stock,
        (
            "stock",
            "stock_qty",
            "available",
            "available_qty",
            "available_units",
            "inventory",
            "quantity_available",
        ),
        ("in_stock", "price", "weight"),
    )
    unit_price = _find_number(
        stock,
        ("unit_price", "unit_price_vnd", "price", "price_vnd", "price_per_unit"),
        ("discount", "shipping", "total"),
    )
    qty = int(shape["quantity"])

    unavailable = found is False or in_stock is False or _observation_failed(
        stock, "item_not_found", "out_of_stock", "not_found"
    )
    if unavailable or (stock_qty is not None and qty > int(stock_qty)):
        result["answer"] = "Khong the dat hang: san pham khong co hoac khong du ton kho. (no total)"
        info["guardrail"] = "stock_refusal"
        return result, info

    if shape["inventory_only"]:
        if unit_price is not None:
            result["answer"] = f"Con hang. Gia: {int(unit_price)} VND."
            info["guardrail"] = "inventory_answer"
        return result, info

    if unit_price is None:
        info["guardrail"] = "missing_unit_price"
        return result, info

    discount_pct = 0
    if shape["needs_discount"]:
        discount_rows = records.get("get_discount") or []
        if not discount_rows:
            info["guardrail"] = "missing_discount_trace"
            return result, info
        discount = discount_rows[-1]
        valid = _as_bool(_find_value(discount, "valid", "is_valid", "active"))
        if valid is not False and not _observation_failed(discount, "expired", "invalid"):
            pct = _find_number(
                discount,
                ("discount_pct", "discount_percent", "percent", "pct", "percentage"),
            )
            discount_pct = int(pct or 0)
    discount_pct = min(100, max(0, discount_pct))

    shipping = 0
    if shape["needs_shipping"]:
        shipping_rows = records.get("calc_shipping") or []
        if not shipping_rows:
            info["guardrail"] = "missing_shipping_trace"
            return result, info
        shipping_obs = shipping_rows[-1]
        supported = _as_bool(_find_value(shipping_obs, "supported", "is_supported", "available"))
        if supported is False or _observation_failed(
            shipping_obs, "unsupported", "not_served", "not_supported"
        ):
            result["answer"] = "Khong the dat hang: dia diem giao hang khong duoc ho tro. (no total)"
            info["guardrail"] = "shipping_refusal"
            return result, info
        shipping_value = _find_number(
            shipping_obs,
            ("shipping", "shipping_fee", "shipping_cost", "shipping_cost_vnd", "fee", "cost", "amount"),
            ("weight", "distance"),
        )
        if shipping_value is None:
            info["guardrail"] = "missing_shipping_fee"
            return result, info
        shipping = int(shipping_value)

    subtotal = int(unit_price) * qty
    discounted = subtotal * (100 - discount_pct) // 100
    total = discounted + shipping
    result["answer"] = f"Tong cong: {total} VND"
    info.update(
        {
            "guardrail": "recomputed",
            "recomputed_total": True,
            "quantity": qty,
            "discount_pct": discount_pct,
        }
    )
    meta = dict(result.get("meta") or {})
    meta["guardrail_recomputed"] = True
    result["meta"] = meta
    return result, info


def _normalize_total_answer(result: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(result)
    answer = result.get("answer")
    if not isinstance(answer, str):
        return result
    matches = re.findall(r"Tong\s+cong\s*:\s*([0-9][0-9.,]*)\s*VND", answer, re.IGNORECASE)
    if not matches:
        return result
    digits = re.sub(r"[^0-9]", "", matches[-1])
    if digits:
        result["answer"] = f"Tong cong: {int(digits)} VND"
    return result


def _redact_answer(result: dict[str, object]) -> tuple[dict[str, object], int]:
    result = copy.deepcopy(result)
    answer = result.get("answer")
    protected: list[str] = []

    def protect_money(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"[SAFE_MONEY_{len(protected) - 1}]"

    prepared = _MONEY_RE.sub(protect_money, answer) if isinstance(answer, str) else answer
    redacted, count = redact(prepared)
    if isinstance(redacted, str):
        for index, money in enumerate(protected):
            redacted = redacted.replace(f"[SAFE_MONEY_{index}]", money)
    if count:
        result["answer"] = redacted
    meta = dict(result.get("meta") or {})
    meta["output_pii_redactions"] = count
    meta.setdefault("cache_hit", False)
    result["meta"] = meta
    return result, count


def _log(event: str, data: dict[str, object]) -> None:
    try:
        logger.log_event(event, data)
    except Exception:
        pass


def _safe_error_message(exc: Exception) -> str:
    message = str(exc)
    redacted, _ = redact(message)
    return redacted[:500]


def _fallback(status: str, message: str, started: float, attempts: int) -> dict[str, object]:
    return {
        "answer": message,
        "status": status,
        "steps": 0,
        "trace": [],
        "meta": {
            "latency_ms": int((time.time() - started) * 1000),
            "usage": dict(_ZERO_USAGE),
            "tools_used": [],
            "cache_hit": False,
            "attempts": attempts,
        },
    }


def mitigate(call_next, question, config, context):
    context = context or {}
    cid = str(context.get("qid") or new_correlation_id())
    set_correlation_id(cid)

    started = time.time()
    _extend_import_paths()
    clean_question, clean_info = _sanitize_question(question)
    agent_question, order_info = _structure_question(clean_question)
    conf = _safe_config(config)
    key = _cache_key(agent_question, conf)

    cached = _cache_get(context, key)
    if cached is not None:
        _log(
            "WRAPPER_CACHE_HIT",
            {
                "qid": context.get("qid"),
                "session_id": context.get("session_id"),
                "turn_index": context.get("turn_index"),
                **clean_info,
            },
        )
        return cached

    retry_conf = conf.get("retry") or {}
    max_attempts = int(retry_conf.get("max_attempts", 1)) if retry_conf.get("enabled", True) else 1
    max_attempts = max(1, min(max_attempts, 3))
    backoff_ms = int(retry_conf.get("backoff_ms", 0))

    result: dict[str, object] | None = None
    guardrail_info: dict[str, object] = {"guardrail": "not_run", "recomputed_total": False}
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        try:
            attempt_result = call_next(agent_question, conf)
        except Exception as exc:
            last_error = exc.__class__.__name__
            _log(
                "WRAPPER_ATTEMPT_ERROR",
                {
                    "attempt": attempt,
                    "error": last_error,
                    "missing_module": getattr(exc, "name", ""),
                    "message": _safe_error_message(exc),
                    **clean_info,
                },
            )
            attempt_result = None

        if isinstance(attempt_result, dict):
            attempt_result, guardrail_info = _validate_answer(agent_question, attempt_result)
            attempt_result = _normalize_total_answer(attempt_result)
            attempt_result, _ = _redact_answer(attempt_result)
            meta = dict(attempt_result.get("meta") or {})
            meta["attempts"] = attempt
            meta["cache_hit"] = False
            attempt_result["meta"] = meta
            result = attempt_result

            if (
                result.get("status") == "ok"
                and result.get("answer")
                and (
                    _required_tools_present(agent_question, result)
                    or guardrail_info.get("guardrail") in {"stock_refusal", "shipping_refusal"}
                )
            ):
                break

        if attempt < max_attempts and backoff_ms > 0:
            time.sleep(backoff_ms / 1000.0)

    if result is None:
        result = _fallback(
            "wrapper_error",
            "Khong the tinh tong tien vi he thong dang loi tam thoi. Vui long thu lai. (no total)",
            started,
            max_attempts,
        )

    stats = _trace_stats(result)
    meta = result.get("meta") or {}
    usage = meta.get("usage") or {}
    wall_ms = int((time.time() - started) * 1000)
    model = str(meta.get("model") or conf.get("model") or "")

    _log(
        "WRAPPER_CALL",
        {
            "qid": context.get("qid"),
            "session_id": context.get("session_id"),
            "turn_index": context.get("turn_index"),
            "status": result.get("status"),
            "wall_ms": wall_ms,
            "latency_ms": meta.get("latency_ms"),
            "steps": result.get("steps"),
            "attempts": meta.get("attempts"),
            "usage": usage,
            "cost_usd": cost_from_usage(model, usage),
            "last_error": last_error,
            **clean_info,
            "structured_input": agent_question != clean_question,
            "parsed_product": bool(order_info.get("product")),
            **stats,
            **guardrail_info,
            "tool_schema": _tool_schema(result),
        },
    )

    if result.get("status") == "ok":
        _cache_put(context, key, result)

    return result
