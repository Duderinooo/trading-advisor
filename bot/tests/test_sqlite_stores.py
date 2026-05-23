"""SQLite-store smoke tests (Phase E7).

Each store gets a fresh isolated DB by pointing core.db._DB_PATH at a
tempfile before importing/calling the store. Tests verify the public
API contract: load returns the same shape that save accepted, append
extends without rewriting, and migrations are idempotent.

These tests don't exercise the legacy-file migration paths (jsonl /
portfolio.json fallback) — those would require fabricating disk state
that conflicts with the developer's live data. The migration helpers
are simple enough to be verified by manual smoke after a real run.
"""

import importlib
import os
import tempfile
import threading
import unittest
from pathlib import Path


def _fresh_db(monkey_target=None):
    """Point core.db at a brand-new tempfile and reset module-level
    schema state. Returns (db_path, cleanup_fn)."""
    import core.db as db_module

    tmpdir = tempfile.mkdtemp(prefix="ta_test_")
    db_path = Path(tmpdir) / "bot.db"

    db_module._DB_PATH = db_path
    db_module._SCHEMA_READY = False
    db_module.init_schema()

    def cleanup():
        try:
            for ext in ("", "-shm", "-wal"):
                p = Path(str(db_path) + ext)
                if p.exists():
                    p.unlink()
            os.rmdir(tmpdir)
        except OSError:
            pass

    return db_path, cleanup


def _reset_store(module_path: str):
    """Re-import a store module so its _MIGRATED flag and any cached
    references pick up the freshly-pointed DB."""
    if module_path in list(__import__("sys").modules):
        importlib.reload(__import__("sys").modules[module_path])


class TestClosedTradesStore(unittest.TestCase):
    def setUp(self):
        self._path, self._cleanup = _fresh_db()
        _reset_store("core.portfolio.closed_trades_store")
        from core.portfolio import closed_trades_store
        # Force the legacy-migration to think it's already done so it
        # doesn't try to read the developer's live portfolio.json.
        closed_trades_store._MIGRATED = True
        self.store = closed_trades_store

    def tearDown(self):
        self._cleanup()

    def test_empty_on_fresh_db(self):
        self.assertEqual(self.store.load_closed_trades(), [])

    def test_append_then_load(self):
        self.store.append_closed_trade(
            {"ticker": "BAS.DE", "pnl_eur": 12.3, "closed_at": "2026-05-23 10:00"}
        )
        rows = self.store.load_closed_trades()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "BAS.DE")
        self.assertEqual(rows[0]["pnl_eur"], 12.3)

    def test_save_replaces_all(self):
        self.store.append_closed_trade({"ticker": "A.DE"})
        self.store.append_closed_trade({"ticker": "B.DE"})
        self.store.save_closed_trades([{"ticker": "C.DE"}])
        rows = self.store.load_closed_trades()
        self.assertEqual([r["ticker"] for r in rows], ["C.DE"])

    def test_insertion_order_preserved(self):
        for ticker in ("X", "Y", "Z"):
            self.store.append_closed_trade({"ticker": ticker})
        rows = self.store.load_closed_trades()
        self.assertEqual([r["ticker"] for r in rows], ["X", "Y", "Z"])


class TestPendingStore(unittest.TestCase):
    def setUp(self):
        self._path, self._cleanup = _fresh_db()
        _reset_store("core.portfolio.pending_store")
        from core.portfolio import pending_store
        pending_store._MIGRATED = True
        self.store = pending_store

    def tearDown(self):
        self._cleanup()

    def test_empty_on_fresh_db(self):
        self.assertEqual(self.store.load_pending(), [])

    def test_append_and_save_round_trip(self):
        self.store.append_pending({"ticker": "DBK.DE", "kind": "entry"})
        self.assertEqual(len(self.store.load_pending()), 1)
        self.store.save_pending([])
        self.assertEqual(self.store.load_pending(), [])


class TestDedupStore(unittest.TestCase):
    def setUp(self):
        self._path, self._cleanup = _fresh_db()
        _reset_store("core.portfolio.dedup_store")
        from core.portfolio import dedup_store
        dedup_store._MIGRATED = True
        self.store = dedup_store

    def tearDown(self):
        self._cleanup()

    def test_empty_on_fresh_db(self):
        state = self.store.load_dedup()
        self.assertEqual(state["seen_news"], {})
        self.assertEqual(state["triggered_events"], [])
        self.assertEqual(state["triggered_price_alerts"], [])
        self.assertEqual(state["geo_news_fired"], {})

    def test_round_trip_all_four_buckets(self):
        payload = {
            "seen_news": {"2026-05-23": ["hash1", "hash2"]},
            "triggered_events": [
                {"key": "BAS.DE:breakout", "date": "2026-05-23",
                 "ts": 1234.0, "time": "10:00"}
            ],
            "triggered_price_alerts": [
                {"key": "BAS.DE:drop", "date": "2026-05-23"}
            ],
            "geo_news_fired": {"oil_set": "2026-05-23T18:00:00"},
        }
        self.store.save_dedup(payload)
        loaded = self.store.load_dedup()
        # seen_news: order-insensitive comparison since we store as set
        self.assertEqual(set(loaded["seen_news"]["2026-05-23"]),
                         {"hash1", "hash2"})
        self.assertEqual(loaded["triggered_events"][0]["key"],
                         "BAS.DE:breakout")
        self.assertEqual(loaded["triggered_price_alerts"][0]["key"],
                         "BAS.DE:drop")
        self.assertEqual(loaded["geo_news_fired"]["oil_set"],
                         "2026-05-23T18:00:00")

    def test_update_dedup_partial_merge(self):
        self.store.save_dedup({"seen_news": {"2026-05-23": ["a"]}})
        self.store.update_dedup({"triggered_events": [
            {"key": "X", "date": "2026-05-23", "ts": 1.0, "time": "10:00"}
        ]})
        loaded = self.store.load_dedup()
        self.assertEqual(loaded["seen_news"]["2026-05-23"], ["a"])
        self.assertEqual(len(loaded["triggered_events"]), 1)


class TestRuntimeStore(unittest.TestCase):
    def setUp(self):
        self._path, self._cleanup = _fresh_db()
        _reset_store("core.portfolio.runtime_store")
        from core.portfolio import runtime_store
        runtime_store._MIGRATED = True
        self.store = runtime_store

    def tearDown(self):
        self._cleanup()

    def test_empty_on_fresh_db(self):
        self.assertEqual(self.store.load_runtime(), {})

    def test_save_then_load(self):
        self.store.save_runtime({
            "heartbeat": {"ts": "2026-05-23T10:00:00", "live_quotes": {}},
            "entry_gate_cooldowns": {"BAS.DE": {"rs": "2026-05-23"}},
        })
        loaded = self.store.load_runtime()
        self.assertEqual(loaded["heartbeat"]["ts"], "2026-05-23T10:00:00")
        self.assertEqual(loaded["entry_gate_cooldowns"]["BAS.DE"]["rs"],
                         "2026-05-23")

    def test_update_preserves_other_keys(self):
        self.store.save_runtime({"heartbeat": {"a": 1}, "cooldowns": {"x": 2}})
        self.store.update_runtime({"heartbeat": {"a": 99}})
        loaded = self.store.load_runtime()
        self.assertEqual(loaded["heartbeat"]["a"], 99)
        self.assertEqual(loaded["cooldowns"], {"x": 2})


class TestTraceStore(unittest.TestCase):
    def setUp(self):
        self._path, self._cleanup = _fresh_db()
        _reset_store("core.llm.telemetry.trace_store")
        from core.llm.telemetry import trace_store
        trace_store._MIGRATED = True
        self.store = trace_store

    def tearDown(self):
        self._cleanup()

    def test_empty_on_fresh_db(self):
        self.assertEqual(self.store.load_traces(), {})

    def test_save_trace_upsert(self):
        self.store.save_trace("last_morning_trace", {"mode": "morning"})
        self.store.save_trace("last_event_trace", {"mode": "event"})
        traces = self.store.load_traces()
        self.assertEqual(traces["last_morning_trace"]["mode"], "morning")
        self.assertEqual(traces["last_event_trace"]["mode"], "event")
        # Overwrite
        self.store.save_trace("last_morning_trace", {"mode": "morning_v2"})
        self.assertEqual(self.store.load_traces()["last_morning_trace"]["mode"],
                         "morning_v2")


class TestSchemaIdempotent(unittest.TestCase):
    def test_init_schema_can_be_called_twice(self):
        self._path, self._cleanup = _fresh_db()
        try:
            from core import db
            # Force a re-init even though _SCHEMA_READY is set.
            db._SCHEMA_READY = False
            db.init_schema()
            # Second call is a no-op (no exception).
            db.init_schema()
            with db.connect() as conn:
                tables = {
                    r["name"]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            for expected in (
                "closed_trades",
                "pending_recommendations",
                "seen_news",
                "triggered_events",
                "triggered_price_alerts",
                "geo_news_fired",
                "kv_state",
            ):
                self.assertIn(expected, tables)
        finally:
            self._cleanup()


class TestConcurrentWrites(unittest.TestCase):
    """SQLite handles concurrent writes from multiple threads under WAL.
    The bot's writer pattern is main loop + Telegram thread, so verify a
    simple two-thread append doesn't lose rows or deadlock."""

    def setUp(self):
        self._path, self._cleanup = _fresh_db()
        _reset_store("core.portfolio.closed_trades_store")
        from core.portfolio import closed_trades_store
        closed_trades_store._MIGRATED = True
        self.store = closed_trades_store

    def tearDown(self):
        self._cleanup()

    def test_two_threads_appending(self):
        N = 50

        def worker(prefix):
            for i in range(N):
                self.store.append_closed_trade({"ticker": f"{prefix}{i}"})

        t1 = threading.Thread(target=worker, args=("A",))
        t2 = threading.Thread(target=worker, args=("B",))
        t1.start(); t2.start()
        t1.join(timeout=15); t2.join(timeout=15)
        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        rows = self.store.load_closed_trades()
        self.assertEqual(len(rows), 2 * N)


if __name__ == "__main__":
    unittest.main()
