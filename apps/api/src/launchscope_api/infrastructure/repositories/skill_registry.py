"""Database lock for immutable, content-addressed Skill versions."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from launchscope_skills import SkillContractError, SkillManifest

from ..db.schema import skill_version
from .base import json_value


class SqlAlchemySkillRegistryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def register(self, manifest: SkillManifest) -> None:
        row = (
            self.session.execute(
                select(skill_version).where(
                    skill_version.c.skill_code == manifest.skill_code,
                    skill_version.c.version == manifest.version,
                )
            )
            .mappings()
            .first()
        )
        if row is not None:
            if row["manifest_sha256"].lower() != manifest.content_sha256:
                raise SkillContractError(
                    f"database Skill version lock conflict: {manifest.skill_code}@{manifest.version}"
                )
            return
        self.session.execute(
            skill_version.insert().values(
                id=uuid4(),
                skill_code=manifest.skill_code,
                version=manifest.version,
                manifest_sha256=manifest.content_sha256,
                input_schema=json_value(manifest.document["input_schema"]),
                output_schema=json_value(manifest.document["output_schema"]),
            )
        )


__all__ = ["SqlAlchemySkillRegistryRepository"]
