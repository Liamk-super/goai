"""Execution-mode admission for real evaluation dispatch."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def execution_mode() -> str:
    return os.getenv("LAUNCHSCOPE_EXECUTION_MODE", "UNSPECIFIED").strip().upper() or "UNSPECIFIED"


def _process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def execution_runtime_unavailable_reason() -> str | None:
    mode = execution_mode()
    if mode == "RECORDED":
        return "Recorded mode is read-only and has no AgentTeams execution services"
    if mode not in {"MATERIAL", "LIVE"}:
        return None
    marker_value = os.getenv("LAUNCHSCOPE_EXECUTION_READINESS_FILE", "").strip()
    if not marker_value:
        return None
    marker_path = Path(marker_value)
    try:
        marker: Any = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "execution readiness has not been established"
    if not isinstance(marker, dict) or marker.get("mode") != mode or marker.get("dispatch_enabled") is not True:
        return "execution readiness marker does not authorize this mode"
    processes = marker.get("processes")
    if not isinstance(processes, list) or not processes:
        return "execution readiness marker contains no execution services"
    for process in processes:
        if not isinstance(process, dict):
            return "execution readiness marker is malformed"
        name = str(process.get("name") or "execution service")
        try:
            pid = int(process["pid"])
        except (KeyError, TypeError, ValueError):
            return f"{name} has no valid process identity"
        if not _process_alive(pid):
            return f"{name} is not running"
    return None


__all__ = ["execution_mode", "execution_runtime_unavailable_reason"]
