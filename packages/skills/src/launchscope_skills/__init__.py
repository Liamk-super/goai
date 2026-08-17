"""Versioned, content-addressed Skill contracts."""

from .registry import (
    P0_SKILL_CODES,
    P0_SKILL_CODES_V2,
    REPORT_V22_SKILL_CODES,
    SkillContractError,
    SkillManifest,
    SkillRegistry,
)

__version__ = "0.1.0"

__all__ = [
    "P0_SKILL_CODES",
    "P0_SKILL_CODES_V2",
    "REPORT_V22_SKILL_CODES",
    "SkillContractError",
    "SkillManifest",
    "SkillRegistry",
]
