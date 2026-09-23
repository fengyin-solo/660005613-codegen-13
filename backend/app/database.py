"""SQLite persistence for execution runs and export records.

All long-lived state lives here so that runs/exports survive a page refresh
and a server restart. Each call opens a short-lived connection (SQLite handles
the worker threads that way without extra locking).
"""
import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")
DB_PATH = os.path.join(DATA_DIR, "engine.db")


def init_db() -> None:
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                workers INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                status TEXT NOT NULL,
                start_ts REAL NOT NULL,
                end_ts REAL,
                completed INTEGER NOT NULL DEFAULT 0,
                nodes_json TEXT NOT NULL,
                edges_json TEXT NOT NULL,
                logs_json TEXT NOT NULL,
                breakers_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_key TEXT NOT NULL UNIQUE,
                run_ids_json TEXT NOT NULL,
                sections_json TEXT NOT NULL,
                start_ts REAL,
                end_ts REAL,
                status TEXT NOT NULL,
                filename TEXT,
                size_bytes INTEGER,
                result_json TEXT,
                error TEXT,
                duplicate_of INTEGER,
                created_at REAL NOT NULL,
                finished_at REAL
            );
            """
        )
    # A server restart while a file was being written means the job was
    # interrupted: mark it FAILED (users can restart it); stale .tmp files
    # are cleaned by the export module.
    with get_conn() as conn:
        conn.execute(
            "UPDATE exports SET status='FAILED', error='导出因服务重启被中断，可重新开始' "
            "WHERE status='PROCESSING'"
        )


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------- runs -----

def create_run(workflow_id: int, name: str, workers: int, strategy: str,
               dag: Dict[str, Any], start_ts: float) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO runs (workflow_id, name, workers, strategy, status, start_ts, "
            "completed, nodes_json, edges_json, logs_json, breakers_json) "
            "VALUES (?,?,?,?, 'RUNNING', ?, 0, ?,?,?, '[]')",
            (workflow_id, name, workers, strategy, start_ts,
             json.dumps(dag["nodes"]), json.dumps(dag["edges"]), json.dumps([])),
        )
        return int(cur.lastrowid)


def update_run_snapshot(run_id: int, nodes: List[Dict[str, Any]],
                        edges: List[List[str]], logs: List[Dict[str, Any]],
                        breakers: List[Dict[str, Any]], status: str,
                        completed: bool, end_ts: Optional[float]) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET nodes_json=?, edges_json=?, logs_json=?, breakers_json=?, "
            "status=?, completed=?, end_ts=? WHERE id=?",
            (json.dumps(nodes), json.dumps(edges), json.dumps(logs),
             json.dumps(breakers), status, 1 if completed else 0, end_ts, run_id),
        )


def _task_counts(nodes: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"taskTotal": len(nodes), "taskSuccess": 0, "taskFailed": 0,
              "taskRunning": 0, "taskPending": 0, "taskSkipped": 0}
    for n in nodes:
        s = n.get("status", "PENDING")
        if s == "SUCCESS":
            counts["taskSuccess"] += 1
        elif s in ("FAILED", "TIMEOUT"):
            counts["taskFailed"] += 1
        elif s == "RUNNING":
            counts["taskRunning"] += 1
        elif s == "SKIPPED":
            counts["taskSkipped"] += 1
        else:
            counts["taskPending"] += 1
    return counts


def run_summary(row: sqlite3.Row) -> Dict[str, Any]:
    """Same mapper for list and detail endpoints — one source of counts."""
    nodes = json.loads(row["nodes_json"])
    logs = json.loads(row["logs_json"])
    counts = _task_counts(nodes)
    return {
        "id": row["id"],
        "workflowId": row["workflow_id"],
        "name": row["name"],
        "workers": row["workers"],
        "strategy": row["strategy"],
        "status": row["status"],
        "startTs": row["start_ts"],
        "endTs": row["end_ts"],
        "completed": bool(row["completed"]),
        "logCount": len(logs),
        **counts,
    }


def list_runs(limit: int = 100) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [run_summary(r) for r in rows]


def get_run_row(run_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()


def run_detail(row: sqlite3.Row) -> Dict[str, Any]:
    detail = run_summary(row)
    detail.update({
        "nodes": json.loads(row["nodes_json"]),
        "edges": json.loads(row["edges_json"]),
        "logs": json.loads(row["logs_json"]),
        "circuitBreakers": json.loads(row["breakers_json"]),
    })
    return detail


# ------------------------------------------------------------- exports -----

def insert_export(scope_key: str, run_ids: List[int], sections: List[str],
                  start_ts: Optional[float], end_ts: Optional[float],
                  filename: str, created_at: float) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO exports (scope_key, run_ids_json, sections_json, start_ts, end_ts, "
            "status, filename, created_at) VALUES (?,?,?,?,?, 'PROCESSING', ?, ?)",
            (scope_key, json.dumps(run_ids), json.dumps(sections), start_ts, end_ts,
             filename, created_at),
        )
        return int(cur.lastrowid)


def find_export_by_key(key: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM exports WHERE scope_key=?", (key,)
        ).fetchone()


def get_export_row(export_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM exports WHERE id=?", (export_id,)
        ).fetchone()


def list_export_rows() -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM exports ORDER BY id DESC").fetchall()


def export_to_api(row: sqlite3.Row) -> Dict[str, Any]:
    result = json.loads(row["result_json"]) if row["result_json"] else None
    return {
        "id": row["id"],
        "status": row["status"],
        "runIds": json.loads(row["run_ids_json"]),
        "sections": json.loads(row["sections_json"]),
        "startTs": row["start_ts"],
        "endTs": row["end_ts"],
        "filename": row["filename"],
        "sizeBytes": row["size_bytes"],
        "counts": result,
        "error": row["error"],
        "duplicateOf": row["duplicate_of"],
        "createdAt": row["created_at"],
        "finishedAt": row["finished_at"],
    }


def rearm_export(export_id: int) -> None:
    """Put an interrupted/failed export back to PROCESSING so it can restart."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE exports SET status='PROCESSING', size_bytes=NULL, result_json=NULL, "
            "error=NULL, duplicate_of=NULL, finished_at=NULL WHERE id=?",
            (export_id,),
        )


def mark_export_result(export_id: int, status: str,
                       counts: Optional[Dict[str, int]] = None,
                       size_bytes: Optional[int] = None,
                       error: Optional[str] = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE exports SET status=?, result_json=?, size_bytes=?, error=?, "
            "finished_at=? WHERE id=?",
            (status, json.dumps(counts) if counts is not None else None,
             size_bytes, error, time.time(), export_id),
        )
