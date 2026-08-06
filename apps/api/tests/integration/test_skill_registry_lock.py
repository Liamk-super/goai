"""PostgreSQL persistence lock for the six immutable P0 Skill versions."""

from __future__ import annotations

from dataclasses import replace

import pytest

from launchscope_api.infrastructure.db.session import session_factory
from launchscope_api.infrastructure.repositories.skill_registry import SqlAlchemySkillRegistryRepository
from launchscope_skills import SkillContractError, SkillRegistry


def test_database_rejects_a_different_hash_for_an_existing_skill_version(database) -> None:
    manifests = SkillRegistry().load_p0()
    factory = session_factory(database)
    with factory.begin() as session:
        repository = SqlAlchemySkillRegistryRepository(session)
        for manifest in manifests:
            repository.register(manifest)
        repository.register(manifests[0])
        with pytest.raises(SkillContractError, match="version lock conflict"):
            repository.register(replace(manifests[0], content_sha256="f" * 64))
