"""Small OpenAI-compatible client for the InferDex LLM endpoint.

Uses SSE streaming for every call, not because callers want partial output --
`call()` still returns one accumulated string -- but because a reverse proxy in
front of a slow/remote endpoint (e.g. Cloudflare, which returns HTTP 524 if the
origin doesn't send *any* bytes back within its idle-timeout window) will hold
a streaming connection open indefinitely as long as chunks keep arriving,
where it would kill a long non-streaming request outright. A chapter draft can
take many minutes on a slow box; streaming is what makes that survive the tunnel.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Optional


DEFAULT_BASE_URL = os.environ.get("INFERDEX_BASE_URL", "https://llm.khosa.co/v1").rstrip("/")
DEFAULT_API_KEY = "sk-28ce12a9162e449cbed628d6326910fd"
DEFAULT_MODEL = os.environ.get(
    "NOVEL_HARNESS_MODEL",
    os.environ.get(
        "INFERDEX_MODEL",
        "DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF",
    ),
)

# Socket-level timeout: how long we'll wait for the *next* chunk, not for the whole
# response. A slow-but-alive stream keeps resetting this; only a truly stalled
# connection (no bytes at all for this long) trips it.
DEFAULT_CHUNK_TIMEOUT = 300


def _repair_json(text: str) -> Optional[dict]:
    """Best-effort recovery for a common small-model failure mode: the JSON is well-formed
    except for a missing trailing `}`/`]` (not a token-limit truncation -- the content is well
    under budget, the model just drops a closer) or a trailing comma before one. Returns a parsed
    dict if a cheap, deterministic repair round-trips through json.loads; otherwise None, and the
    caller falls through to its existing retry/error path unchanged.

    Bracket balancing walks the string tracking a stack of expected closers, skipping over
    characters inside string literals (respecting backslash escapes) so a `{` or `[` appearing
    inside a quoted value is never mistaken for real nesting."""
    candidate = re.sub(r",\s*([}\]])", r"\1", text)

    stack: list[str] = []
    in_string = False
    escaped = False
    for ch in candidate:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack and stack[-1] == ch:
            stack.pop()

    if in_string:
        return None  # an unterminated string literal isn't repairable here
    if stack:
        candidate += "".join(reversed(stack))
    if candidate == text:
        return None  # nothing was actually changed -- the original json.loads failure stands
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


class LLMClient:
    """Call an OpenAI-compatible chat-completions endpoint, always via SSE streaming."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_CHUNK_TIMEOUT,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = (
            api_key
            or os.environ.get("INFERDEX_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or DEFAULT_API_KEY
        )
        # Set by call() to the last stream's finish_reason ("stop", "length", ...) --
        # call_json() uses "length" to tell a truncated-response parse failure from a
        # genuinely malformed one, and retry with more room instead of just failing.
        self.last_finish_reason: Optional[str] = None

    def call(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        on_token: Optional[Any] = None,
    ) -> str:
        """Stream a completion and return the accumulated text.

        `on_token`, if given, is called with each incremental text chunk as it
        arrives -- useful for a caller that wants to show live progress. The
        return value is always the full accumulated string either way.
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "reasoning_effort": "none",
            "chat_template_kwargs": {"thinking": False, "enable_thinking": False},
            "stream": True,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "User-Agent": "NovelHarness/1.0",
            },
            method="POST",
        )

        parts: list[str] = []
        self.last_finish_reason = None
        try:
            # timeout here is a *per-recv* socket timeout, not a total-request
            # deadline -- as long as new bytes arrive within this window the
            # connection stays open no matter how long the whole stream runs.
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk: dict[str, Any] = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    if choice.get("finish_reason"):
                        self.last_finish_reason = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if piece is None and "message" in choice:
                        piece = (choice.get("message") or {}).get("content")
                    if piece is None:
                        piece = choice.get("text")
                    if piece:
                        parts.append(piece)
                        if on_token is not None:
                            on_token(piece)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"InferDex request failed: HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Could not reach InferDex endpoint: {error.reason}") from error
        except TimeoutError as error:
            raise RuntimeError(
                f"InferDex stream stalled: no data received for {self.timeout}s"
            ) from error

        content = "".join(parts)
        if not content:
            raise RuntimeError("InferDex stream produced no content (check model id / server logs)")
        return content

    def call_json(self, system: str, user: str, max_tokens: int = 2048, max_retry_tokens: int = 16384, _retried: bool = False) -> dict:
        """Call the model expecting one bare JSON object. `finish_reason` reporting turned out
        not to be reliable enough on every endpoint to gate a retry on it (that's what silently
        broke the original version of this fix), so instead: any JSON parse failure gets ONE
        retry at double the token budget (capped at max_retry_tokens), unconditionally, before
        giving up. A wasted retry costs a few extra seconds; a crashed pipeline after minutes of
        drafting costs a lot more. Raise max_retry_tokens for calls that must echo large amounts
        of source text back inside the JSON (e.g. speaker attribution)."""
        system_with_instruction = system + "\n\nRespond with ONLY a single JSON object. No prose, no markdown fences."
        raw = self.call(system_with_instruction, user, max_tokens=max_tokens, temperature=0)
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE).strip()
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError as error:
            repaired = _repair_json(cleaned)
            if repaired is not None:
                return repaired
            if not _retried and max_tokens < max_retry_tokens:
                retry_tokens = min(max_tokens * 2, max_retry_tokens)
                try:
                    return self.call_json(system, user, max_tokens=retry_tokens, max_retry_tokens=max_retry_tokens, _retried=True)
                except ValueError as retry_error:
                    raise ValueError(
                        f"Model did not return valid JSON at max_tokens={max_tokens} ({error}), and still "
                        f"didn't after retrying at max_tokens={retry_tokens}: {retry_error}\n"
                        f"First raw output:\n{raw}"
                    ) from error
            raise ValueError(f"Model did not return valid JSON: {error}\nRaw output:\n{raw}") from error
        if not isinstance(value, dict):
            raise ValueError(f"Model returned JSON that is not an object: {raw}")
        return value
