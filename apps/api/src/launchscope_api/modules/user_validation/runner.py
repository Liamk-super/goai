"""Bounded subprocess adapter for the stateless Node UVD runner."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Protocol


class RunnerUnavailableError(RuntimeError):
    pass


class RunnerRejectedError(ValueError):
    def __init__(self, code: str, message: str, details: list[object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or []


class UserValidationRunner(Protocol):
    def invoke(self, request: dict[str, object]) -> dict[str, object]: ...


class NodeUserValidationRunner:
    def __init__(
        self,
        root: Path | None = None,
        *,
        executable: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self._root = root or Path(__file__).resolve().parents[6]
        self._entrypoint = self._root / "packages" / "user-validation-designer" / "runner" / "cli.mjs"
        self._executable: str = executable if executable is not None else os.getenv(
            "LAUNCHSCOPE_NODE_EXECUTABLE", "node"
        )
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("runner timeout must be between one and sixty seconds")
        self._timeout_seconds = timeout_seconds

    def invoke(self, request: dict[str, object]) -> dict[str, object]:
        payload = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(payload) > 2_000_000:
            raise RunnerRejectedError("REQUEST_TOO_LARGE", "runner request exceeds two megabytes")
        try:
            completed = subprocess.run(
                [self._executable, str(self._entrypoint)],
                cwd=self._root,
                input=payload,
                capture_output=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RunnerUnavailableError("user-validation runner is unavailable") from exc
        if completed.returncode != 0:
            raise RunnerUnavailableError("user-validation runner failed without a contract response")
        try:
            response = json.loads(completed.stdout)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RunnerUnavailableError("user-validation runner returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise RunnerUnavailableError("user-validation runner response must be an object")
        if response.get("status") == "error":
            raise RunnerRejectedError(
                str(response.get("error_code") or "RUNNER_REJECTED"),
                str(response.get("message") or "user-validation runner rejected the request"),
                response.get("details") if isinstance(response.get("details"), list) else [],
            )
        return response


__all__ = ["NodeUserValidationRunner", "RunnerRejectedError", "RunnerUnavailableError", "UserValidationRunner"]
