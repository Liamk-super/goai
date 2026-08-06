"""SQLAlchemy repository adapters for domain aggregates."""

from .decision_report import DecisionReportRepositoryAdapter, SqlAlchemyDecisionReportRepository
from .evaluation_run import EvaluationRunRepositoryAdapter, SqlAlchemyEvaluationRunRepository
from .evidence_review import EvidenceReviewRepositoryAdapter, SqlAlchemyEvidenceReviewRepository
from .material_ingestion import SqlAlchemyMaterialIngestionRepository
from .project_dossier import ProjectDossierRepositoryAdapter, SqlAlchemyProjectDossierRepository
from .skill_registry import SqlAlchemySkillRegistryRepository

__all__ = [
    "DecisionReportRepositoryAdapter",
    "EvaluationRunRepositoryAdapter",
    "EvidenceReviewRepositoryAdapter",
    "ProjectDossierRepositoryAdapter",
    "SqlAlchemyDecisionReportRepository",
    "SqlAlchemyEvaluationRunRepository",
    "SqlAlchemyEvidenceReviewRepository",
    "SqlAlchemyProjectDossierRepository",
    "SqlAlchemyMaterialIngestionRepository",
    "SqlAlchemySkillRegistryRepository",
]
