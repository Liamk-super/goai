from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    config = options.get("config") or {}
    if config.get("mode", "deterministic") == "deterministic":
        action = "REJECT" if "NO_EVIDENCE" in prompt else "MARK_UNKNOWN"
        return {
            "output": json.dumps(
                {"action": action, "provider": "deterministic-smoke", "network_used": False},
                sort_keys=True,
            )
        }
    if str(config.get("authorized", "false")).lower() != "true":
        return {"error": "Live Promptfoo provider requires authorized=true"}
    model = str(config.get("model", ""))
    base_url = os.getenv("AGENTTEAMS_MODEL_BASE_URL", "").rstrip("/")
    api_key = os.getenv("AGENTTEAMS_MODEL_API_KEY", "")
    if not model or not base_url or not api_key:
        return {"error": "Model, AGENTTEAMS_MODEL_BASE_URL and AGENTTEAMS_MODEL_API_KEY are required"}
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Return exactly one JSON object. Do not use Markdown or unsupported facts.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 500,
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read(4_194_305))
    except urllib.error.HTTPError as exc:
        status = f"HTTP_{exc.code}" if 400 <= exc.code < 500 else "SUBMISSION_UNKNOWN"
        return {"error": status}
    except (TimeoutError, urllib.error.URLError, OSError):
        return {"error": "SUBMISSION_UNKNOWN"}
    observed_model = str(payload.get("model", ""))
    if observed_model != model:
        return {"error": f"RUNTIME_MODEL_MISMATCH requested={model} observed={observed_model or '<missing>'}"}
    try:
        output = json.loads(payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {"error": "INVALID_PROVIDER_JSON"}
    usage = payload.get("usage") or {}
    return {
        "output": json.dumps(output, ensure_ascii=False, sort_keys=True),
        "tokenUsage": {
            "prompt": int(usage.get("prompt_tokens", 0)),
            "completion": int(usage.get("completion_tokens", 0)),
            "total": int(usage.get("total_tokens", 0)),
        },
        "metadata": {
            "requested_model": model,
            "observed_model": observed_model,
            "runtime_model_verified": True,
            "case_id": context.get("vars", {}).get("case_id"),
        },
    }
