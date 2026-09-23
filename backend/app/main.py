import asyncio, time, random, json, threading, os
from collections import defaultdict, deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional

from . import storage

app = FastAPI(title="DAG Workflow Engine")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ACTIVE_CLIENTS = []
WORKFLOW_ID = 0
MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None

@app.on_event("startup")
def _startup():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_event_loop()
    storage.init_db()


class WorkflowCreate(BaseModel):
    name: str = "data-pipeline"

class RunRequest(BaseModel):
    workflowId: int
    workers: int = 3
    strategy: str = "fifo"

class ExportRequest(BaseModel):
    runIds: List[int]
    stages: List[str]
    timeStart: Optional[float] = None
    timeEnd: Optional[float] = None


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


@app.get("/api/stages")
def list_stages():
    """Catalog of selectable 环节 (stages)."""
    dag = generate_dag_workflow("workflow")
    return [{"id": n["id"], "name": n["name"]} for n in dag["nodes"]]


@app.post("/api/run")
def run_workflow(req: RunRequest):
    dag = generate_dag_workflow("workflow")
    for n in dag["nodes"]:
        n["duration"] = dag["durations"][n["id"]]
    run_id = storage.create_run(req.workflowId, "workflow", req.workers, req.strategy, dag["nodes"])
    t = threading.Thread(target=execute_workflow,
                         args=(run_id, dag, req.workers, req.strategy), daemon=True)
    t.start()
    return {
        "runId": run_id,
        "workflow": {"id": req.workflowId, "name": "workflow", "nodes": dag["nodes"], "edges": dag["edges"]},
        "logs": [], "circuitBreakers": [], "completed": False
    }


def execute_workflow(run_id, dag, workers, strategy):
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

    def add_log(tid, status, ts, message):
        entry = {"taskId": tid, "status": status, "timestamp": ts, "message": message}
        logs.append(entry)
        storage.insert_log(run_id, tid, status, ts, message)

    def send_update(completed_flag=False):
        payload = {
            "runId": run_id,
            "workflow": {"id": 1, "name": "workflow", "nodes": nodes, "edges": edges},
            "logs": logs[-30:],
            "circuitBreakers": [{"taskId": k, **v} for k, v in cb_state.items()],
            "completed": completed_flag
        }
        if MAIN_LOOP and MAIN_LOOP.is_running():
            for ws in list(ACTIVE_CLIENTS):
                try:
                    asyncio.run_coroutine_threadsafe(ws.send_text(json.dumps(payload)), MAIN_LOOP)
                except Exception:
                    pass
        time.sleep(0.3)

    while ready or running_tasks:
        # Start tasks
        while ready and len(running_tasks) < workers:
            tid = ready.popleft()
            node = node_map[tid]
            cb = cb_state[tid]
            if cb["state"] == "OPEN" and time.time() < cb["cooldownUntil"]:
                ready.appendleft(tid)
                continue
            if cb["state"] == "OPEN":
                cb["state"] = "HALF_OPEN"

            node["status"] = "RUNNING"
            node["startTime"] = time.time()
            storage.update_node(run_id, tid, "RUNNING", retries=node["retries"],
                                start_time=node["startTime"])

            # Simulate task execution (random success/failure)
            will_fail = random.random() < 0.12  # 12% failure rate
            runtime = durations.get(tid, 1.5) * random.uniform(0.7, 1.3)
            running_tasks[tid] = {
                "end_time": time.time() + runtime,
                "will_fail": will_fail,
                "retries": node["retries"]
            }
            add_log(tid, "RUNNING", time.time(), f"开始执行 {node['name']}")

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
                    storage.update_node(run_id, tid, "PENDING", retries=node["retries"])
                    add_log(tid, "FAILED", now, f"重试 {node['retries']}/3")
                    if cb["failureCount"] >= failure_threshold:
                        cb["state"] = "OPEN"
                        cb["cooldownUntil"] = now + 5
                        add_log(tid, "CIRCUIT_OPEN", now, f"熔断! {failure_threshold}次连续失败")
                else:
                    node["status"] = "SUCCESS"
                    node["endTime"] = now
                    completed.add(tid)
                    cb_state[tid]["failureCount"] = 0
                    cb_state[tid]["state"] = "CLOSED"
                    storage.update_node(run_id, tid, "SUCCESS", retries=node["retries"],
                                        end_time=now)
                    add_log(tid, "SUCCESS", now, f"完成 {node['name']}")
                    for next_tid in adj[tid]:
                        in_degree[next_tid] -= 1
                        if in_degree[next_tid] == 0:
                            ready.append(next_tid)
                finished.append(tid)

        for tid in finished:
            del running_tasks[tid]

        storage.replace_breakers(
            run_id, [{"taskId": k, **v} for k, v in cb_state.items()]
        )
        send_update()
        if len(completed) == len(nodes):
            break

    storage.finish_run(
        run_id, nodes,
        [{"taskId": k, **v} for k, v in cb_state.items()],
    )
    send_update(True)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    ACTIVE_CLIENTS.append(ws)
    try:
        while True: await ws.receive_text()
    except Exception:
        if ws in ACTIVE_CLIENTS: ACTIVE_CLIENTS.remove(ws)


# ------------------------------------------------------------------ runs

@app.get("/api/runs")
def get_runs():
    return storage.list_runs()


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    run = storage.get_run(run_id)
    if not run:
        raise HTTPException(404, "执行记录不存在")
    return run


# ---------------------------------------------------------------- exports

def _validate_export_params(req: ExportRequest):
    run_ids = sorted(set(int(r) for r in req.runIds))
    if not run_ids:
        raise HTTPException(400, "请至少选择一次执行")
    existing = storage.get_runs(run_ids)
    if not existing:
        raise HTTPException(404, "所选执行记录不存在")
    existing_ids = {r["id"] for r in existing}
    missing = [r for r in run_ids if r not in existing_ids]
    if missing:
        raise HTTPException(404, f"执行记录不存在: {missing}")
    running = [r["id"] for r in existing if r["status"] != "completed"]
    if running:
        raise HTTPException(400, f"执行尚未结束，暂不能导出: #{running}")
    if not req.stages:
        raise HTTPException(400, "请至少选择一个环节")
    t0, t1 = req.timeStart, req.timeEnd
    if t0 is not None and t1 is not None and t0 > t1:
        raise HTTPException(400, "时间范围起始不能晚于结束")
    return run_ids, t0, t1, existing_ids


@app.post("/api/exports/preview")
def preview_export(req: ExportRequest):
    """Filter result without writing anything; used by the detail dialog too."""
    run_ids, t0, t1, _ = _validate_export_params(req)
    data = storage.build_export_data(run_ids, req.stages, t0, t1)
    return {
        "runCount": len(data["runs"]),
        "nodeCount": data["nodeCount"],
        "logCount": data["logCount"],
        "breakerCount": data["breakerCount"],
        "empty": not (data["nodeCount"] or data["logCount"] or data["breakerCount"]),
        "runs": [{"id": r["id"], "name": r["name"], "nodeCount": len(r["nodes"]),
                  "logCount": len(r["logs"]), "breakerCount": len(r["circuitBreakers"])}
                 for r in data["runs"]],
    }


def _generate(export_id, run_ids, stages, t0, t1, file_path, file_name):
    data = storage.build_export_data(run_ids, stages, t0, t1)
    if not (data["nodeCount"] or data["logCount"] or data["breakerCount"]):
        # No qualifying data: never produce an empty file
        storage.mark_export_no_data(export_id)
        return storage.get_export(export_id), False
    payload = {
        "exportedAt": time.time(),
        "scope": {
            "runIds": sorted(set(run_ids)),
            "stages": sorted(stages),
            "timeStart": t0,
            "timeEnd": t1,
        },
        "summary": {
            "runs": len(data["runs"]),
            "nodes": data["nodeCount"],
            "logs": data["logCount"],
            "circuitBreakers": data["breakerCount"],
        },
        "runs": data["runs"],
    }
    try:
        storage.write_export_file(file_path, payload)
        storage.complete_export(export_id, data)
    except Exception as exc:  # pragma: no cover - defensive
        storage.fail_export(export_id, str(exc))
        raise
    return storage.get_export(export_id), True


@app.post("/api/exports")
def create_export(req: ExportRequest):
    run_ids, t0, t1, _ = _validate_export_params(req)
    scope_hash = storage.compute_scope(run_ids, req.stages, t0, t1)

    # Same scope never produces a second file: reuse the existing record,
    # or restart a previously failed attempt in place.
    existing = storage.find_export_by_scope(scope_hash)
    if existing and existing["status"] in ("success", "no_data", "running", "pending"):
        return {**existing, "reused": True}
    if existing and existing["status"] == "failed":
        info = storage.reset_export_for_retry(existing["id"])
        if info:
            record, _ = _generate(existing["id"], info["runIds"], info["stages"],
                                  info["timeStart"], info["timeEnd"],
                                  info["filePath"], info["fileName"])
            return {**record, "reused": True}

    file_path = storage.export_file_path(scope_hash)
    export_id = storage.create_export_record(
        scope_hash, run_ids, req.stages, t0, t1,
        file_path, os.path.basename(file_path),
    )
    try:
        record, _ = _generate(export_id, run_ids, req.stages, t0, t1,
                              file_path, os.path.basename(file_path))
    except Exception as exc:
        raise HTTPException(500, f"导出失败: {exc}")
    return {**record, "reused": False}


@app.get("/api/exports")
def get_exports():
    return storage.list_exports()


@app.get("/api/exports/{export_id}")
def get_export_detail(export_id: int):
    """Record detail: scope + counts recomputed through the same filter
    used to generate the file, so record page and file content agree."""
    record = storage.get_export(export_id)
    if not record:
        raise HTTPException(404, "导出记录不存在")
    detail = storage.build_export_data(record["runIds"], record["stages"],
                                       record["timeStart"], record["timeEnd"])
    return {
        **record,
        "detail": {
            "runCount": len(detail["runs"]),
            "nodeCount": detail["nodeCount"],
            "logCount": detail["logCount"],
            "breakerCount": detail["breakerCount"],
            "runs": [{"id": r["id"], "name": r["name"], "nodeCount": len(r["nodes"]),
                      "logCount": len(r["logs"]), "breakerCount": len(r["circuitBreakers"])}
                     for r in detail["runs"]],
        },
    }


@app.post("/api/exports/{export_id}/retry")
def retry_export(export_id: int):
    info = storage.reset_export_for_retry(export_id)
    if not info:
        raise HTTPException(400, "只有失败或中断的导出可以重新开始")
    try:
        record, _ = _generate(export_id, info["runIds"], info["stages"],
                              info["timeStart"], info["timeEnd"],
                              info["filePath"], info["fileName"])
    except Exception as exc:
        raise HTTPException(500, f"导出失败: {exc}")
    return record


@app.get("/api/exports/{export_id}/download")
def download_export(export_id: int):
    record = storage.get_export(export_id)
    if not record:
        raise HTTPException(404, "导出记录不存在")
    if record["status"] != "success":
        raise HTTPException(409, "导出未成功完成，无文件可下载")
    path = record["filePath"]
    if not path or not os.path.exists(path):
        raise HTTPException(410, "导出文件已丢失，请重新导出")
    return FileResponse(
        path,
        media_type="application/json; charset=utf-8",
        filename=record["fileName"],
    )
