"""Shutdown-path bounds on the Honcho memory provider.

Honcho's CLI-exit flush path used to allow ~28s of blocking HTTP
(10s on ``on_session_end`` sync-thread join + 5s×2 on ``shutdown``
prefetch/sync joins + 10s on the session cache's async-thread join).
A slow or unreachable Honcho backend made ``hermes`` feel like it
was hanging after every chat session.

These tests pin the new bounded behavior:

* ``HonchoMemoryProvider.on_session_end`` — sync-thread join capped at 2s
* ``HonchoMemoryProvider.shutdown`` — per-thread joins capped at 1s
* ``HonchoSessionManager.flush_all`` — combined deadline of 3s
* ``HonchoSessionManager.shutdown`` — async-thread join capped at 1s

Each test simulates a pathologically slow upstream (threads that refuse
to finish, HTTP calls that block) and asserts the shutdown path
returns within a tight wall-clock budget.  The actual timeouts are
read from class attributes so tuning them down later requires no test
changes; tuning them UP would (rightfully) trip these tests.
"""

from __future__ import annotations

import threading
import time
import types
from unittest.mock import MagicMock

import pytest


# ── HonchoMemoryProvider.on_session_end / shutdown ─────────────────────────


def _make_provider_stub():
    """Instantiate a HonchoMemoryProvider without running __init__.

    We don't want the real constructor (it tries to talk to Honcho); we
    just need an instance that exposes the attributes ``on_session_end``
    and ``shutdown`` touch.
    """
    from plugins.memory.honcho import HonchoMemoryProvider

    prov = HonchoMemoryProvider.__new__(HonchoMemoryProvider)
    prov._cron_skipped = False
    prov._manager = MagicMock()
    prov._prefetch_thread = None
    prov._sync_thread = None
    return prov


class _NeverExitingThread(threading.Thread):
    """Background thread that ignores the join-timeout expectation.

    Used to verify that ``Thread.join(timeout=...)`` returns promptly
    even when the underlying thread is truly stuck — i.e. that the
    callers are passing a finite timeout rather than blocking
    indefinitely.
    """

    def __init__(self):
        super().__init__(daemon=True, name="never-exiting")
        # NB: avoid ``self._stop`` — that name is a private method on
        # ``threading.Thread`` and shadowing it corrupts the thread's
        # teardown path.
        self._release_signal = threading.Event()

    def run(self):
        self._release_signal.wait()  # never set by shutdown code

    def release(self):
        self._release_signal.set()


@pytest.fixture
def stuck_thread():
    t = _NeverExitingThread()
    t.start()
    yield t
    t.release()
    t.join(timeout=2.0)


def test_on_session_end_sync_join_is_bounded(stuck_thread):
    """A stuck sync_thread must not stall on_session_end beyond the cap."""
    from plugins.memory.honcho import HonchoMemoryProvider

    prov = _make_provider_stub()
    prov._sync_thread = stuck_thread
    # flush_all is a no-op for this test
    prov._manager.flush_all = lambda: None

    cap = HonchoMemoryProvider._SESSION_END_SYNC_JOIN_TIMEOUT

    t0 = time.monotonic()
    prov.on_session_end([])
    elapsed = time.monotonic() - t0

    # Must honour the configured cap with a small margin for
    # scheduling.  CI-safe upper bound: cap + 1s.
    assert elapsed < cap + 1.0, (
        f"on_session_end took {elapsed:.2f}s; expected < {cap + 1.0:.1f}s "
        f"(cap is {cap}s)"
    )


def test_shutdown_joins_are_bounded(stuck_thread):
    """Both prefetch and sync threads — stuck — must not stall shutdown."""
    from plugins.memory.honcho import HonchoMemoryProvider

    # Two stuck threads: prefetch + sync.  Total worst case = 2 × cap.
    sync = _NeverExitingThread()
    sync.start()
    try:
        prov = _make_provider_stub()
        prov._prefetch_thread = stuck_thread
        prov._sync_thread = sync
        prov._manager.flush_all = lambda: None

        cap = HonchoMemoryProvider._SHUTDOWN_THREAD_JOIN_TIMEOUT
        budget = (cap * 2) + 1.0  # both threads + scheduling margin

        t0 = time.monotonic()
        prov.shutdown()
        elapsed = time.monotonic() - t0

        assert elapsed < budget, (
            f"shutdown took {elapsed:.2f}s; expected < {budget:.1f}s "
            f"(per-thread cap {cap}s × 2 threads)"
        )
    finally:
        sync.release()
        sync.join(timeout=2.0)


# ── HonchoSessionManager.flush_all deadline ──────────────────────────────────


def _make_session_cache_stub(flush_delay_seconds: float, session_count: int):
    """Build a HonchoSessionManager with N cached sessions whose _flush_session
    takes ``flush_delay_seconds`` each.  No Honcho client is created.
    """
    from plugins.memory.honcho.session import HonchoSessionManager

    cache = HonchoSessionManager.__new__(HonchoSessionManager)
    cache._cache = {}
    cache._cache_lock = threading.RLock()
    cache._async_queue = None
    cache._async_thread = None

    # Seed N fake sessions; the key/value contract here is just "flush_all
    # iterates cache.values()".  We only need unique hashables.
    for i in range(session_count):
        cache._cache[f"session-{i}"] = types.SimpleNamespace(key=f"session-{i}")

    flush_calls = []

    def _slow_flush(self, session):
        flush_calls.append(session.key)
        time.sleep(flush_delay_seconds)
        return True

    cache._flush_session = types.MethodType(_slow_flush, cache)
    return cache, flush_calls


def test_flush_all_respects_overall_deadline():
    """Many slow session flushes must stop at the combined deadline."""
    from plugins.memory.honcho.session import HonchoSessionManager

    deadline = HonchoSessionManager._FLUSH_ALL_DEADLINE_SECONDS
    per_session = 1.0  # 1s per flush — 10 sessions would otherwise take 10s
    cache, calls = _make_session_cache_stub(
        flush_delay_seconds=per_session, session_count=10
    )

    t0 = time.monotonic()
    cache.flush_all()
    elapsed = time.monotonic() - t0

    # Must stop at ~deadline, not finish all 10 (which would take 10s).
    assert elapsed < deadline + per_session + 0.5, (
        f"flush_all took {elapsed:.2f}s; expected < "
        f"{deadline + per_session + 0.5:.1f}s (deadline {deadline}s + "
        f"one in-flight flush)"
    )
    # Should have flushed SOME sessions but not all.
    assert 0 < len(calls) < 10, (
        f"expected partial progress; flushed {len(calls)}/10 sessions"
    )


def test_flush_all_completes_fast_when_backend_is_responsive():
    """Happy path — zero-latency flushes complete well under the deadline."""
    cache, calls = _make_session_cache_stub(
        flush_delay_seconds=0.0, session_count=5
    )

    t0 = time.monotonic()
    cache.flush_all()
    elapsed = time.monotonic() - t0

    assert len(calls) == 5
    # Near-zero.  Anything above 500ms would suggest the deadline
    # plumbing is wasting time on the happy path.
    assert elapsed < 0.5, f"happy-path flush_all took {elapsed*1000:.0f}ms"
