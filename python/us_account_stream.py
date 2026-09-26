"""Private US updates augment the durable journal; REST recovers disconnect gaps."""

import asyncio
import json
import logging
import time

import websockets

import polymarket_auth as auth
import order_journal


PRIVATE_STREAM_URL = 'wss://api.polymarket.us/v1/ws/private'
PRIVATE_SUBSCRIPTIONS = ('ORDER', 'POSITION', 'ACCOUNT_BALANCE')
ACCOUNT_UPDATE_KEYS = (
    'orderSubscriptionSnapshot',
    'orderSubscriptionUpdate',
    'positionSubscription',
    'accountBalancesSnapshot',
    'accountBalancesUpdate',
)

_task = None
_dirty = False
log = logging.getLogger(__name__)

# Observability for health(). The counters are cumulative for the process,
# like the market stream's, so a reconnect loop stays visible after stop().
_connected = False
_connected_at = 0.0
_last_message_at = 0.0
_last_disconnect_at = 0.0
_messages = 0
_reconnects = 0
_last_error = ''


def ingest(message):
    """Record private order updates and flag account state for reconciliation."""
    global _dirty, _messages, _last_message_at

    _messages += 1
    _last_message_at = time.monotonic()
    if message.get('error'):
        raise ValueError('Private subscription rejected')

    order_snapshot = message.get('orderSubscriptionSnapshot') or {}
    for order in order_snapshot.get('orders', []):
        order_journal.record_order(order)

    order_update = message.get('orderSubscriptionUpdate') or {}
    execution = order_update.get('execution')
    if execution:
        order_journal.record_execution(execution)

    if any(key in message for key in ACCOUNT_UPDATE_KEYS):
        _dirty = True


def consume_dirty():
    """Return whether reconciliation is needed, then clear that signal."""
    global _dirty

    was_dirty = _dirty
    _dirty = False
    return was_dirty


def health():
    """Connection state for operators and the livecheck stream stage.

    The private feed is quiet whenever the account is, so unlike the market
    stream there is no staleness verdict: message age is context, and the
    websocket's own pings are what detect a dead connection.
    """
    now = time.monotonic()
    running = _task is not None and not _task.done()
    if _connected:
        state = 'connected'
    elif running:
        state = 'reconnecting'
    else:
        state = 'stopped'
    return {
        'state': state,
        'connected': bool(_connected),
        'connectedSeconds': round(now-_connected_at, 1) if _connected and _connected_at else None,
        'messages': _messages,
        'lastMessageAgeSeconds': round(max(0.0, now-_last_message_at), 1) if _last_message_at else None,
        'reconnects': _reconnects,
        'lastDisconnectAt': _last_disconnect_at or None,
        'lastError': _last_error,
    }


def start():
    """Start the private stream once authenticated, unless it is already running."""
    global _task

    if auth.credentials_present() and (_task is None or _task.done()):
        _task = asyncio.create_task(_run())


async def stop():
    """Stop the private stream and clear its pending reconciliation signal."""
    global _task, _dirty, _connected, _connected_at

    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task, _dirty = None, False
    _connected, _connected_at = False, 0.0


async def _subscribe(ws):
    """Subscribe an authenticated socket to all private account feeds."""
    for subscription in PRIVATE_SUBSCRIPTIONS:
        await ws.send(json.dumps({
            'subscribe': {
                'requestId': f'rom-{subscription}',
                'subscriptionType': f'SUBSCRIPTION_TYPE_{subscription}',
            },
        }))


async def _run():
    """Keep the private stream connected and request reconciliation after gaps."""
    global _dirty, _connected, _connected_at, _last_disconnect_at
    global _reconnects, _last_error

    while auth.credentials_present():
        try:
            async with websockets.connect(
                PRIVATE_STREAM_URL,
                extra_headers=auth.l2_headers('GET', '/v1/ws/private'),
                ping_interval=20,
                ping_timeout=20,
            ) as ws:
                _connected, _connected_at = True, time.monotonic()
                try:
                    await _subscribe(ws)
                    # Always reconcile after reconnect; streams can have gaps.
                    _dirty = True
                    async for payload in ws:
                        ingest(json.loads(payload))
                finally:
                    _connected, _connected_at = False, 0.0
                    _last_disconnect_at = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _dirty = True
            _reconnects += 1
            _last_error = type(exc).__name__
            log.warning('Private account stream reconnecting: %s', type(exc).__name__)
            await asyncio.sleep(5)
