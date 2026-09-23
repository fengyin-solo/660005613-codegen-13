"""SQLite persistence for execution runs and export records.

Export files are written atomically (temp file -> os.replace) so an
interrupted/failed export never leaves a half file behind. A scope hash
guarantees that exporting the same runs/stages/time-range twice reuses
one file instead of producing duplicates.
"""
import os
import json
import time
import sqlite3
import threading
import hashlib

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
EXPORT_DIR = os.path.join(DATA_DIR, "exports")
DB_PATH = os.path.join(DATA_DIR, "workflow.db")

_write_lock = threading.Lock()


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    os.makedirs(EXPORT_DIR, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY,
                workflow_id INTEGER,
                name TEXT,
                workers INTEGER,
                strategy TEXT,
                status TEXT DEFAULT 'running',
                created_at REAL,
                finished_at REAL
            );
            CREATE TABLE IF NOT EXISTS run_nodes (
                run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
                node_id TEXT,
                name TEXT,
                deps TEXT,
                x REAL,
                y REAL,
                status TEXT,
                retries INTEGER,
                start_time REAL,
                end_time REAL,
                duration REAL,
                PRIMARY KEY (run_id, node_id)
            );
            CREATE TABLE IF NOT EXISTS run_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
                task_id TEXT,
                status TEXT,
                timestamp REAL,
                message TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_run_logs_run ON run_logs(run_id, timestamp);
            CREATE TABLE IF NOT EXISTS run_breakers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
                task_id TEXT,
                failure_count INTEGER,
                state TEXT,
                cooldown_until REAL
            );
            CREATE INDEX IF NOT EXISTS idx_run_breakers_run ON run_breakers(run_id);
            CREATE TABLE IF NOT EXISTS exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_hash TEXT UNIQUE,
                run_ids TEXT,
                stages TEXT,
                time_start REAL,
                time_end REAL,
                status TEXT,
                file_path TEXT,
                file_name TEXT,
                file_size INTEGER DEFAULT 0,
                node_count INTEGER DEFAULT 0,
                log_count INTEGER DEFAULT 0,
                breaker_count INTEGER DEFAULT 0,
                error TEXT,
                created_at REAL,
                finished_at REAL
            );
            """
        )
    _recover_interrupted()


def _recover_interrupted():
    """Mark in-flight exports as failed after a restart and remove temp files."""
    with _write_lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, file_path FROM exports WHERE status IN ('running','pending')"
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE exports SET status='failed', error='服务重启，导出中断，可重新导出', "
                "finished_at=? WHERE id=?",
                (time.time(), r["id"]),
            )
            if r["file_path"]:
                tmp = f"{r['file_path']}.tmp"
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass


# ---------------------------------------------------------------- runs

def create_run(workflow_id, name, workers, strategy, nodes):
    now = time.time()
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (workflow_id, name, workers, strategy, status, created_at) "
            "VALUES (?,?,?,?, 'running', ?)",
            (workflow_id, name, workers, strategy, now),
        )
        run_id = cur.lastrowid
        for n in nodes:
            conn.execute(
                "INSERT INTO run_nodes (run_id, node_id, name, deps, x, y, status, retries, "
                "start_time, end_time, duration) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, n["id"], n["name"], json.dumps(n.get("deps", []), ensure_ascii=False),
                 n.get("x", 0), n.get("y", 0), n["status"], n.get("retries", 0),
                 n.get("startTime"), n.get("endTime"), n.get("duration", 0)),
            )
    return run_id


def update_node(run_id, node_id, status, retries=None, start_time=None, end_time=None):
    fields = ["status=?"]
    values = [status]
    if retries is not None:
        fields.append("retries=?"); values.append(retries)
    if start_time is not None:
        fields.append("start_time=?"); values.append(start_time)
    if end_time is not None:
        fields.append("end_time=?"); values.append(end_time)
    values += [run_id, node_id]
    with _write_lock, _connect() as conn:
        conn.execute(f"UPDATE run_nodes SET {', '.join(fields)} WHERE run_id=? AND node_id=?", values)


def insert_log(run_id, task_id, status, timestamp, message):
    with _write_lock, _connect() as conn:
        conn.execute(
            "INSERT INTO run_logs (run_id, task_id, status, timestamp, message) VALUES (?,?,?,?,?)",
            (run_id, task_id, status, timestamp, message),
        )


def replace_breakers(run_id, breakers):
    with _write_lock, _connect() as conn:
        conn.execute("DELETE FROM run_breakers WHERE run_id=?", (run_id,))
        for b in breakers:
            conn.execute(
                "INSERT INTO run_breakers (run_id, task_id, failure_count, state, cooldown_until) "
                "VALUES (?,?,?,?,?)",
                (run_id, b["taskId"], b.get("failureCount", 0), b.get("state", "CLOSED"),
                 b.get("cooldownUntil", 0)),
            )


def finish_run(run_id, nodes, breakers):
    """Final snapshot: node states + breaker table are replaced wholesale."""
    now = time.time()
    with _write_lock, _connect() as conn:
        for n in nodes:
            conn.execute(
                "UPDATE run_nodes SET status=?, retries=?, start_time=?, end_time=? "
                "WHERE run_id=? AND node_id=?",
                (n["status"], n.get("retries", 0), n.get("startTime"), n.get("endTime"),
                 run_id, n["id"]),
            )
        conn.execute("DELETE FROM run_breakers WHERE run_id=?", (run_id,))
        for b in breakers:
            conn.execute(
                "INSERT INTO run_breakers (run_id, task_id, failure_count, state, cooldown_until) "
                "VALUES (?,?,?,?,?)",
                (run_id, b["taskId"], b.get("failureCount", 0), b.get("state", "CLOSED"),
                 b.get("cooldownUntil", 0)),
            )
        conn.execute(
            "UPDATE runs SET status='completed', finished_at=? WHERE id=?", (now, run_id)
        )


def list_runs():
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, workflow_id, name, workers, strategy, status, created_at, finished_at "
            "FROM runs ORDER BY id DESC"
        ).fetchall()
        result = []
        for r in rows:
            counts = conn.execute(
                "SELECT COUNT(*) c, MIN(start_time) t0, MAX(COALESCE(end_time, start_time)) t1 "
                "FROM run_nodes WHERE run_id=? AND start_time IS NOT NULL", (r["id"],)
            ).fetchone()
            logrow = conn.execute(
                "SELECT COUNT(*) c FROM run_logs WHERE run_id=?", (r["id"],)
            ).fetchone()
            result.append({
                "id": r["id"], "workflowId": r["workflow_id"], "name": r["name"],
                "workers": r["workers"], "strategy": r["strategy"], "status": r["status"],
                "createdAt": r["created_at"], "finishedAt": r["finished_at"],
                "startedAt": counts["t0"], "endedAt": counts["t1"],
                "nodeCount": counts["c"], "logCount": logrow["c"],
            })
        return result


def _load_run(conn, run_id):
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        return None
    nodes = []
    for nr in conn.execute("SELECT * FROM run_nodes WHERE run_id=?", (run_id,)).fetchall():
        nodes.append({
            "id": nr["node_id"], "name": nr["name"], "deps": json.loads(nr["deps"]),
            "x": nr["x"], "y": nr["y"], "status": nr["status"], "retries": nr["retries"],
            "startTime": nr["start_time"], "endTime": nr["end_time"],
        })
    logs = [{
        "taskId": lr["task_id"], "status": lr["status"], "timestamp": lr["timestamp"],
        "message": lr["message"],
    } for lr in conn.execute(
        "SELECT * FROM run_logs WHERE run_id=? ORDER BY timestamp, id", (run_id,)
    ).fetchall()]
    breakers = [{
        "taskId": br["task_id"], "failureCount": br["failure_count"], "state": br["state"],
        "cooldownUntil": br["cooldown_until"],
    } for br in conn.execute(
        "SELECT * FROM run_breakers WHERE run_id=?", (run_id,)
    ).fetchall()]
    edges = []
    for n in nodes:
        for d in n["deps"]:
            edges.append([d, n["id"]])
    return {
        "id": run["id"], "workflowId": run["workflow_id"], "name": run["name"],
        "workers": run["workers"], "strategy": run["strategy"], "status": run["status"],
        "createdAt": run["created_at"], "finishedAt": run["finished_at"],
        "nodes": nodes, "edges": edges, "logs": logs, "circuitBreakers": breakers,
    }


def get_runs(run_ids):
    with _connect() as conn:
        runs = [_load_run(conn, rid) for rid in run_ids]
    return [r for r in runs if r is not None]


def get_run(run_id):
    with _connect() as conn:
        return _load_run(conn, run_id)


# --------------------------------------------------------------- export

def compute_scope(run_ids, stages, time_start, time_end):
    """Canonical hash of an export scope; same scope -> same hash -> one file."""
    key = json.dumps({
        "run_ids": sorted(int(r) for r in run_ids),
        "stages": sorted(stages),
        "time_start": time_start or None,
        "time_end": time_end or None,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def build_export_data(run_ids, stages, time_start=None, time_end=None):
    """Filter selected runs by stages and time range.

    Single filtering rule shared by preview, file generation and record
    stats, so 导出记录 and 页面明细 always report the same counts.
    """
    stage_set = set(stages)
    runs = get_runs(sorted(set(int(r) for r in run_ids)))

    def in_window(ts):
        if ts is None:
            return False
        if time_start is not None and ts < time_start:
            return False
        if time_end is not None and ts > time_end:
            return False
        return True

    exported_runs = []
    total_nodes = total_logs = total_breakers = 0
    for run in runs:
        # stage filter applies first; a node outside selected stages is not
        # counted and its logs/breaker are excluded too
        sel_nodes = [n for n in run["nodes"] if n["id"] in stage_set]
        sel_node_ids = {n["id"] for n in sel_nodes}

        nodes_out, node_seen = [], set()
        for n in sel_nodes:
            started = in_window(n["startTime"])
            finished = in_window(n["endTime"])
            if started or finished:
                out = dict(n)
                if not started:
                    out["startTime"] = None
                if not finished:
                    out["endTime"] = None
                nodes_out.append(out)
                node_seen.add(n["id"])
        logs_out = [l for l in run["logs"]
                    if l["taskId"] in sel_node_ids and in_window(l["timestamp"])]
        breakers_out = [b for b in run["circuitBreakers"]
                        if b["taskId"] in sel_node_ids
                        and any(l["taskId"] == b["taskId"] for l in logs_out)]

        node_ids = {n["id"] for n in run["nodes"]}
        edges_out = [[u, v] for u, v in run["edges"]
                     if u in node_ids and v in node_ids and v in sel_node_ids]

        if nodes_out or logs_out or breakers_out:
            exported_runs.append({
                "id": run["id"], "name": run["name"],
                "workflowId": run["workflowId"],
                "workers": run["workers"], "strategy": run["strategy"],
                "status": run["status"],
                "nodes": nodes_out, "edges": edges_out,
                "logs": logs_out, "circuitBreakers": breakers_out,
            })
            total_nodes += len(nodes_out)
            total_logs += len(logs_out)
            total_breakers += len(breakers_out)

    return {
        "runs": exported_runs,
        "nodeCount": total_nodes,
        "logCount": total_logs,
        "breakerCount": total_breakers,
    }


def find_export_by_scope(scope_hash):
    with _connect() as conn:
        r = conn.execute("SELECT * FROM exports WHERE scope_hash=?", (scope_hash,)).fetchone()
        return _export_row(r) if r else None


def _export_row(r):
    return {
        "id": r["id"], "scopeHash": r["scope_hash"],
        "runIds": json.loads(r["run_ids"]), "stages": json.loads(r["stages"]),
        "timeStart": r["time_start"], "timeEnd": r["time_end"],
        "status": r["status"], "filePath": r["file_path"], "fileName": r["file_name"],
        "fileSize": r["file_size"] or 0, "nodeCount": r["node_count"] or 0,
        "logCount": r["log_count"] or 0, "breakerCount": r["breaker_count"] or 0,
        "error": r["error"], "createdAt": r["created_at"], "finishedAt": r["finished_at"],
    }


def list_exports():
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM exports ORDER BY id DESC").fetchall()
        return [_export_row(r) for r in rows]


def get_export(export_id):
    with _connect() as conn:
        r = conn.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
        return _export_row(r) if r else None


def create_export_record(scope_hash, run_ids, stages, time_start, time_end, file_path, file_name):
    now = time.time()
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO exports (scope_hash, run_ids, stages, time_start, time_end, status, "
            "file_path, file_name, created_at) VALUES (?,?,?,?,?, 'running', ?, ?, ?)",
            (scope_hash, json.dumps(sorted(set(int(r) for r in run_ids))),
             json.dumps(sorted(stages), ensure_ascii=False),
             time_start, time_end, file_path, file_name, now),
        )
        return cur.lastrowid


def mark_export_no_data(export_id):
    with _write_lock, _connect() as conn:
        conn.execute(
            "UPDATE exports SET status='no_data', file_path=NULL, file_name=NULL, "
            "finished_at=? WHERE id=?",
            (time.time(), export_id),
        )


def complete_export(export_id, counts):
    with _write_lock, _connect() as conn:
        r = conn.execute("SELECT file_path FROM exports WHERE id=?", (export_id,)).fetchone()
        size = os.path.getsize(r["file_path"]) if r["file_path"] and os.path.exists(r["file_path"]) else 0
        conn.execute(
            "UPDATE exports SET status='success', file_size=?, node_count=?, log_count=?, "
            "breaker_count=?, finished_at=? WHERE id=?",
            (size, counts["nodeCount"], counts["logCount"], counts["breakerCount"],
             time.time(), export_id),
        )


def fail_export(export_id, error):
    with _write_lock, _connect() as conn:
        r = conn.execute("SELECT file_path FROM exports WHERE id=?", (export_id,)).fetchone()
        if r and r["file_path"]:
            tmp = f"{r['file_path']}.tmp"
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        conn.execute(
            "UPDATE exports SET status='failed', error=?, finished_at=? WHERE id=?",
            (error, time.time(), export_id),
        )


def reset_export_for_retry(export_id):
    """A failed/interrupted export is retried in place: same record, no half file."""
    with _write_lock, _connect() as conn:
        r = conn.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
        if not r or r["status"] != "failed":
            return None
        if r["file_path"] and os.path.exists(r["file_path"]):
            try:
                os.remove(r["file_path"])
            except OSError:
                pass
        tmp = f"{r['file_path']}.tmp" if r["file_path"] else None
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        conn.execute(
            "UPDATE exports SET status='running', error=NULL, file_size=0, node_count=0, "
            "log_count=0, breaker_count=0, created_at=?, finished_at=NULL WHERE id=?",
            (time.time(), export_id),
        )
        return {
            "filePath": r["file_path"], "fileName": r["file_name"],
            "runIds": json.loads(r["run_ids"]), "stages": json.loads(r["stages"]),
            "timeStart": r["time_start"], "timeEnd": r["time_end"],
        }


def write_export_file(file_path, payload):
    """Atomic write: fully write .tmp then os.replace; no half file is observable."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    tmp = f"{file_path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, file_path)


def export_file_path(scope_hash):
    return os.path.join(EXPORT_DIR, f"execution-export-{scope_hash}.json")
