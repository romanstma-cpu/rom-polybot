"""Bounded, read-only-to-the-UI history of main-strategy decisions.

Rows contain market identifiers and gate explanations, never credentials or
order payloads. A journal failure must not change a trading decision.
"""
from __future__ import annotations

import logging
import time

import db

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS main_decision_journal (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 at REAL NOT NULL,
 trace_id TEXT NOT NULL,
 mode TEXT NOT NULL,
 source TEXT NOT NULL,
 ticker TEXT NOT NULL,
 signal_id INTEGER,
 stage TEXT NOT NULL,
 outcome TEXT NOT NULL,
 reason TEXT NOT NULL,
 position_id INTEGER
);
CREATE INDEX IF NOT EXISTS main_decision_journal_at ON main_decision_journal(at,id);
"""
RETENTION_SECONDS = 7 * 86400
MAX_ROWS = 25_000


def write(rows: list[dict]) -> None:
    if not rows:
        return
    try:
        with db.get_db() as conn:
            conn.executescript(SCHEMA)
            conn.executemany(
                "INSERT INTO main_decision_journal"
                "(at,trace_id,mode,source,ticker,signal_id,stage,outcome,reason,position_id) "
                "VALUES (:at,:trace_id,:mode,:source,:ticker,:signal_id,:stage,:outcome,:reason,:position_id)",
                rows,
            )
            conn.execute("DELETE FROM main_decision_journal WHERE at < ?", (time.time() - RETENTION_SECONDS,))
            conn.execute(
                "DELETE FROM main_decision_journal WHERE id < COALESCE("
                "(SELECT id FROM main_decision_journal ORDER BY id DESC LIMIT 1 OFFSET ?),0)",
                (MAX_ROWS - 1,),
            )
    except Exception:
        logger.exception("Could not save main-strategy decision journal")


def report(*, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    cutoff = now - 24 * 3600
    with db.get_db() as conn:
        conn.executescript(SCHEMA)
        totals = {row[0]: row[1] for row in conn.execute(
            "SELECT outcome,COUNT(*) FROM main_decision_journal WHERE at>=? "
            "GROUP BY outcome", (cutoff,),
        )}
        reasons = [dict(row) for row in conn.execute(
            "SELECT stage,reason,COUNT(*) count FROM main_decision_journal "
            "WHERE at>=? AND outcome='skipped' GROUP BY stage,reason "
            "ORDER BY count DESC LIMIT 8", (cutoff,),
        )]
        recent = [dict(row) for row in conn.execute(
            "SELECT at,trace_id,mode,source,ticker,signal_id,stage,outcome,reason,position_id "
            "FROM main_decision_journal WHERE at>=? ORDER BY id DESC LIMIT 12",
            (now - RETENTION_SECONDS,),
        )]
        # Submission is not a fill. Show the current persisted position state
        # for each decision that produced one, including later reconciliation.
        for item in recent:
            item['positionStatus'] = None
            if item['position_id'] is not None:
                found = conn.execute("SELECT status FROM bot_positions WHERE id=?", (item['position_id'],)).fetchone()
                item['positionStatus'] = found[0] if found else None
    return {'windowHours': 24, 'totals': totals, 'topSkipped': reasons, 'recent': recent}
