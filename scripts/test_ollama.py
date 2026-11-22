#!/usr/bin/env python3
"""Quick smoke test for an Ollama / OpenAI-compatible LLM endpoint.

Verifies:
1. TCP/HTTP reachability & listed models (/v1/models)
2. Basic chat completion latency (/v1/chat/completions)
3. Function/tool-calling support required by the ReAct agent
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def _request(
    url: str,
    *,
    api_key: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any], float]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw), round(time.perf_counter() - started, 2)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:400]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Connection failed to {url}: {exc.reason}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Ollama / OpenAI-compatible endpoint for ai-service")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("AI_OPENAI_BASE_URL", "http://localhost:11434/v1"),
        help="OpenAI-compatible base URL (default: $AI_OPENAI_BASE_URL or http://localhost:11434/v1)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AI_MODEL_NAME", "qwen3.5:9b").split("#", 1)[0].strip(),
        help="Model name (default: $AI_MODEL_NAME or qwen3.5:9b)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("AI_OPENAI_API_KEY", "ollama"),
        help="Bearer token if required (default: $AI_OPENAI_API_KEY or 'ollama')",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="Request timeout in seconds (default: 30)")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    print(f"[INFO] Testing endpoint={base} model={args.model} timeout={args.timeout}s")

    # 1. Check /models
    try:
        status, models_data, elapsed = _request(f"{base}/models", api_key=args.api_key, timeout=args.timeout)
        model_ids = [m.get("id") for m in models_data.get("data", []) if isinstance(m, dict)]
        print(f"[OK]   GET /models -> {status} ({elapsed}s), found {len(model_ids)} models: {model_ids[:10]}")
        if model_ids and args.model not in model_ids:
            print(f"[WARN] Model '{args.model}' is not in /models list; request may fail or trigger pull/alias.")
    except Exception as exc:
        print(f"[FAIL] GET /models failed: {exc}")
        return 1

    # 2. Basic chat completion
    try:
        status, chat_data, elapsed = _request(
            f"{base}/chat/completions",
            api_key=args.api_key,
            timeout=args.timeout,
            payload={
                "model": args.model,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 32,
                "temperature": 0,
            },
        )
        msg = chat_data["choices"][0]["message"]
        content = (msg.get("content") or "").strip()
        print(f"[OK]   Basic chat -> {status} ({elapsed}s), content={content!r}")
    except Exception as exc:
        print(f"[FAIL] Basic chat failed: {exc}")
        return 1

    # 3. Tool-calling check (required by ReAct agent)
    try:
        status, tool_data, elapsed = _request(
            f"{base}/chat/completions",
            api_key=args.api_key,
            timeout=args.timeout,
            payload={
                "model": args.model,
                "messages": [{"role": "user", "content": "Search the catalog for RTX 4060."}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "search_catalog",
                            "description": "Search PC parts in the store catalog.",
                            "parameters": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                                "required": ["query"],
                            },
                        },
                    }
                ],
                "tool_choice": "auto",
                "max_tokens": 128,
                "temperature": 0,
            },
        )
        msg = tool_data["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            print(f"[OK]   Tool calling -> {status} ({elapsed}s), tool_calls={tool_calls}")
        else:
            print(f"[WARN] Tool calling returned no tool_calls ({elapsed}s); content={(msg.get('content') or '')[:160]!r}")
    except Exception as exc:
        print(f"[FAIL] Tool calling check failed: {exc}")
        return 1

    print("[DONE] Endpoint is reachable and ready for ai-service.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
