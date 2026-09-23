"""End-to-end checks for the execution-export feature.

Run against a fresh data dir:
    rm -rf backend/data && cd backend && \
    .venv311/bin/python -m uvicorn app.main:app --port 8000 &
    ./test_exports.py
"""
import json
import sys
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
ALL_STAGES = ["extract", "validate", "clean_a", "clean_b", "transform",
              "enrich", "aggregate", "quality", "export_db", "export_report", "notify"]
failures = []


def call(method, path, body=None, expect_error=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        payload = json.loads(e.read() or b"null")
        if expect_error:
            return e.code, payload
        raise


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), name, detail)
    if not cond:
        failures.append(name)


def wait_run(rid, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, runs = call("GET", "/api/runs")
        for r in runs:
            if r["id"] == rid and r["status"] == "completed":
                return r
        time.sleep(1)
    raise TimeoutError(f"run {rid} did not complete")


def main():
    call("POST", "/api/workflow", {"name": "ci"})
    _, r1 = call("POST", "/api/run", {"workflowId": 1, "workers": 3, "strategy": "fifo"})
    wait_run(r1["runId"])
    _, r2 = call("POST", "/api/run", {"workflowId": 1, "workers": 5, "strategy": "priority"})
    wait_run(r2["runId"])

    # 1. preview counts
    _, prev = call("POST", "/api/exports/preview",
                   {"runIds": [r1["runId"], r2["runId"]], "stages": ALL_STAGES})
    check("preview has 2 runs", prev["runCount"] == 2, str(prev["runCount"]))
    check("preview not empty", prev["empty"] is False)

    # 2. create export
    _, e1 = call("POST", "/api/exports",
                 {"runIds": [r1["runId"], r2["runId"]], "stages": ALL_STAGES})
    check("export succeeds", e1["status"] == "success", e1["status"])
    check("file size > 0", e1["fileSize"] > 0, str(e1["fileSize"]))

    # 3. same scope (reordered) -> same record, reused flag, single file
    _, e1b = call("POST", "/api/exports",
                  {"runIds": [r2["runId"], r1["runId"]], "stages": list(reversed(ALL_STAGES))})
    check("same scope reuses record", e1b["id"] == e1["id"])
    check("reused flag set", e1b.get("reused") is True)
    _, all_exports = call("GET", "/api/exports")
    same_scope = [x for x in all_exports
                  if x["runIds"] == e1["runIds"] and x["stages"] == e1["stages"]]
    check("one record for this scope", len(same_scope) == 1 and same_scope[0]["id"] == e1["id"],
          f"{len(same_scope)} records")

    # 4. detail counts == file summary == record counts
    _, detail = call("GET", f"/api/exports/{e1['id']}")
    check("record/detail node counts agree",
          detail["nodeCount"] == detail["detail"]["nodeCount"])
    check("record/detail log counts agree",
          detail["logCount"] == detail["detail"]["logCount"])
    # urllib cannot fetch FileResponse with path? it can; do raw request
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", 8000)
    conn.request("GET", f"/api/exports/{e1['id']}/download")
    resp = conn.getresponse()
    payload = json.loads(resp.read())
    check("download 200", resp.status == 200)
    check("file summary matches record",
          payload["summary"]["nodes"] == e1["nodeCount"]
          and payload["summary"]["logs"] == e1["logCount"])

    # 5. no qualifying data -> no_data, no file
    _, empty = call("POST", "/api/exports",
                    {"runIds": [r1["runId"]], "stages": ["extract"],
                     "timeStart": 946684800, "timeEnd": 946684900})
    check("empty window -> no_data", empty["status"] == "no_data", empty["status"])
    check("empty window -> no filePath", not empty["filePath"])
    code, _ = call("GET", f"/api/exports/{empty['id']}/download", expect_error=True)
    check("no_data download -> 409", code == 409, str(code))

    # 6. validation errors
    code, _ = call("POST", "/api/exports", {"runIds": [], "stages": ["extract"]},
                   expect_error=True)
    check("empty runs -> 400", code == 400, str(code))
    code, _ = call("POST", "/api/exports", {"runIds": [r1["runId"]], "stages": []},
                   expect_error=True)
    check("empty stages -> 400", code == 400, str(code))
    code, _ = call("POST", "/api/exports",
                   {"runIds": [r1["runId"]], "stages": ["extract"],
                    "timeStart": 100, "timeEnd": 50}, expect_error=True)
    check("inverted range -> 400", code == 400, str(code))
    code, _ = call("POST", "/api/exports",
                   {"runIds": [99999], "stages": ["extract"]}, expect_error=True)
    check("missing run -> 404", code == 404, str(code))

    # 7. stage subset + time range filtering
    _, sub = call("POST", "/api/exports",
                  {"runIds": [r1["runId"]], "stages": ["extract", "validate"]})
    check("subset export 2 nodes", sub["nodeCount"] == 2, str(sub["nodeCount"]))

    print()
    if failures:
        print(f"{len(failures)} FAILURES:", failures)
        sys.exit(1)
    print("ALL EXPORT CHECKS PASSED")


if __name__ == "__main__":
    main()
