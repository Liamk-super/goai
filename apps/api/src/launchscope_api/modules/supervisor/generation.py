from __future__ import annotations


def is_supervisor_generation(value: object) -> bool:
    return isinstance(value, str) and value.startswith("supervisor-1p4-")


__all__ = ["is_supervisor_generation"]
