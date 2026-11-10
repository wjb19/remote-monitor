"""SQLite time-series + alert state for lab telemetry."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class TelemetryStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS points (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  metric TEXT NOT NULL,
                  ts REAL NOT NULL,
                  value REAL NOT NULL,
                  tags TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_points_metric_ts ON points(metric, ts);

                CREATE TABLE IF NOT EXISTS alert_config (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alert_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL NOT NULL,
                  kind TEXT NOT NULL,
                  message TEXT NOT NULL,
                  payload TEXT
                );

                CREATE TABLE IF NOT EXISTS edge_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL NOT NULL,
                  camera_id TEXT,
                  kind TEXT,
                  score REAL,
                  path TEXT,
                  meta TEXT
                );

                CREATE TABLE IF NOT EXISTS locations (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL NOT NULL,
                  source TEXT NOT NULL,
                  latitude REAL NOT NULL,
                  longitude REAL NOT NULL,
                  altitude_m REAL,
                  accuracy_m REAL,
                  fix_source TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_locations_ts ON locations(ts);

                CREATE TABLE IF NOT EXISTS map_features (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_ts REAL NOT NULL,
                  updated_ts REAL NOT NULL,
                  name TEXT NOT NULL,
                  description TEXT,
                  marker_type TEXT NOT NULL,
                  latitude REAL NOT NULL,
                  longitude REAL NOT NULL,
                  altitude_m REAL,
                  meta TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_map_features_type ON map_features(marker_type);
                """
            )
            # defaults
            cur = conn.execute("SELECT value FROM alert_config WHERE key='voltage_min_v'")
            if cur.fetchone() is None:
                conn.execute(
                    "INSERT INTO alert_config(key, value) VALUES (?, ?)",
                    ("voltage_min_v", "4.60"),
                )
                conn.execute(
                    "INSERT INTO alert_config(key, value) VALUES (?, ?)",
                    ("voltage_alert_cooldown_s", "300"),
                )

    def insert_points(self, points: list[dict]) -> int:
        with self._lock, self._connect() as conn:
            n = 0
            for p in points:
                conn.execute(
                    "INSERT INTO points(metric, ts, value, tags) VALUES (?, ?, ?, ?)",
                    (
                        p["metric"],
                        float(p.get("ts") or time.time()),
                        float(p["value"]),
                        json.dumps(p.get("tags") or {}),
                    ),
                )
                n += 1
            return n

    def query(self, metric: str, since_s: float = 3600, limit: int = 2000) -> list[dict]:
        cutoff = time.time() - since_s
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ts, value, tags FROM points
                WHERE metric=? AND ts>=?
                ORDER BY ts ASC
                LIMIT ?
                """,
                (metric, cutoff, limit),
            ).fetchall()
        return [
            {"ts": r["ts"], "value": r["value"], "tags": json.loads(r["tags"] or "{}")}
            for r in rows
        ]

    def latest_per_node(self, metrics: list[str], scan: int = 400) -> list[dict]:
        found: dict[str, dict] = {}
        with self._lock, self._connect() as conn:
            for metric in metrics:
                rows = conn.execute(
                    "SELECT ts, value, tags FROM points WHERE metric=? ORDER BY ts DESC LIMIT ?",
                    (metric, scan),
                ).fetchall()
                for row in rows:
                    tags = json.loads(row["tags"] or "{}")
                    node_id = str(tags.get("node_id") or "unknown")
                    slot = found.setdefault(
                        node_id, {"node_id": node_id, "tags": tags, "metrics": {}}
                    )
                    if metric not in slot["metrics"]:
                        slot["metrics"][metric] = {"ts": row["ts"], "value": row["value"]}
                        slot["tags"] = tags
        return list(found.values())

    def latest(self, metric: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT ts, value, tags FROM points WHERE metric=? ORDER BY ts DESC LIMIT 1",
                (metric,),
            ).fetchone()
        if not row:
            return None
        return {"ts": row["ts"], "value": row["value"], "tags": json.loads(row["tags"] or "{}")}

    def get_alert_config(self) -> dict:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM alert_config").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def set_alert_config(self, updates: dict) -> dict:
        with self._lock, self._connect() as conn:
            for k, v in updates.items():
                conn.execute(
                    "INSERT INTO alert_config(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, str(v)),
                )
        return self.get_alert_config()

    def recent_alert(self, kind: str, within_s: float) -> bool:
        cutoff = time.time() - within_s
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM alert_events WHERE kind=? AND ts>=? LIMIT 1",
                (kind, cutoff),
            ).fetchone()
        return row is not None

    def add_alert(self, kind: str, message: str, payload: dict | None = None) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO alert_events(ts, kind, message, payload) VALUES (?, ?, ?, ?)",
                (time.time(), kind, message, json.dumps(payload or {})),
            )

    def list_alerts(self, limit: int = 50) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, kind, message, payload FROM alert_events ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "ts": r["ts"],
                "kind": r["kind"],
                "message": r["message"],
                "payload": json.loads(r["payload"] or "{}"),
            }
            for r in rows
        ]

    def record_location(self, fix: dict) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO locations(ts, source, latitude, longitude, altitude_m, accuracy_m, fix_source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    float(fix.get("ts") or time.time()),
                    str(fix.get("source") or "gateway"),
                    float(fix["latitude"]),
                    float(fix["longitude"]),
                    fix.get("altitude_m"),
                    fix.get("accuracy_m"),
                    fix.get("fix_source"),
                ),
            )

    def latest_location(self) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT ts, source, latitude, longitude, altitude_m, accuracy_m, fix_source
                FROM locations ORDER BY ts DESC LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        fix = {
            "ts": row["ts"],
            "source": row["source"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "fix_source": row["fix_source"] or "GPS",
        }
        if row["altitude_m"] is not None:
            fix["altitude_m"] = row["altitude_m"]
        if row["accuracy_m"] is not None:
            fix["accuracy_m"] = row["accuracy_m"]
        return fix

    def list_locations(self, since_s: float = 86400 * 30, limit: int = 2000) -> list[dict]:
        cutoff = time.time() - since_s
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ts, source, latitude, longitude, altitude_m, accuracy_m, fix_source
                FROM locations WHERE ts>=?
                ORDER BY ts ASC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
        out = []
        for row in rows:
            item = {
                "ts": row["ts"],
                "source": row["source"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "fix_source": row["fix_source"] or "GPS",
            }
            if row["altitude_m"] is not None:
                item["altitude_m"] = row["altitude_m"]
            if row["accuracy_m"] is not None:
                item["accuracy_m"] = row["accuracy_m"]
            out.append(item)
        return out

    def list_metrics(self) -> list[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT metric FROM points ORDER BY metric ASC"
            ).fetchall()
        return [r["metric"] for r in rows]

    def _feature_row(self, row: sqlite3.Row) -> dict:
        item = {
            "id": row["id"],
            "created_ts": row["created_ts"],
            "updated_ts": row["updated_ts"],
            "name": row["name"],
            "description": row["description"] or "",
            "marker_type": row["marker_type"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
        }
        if row["altitude_m"] is not None:
            item["altitude_m"] = row["altitude_m"]
        try:
            item["meta"] = json.loads(row["meta"] or "{}")
        except Exception:
            item["meta"] = {}
        return item

    def list_map_features(self) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_ts, updated_ts, name, description, marker_type,
                       latitude, longitude, altitude_m, meta
                FROM map_features ORDER BY name ASC, id ASC
                """
            ).fetchall()
        return [self._feature_row(r) for r in rows]

    def get_map_feature(self, feature_id: int) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, created_ts, updated_ts, name, description, marker_type,
                       latitude, longitude, altitude_m, meta
                FROM map_features WHERE id=?
                """,
                (feature_id,),
            ).fetchone()
        return self._feature_row(row) if row else None

    def add_map_feature(self, data: dict) -> dict:
        now = time.time()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO map_features(
                  created_ts, updated_ts, name, description, marker_type,
                  latitude, longitude, altitude_m, meta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    now,
                    str(data["name"]).strip(),
                    (data.get("description") or "").strip(),
                    str(data.get("marker_type") or "interest").strip(),
                    float(data["latitude"]),
                    float(data["longitude"]),
                    data.get("altitude_m"),
                    json.dumps(data.get("meta") or {}),
                ),
            )
            feature_id = int(cur.lastrowid)
        found = self.get_map_feature(feature_id)
        assert found is not None
        return found

    def update_map_feature(self, feature_id: int, data: dict) -> dict | None:
        current = self.get_map_feature(feature_id)
        if not current:
            return None
        merged = dict(current)
        merged.update({k: v for k, v in data.items() if v is not None})
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE map_features SET
                  updated_ts=?, name=?, description=?, marker_type=?,
                  latitude=?, longitude=?, altitude_m=?, meta=?
                WHERE id=?
                """,
                (
                    time.time(),
                    str(merged["name"]).strip(),
                    (merged.get("description") or "").strip(),
                    str(merged.get("marker_type") or "interest").strip(),
                    float(merged["latitude"]),
                    float(merged["longitude"]),
                    merged.get("altitude_m"),
                    json.dumps(merged.get("meta") or {}),
                    feature_id,
                ),
            )
        return self.get_map_feature(feature_id)

    def delete_map_feature(self, feature_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM map_features WHERE id=?", (feature_id,))
            return cur.rowcount > 0

    def add_edge_event(self, meta: dict, path: str) -> int:
        loc = self.latest_location()
        if loc and "location" not in meta:
            meta = dict(meta)
            meta["location"] = loc
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO edge_events(ts, camera_id, kind, score, path, meta)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    float(meta.get("ts") or time.time()),
                    meta.get("camera_id"),
                    meta.get("kind"),
                    float(meta["score"]) if meta.get("score") is not None else None,
                    path,
                    json.dumps(meta),
                ),
            )
            return int(cur.lastrowid)

    def list_edge_events(self, limit: int = 50) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, ts, camera_id, kind, score, path, meta FROM edge_events "
                "ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            try:
                item["meta"] = json.loads(item["meta"] or "{}")
            except Exception:
                item["meta"] = {}
            events.append(item)
        return events

    def prune_points(self, keep_days: float = 14.0) -> int:
        cutoff = time.time() - keep_days * 86400
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM points WHERE ts < ?", (cutoff,))
            return cur.rowcount
