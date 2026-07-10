"""Real-SQLite coverage for the profile-local outbound delivery outbox."""

import asyncio
import hashlib
import sqlite3

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.delivery import DeliveryRouter, DeliveryTarget
from gateway.delivery_outbox import DeliveryOutbox, UnknownDeliveryError
from gateway.platforms.base import SendResult
from cron.scheduler import _cron_delivery_id


class ReceiptAdapter:
    def __init__(self):
        self.calls = []

    async def send(self, chat_id, content, metadata=None):
        self.calls.append((chat_id, content, metadata))
        return SendResult(
            success=True,
            message_id="provider-42",
            raw_response={"request_id": "req-7"},
        )


class BlockingAdapter:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def send(self, chat_id, content, metadata=None):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return SendResult(success=True, message_id="late-1")


def test_cron_fanout_delivery_id_is_stable_per_run_target():
    job = {"id": "job-1", "last_run_at": "2026-07-09T12:00:00+00:00"}

    first = _cron_delivery_id(job, "telegram", "123", "hello")

    assert first == _cron_delivery_id(job, "telegram", "123", "hello")
    assert first != _cron_delivery_id(job, "telegram", "456", "hello")
    assert first != _cron_delivery_id(job, "telegram", "123", "changed")


def test_outbox_is_profile_local_real_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    outbox = DeliveryOutbox()
    row = outbox.create(
        delivery_id="d-1",
        origin="cron:job-1",
        destination="telegram:123",
        payload="hello",
    )

    assert outbox.path == tmp_path / "gateway" / "delivery_outbox.sqlite3"
    assert row.state == "pending"
    assert row.attempt == 0
    assert row.payload_hash == hashlib.sha256(b"hello").hexdigest()
    with sqlite3.connect(outbox.path) as conn:
        stored = conn.execute(
            "SELECT delivery_id, origin, destination, payload_hash, state, attempt "
            "FROM deliveries"
        ).fetchone()
    assert stored == ("d-1", "cron:job-1", "telegram:123", row.payload_hash, "pending", 0)


def test_restart_reconciliation_marks_dispatched_unknown_but_keeps_pending(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    first = DeliveryOutbox(path)
    first.create("sent-maybe", "cron:j", "telegram:1", "payload")
    first.mark_dispatched("sent-maybe")
    first.create("not-started", "cron:j", "telegram:2", "payload")
    first.close()

    restarted = DeliveryOutbox(path)

    assert restarted.get("sent-maybe").state == "unknown"
    assert restarted.get("not-started").state == "pending"
    assert restarted.retryable("sent-maybe") is False
    assert restarted.retryable("not-started") is True


@pytest.mark.asyncio
async def test_delivery_chokepoint_persists_confirmation_and_provider_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = ReceiptAdapter()
    router = DeliveryRouter(GatewayConfig(), adapters={Platform.TELEGRAM: adapter})
    target = DeliveryTarget.parse("telegram:123")

    result = await router._deliver_to_platform(
        target,
        "hello",
        metadata={"delivery_id": "cron-run-1", "delivery_origin": "cron:job-1"},
    )

    row = router.outbox.get("cron-run-1")
    assert result.message_id == "provider-42"
    assert row.state == "confirmed"
    assert row.attempt == 1
    assert row.provider_receipt == {
        "message_id": "provider-42",
        "raw_response": {"request_id": "req-7"},
    }
    assert adapter.calls[0][2]["delivery_id"] == "cron-run-1"


@pytest.mark.asyncio
async def test_confirmed_delivery_id_is_not_sent_twice_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    target = DeliveryTarget.parse("telegram:123")
    first_adapter = ReceiptAdapter()
    first = DeliveryRouter(GatewayConfig(), adapters={Platform.TELEGRAM: first_adapter})
    metadata = {"delivery_id": "stable-id", "delivery_origin": "cron:job-1"}
    await first._deliver_to_platform(target, "hello", metadata=metadata)
    first.outbox.close()

    restarted_adapter = ReceiptAdapter()
    restarted = DeliveryRouter(GatewayConfig(), adapters={Platform.TELEGRAM: restarted_adapter})
    result = await restarted._deliver_to_platform(target, "hello", metadata=metadata)

    assert result["success"] is True
    assert result["deduplicated"] is True
    assert result["provider_receipt"]["message_id"] == "provider-42"
    assert restarted_adapter.calls == []


@pytest.mark.asyncio
async def test_cancelled_in_flight_send_becomes_unknown_and_is_not_blindly_retried(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = BlockingAdapter()
    target = DeliveryTarget.parse("telegram:123")
    metadata = {"delivery_id": "maybe-sent", "delivery_origin": "cron:job-1"}
    router = DeliveryRouter(GatewayConfig(), adapters={Platform.TELEGRAM: adapter})

    task = asyncio.create_task(router._deliver_to_platform(target, "hello", metadata=metadata))
    await adapter.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert router.outbox.get("maybe-sent").state == "unknown"

    restarted = DeliveryRouter(GatewayConfig(), adapters={Platform.TELEGRAM: adapter})
    with pytest.raises(UnknownDeliveryError, match="not retried automatically"):
        await restarted._deliver_to_platform(target, "hello", metadata=metadata)
    assert adapter.calls == 1


def test_failed_delivery_can_be_retried_with_incremented_attempt(tmp_path):
    outbox = DeliveryOutbox(tmp_path / "outbox.sqlite3")
    outbox.create("d", "cron:j", "telegram:1", "payload")
    outbox.mark_dispatched("d")
    outbox.mark_failed("d", "network refused")

    assert outbox.retryable("d") is True
    outbox.mark_dispatched("d")
    assert outbox.get("d").attempt == 2
    assert outbox.get("d").state == "dispatched"


def test_delivery_id_rejects_changed_payload_or_destination(tmp_path):
    outbox = DeliveryOutbox(tmp_path / "outbox.sqlite3")
    outbox.create("same", "cron:j", "telegram:1", "one")

    with pytest.raises(ValueError, match="different delivery"):
        outbox.create("same", "cron:j", "telegram:1", "two")
    with pytest.raises(ValueError, match="different delivery"):
        outbox.create("same", "cron:j", "telegram:2", "one")
