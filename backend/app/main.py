import asyncio
import json
import os
import random
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import database as db
from . import exporting


@asynccontextmanager
async def lifespan(_: FastAPI):
    global LOOP
    db.init_db()
    exporting.cleanup_temp_files()
    LOOP = asyncio.get_running_loop()
    yield


app = FastAPI(title="DAG Workflow Engine", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ACTIVE_CLIENTS: List[WebSocket] = []
WORKFLOW_ID = 0
# Event loop of the web server, captured at startup. Worker threads need it to
# push WebSocket messages back onto the loop.
LOOP: Optional[asyncio.AbstractEventLoop] = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global LOOP
    db.init_db()
    exporting.cleanup_temp_files()
    LOOP = asyncio.get_running_loop()
    yield



class WorkflowCreate(BaseModel):
    name: str = "data-pipeline"


class RunRequest(BaseModel):
    workflowId: int
    workers: int = 3
    strategy: str = "fifo"


class ExportRequest(BaseModel):
    runIds: List[int]
    sections: List[str] = ["workflow", "tasks", "logs", "breakers"]
    startTs: Optional[float] = None
    endTs: Optional[float] = None


def generate_dag_workflow(name: str):
    """Create a realistic DAG pipeline"""
    nodes = [
        {"id": "extract", "name": "数据提取", "deps": [], "duration": 2.0},
        {"id": "validate", "name": "数据校验", "deps": ["extract"], "duration": 1.5},
        {"id": "clean_a", "name": "清洗分支A", "deps": ["validate"], "duration": 1.8},
        {"id": "clean_b", "name": "清洗分支B", "deps": ["validate"], "duration": 1.2},
        {"id": "transform", "name": "数据转换", "deps": ["clean_a"], "duration": 3.0},
        {"id": "enrich", "name": "数据增强", "deps": ["clean_a", "clean_b"], "duration": 2.0},
        {"id": "aggregate", "name": "聚合计算", "deps": ["transform", "enrich"], "duration": 2.5},
        {"id": "quality", "name": "质量检查", "deps": ["aggregate"], "duration": 1.0},
        {"id": "export_db", "name": "入库", "deps": ["quality"], "duration": 1.8},
        {"id": "export_report", "name": "报表生成", "deps": ["quality"], "duration": 2.2},
        {"id": "notify", "name": "通知", "deps": ["export_db", "export_report"], "duration": 0.5},
    ]
    positions = [
        (0, 0), (0, 1), (-1, 2), (1, 2), (-1, 3),
        (0.5, 3), (-0.3, 4), (-0.3, 5), (-1, 6), (0.5, 6), (-0.3, 7)
    ]
    for i, n in enumerate(nodes):
        n["x"] = positions[i][0] * 2.5 + 2.5
        n["y"] = positions[i][1] * 0.9
        n["status"] = "PENDING"
        n["retries"] = 0
        n["startTime"] = None
        n["endTime"] = None

    edges = []
    for n in nodes:
        for d in n["deps"]:
            edges.append([d, n["id"]])

    return {"nodes": [{
        "id": n["id"], "name": n["name"], "deps": n["deps"],
        "x": n["x"], "y": n["y"], "status": n["status"],
        "startTime": None, "endTime": None, "retries": n["retries"]
    } for n in nodes], "edges": edges, "durations": {n["id"]: n["duration"] for n in nodes}}


@app.post("/api/workflow")
def create_workflow(req: WorkflowCreate):
    global WORKFLOW_ID
    WORKFLOW_ID += 1
    dag = generate_dag_workflow(req.name)
    return {"id": WORKFLOW_ID, "name": req.name, "nodes": dag["nodes"], "edges": dag["edges"],
            "_durations": dag["durations"]}


@app.post("/api/run")
def run_workflow(req: RunRequest):
    dag = generate_dag_workflow("workflow")
    run_id = db.create_run(req.workflowId, "workflow", req.workers, req.strategy, dag, time.time())
    t = threading.Thread(target=execute_workflow,
                         args=(run_id, req.workflowId, dag, req.workers, req.strategy),
                         daemon=True)
    t.start()
    return {
        "runId": run_id,
        "workflow": {"id": req.workflowId, "name": "workflow", "nodes": dag["nodes"], "edges": dag["edges"]},
        "logs": [], "circuitBreakers": [], "completed": False, "status": "RUNNING"
    }


def execute_workflow(run_id, workflow_id, dag, workers, strategy):
    nodes = dag["nodes"]
    durations = dag["durations"]
    edges = dag["edges"]
    in_degree = defaultdict(int)
    adj = defaultdict(list)
    for u, v in edges:
        in_degree[v] += 1
        adj[u].append(v)

    # BFS topological sort
    ready = deque([n["id"] for n in nodes if in_degree[n["id"]] == 0])
    node_map = {n["id"]: n for n in nodes}
    logs = []
    cb_state = defaultdict(lambda: {"failureCount": 0, "state": "CLOSED", "cooldownUntil": 0})
    failure_threshold = 3
    running_tasks = {}
    completed = set()
    terminally_failed = set()
    start_ts = time.time()
    last_save = [start_ts]

    def breaker_payload():
        return [{"taskId": k, **v} for k, v in cb_state.items()]

    def send_update(completed_flag=False, status="RUNNING", force_save=False):
        payload = {
            "runId": run_id,
            "workflow": {"id": workflow_id, "name": "workflow", "nodes": nodes, "edges": edges},
            "logs": logs[-30:],
            "circuitBreakers": breaker_payload(),
            "completed": completed_flag,
            "status": status,
        }
        dead = []
        for ws in ACTIVE_CLIENTS:
            try:
                if LOOP is not None:
                    asyncio.run_coroutine_threadsafe(ws.send_text(json.dumps(payload)), LOOP)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in ACTIVE_CLIENTS:
                ACTIVE_CLIENTS.remove(ws)
        # Persist snapshots a few times per run so a refresh mid-run still
        # shows progress; the final snapshot is always written.
        now = time.time()
        if force_save or now - last_save[0] >= 2:
            db.update_run_snapshot(run_id, nodes, edges, logs, breaker_payload(),
                                   status, completed_flag,
                                   now if completed_flag else None)
            last_save[0] = now
        time.sleep(0.3)

    while ready or running_tasks:
        # Start tasks
        while ready and len(running_tasks) < workers:
            tid = ready.popleft()
            node = node_map[tid]
            cb = cb_state[tid]
            if cb["state"] == "OPEN" and time.time() < cb["cooldownUntil"]:
                ready.appendleft(tid)
                break
            if cb["state"] == "OPEN":
                cb["state"] = "HALF_OPEN"

            node["status"] = "RUNNING"
            node["startTime"] = time.time()

            # Simulate task execution (random success/failure)
            will_fail = random.random() < 0.12  # 12% failure rate
            runtime = durations.get(tid, 1.5) * random.uniform(0.7, 1.3)
            running_tasks[tid] = {
                "end_time": time.time() + runtime,
                "will_fail": will_fail,
                "retries": node["retries"]
            }
            logs.append({"taskId": tid, "status": "RUNNING", "timestamp": time.time(), "message": f"开始执行 {node['name']}"})

        # Check completed tasks
        now = time.time()
        finished = []
        for tid, info in running_tasks.items():
            if now >= info["end_time"]:
                node = node_map[tid]
                if info["will_fail"] and node["retries"] < 3:
                    node["retries"] += 1
                    node["status"] = "PENDING"
                    ready.appendleft(tid)
                    cb = cb_state[tid]
                    cb["failureCount"] += 1
                    logs.append({"taskId": tid, "status": "FAILED", "timestamp": now, "message": f"重试 {node['retries']}/3"})
                    if cb["failureCount"] >= failure_threshold:
                        cb["state"] = "OPEN"
                        cb["cooldownUntil"] = now + 5
                        logs.append({"taskId": tid, "status": "CIRCUIT_OPEN", "timestamp": now, "message": f"熔断! {failure_threshold}次连续失败"})
                elif info["will_fail"]:
                    # Retries exhausted: terminal failure. Downstream tasks can
                    # never become ready; the loop drains and they are SKIPPED.
                    node["status"] = "FAILED"
                    node["endTime"] = now
                    terminally_failed.add(tid)
                    cb_state[tid]["state"] = "OPEN"
                    cb_state[tid]["cooldownUntil"] = now + 5
                    logs.append({"taskId": tid, "status": "FAILED", "timestamp": now, "message": f"执行失败 {node['name']}（已重试{node['retries']}次）"})
                else:
                    node["status"] = "SUCCESS"
                    node["endTime"] = now
                    completed.add(tid)
                    cb_state[tid]["failureCount"] = 0
                    cb_state[tid]["state"] = "CLOSED"
                    logs.append({"taskId": tid, "status": "SUCCESS", "timestamp": now, "message": f"完成 {node['name']}"})
                    for next_tid in adj[tid]:
                        in_degree[next_tid] -= 1
                        if in_degree[next_tid] == 0:
                            ready.append(next_tid)
                finished.append(tid)

        for tid in finished:
            del running_tasks[tid]

        send_update()

    # Anything never started was unreachable due to an upstream failure.
    for n in nodes:
        if n["status"] == "PENDING":
            n["status"] = "SKIPPED"

    final_status = "FAILED" if terminally_failed else "SUCCESS"
    send_update(True, status=final_status, force_save=True)


# ------------------------------------------------------------- run history

@app.get("/api/runs")
def list_runs():
    return db.list_runs()


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    row = db.get_run_row(run_id)
    if row is None:
        raise HTTPException(404, f"执行 #{run_id} 不存在")
    return db.run_detail(row)


# ---------------------------------------------------------------- exports

def _normalize_or_400(req: ExportRequest):
    try:
        return exporting.normalize_scope(req.runIds, req.sections, req.startTs, req.endTs)
    except exporting.ExportError as e:
        raise HTTPException(400, str(e))


def _preview(req: ExportRequest):
    run_ids, sections, st, et = _normalize_or_400(req)
    try:
        content = exporting.build_content(run_ids, sections, st, et)
    except exporting.ExportError as e:
        raise HTTPException(400, str(e))
    per_run = []
    for entry in content["runs"]:
        per_run.append({
            "runId": entry["runId"],
            "name": entry["name"],
            "status": entry["status"],
            "counts": {
                "workflow": 1 if "workflow" in entry else 0,
                "tasks": len(entry.get("tasks", [])),
                "logs": len(entry.get("logs", [])),
                "breakers": len(entry.get("breakers", [])),
            },
        })
    return run_ids, sections, st, et, content, per_run


@app.post("/api/export-preview")
def export_preview(req: ExportRequest):
    """Dry-run the scope without writing anything."""
    run_ids, sections, st, et, content, per_run = _preview(req)
    return {"counts": content["counts"], "hasData": content["_hasData"],
            "runs": per_run}


@app.post("/api/exports")
def create_export(req: ExportRequest):
    """Create (or reuse) an export. Same scope -> same file, never duplicated."""
    run_ids, sections, st, et, content, _ = _preview(req)
    key = exporting.scope_key(run_ids, sections, st, et)
    filename = f"export_{key}.json"

    # Hold the scope lock across the lookup/insert so two identical concurrent
    # requests collapse into one record (and one file). The worker thread
    # releases the key when the job finishes; the early-return paths release
    # it here.
    if not exporting.try_acquire_scope(key):
        existing = db.find_export_by_key(key)
        if existing is not None:
            return {"reused": True, "record": db.export_to_api(existing)}
        raise HTTPException(409, "相同范围的导出正在创建中，请稍后查看导出记录")

    existing = db.find_export_by_key(key)
    if existing is not None:
        status = existing["status"]
        if status == "SUCCESS":
            exporting.release_scope(key)
            return {"reused": True, "record": db.export_to_api(existing)}
        if status == "EMPTY" and not content["_hasData"]:
            # Same conclusion still holds: keep the one record, no file.
            exporting.release_scope(key)
            return {"reused": True, "record": db.export_to_api(existing)}
        # FAILED / interrupted PROCESSING / EMPTY-with-new-data: rearm the
        # existing record instead of duplicating it; atomic_write guarantees
        # no half file can remain. Worker thread releases the key on finish.
        db.rearm_export(existing["id"])
        try:
            exporting.start_export_job(existing["id"], key, run_ids, sections,
                                      st, et, filename)
        except Exception:
            exporting.release_scope(key)
            raise
        return {"reused": True,
                "record": db.export_to_api(db.get_export_row(existing["id"]))}

    try:
        export_id = db.insert_export(key, run_ids, sections, st, et, filename,
                                     time.time())
        exporting.start_export_job(export_id, key, run_ids, sections, st, et, filename)
    except Exception:
        exporting.release_scope(key)
        raise
    return {"reused": False,
            "record": db.export_to_api(db.get_export_row(export_id))}


@app.get("/api/exports")
def list_exports():
    return [db.export_to_api(r) for r in db.list_export_rows()]


@app.get("/api/exports/{export_id}")
def get_export(export_id: int):
    row = db.get_export_row(export_id)
    if row is None:
        raise HTTPException(404, "导出记录不存在")
    record = db.export_to_api(row)
    content = None
    if row["status"] == "SUCCESS" and row["filename"]:
        path = os.path.join(db.EXPORTS_DIR, row["filename"])
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                content = json.load(f)
    # Detail page counts come straight from the written file — identical 口径
    # to the export-record list.
    return {"record": record, "content": content}


@app.get("/api/exports/{export_id}/download")
def download_export(export_id: int):
    row = db.get_export_row(export_id)
    if row is None:
        raise HTTPException(404, "导出记录不存在")
    if row["status"] != "SUCCESS":
        raise HTTPException(409, "导出尚未完成或没有可下载的文件")
    path = os.path.join(db.EXPORTS_DIR, row["filename"])
    if not os.path.exists(path):
        raise HTTPException(410, "导出文件不存在，请重新开始导出")
    label = "_".join(str(i) for i in json.loads(row["run_ids_json"])[:5])
    return FileResponse(
        path,
        media_type="application/json; charset=utf-8",
        filename=f"execution_runs_{label}_{row['id']}.json",
    )


@app.post("/api/exports/{export_id}/retry")
def retry_export(export_id: int):
    """Restart an interrupted/failed/empty export; no partial file survives."""
    row = db.get_export_row(export_id)
    if row is None:
        raise HTTPException(404, "导出记录不存在")
    if row["status"] == "SUCCESS":
        return {"record": db.export_to_api(row)}

    run_ids = json.loads(row["run_ids_json"])
    sections = json.loads(row["sections_json"])
    st, et = row["start_ts"], row["end_ts"]
    key = row["scope_key"]
    filename = row["filename"]

    if not exporting.try_acquire_scope(key):
        raise HTTPException(409, "该导出正在进行中")
    # Remove any partial artifact, then rearm the same record.
    for suffix in ("", ".tmp"):
        p = os.path.join(db.EXPORTS_DIR, (filename or "") + suffix)
        if filename and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    db.rearm_export(export_id)
    try:
        exporting.start_export_job(export_id, key, run_ids, sections, st, et, filename)
    except Exception:
        exporting.release_scope(key)
        raise
    return {"record": db.export_to_api(db.get_export_row(export_id))}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    ACTIVE_CLIENTS.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in ACTIVE_CLIENTS:
            ACTIVE_CLIENTS.remove(ws)
    except Exception:
        if ws in ACTIVE_CLIENTS:
            ACTIVE_CLIENTS.remove(ws)
