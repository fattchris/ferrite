"""Hermes agent backend — replaces Pi with two modes:

1. **LLM mode** (default): calls OpenRouter API directly for pure text agents
   (scout, reviewer, planner, documenter). No coding tools — just prompt in,
   JSON envelope out.

2. **CLI mode**: shells out to `claude` or `codex` CLI for coding agents
   (builder). Full tool access (read, edit, write, bash) in the agent's
   own session.

Both modes stream events to the tracer. LLM mode uses the OpenRouter
chat completions API with streaming. CLI mode tails the subprocess stdout.

The `coding_agent` field in sssf.config.yaml selects the backend:
  - "openrouter"  → LLM mode (pure text, no repo access)
  - "claude_code"  → CLI mode via `claude` binary
  - "codex"        → CLI mode via `codex` binary
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from .data_types import HermesRequest, HermesResult
from .utils import now_iso, operator_env

# ── API config ─────────────────────────────────────────────────────────────

OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")

LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
LITELLM_KEY = os.environ.get("LITELLM_API_KEY", os.environ.get("OPENROUTER_API_KEY", ""))

# CLI binary paths (checked at runtime, not install time)
CLAUDE_PATH = os.environ.get("CLAUDE_PATH", "claude")
CODEX_PATH = os.environ.get("CODEX_PATH", "codex")
OMP_PATH = os.environ.get("OMP_PATH", "omp")

# Max chars of tool output to keep in trace
RESULT_SNIPPET_CHARS = 20_000
ARG_VALUE_CHARS = 20_000
LABEL_CHARS = 80

PRIMARY_ARGS = ("command", "path", "file_path", "pattern", "query", "url")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _label(tool: str, args: dict) -> str:
    value = next((args[key] for key in PRIMARY_ARGS
                  if isinstance(args.get(key), str) and args[key].strip()), "")
    if not value:
        value = next((v for v in args.values() if isinstance(v, str) and v.strip()), "")
    value = " ".join(str(value).split())
    return f"{tool}: {_clip(value, LABEL_CHARS)}" if value else tool


# ── Model resolution ───────────────────────────────────────────────────────

def resolve_model(pattern: str) -> tuple[str, str]:
    """Resolve a model pattern to (provider, model_id).

    Patterns starting with `litellm/` route to the local LiteLLM proxy.
    Patterns with `openrouter/` or standard `provider/model` route to
    OpenRouter. Bare names are treated as OpenRouter model IDs.

    Examples:
      "litellm/deepseek-v4-flash" → ("litellm", "deepseek-v4-flash")
      "z-ai/glm-5.2"             → ("z-ai", "glm-5.2")
      "openai/gpt-5.6-luna"       → ("openai", "gpt-5.6-luna")
    """
    if pattern.startswith("litellm/"):
        return "litellm", pattern[len("litellm/"):]
    if "/" in pattern:
        provider, model_id = pattern.split("/", 1)
        return provider, model_id
    return "openrouter", pattern


def context_window(provider: str, model_id: str) -> int:
    """Return the model's context ceiling, or 0 if unknown."""
    known = {
        "glm-5.2": 200_000,
        "glm": 200_000,
        "gpt-5.6-luna": 1_050_000,
        "gpt-5.6-terra": 1_050_000,
        "gpt-5.6-sol": 1_050_000,
        "grok-4.5": 256_000,
        "deepseek-v4-flash": 131_072,
        "deepseek": 131_072,
        "laguna": 262_144,
        "qwen3-coder-next": 131_072,
        "qwen3.6-35b": 131_072,
        "qwen2.5-coder": 131_072,
        "gemini-3-flash": 1_000_000,
        "gemini-3-pro": 2_000_000,
    }
    for key, window in known.items():
        if key in model_id.lower():
            return window
    return 0


# ── OpenRouter LLM backend ──────────────────────────────────────────────────

class ToolCallTracker:
    """Folds a streaming response into normalized tool-call records.

    For LLM mode, there are no tool calls — the agent responds with text only.
    This tracker is a no-op for LLM mode but kept for CLI mode compatibility.
    """

    def __init__(self) -> None:
        self._open: dict[str, dict] = {}

    def observe(self, event: dict) -> Optional[dict]:
        """Returns the record for a finished tool call, else None."""
        # CLI mode: parse tool call events from claude/codex stdout
        etype = event.get("type", "")
        if etype == "tool_execution_end":
            call_id = str(event.get("toolCallId") or "")
            opened = self._open.pop(call_id, {})
            tool = str(event.get("toolName") or opened.get("tool") or "tool")
            args = event.get("args") or opened.get("args") or {}
            record = {
                "tool": tool,
                "tool_call_id": call_id,
                "args": {key: _clip(value, ARG_VALUE_CHARS) if isinstance(value, str) else value
                         for key, value in args.items()},
                "ok": not event.get("isError", False),
                "label": _label(tool, args),
            }
            result_text = event.get("result", "")
            if result_text:
                record["result_snippet"] = _clip(str(result_text), RESULT_SNIPPET_CHARS)
            record["ended_at"] = now_iso()
            if opened.get("clock"):
                record["duration_ms"] = int((time.monotonic() - opened["clock"]) * 1000)
            if opened.get("started_at"):
                record["started_at"] = opened["started_at"]
            return record
        if etype == "tool_execution_start":
            call_id = str(event.get("toolCallId") or "")
            if call_id:
                self._open[call_id] = {
                    "tool": event.get("toolName", ""),
                    "args": event.get("args", {}),
                    "started_at": now_iso(),
                    "clock": time.monotonic(),
                }
        return None


def _run_openai_compatible(request: HermesRequest,
                           base_url: str, api_key: str,
                           model_id: str,
                           on_event: Optional[Callable[[dict], None]] = None,
                           on_spawn: Optional[Callable[[int], None]] = None,
                           on_exit: Optional[Callable[[int], None]] = None,
                           extra_headers: dict | None = None) -> HermesResult:
    """Generic OpenAI-compatible streaming chat completions call.
    Used by both OpenRouter and LiteLLM backends.
    """
    provider, _ = resolve_model(request.model)
    result = HermesResult(
        session_id=request.session_id,
        context_window=context_window(provider, model_id),
    )

    messages = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    if request.history:
        messages.extend(request.history)
    messages.append({"role": "user", "content": request.prompt})

    body: dict = {
        "model": model_id,
        "messages": messages,
        "stream": True,
        "max_tokens": request.max_tokens or 16_384,
    }

    # Thinking level → reasoning effort (for models that support it)
    if request.thinking and request.thinking != "off":
        effort_map = {
            "minimal": "low", "low": "low",
            "medium": "medium", "high": "high",
            "xhigh": "high", "max": "high",
        }
        body["reasoning"] = {"effort": effort_map.get(request.thinking, "medium")}

    body_json = json.dumps(body)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body_json.encode("utf-8"),
        headers=headers,
        method="POST",
    )

    raw_path = Path(request.raw_output_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    if on_spawn:
        on_spawn(os.getpid())

    full_text = ""
    usage_data = {}

    try:
        with raw_path.open("a") as raw, urllib.request.urlopen(req, timeout=300) as resp:
            for line in resp:
                line = line.decode("utf-8").strip()
                raw.write(line + "\n")
                raw.flush()
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        full_text += content
                        if on_event:
                            on_event({"type": "content_delta", "text": content})
                if "usage" in chunk:
                    usage_data = chunk["usage"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        result.returncode = e.code
        result.text = ""
        if raw_path.exists():
            with raw_path.open("a") as raw:
                raw.write(f"\n[ERROR {e.code}] {error_body}\n")
        raise RuntimeError(f"API error {e.code}: {error_body[-800:]}")
    except Exception as e:
        result.returncode = 1
        raise RuntimeError(f"request failed: {e}")

    result.text = full_text
    result.returncode = 0

    if usage_data:
        result.tokens = usage_data.get("total_tokens", 0) or 0
        result.cost = float(usage_data.get("cost", 0) or 0)
        result.usage.input_tokens = usage_data.get("prompt_tokens", 0) or 0
        result.usage.output_tokens = usage_data.get("completion_tokens", 0) or 0
        result.context_tokens = result.tokens

    if on_exit:
        on_exit(os.getpid())
    return result


def _run_openrouter(request: HermesRequest,
                    on_event: Optional[Callable[[dict], None]] = None,
                    on_spawn: Optional[Callable[[int], None]] = None,
                    on_exit: Optional[Callable[[int], None]] = None) -> HermesResult:
    """OpenRouter backend — routes to the OpenAI-compatible API."""
    _, model_id = resolve_model(request.model)
    return _run_openai_compatible(
        request, OPENROUTER_BASE, OPENROUTER_KEY, model_id,
        on_event, on_spawn, on_exit,
        extra_headers={
            "HTTP-Referer": "https://hermes-agent.nousresearch.com",
            "X-Title": "SSSF/Hermes",
        })


def _run_litellm(request: HermesRequest,
                 on_event: Optional[Callable[[dict], None]] = None,
                 on_spawn: Optional[Callable[[int], None]] = None,
                 on_exit: Optional[Callable[[int], None]] = None) -> HermesResult:
    """LiteLLM proxy backend — local OpenAI-compatible API at :4000."""
    _, model_id = resolve_model(request.model)
    return _run_openai_compatible(
        request, LITELLM_BASE, LITELLM_KEY, model_id,
        on_event, on_spawn, on_exit)


# ── Claude Code CLI backend ─────────────────────────────────────────────────

def _run_claude_code(request: HermesRequest,
                     on_event: Optional[Callable[[dict], None]] = None,
                     on_spawn: Optional[Callable[[int], None]] = None,
                     on_exit: Optional[Callable[[int], None]] = None) -> HermesResult:
    """Run Claude Code CLI in non-interactive mode."""

    provider, model_id = resolve_model(request.model)
    result = HermesResult(
        session_id=request.session_id,
        context_window=context_window(provider, model_id),
    )

    cmd = [
        CLAUDE_PATH,
        "--print",                      # non-interactive
        "--output-format", "stream-json",
        "--verbose",                    # required by stream-json in --print mode
    ]
    if request.model:
        cmd += ["--model", request.model]
    if request.session_id:
        # Claude Code requires a valid UUID for --session-id; SSSF uses
        # descriptive IDs like "sssf-<adwid>-builder-<hash>". Skip the
        # flag when the ID isn't UUID-shaped — let Claude create its own.
        import uuid as _uuid
        try:
            _uuid.UUID(str(request.session_id))
            cmd += ["--session-id", str(request.session_id)]
        except (ValueError, AttributeError):
            pass  # not a UUID, let Claude generate one
    if request.system_prompt:
        cmd += ["--system-prompt", request.system_prompt]

    cmd.append(request.prompt)

    raw_path = Path(request.raw_output_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    tracker = ToolCallTracker()

    process = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, cwd=request.cwd,
        env=operator_env(),
    )
    if on_spawn:
        on_spawn(process.pid)

    full_text = ""
    with raw_path.open("a") as raw:
        assert process.stdout is not None
        for line in process.stdout:
            raw.write(line)
            raw.flush()
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")
            if etype == "assistant":
                # Extract text content
                for block in event.get("message", {}).get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        full_text += block.get("text", "")
                if event.get("usage"):
                    u = event["usage"]
                    result.tokens += u.get("total_tokens", 0) or 0
                    result.usage.input_tokens += u.get("input_tokens", 0) or 0
                    result.usage.output_tokens += u.get("output_tokens", 0) or 0
            else:
                record = tracker.observe(event)
                if record and on_event:
                    on_event({"type": "tool_call", **record})

    stderr = process.stderr.read() if process.stderr else ""
    result.returncode = process.wait()
    result.text = full_text
    result.context_tokens = result.tokens

    if on_exit:
        on_exit(process.pid)

    if result.returncode != 0 and not result.text:
        raise RuntimeError(f"claude exited {result.returncode}: {stderr.strip()[-800:]}")

    return result


# ── Codex CLI backend ───────────────────────────────────────────────────────

def _run_codex(request: HermesRequest,
               on_event: Optional[Callable[[dict], None]] = None,
               on_spawn: Optional[Callable[[int], None]] = None,
               on_exit: Optional[Callable[[int], None]] = None) -> HermesResult:
    """Run OpenAI Codex CLI in non-interactive mode."""

    provider, model_id = resolve_model(request.model)
    result = HermesResult(
        session_id=request.session_id,
        context_window=context_window(provider, model_id),
    )

    cmd = [
        CODEX_PATH,
        "--non-interactive",
        "--model", request.model,
    ]
    if request.system_prompt:
        cmd += ["--system-prompt", request.system_prompt]

    cmd.append(request.prompt)

    raw_path = Path(request.raw_output_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    tracker = ToolCallTracker()

    process = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, cwd=request.cwd,
        env=operator_env(),
    )
    if on_spawn:
        on_spawn(process.pid)

    full_text = ""
    with raw_path.open("a") as raw:
        assert process.stdout is not None
        for line in process.stdout:
            raw.write(line)
            raw.flush()
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Plain text output
                full_text += line + "\n"
                continue

            etype = event.get("type", "")
            if etype == "assistant":
                content = event.get("content", "")
                if isinstance(content, str):
                    full_text += content
                if event.get("usage"):
                    u = event["usage"]
                    result.tokens += u.get("total_tokens", 0) or 0
                    result.usage.input_tokens += u.get("input_tokens", 0) or 0
                    result.usage.output_tokens += u.get("output_tokens", 0) or 0
            else:
                record = tracker.observe(event)
                if record and on_event:
                    on_event({"type": "tool_call", **record})

    stderr = process.stderr.read() if process.stderr else ""
    result.returncode = process.wait()
    result.text = full_text
    result.context_tokens = result.tokens

    if on_exit:
        on_exit(process.pid)

    if result.returncode != 0 and not result.text:
        raise RuntimeError(f"codex exited {result.returncode}: {stderr.strip()[-800:]}")

    return result


# ── omp CLI backend ─────────────────────────────────────────────────────────

def _run_omp(request: HermesRequest,
             on_event: Optional[Callable[[dict], None]] = None,
             on_spawn: Optional[Callable[[int], None]] = None,
             on_exit: Optional[Callable[[int], None]] = None) -> HermesResult:
    """Run omp (Oh My Pi) CLI in non-interactive print mode.

    omp is a terminal coding agent with full file access (read, write, grep,
    bash, edit). It supports OpenRouter, LiteLLM, and local model providers.

    The model pattern in sssf.config.yaml should use the `openrouter/` prefix
    for OpenRouter models (e.g. `openrouter/z-ai/glm-5.2`) or `litellm/` prefix
    for local models (e.g. `litellm/deepseek-v4-flash`).
    """
    provider, model_id = resolve_model(request.model)
    result = HermesResult(
        session_id=request.session_id,
        context_window=context_window(provider, model_id),
    )

    # Build the omp model argument
    # omp expects: openrouter/<author>/<slug> or bare provider/model
    omp_model = request.model
    if not omp_model.startswith("openrouter/") and not omp_model.startswith("litellm/"):
        omp_model = f"openrouter/{omp_model}"

    thinking_level = request.thinking if request.thinking else "off"
    # Pass system prompt via --system-prompt flag so it overrides omp's
    # default coding-agent prompt. Without this, omp's built-in prompt
    # dominates and the agent ignores SSSF's JSON envelope instructions.
    system_prompt_text = request.system_prompt or ""
    cmd = [
        OMP_PATH,
        "-p",                          # non-interactive print mode
        "--model", omp_model,
        f"--thinking={thinking_level}",
        "--system-prompt", system_prompt_text,
    ]

    # Build the prompt — system prompt passed via --system-prompt flag,
    # so just the user prompt goes as the positional argument.
    cmd.append(request.prompt)

    raw_path = Path(request.raw_output_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    process = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, cwd=request.cwd,
        env=operator_env(),
    )
    if on_spawn:
        on_spawn(process.pid)

    full_text = ""
    with raw_path.open("a") as raw:
        assert process.stdout is not None
        for line in process.stdout:
            raw.write(line)
            raw.flush()
            # omp -p outputs plain text (no JSON streaming like claude)
            full_text += line

    stderr = process.stderr.read() if process.stderr else ""
    result.returncode = process.wait()
    result.text = full_text.strip()
    result.context_tokens = result.tokens

    if on_exit:
        on_exit(process.pid)

    if result.returncode != 0 and not result.text:
        raise RuntimeError(f"omp exited {result.returncode}: {stderr.strip()[-800:]}")

    return result


# ── Unified entry point ─────────────────────────────────────────────────────

def run(request: HermesRequest, on_event: Optional[Callable[[dict], None]] = None,
        on_spawn: Optional[Callable[[int], None]] = None,
        on_exit: Optional[Callable[[int], None]] = None) -> HermesResult:
    """Run one agent turn. Dispatches to the right backend.

    Backend is selected by `coding_agent` field + model prefix:
      - "openrouter"    → OpenRouter API (LLM mode, no tools)
      - "litellm"        → LiteLLM proxy at localhost:4000 (local models)
      - "pi"             → omp CLI (full coding tools, file access)
      - "claude_code"    → Claude Code CLI (full coding tools)
      - "codex"          → Codex CLI (full coding tools)

    Models prefixed with `litellm/` auto-route to LiteLLM regardless of
    coding_agent setting.
    """
    backend = request.coding_agent or "openrouter"

    # Auto-route litellm/ models to the LiteLLM backend, but ONLY when the
    # backend is openrouter (LLM-only). If the user explicitly chose a CLI
    # backend (pi, claude_code, codex), the model prefix is just a hint for
    # that CLI — don't override it.
    if request.model.startswith("litellm/") and backend == "openrouter":
        backend = "litellm"

    if backend == "litellm":
        return _run_litellm(request, on_event, on_spawn, on_exit)
    elif backend in ("pi", "omp"):
        return _run_omp(request, on_event, on_spawn, on_exit)
    elif backend == "claude_code":
        return _run_claude_code(request, on_event, on_spawn, on_exit)
    elif backend == "codex":
        return _run_codex(request, on_event, on_spawn, on_exit)
    else:
        return _run_openrouter(request, on_event, on_spawn, on_exit)
