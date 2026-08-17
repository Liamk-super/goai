from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
_ENUM = re.compile(r"^[A-Z][A-Z0-9_-]*$")


def generated_locale_matches(locale: str, values: Iterable[Any]) -> bool:
    for value in _strings(values):
        if _ENUM.fullmatch(value):
            continue
        if locale == "zh-CN" and not _CJK.search(value) and len(_LATIN.findall(value)) >= 12:
            return False
        if locale == "en" and _CJK.search(value):
            return False
    return True


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings(item)


__all__ = ["generated_locale_matches"]
