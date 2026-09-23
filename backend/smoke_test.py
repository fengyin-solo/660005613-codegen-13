import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# fresh data dir for the smoke test (isolated from any running server)
import tempfile

# Override storage paths BEFORE importing app.main, so init_db uses them.
from app import database as db  # noqa: E402

_tmp = tempfile.mkdtemp(prefix="dag_smoke_")
db.DATA_DIR = os.path.join(_tmp, "data")
db.EXPORTS_DIR = os.path.join(db.DATA_DIR, "exports")
db.DB_PATH = os.path.join(db.DATA_DIR, "engine.db")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

with TestClient(app) as client:
    # 1. start a run and wait for completion
    wf = client.post("/api/workflow", json={"name": "smoke"}).json()
    resp = client.post("/api/run", json={"workflowId": wf["id"], "workers": 3, "strategy": "fifo"})
    run_id = resp.json()["runId"]
    print("run started:", run_id)

    for _ in range(120):
        detail = client.get(f"/api/runs/{run_id}").json()
        if detail["completed"]:
            break
        time.sleep(0.5)
    else:
        raise SystemExit("run never completed")
    print("run completed status:", detail["status"],
          "success:", detail["taskSuccess"], "failed:", detail["taskFailed"],
          "skipped:", detail["taskSkipped"], "logs:", detail["logCount"])

    # 2. history survives (separate request = refresh simulation)
    runs = client.get("/api/runs").json()
    assert any(r["id"] == run_id for r in runs), "run missing from history"

    # 3. preview full scope
    pv = client.post("/api/export-preview", json={"runIds": [run_id],
                     "sections": ["workflow", "tasks", "logs", "breakers"]}).json()
    print("preview counts:", pv["counts"], "hasData:", pv["hasData"])
    assert pv["hasData"] and pv["counts"]["runCount"] == 1
    assert pv["counts"]["taskCount"] == detail["taskTotal"]

    # 4. create export, wait for SUCCESS
    r1 = client.post("/api/exports", json={"runIds": [run_id],
                     "sections": ["workflow", "tasks", "logs", "breakers"]}).json()
    eid = r1["record"]["id"]
    for _ in range(50):
        rec = client.get(f"/api/exports/{eid}").json()["record"]
        if rec["status"] in ("SUCCESS", "FAILED", "EMPTY"):
            break
        time.sleep(0.2)
    print("export:", rec["status"], rec["sizeBytes"], "bytes", rec["counts"])
    assert rec["status"] == "SUCCESS" and rec["sizeBytes"] > 0

    # 5. identical scope reuses the same record/file
    r2 = client.post("/api/exports", json={"runIds": [run_id],
                     "sections": ["workflow", "tasks", "logs", "breakers"]}).json()
    assert r2["reused"] is True and r2["record"]["id"] == eid, "duplicate export created"
    print("idempotent reuse OK -> export #", eid)

    # ordering of runIds / sections must not matter
    r2b = client.post("/api/exports", json={"runIds": [run_id],
                      "sections": ["logs", "breakers", "tasks", "workflow"]}).json()
    assert r2b["record"]["id"] == eid, "scope key not order-insensitive"

    # 6. no matching data -> EMPTY, no file
    far_future = time.time() + 100000
    r3 = client.post("/api/exports", json={"runIds": [run_id],
                     "sections": ["logs"], "startTs": far_future,
                     "endTs": far_future + 100}).json()
    eid3 = r3["record"]["id"]
    for _ in range(50):
        rec3 = client.get(f"/api/exports/{eid3}").json()["record"]
        if rec3["status"] in ("SUCCESS", "FAILED", "EMPTY"):
            break
        time.sleep(0.2)
    print("empty scope export:", rec3["status"], "| size:", rec3["sizeBytes"])
    assert rec3["status"] == "EMPTY" and not rec3["sizeBytes"]
    dl = client.get(f"/api/exports/{eid3}/download")
    assert dl.status_code == 409, "empty export must not be downloadable"
    files = os.listdir(db.EXPORTS_DIR)
    from app.exporting import scope_key
    empty_path = os.path.join(
        db.EXPORTS_DIR,
        f"export_{scope_key([run_id], ['logs'], far_future, far_future + 100)}.json")
    assert not os.path.exists(empty_path), "EMPTY scope must not produce a file"
    assert all(f.endswith(".json") for f in files), f"non-final file left: {files}"
    assert len([f for f in files if f.endswith('.json')]) == 1, files

    # 7. detail 口径 matches record counts
    det = client.get(f"/api/exports/{eid}").json()
    file_counts = det["content"]["counts"]
    assert file_counts == rec["counts"], (file_counts, rec["counts"])
    print("counts consistent record vs file:", file_counts)

    # 8. time-range + subset sections
    r4 = client.post("/api/export-preview", json={"runIds": [run_id],
                     "sections": ["tasks", "logs"],
                     "startTs": detail["startTs"], "endTs": detail["endTs"]}).json()
    assert r4["counts"]["taskCount"] == detail["taskTotal"]
    assert r4["counts"]["logCount"] == detail["logCount"]
    # window before the run started: tasks excluded, no data
    r5 = client.post("/api/export-preview", json={"runIds": [run_id],
                     "sections": ["tasks", "logs"],
                     "startTs": detail["startTs"] - 100,
                     "endTs": detail["startTs"] - 50}).json()
    assert r5["hasData"] is False and r5["counts"]["taskCount"] == 0
    print("time-range filtering OK")

    # 9. validation
    bad = client.post("/api/exports", json={"runIds": [], "sections": ["logs"]})
    assert bad.status_code == 400
    bad = client.post("/api/exports", json={"runIds": [run_id], "sections": []})
    assert bad.status_code == 400
    bad = client.post("/api/exports", json={"runIds": [999999], "sections": ["logs"]})
    assert bad.status_code == 400
    print("validation OK")

    # 10. download
    dl = client.get(f"/api/exports/{eid}/download")
    assert dl.status_code == 200 and len(dl.content) == rec["sizeBytes"]
    doc = json.loads(dl.content)
    assert len(doc["runs"]) == 1
    print("download OK,", len(dl.content), "bytes")

    # 11. failed export can restart (simulate by corrupting status + retry)
    #     first delete the success file and mark a fake failed record
    r6 = client.post("/api/exports", json={"runIds": [run_id],
                     "sections": ["breakers"]}).json()
    eid6 = r6["record"]["id"]
    for _ in range(50):
        rec6 = client.get(f"/api/exports/{eid6}").json()["record"]
        if rec6["status"] in ("SUCCESS", "FAILED", "EMPTY"):
            break
        time.sleep(0.2)
    assert rec6["status"] == "SUCCESS"
    os.remove(os.path.join(db.EXPORTS_DIR, rec6["filename"]))
    db.mark_export_result(eid6, "FAILED", error="simulated failure")
    rr = client.post(f"/api/exports/{eid6}/retry")
    assert rr.status_code == 200
    for _ in range(50):
        rec6 = client.get(f"/api/exports/{eid6}").json()["record"]
        if rec6["status"] in ("SUCCESS", "FAILED", "EMPTY"):
            break
        time.sleep(0.2)
    assert rec6["status"] == "SUCCESS" and os.path.exists(
        os.path.join(db.EXPORTS_DIR, rec6["filename"]))
    print("retry-after-failure OK")

    # 12. no .tmp leftovers
    leftovers = [f for f in os.listdir(db.EXPORTS_DIR) if f.endswith(".tmp")]
    assert not leftovers, leftovers
    print("exports dir:", sorted(os.listdir(db.EXPORTS_DIR)))

print("ALL SMOKE TESTS PASSED")
