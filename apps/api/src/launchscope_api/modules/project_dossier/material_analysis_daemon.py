from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from uuid import UUID

from launchscope_api.infrastructure.db.session import DatabaseSettings, create_database_engine, session_factory
from launchscope_api.infrastructure.messaging.inbox import InboxConsumer
from launchscope_api.infrastructure.object_store import S3QuarantineObjectStore
from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_domain.events import EventEnvelope
from launchscope_domain.value_objects import TenantScope

from .material_analysis import MaterialAnalysisApplication


def main() -> None:
    from rocketmq import (  # type: ignore[import-untyped]
        ClientConfiguration,
        Credentials,
        FilterExpression,
        SimpleConsumer,
    )

    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime")
    )
    sessions = session_factory(engine)
    application = MaterialAnalysisApplication(sessions, S3QuarantineObjectStore.from_env())
    topic = os.getenv("LAUNCHSCOPE_ROCKETMQ_TOPIC", "launchscope-evaluation-events-v1")
    consumer = SimpleConsumer(
        ClientConfiguration(os.environ["ROCKETMQ_ENDPOINTS"], Credentials()),
        os.getenv("LAUNCHSCOPE_MATERIAL_CONSUMER_GROUP", "launchscope-material-analysis-v1"),
        {topic: FilterExpression("material.analysis.requested.v1")},
        await_duration=20,
    )
    consumer.startup()
    try:
        while True:
            try:
                batch = consumer.receive(8, 30)
            except Exception as exc:
                message = str(exc).lower()
                if any(value in message for value in ("deadline", "timeout", "no new message")):
                    time.sleep(1)
                    continue
                raise
            for message in batch:
                event = _event(json.loads(message.body))
                scope = TenantScope(UUID(str(event.tenant_id)))
                with sessions() as session:

                    def handle(_session: object, current: EventEnvelope) -> None:
                        analysis_id = UUID(str(current.payload["analysis_id"]))
                        application.process(
                            Actor(UUID(str(current.tenant_id)), "material-analysis-worker"), analysis_id
                        )

                    InboxConsumer(session, "material-analysis-worker").consume_once(event, handle, scope=scope)
                consumer.ack(message)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"material-analysis-worker: fatal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
    finally:
        consumer.shutdown()
        engine.dispose()


def _event(document: object) -> EventEnvelope:
    if not isinstance(document, dict):
        raise ValueError("RocketMQ material event must be an object")
    return EventEnvelope(
        event_type=document["event_type"],
        event_id=document["event_id"],
        tenant_id=document["tenant_id"],
        run_id=document["run_id"],
        task_id=document.get("task_id"),
        correlation_id=document["correlation_id"],
        causation_id=document.get("causation_id"),
        idempotency_key=document["idempotency_key"],
        schema_version=document["schema_version"],
        occurred_at=_parse_time(str(document["occurred_at"])),
        payload=document["payload"],
    )


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


if __name__ == "__main__":
    main()
