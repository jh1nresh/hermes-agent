"""OpenAI-compatible shim that forwards Hermes requests to ``codex exec --json``.

This adapter lets Hermes treat the OpenAI Codex CLI as a chat-style backend.
Each request spawns ``codex exec --json --ephemeral --dangerously-bypass-approvals-and-sandbox``,
parses the JSONL event stream, extracts the agent message text and token usage,
and converts the result into the minimal shape Hermes expects from an OpenAI client.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

_CODEX_CLI_BASE_URL = "codex-cli://local"
_DEFAULT_TIMEOUT_SECONDS = 900.0


def _resolve_command() -> str:
    return (
        os.getenv("HERMES_CODEX_CLI_COMMAND", "").strip()
        or os.getenv("CODEX_CLI_PATH", "").strip()
        or "codex"
    )


def _resolve_args() -> list[str]:
    raw = os.getenv("HERMES_CODEX_CLI_ARGS", "").strip()
    if not raw:
        return [
            "exec",
            "--json",
            "--ephemeral",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
        ]
    import shlex
    return shlex.split(raw)


def _build_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    # Preserve HOME so codex can find ~/.codex/auth.json
    home = os.environ.get("HOME", "")
    if not home:
        home = os.path.expanduser("~")
    if home and home != "~":
        env["HOME"] = home
    return env


def _parse_turn_completed_usage(event: dict[str, Any]) -> SimpleNamespace:
    usage = event.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    cached_tokens = int(usage.get("cached_input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    reasoning_tokens = int(usage.get("reasoning_output_tokens") or 0)
    return SimpleNamespace(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens + reasoning_tokens,
        total_tokens=input_tokens + output_tokens + reasoning_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
    )


class _CodexCLIChatCompletions:
    def __init__(self, client: "CodexCLIClient"):
        self._client = client

    def create(self, **kwargs: Any) -> Any:
        return self._client._create_chat_completion(**kwargs)


class _CodexCLIChatNamespace:
    def __init__(self, client: "CodexCLIClient"):
        self.completions = _CodexCLIChatCompletions(client)


class CodexCLIClient:
    """Minimal OpenAI-client-compatible facade for Codex CLI."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_headers: dict[str, str] | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        **_: Any,
    ):
        self.api_key = api_key or "codex-cli"
        self.base_url = base_url or _CODEX_CLI_BASE_URL
        self._default_headers = dict(default_headers or {})
        self._command = command or _resolve_command()
        self._args = list(args or _resolve_args())
        self.chat = _CodexCLIChatNamespace(self)
        self.is_closed = False
        self._active_process: subprocess.Popen[str] | None = None
        self._active_process_lock = threading.Lock()

    def close(self) -> None:
        proc: subprocess.Popen[str] | None
        with self._active_process_lock:
            proc = self._active_process
            self._active_process = None
        self.is_closed = True
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _build_prompt(self, messages: list[dict[str, Any]], model: str | None = None) -> str:
        sections: list[str] = [
            "You are being used as the active Codex CLI agent backend for Hermes.",
            "Respond to the user's request directly. Do NOT call tools — Hermes handles tools.",
        ]
        if model:
            sections.append(f"Hermes requested model hint: {model}")

        transcript: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "unknown").strip().lower()
            content = message.get("content")
            if content is None:
                continue
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict) and "text" in item:
                        parts.append(str(item["text"]))
                content = "\n".join(parts).strip()
            if not content:
                continue
            label = {
                "system": "System",
                "user": "User",
                "assistant": "Assistant",
                "tool": "Tool",
            }.get(role, role.title())
            transcript.append(f"{label}:\n{content}")

        if transcript:
            sections.append("Conversation transcript:\n\n" + "\n\n".join(transcript))

        sections.append("Continue the conversation from the latest user request.")
        return "\n\n".join(s.strip() for s in sections if s and s.strip())

    def _create_chat_completion(
        self,
        *,
        model: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        timeout: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **_: Any,
    ) -> Any:
        prompt_text = self._build_prompt(messages or [], model=model)

        # Normalise timeout: run_agent.py may pass an httpx.Timeout object
        if timeout is None:
            effective_timeout = _DEFAULT_TIMEOUT_SECONDS
        elif isinstance(timeout, (int, float)):
            effective_timeout = float(timeout)
        else:
            candidates = [
                getattr(timeout, attr, None)
                for attr in ("read", "write", "connect", "pool", "timeout")
            ]
            numeric = [float(v) for v in candidates if isinstance(v, (int, float))]
            effective_timeout = max(numeric) if numeric else _DEFAULT_TIMEOUT_SECONDS

        response_text, usage = self._run_prompt(prompt_text, timeout_seconds=effective_timeout)

        assistant_message = SimpleNamespace(
            content=response_text,
            tool_calls=[],
            reasoning=None,
            reasoning_content=None,
            reasoning_details=None,
        )
        choice = SimpleNamespace(message=assistant_message, finish_reason="stop")
        return SimpleNamespace(
            choices=[choice],
            usage=usage,
            model=model or "codex-cli",
        )

    def _run_prompt(self, prompt_text: str, *, timeout_seconds: float) -> tuple[str, SimpleNamespace]:
        cmd = [self._command] + self._args
        # The prompt is a positional arg — pass it via stdin with pipe
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=_build_subprocess_env(),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Could not start Codex CLI command '{self._command}'. "
                "Install Codex CLI (npm install -g @openai/codex) or set "
                f"HERMES_CODEX_CLI_COMMAND / CODEX_CLI_PATH."
            ) from exc

        if proc.stdin is None or proc.stdout is None:
            proc.kill()
            raise RuntimeError("Codex CLI process did not expose stdin/stdout pipes.")

        self.is_closed = False
        with self._active_process_lock:
            self._active_process = proc

        response_parts: list[str] = []
        usage = SimpleNamespace(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        )
        stderr_lines: list[str] = []

        try:
            # Write prompt to stdin and close it to signal end of input
            proc.stdin.write(prompt_text)
            proc.stdin.close()

            deadline = time.monotonic() + timeout_seconds
            stdout_thread = threading.Thread(target=lambda: None, daemon=True)

            # Collect stdout lines
            stdout_lines: list[str] = []

            def _read_stdout():
                if proc.stdout is None:
                    return
                for line in proc.stdout:
                    stdout_lines.append(line.rstrip("\n"))

            stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
            stdout_thread.start()

            # We'll also collect stderr
            stderr_output: list[str] = []

            def _read_stderr():
                if proc.stderr is None:
                    return
                for line in proc.stderr:
                    stderr_output.append(line.rstrip("\n"))

            stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
            stderr_thread.start()

            # Wait for process to complete or timeout
            remaining = deadline - time.monotonic()
            while remaining > 0:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
                remaining = deadline - time.monotonic()

            if proc.poll() is None:
                proc.kill()
                raise TimeoutError("Timed out waiting for Codex CLI response.")

            # Wait for threads to finish reading
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)

            # Parse JSONL output
            agent_text = ""
            for line in stdout_lines:
                try:
                    event = json.loads(line)
                except Exception:
                    # Non-JSON line (banner, status) — skip
                    continue
                event_type = event.get("type", "")
                if event_type == "item.completed":
                    item = event.get("item") or {}
                    if item.get("type") == "agent_message":
                        text = item.get("text") or ""
                        if text:
                            agent_text += text
                elif event_type == "turn.completed":
                    usage = _parse_turn_completed_usage(event)

            if agent_text:
                response_parts.append(agent_text)

            # Stderr with useful diagnostics
            for line in stderr_output:
                if line.strip():
                    stderr_lines.append(line)
            if stderr_lines and not agent_text:
                raise RuntimeError(
                    "Codex CLI produced no agent message. "
                    f"stderr: {'; '.join(stderr_lines[-5:])}"
                )

            return "\n".join(response_parts).strip(), usage

        finally:
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            with self._active_process_lock:
                if self._active_process is proc:
                    self._active_process = None
