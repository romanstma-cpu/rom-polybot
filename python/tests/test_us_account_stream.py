"""Account stream ingest: dirty flag propagation and message routing.

The private account stream augments the durable order journal and triggers
periodic reconciliation. ingest() is synchronous (called from async via
json.loads); consume_dirty() is polled by the service loop.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import us_account_stream as stream


def _reset():
    stream._dirty = False
    stream._task = None
    stream._connected, stream._connected_at = False, 0.0
    stream._last_message_at = stream._last_disconnect_at = 0.0
    stream._messages = stream._reconnects = 0
    stream._last_error = ''


@pytest.fixture(autouse=True)
def reset_dirty():
    _reset()
    yield
    _reset()


class TestConsumeDirty:
    def test_initially_false(self):
        assert stream.consume_dirty() is False

    def test_consume_clears_flag(self):
        stream._dirty = True
        assert stream.consume_dirty() is True
        assert stream.consume_dirty() is False

    def test_consume_returns_false_when_not_dirty(self):
        assert stream.consume_dirty() is False
        assert stream.consume_dirty() is False


class TestIngestDirtyFlag:
    def test_position_subscription_sets_dirty(self):
        stream.ingest({"positionSubscription": {"positions": []}})
        assert stream._dirty is True

    def test_balance_snapshot_sets_dirty(self):
        stream.ingest({"accountBalancesSnapshot": {}})
        assert stream._dirty is True

    def test_balance_update_sets_dirty(self):
        stream.ingest({"accountBalancesUpdate": {}})
        assert stream._dirty is True

    def test_order_snapshot_sets_dirty(self):
        stream.ingest({"orderSubscriptionSnapshot": {"orders": []}})
        assert stream._dirty is True

    def test_order_update_sets_dirty(self):
        stream.ingest({"orderSubscriptionUpdate": {"execution": {}}})
        assert stream._dirty is True

    def test_irrelevant_message_does_not_set_dirty(self):
        stream.ingest({"type": "ping"})
        assert stream._dirty is False

    def test_empty_message_does_not_set_dirty(self):
        stream.ingest({})
        assert stream._dirty is False


class TestIngestError:
    def test_error_message_raises(self):
        with pytest.raises(ValueError, match="rejected"):
            stream.ingest({"error": "some subscription error"})

    def test_none_error_field_ignored(self):
        stream.ingest({"error": None})
        assert stream._dirty is False

    def test_no_error_field_ignored(self):
        stream.ingest({"somethingElse": "ok"})
        assert stream._dirty is False


class TestIngestMessageRouting:
    def test_orders_snapshot_dispatches_to_journal(self, monkeypatch):
        called = []
        monkeypatch.setattr("order_journal.record_order", lambda o: called.append(o))
        stream.ingest({"orderSubscriptionSnapshot": {
            "orders": [{"id": "1"}, {"id": "2"}],
        }})
        assert called == [{"id": "1"}, {"id": "2"}]

    def test_order_update_dispatches_execution(self, monkeypatch):
        called = []
        monkeypatch.setattr("order_journal.record_execution", lambda e: called.append(e))
        stream.ingest({"orderSubscriptionUpdate": {
            "execution": {"order_id": "x", "fill": True},
        }})
        assert called == [{"order_id": "x", "fill": True}]

    def test_no_orders_snapshot_dispatches_nothing(self, monkeypatch):
        called = []
        monkeypatch.setattr("order_journal.record_order", lambda o: called.append(o))
        stream.ingest({"orderSubscriptionSnapshot": {}})
        assert called == []

    def test_position_message_does_not_dispatch(self, monkeypatch):
        called_order = []
        called_exec = []
        monkeypatch.setattr("order_journal.record_order", lambda o: called_order.append(o))
        monkeypatch.setattr("order_journal.record_execution", lambda e: called_exec.append(e))
        stream.ingest({"positionSubscription": {"positions": [{"id": "p1"}]}})
        assert called_order == []
        assert called_exec == []

    def test_multiple_fields_all_dispatched(self, monkeypatch):
        orders = []
        execs = []
        monkeypatch.setattr("order_journal.record_order", lambda o: orders.append(o))
        monkeypatch.setattr("order_journal.record_execution", lambda e: execs.append(e))
        stream.ingest({
            "orderSubscriptionSnapshot": {"orders": [{"id": "1"}]},
            "orderSubscriptionUpdate": {"execution": {"order_id": "2"}},
            "positionSubscription": {"positions": []},
        })
        assert orders == [{"id": "1"}]
        assert execs == [{"order_id": "2"}]
        assert stream._dirty is True

class TestHealth:
    def test_idle_stream_reports_stopped(self):
        h = stream.health()
        assert h['state'] == 'stopped' and h['connected'] is False
        assert h['messages'] == 0 and h['reconnects'] == 0
        assert h['lastMessageAgeSeconds'] is None and h['lastError'] == ''

    def test_ingest_counts_every_message(self):
        stream.ingest({"type": "ping"})
        stream.ingest({"accountBalancesSnapshot": {}})
        h = stream.health()
        assert h['messages'] == 2
        assert h['lastMessageAgeSeconds'] is not None

    def test_a_dropped_connection_is_visible_before_the_retry(self, monkeypatch):
        """Connected while the socket is open; disconnected, counted and named
        during the back-off, not only once the next connect succeeds."""
        seen = {}
        passes = iter([True])

        class Socket:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def send(self, _frame):
                pass

            def __aiter__(self):
                return self._frames()

            async def _frames(self):
                yield json.dumps({"accountBalancesSnapshot": {}})
                seen['open'] = stream.health()
                raise ConnectionResetError('dropped')

        async def back_off(_seconds):
            seen['backing_off'] = stream.health()

        monkeypatch.setattr(stream.auth, 'credentials_present', lambda: next(passes, False))
        monkeypatch.setattr(stream.auth, 'l2_headers', lambda *_a: {})
        monkeypatch.setattr(stream.websockets, 'connect', lambda *_a, **_kw: Socket())
        monkeypatch.setattr(stream.asyncio, 'sleep', back_off)
        asyncio.run(stream._run())

        assert seen['open']['connected'] is True and seen['open']['state'] == 'connected'
        assert seen['open']['messages'] == 1
        off = seen['backing_off']
        assert off['connected'] is False
        assert off['reconnects'] == 1 and off['lastError'] == 'ConnectionResetError'
        assert off['lastDisconnectAt'] is not None
        assert stream._dirty is True  # the gap still asks for reconciliation
