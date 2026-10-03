"""Two narrow, non-streaming HTTPS adapters, using the Python standard library."""
import base64
import json
import os
import secrets
import urllib.error
import urllib.request
from .core import encode, now, object_hash

PROVIDERS = {
    "openai": ("https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1/chat/completions", "MISTRAL_API_KEY"),
    "local": ("http://127.0.0.1:8876/v1/chat/completions", None),
}


def credentials_present(model):
    env = PROVIDERS[model["provider"]][1]
    return env is None or bool(os.environ.get(env))


def response_settings_match(model, config, normalized):
    tier = settings_for(model, config).get('service_tier')
    return tier is None or normalized.get('service_tier') == tier


def live_payload(model, config, text):
    body = payload(model, config, text)
    if model["provider"] == "local":
        body["seed"] = secrets.randbelow(2 ** 31)
    return body


def settings_for(model, config):
    # A model override is a complete profile, not a silent partial merge.
    s = model.get("settings", config["settings"])
    required = {"temperature", "max_tokens", "timeout_seconds"}
    optional = {"top_p", "top_k", "min_p"} if model["provider"] == "local" else {"service_tier"}
    if not required <= set(s) or set(s) - required - optional:
        raise ValueError("Unexpected settings; explicitly implement new controls")
    if not isinstance(s["max_tokens"], int) or isinstance(s["max_tokens"], bool):
        raise ValueError("Output cap must be an integer")
    if not isinstance(s["timeout_seconds"], (int, float)) or not 1 <= s["timeout_seconds"] <= 600:
        raise ValueError("Invalid timeout")
    return s


def fingerprint(model, config):
    return object_hash({"model": model, "settings": settings_for(model, config), "system_prompt": config["system_prompt"]})


def payload(model, config, text):
    s = settings_for(model, config)
    if model["provider"] not in PROVIDERS or "latest" in model["model"]:
        raise ValueError("Use a supported provider and pinned model ID")
    if model["provider"] == "local":
        if model["model"] != "Qwen3-4B-Instruct-2507-Q4_K_M" or model["reasoning"] != "not_supported":
            raise ValueError("Local adapter supports the selected non-thinking Qwen deployment only")
        if not {"top_p", "top_k", "min_p"} <= set(s):
            raise ValueError("Local sampler settings must be explicit")
        if not isinstance(s["temperature"], (int, float)) or not 0 < s["temperature"] <= 1 or not 1 <= s["max_tokens"] <= 2048 or not 0 < s["top_p"] <= 1 or s["top_k"] != 20 or s["min_p"] != 0:
            raise ValueError("Invalid reviewed local controls")
        return {"model": model["model"], "messages": [{"role": "system", "content": config["system_prompt"]}, {"role": "user", "content": text}], "stream": False, "max_tokens": s["max_tokens"], "temperature": s["temperature"], "top_p": s["top_p"], "top_k": s["top_k"], "min_p": s["min_p"], "seed": 0, "cache_prompt": False}
    sol = model["provider"] == "openai" and model["model"] == "gpt-6.1-sol"
    if sol:
        if s["temperature"] is not None:
            raise ValueError("Sol reasoning requests must omit temperature; set the explicit profile value to null")
        if model["reasoning"] not in ("low", "medium", "high", "xhigh", "max"):
            raise ValueError("Sol does not support none/minimal reasoning")
        if not 1 <= s["max_tokens"] <= 128000 or s.get("service_tier") != "default":
            raise ValueError("Sol needs a valid completion cap and explicit standard service_tier default")
    elif not isinstance(s["temperature"], (int, float)) or not 0 <= s["temperature"] <= 1 or not 1 <= s["max_tokens"] <= 4096:
        raise ValueError("Invalid pilot sampling controls")
    if "service_tier" in s and (model["provider"] != "openai" or s["service_tier"] != "default"):
        raise ValueError("Only explicitly priced OpenAI standard processing is supported")
    body = {"model": model["model"], "max_tokens": s["max_tokens"], "stream": False}
    if not sol:
        body["temperature"] = s["temperature"]
    if model["provider"] == "mistral":
        if model["reasoning"] != "none":
            raise ValueError("Mistral pilot must request reasoning_effort none")
        body.update(messages=[{"role": "system", "content": config["system_prompt"]}, {"role": "user", "content": text}], reasoning_effort="none", safe_prompt=False, tool_choice="none")
    else:
        if not sol and (model["model"] not in ("gpt-4.1-mini-2025-04-14", "gpt-4.1-nano-2025-04-14") or model["reasoning"] != "not_applicable"):
            raise ValueError("OpenAI adapter supports reviewed GPT-4.1 snapshots and GPT-6.1 Sol only")
        body.pop("max_tokens")
        body.update(max_completion_tokens=s["max_tokens"], messages=[{"role": "system", "content": config["system_prompt"]}, {"role": "user", "content": text}], store=False, n=1)
        if sol:
            body["reasoning_effort"] = model["reasoning"]
        if "service_tier" in s:
            body["service_tier"] = s["service_tier"]
    return body


def reserve_usd(model, config, body):
    # Deliberately generous bytes proxy plus 4,096 input-token allowance.
    # This is a conservative planning estimate, NOT a provider-enforced billing cap.
    if model["provider"] == "local":
        return 0.0  # No API charge; runtime/download costs are reported separately.
    rates = config["pricing_usd_per_million"][model["id"]]
    if rates["input"] <= 0 or rates["output"] <= 0:
        raise ValueError("Approve positive current token prices")
    return ((len(encode(body)) + 4096) * rates["input"] + settings_for(model, config)["max_tokens"] * rates["output"]) / 1_000_000


def decode_response(model, body):
    choice = body["choices"][0]
    content = choice["message"].get("content")
    if isinstance(content, list):
        text = "\n".join(b["text"] for b in content if b.get("type") == "text")
    else:
        text = content or ""
    reason = choice.get("finish_reason")
    blocked = reason in ("content_filter", "refusal")
    status = "provider_block" if blocked else ("truncated" if reason in ("length", "max_tokens") else ("received" if text else "empty"))
    return {"text": text, "status": status, "finish_reason": reason, "usage": body.get("usage"), "returned_model": body.get("model"), "service_tier": body.get("service_tier"), "system_fingerprint": body.get("system_fingerprint")}


def send(model, config, body):
    endpoint, env = PROVIDERS[model["provider"]]
    key = os.environ.get(env) if env else None
    if env and not key:
        raise ValueError(f"{env} is absent; set it outside saved artifacts")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    # Credentials are only attached here, never included in saved request records.
    request = urllib.request.Request(endpoint, data=encode(body), headers=headers, method="POST")
    result = {"started_at": now(), "endpoint": endpoint}
    try:
        with urllib.request.urlopen(request, timeout=settings_for(model, config)["timeout_seconds"]) as response:
            data = response.read()
            result.update(http_status=response.status, request_id=response.headers.get("request-id") or response.headers.get("x-request-id"))
    except urllib.error.HTTPError as error:
        data = error.read()
        result.update(http_status=error.code, request_id=error.headers.get("request-id") or error.headers.get("x-request-id"))
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        # Exception class only: proxy URLs/messages may contain sensitive data.
        result.update(http_status=None, error_type=type(error).__name__, raw_body_base64=None, normalized={"text": "", "status": "transport_failed"})
        result["ended_at"] = now()
        return result
    result.update(raw_body_base64=base64.b64encode(data).decode(), ended_at=now())
    try:
        parsed = json.loads(data)
        result["response_json"] = parsed
        if result["http_status"] == 200:
            result["normalized"] = decode_response(model, parsed)
        else:
            result["normalized"] = {"text": "", "status": "transport_failed"}
    except (ValueError, KeyError, TypeError, IndexError):
        result["normalized"] = {"text": "", "status": "transport_failed"}
        result["error_type"] = "UnparseableEnvelope"
    return result
