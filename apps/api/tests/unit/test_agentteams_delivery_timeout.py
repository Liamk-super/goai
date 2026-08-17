from datetime import UTC, datetime, timedelta

from launchscope_api.modules.evaluation.agentteams_delivery import extended_delivery_deadline


def test_legacy_short_deadline_is_raised_to_execution_floor() -> None:
    delivered_at = datetime(2026, 8, 14, 2, 0, tzinfo=UTC)
    now = delivered_at + timedelta(seconds=601)

    assert extended_delivery_deadline(
        delivered_at=delivered_at,
        configured_timeout_seconds=600,
        now=now,
        active_model=False,
    ) == delivered_at + timedelta(seconds=3600)


def test_active_model_stream_gets_another_full_execution_window() -> None:
    delivered_at = datetime(2026, 8, 14, 2, 0, tzinfo=UTC)
    now = delivered_at + timedelta(seconds=3601)

    assert extended_delivery_deadline(
        delivered_at=delivered_at,
        configured_timeout_seconds=600,
        now=now,
        active_model=True,
    ) == now + timedelta(seconds=3600)


def test_inactive_delivery_can_expire_after_execution_floor() -> None:
    delivered_at = datetime(2026, 8, 14, 2, 0, tzinfo=UTC)
    now = delivered_at + timedelta(seconds=3601)

    assert extended_delivery_deadline(
        delivered_at=delivered_at,
        configured_timeout_seconds=600,
        now=now,
        active_model=False,
    ) is None
