"""Export job engine: scope normalization, idempotent keys, content building,
atomic file writes.

Both the export record and the on-page export detail are produced by
`build_content`, so the counts / 口径 can never drift apart.
"""
import hashlib
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import database as db

SECTIONS = ["workflow", "tasks", "logs", "breakers"]
SECTION_LABELS = {
    "workflow": "工作流定义",
    "tasks": "任务执行结果",
    "logs": "执行日志",
    "breakers": "熔断器状态",
}

# Per-process guard: two identical exports started at the exact same moment
# must collapse into one file instead of racing.
_processing_keys = set()
_processing_lock = threading.Lock()


class ExportError(Exception):
    pass


class EmptyScopeError(ExportError):
    """Raised when the selected scope contains no eligible data."""


def normalize_scope(run_ids: List[int], sections: List[str],
                    start_ts: Optional[float],
                    end_ts: Optional[float]) -> Tuple[List[int], List[str],
                                                      Optional[float], Optional[float]]:
    ids = sorted({int(i) for i in run_ids})
    if not ids:
        raise ExportError("请至少选择一次执行")
    secs = [s for s in SECTIONS if s in set(sections)]
    if not secs:
        raise ExportError("请至少选择一个导出环节")
    st = float(start_ts) if start_ts else None
    et = float(end_ts) if end_ts else None
    if st is not None and et is not None and et < st:
        raise ExportError("结束时间不能早于开始时间")
    return ids, secs, st, et


def scope_key(run_ids: List[int], sections: List[str],
              start_ts: Optional[float], end_ts: Optional[float]) -> str:
    """Deterministic key: the same scope always maps to the same file."""
    raw = json.dumps({
        "runs": run_ids,
        "sections": sections,
        "start": round(start_ts, 3) if start_ts else None,
        "end": round(end_ts, 3) if end_ts else None,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _in_range(ts: Optional[float], st: Optional[float], et: Optional[float]) -> bool:
    if ts is None:
        return False
    return (st is None or ts >= st) and (et is None or ts <= et)


def _task_in_range(node: Dict[str, Any], st: Optional[float],
                   et: Optional[float]) -> bool:
    # A task belongs to the window when its execution interval overlaps it;
    # tasks that never started are excluded when any boundary is set.
    start, end = node.get("startTime"), node.get("endTime")
    if start is None:
        return st is None and et is None
    if end is None:
        end = start
    if st is not None and end < st:
        return False
    if et is not None and start > et:
        return False
    return True


def load_runs(run_ids: List[int]) -> List[Dict[str, Any]]:
    runs = []
    for rid in run_ids:
        row = db.get_run_row(rid)
        if row is None:
            raise ExportError(f"执行 #{rid} 不存在")
        runs.append(db.run_detail(row))
    return runs


def build_content(run_ids: List[int], sections: List[str],
                  st: Optional[float], et: Optional[float]) -> Dict[str, Any]:
    """Assemble the export document and its per-section counts.

    This is the only place filtering happens — preview, the written file and
    the export-detail view all call it, so their numbers always agree.

    The time window filters task results and logs (event-style data). The
    workflow definition is structural: when included it always carries the
    full graph, never a graph broken by a narrow time window. Breaker state
    is a run snapshot and is likewise included/excluded whole via its toggle.
    """
    runs = load_runs(run_ids)
    exported_runs: List[Dict[str, Any]] = []
    for run in runs:
        all_nodes = run["nodes"]
        edges = run["edges"]
        logs = run["logs"]
        breakers = run["circuitBreakers"]

        if st is not None or et is not None:
            logs = [l for l in logs if _in_range(l.get("timestamp"), st, et)]
        task_nodes = all_nodes
        if st is not None or et is not None:
            task_nodes = [n for n in all_nodes if _task_in_range(n, st, et)]

        entry: Dict[str, Any] = {
            "runId": run["id"],
            "name": run["name"],
            "status": run["status"],
            "startTs": run["startTs"],
            "endTs": run["endTs"],
        }
        if "workflow" in sections:
            entry["workflow"] = {"nodes": all_nodes, "edges": edges}
        if "tasks" in sections:
            entry["tasks"] = task_nodes
        if "logs" in sections:
            entry["logs"] = logs
        if "breakers" in sections:
            entry["breakers"] = breakers
        exported_runs.append(entry)

    counts = {"runCount": len(exported_runs), "workflowCount": 0,
              "taskCount": 0, "logCount": 0, "breakerCount": 0}
    for entry in exported_runs:
        if "workflow" in entry:
            counts["workflowCount"] += 1
        counts["taskCount"] += len(entry.get("tasks", []))
        counts["logCount"] += len(entry.get("logs", []))
        counts["breakerCount"] += len(entry.get("breakers", []))

    # Whether meaningful data exists. The workflow-definition section alone
    # (no surviving tasks/edges is impossible for a real run — its edges and
    # identity always exist) is still considered data.
    has_data = (
        ("workflow" in sections and len(exported_runs) > 0)
        or counts["taskCount"] > 0
        or counts["logCount"] > 0
        or counts["breakerCount"] > 0
    )
    return {
        "exportedAt": time.time(),
        "scope": {
            "runIds": run_ids,
            "sections": sections,
            "startTs": st,
            "endTs": et,
        },
        "counts": counts,
        "runs": exported_runs,
        "_hasData": has_data,
    }


def render_file(content: Dict[str, Any]) -> bytes:
    # Strip helper fields and produce a readable document.
    doc = {k: v for k, v in content.items() if not k.startswith("_")}
    return (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=False)
            .encode("utf-8"))


def _tmp_path(filename: str) -> str:
    return os.path.join(db.EXPORTS_DIR, filename + ".tmp")


def _final_path(filename: str) -> str:
    return os.path.join(db.EXPORTS_DIR, filename)


def atomic_write(filename: str, payload: bytes) -> int:
    """Write to a temp file then atomically rename.

    On failure the temp file is removed, so an interrupted/failed export never
    leaves a half-written final file.
    """
    tmp, final = _tmp_path(filename), _final_path(filename)
    if os.path.exists(final):
        return os.path.getsize(final)
    try:
        with open(tmp, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return os.path.getsize(final)


def cleanup_temp_files() -> None:
    if not os.path.isdir(db.EXPORTS_DIR):
        return
    for name in os.listdir(db.EXPORTS_DIR):
        if name.endswith(".tmp"):
            try:
                os.remove(os.path.join(db.EXPORTS_DIR, name))
            except OSError:
                pass


def process_export(export_id: int, run_ids: List[int], sections: List[str],
                   st: Optional[float], et: Optional[float],
                   filename: str) -> None:
    key = None
    try:
        try:
            content = build_content(run_ids, sections, st, et)
            if not content["_hasData"]:
                raise EmptyScopeError("选定的时间范围内没有符合条件的数据，未生成文件")
            size = atomic_write(filename, render_file(content))
        except EmptyScopeError as e:
            db.mark_export_result(export_id, "EMPTY", counts=None,
                                  size_bytes=None, error=str(e))
            return
        except ExportError as e:
            db.mark_export_result(export_id, "FAILED", error=str(e))
            return
        except Exception as e:  # unexpected I/O / serialization error
            db.mark_export_result(export_id, "FAILED",
                                  error=f"导出失败：{e}")
            return
        db.mark_export_result(export_id, "SUCCESS",
                              counts=content["counts"], size_bytes=size)
    finally:
        row = db.get_export_row(export_id)
        if row is not None:
            key = row["scope_key"]
        if key is not None:
            with _processing_lock:
                _processing_keys.discard(key)


def try_acquire_scope(key: str) -> bool:
    with _processing_lock:
        if key in _processing_keys:
            return False
        _processing_keys.add(key)
        return True


def release_scope(key: str) -> None:
    with _processing_lock:
        _processing_keys.discard(key)


def start_export_job(export_id: int, key: str, run_ids: List[int],
                     sections: List[str], st: Optional[float],
                     et: Optional[float], filename: str) -> None:
    t = threading.Thread(
        target=process_export,
        args=(export_id, run_ids, sections, st, et, filename),
        daemon=True,
    )
    t.start()
