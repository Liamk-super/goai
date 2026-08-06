"""Long-running, least-privilege Outbox publisher entrypoint for local Demo."""

from __future__ import annotations

import os
import socket
import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from launchscope_api.infrastructure.db.schema import outbox_message
from launchscope_api.infrastructure.db.session import DatabaseSettings, create_database_engine, session_factory
from launchscope_domain.value_objects import TenantScope

from .publisher import OutboxPublisher, RocketMQTransport


def pending_tenants(session: Session) -> tuple[UUID, ...]:
    return tuple(session.execute(
        select(outbox_message.c.tenant_id).where(
            outbox_message.c.publish_status.in_(("PENDING", "FAILED", "CLAIMED"))
        ).distinct()
    ).scalars())


def main() -> None:
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_PUBLISHER_DB_ROLE", "launchscope_publisher")
    )
    sessions = session_factory(engine)
    endpoints = os.environ["ROCKETMQ_ENDPOINTS"]
    topic = os.getenv("LAUNCHSCOPE_ROCKETMQ_TOPIC", "launchscope-evaluation-events-v1")
    publisher_id = f"publisher-{socket.gethostname().lower()}-{os.getpid()}"[:119]
    transport = RocketMQTransport(endpoints, (topic,))
    try:
        while True:
            with sessions() as session:
                tenants = pending_tenants(session)
            publisher = OutboxPublisher(
                sessions, transport, publisher_id=publisher_id, topic=topic
            )
            for tenant_id in tenants:
                publisher.publish_scope(TenantScope(tenant_id), limit=50)
            time.sleep(1 if tenants else 2)
    except KeyboardInterrupt:
        pass
    finally:
        transport.close()
        engine.dispose()


if __name__ == "__main__":
    main()
