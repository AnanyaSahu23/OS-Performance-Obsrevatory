"""Collect a point-in-time snapshot of system and process metrics."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import psutil


ProcessMetrics = Dict[str, Any]


def _collect_processes(limit: int) -> Tuple[int, List[ProcessMetrics], List[ProcessMetrics]]:
    if limit < 1:
        raise ValueError("limit must be at least 1")

    processes: List[ProcessMetrics] = []
    running_process_count = 0

    for proc in psutil.process_iter(
        ["pid", "name", "memory_percent", "memory_info", "status"]
    ):
        try:
            info = proc.info
            status = info.get("status") or "unknown"
            if status == psutil.STATUS_RUNNING:
                running_process_count += 1

            memory_info = info.get("memory_info")
            processes.append(
                {
                    "pid": info["pid"],
                    "name": info.get("name") or "Unknown",
                    "cpu_percent": round(proc.cpu_percent(interval=None), 2),
                    "memory_percent": round(info.get("memory_percent") or 0.0, 2),
                    "memory_rss": memory_info.rss if memory_info else 0,
                    "status": status,
                }
            )
        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess,
        ):
            # Processes may exit or become inaccessible during sampling.
            continue

    top_cpu = sorted(
        processes, key=lambda process: process["cpu_percent"], reverse=True
    )[:limit]
    top_memory = sorted(
        processes, key=lambda process: process["memory_rss"], reverse=True
    )[:limit]
    return running_process_count, top_cpu, top_memory


def get_top_processes(limit: int = 10) -> Dict[str, List[ProcessMetrics]]:
    """Return processes ranked separately by CPU usage and resident memory."""
    _, top_cpu, top_memory = _collect_processes(limit)
    return {"top_cpu": top_cpu, "top_memory": top_memory}


def collect_snapshot(limit: int = 10) -> Dict[str, Any]:
    """Collect system metrics and ranked processes in a predictable structure.

    CPU percentages are interval-based. The first reading may be zero; compare
    subsequent snapshots for useful CPU utilization and per-process readings.
    """
    running_process_count, top_cpu, top_memory = _collect_processes(limit)
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()

    return {
        "sampled_at": datetime.now(timezone.utc).isoformat(),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory": {
            "total": memory.total,
            "used": memory.used,
            "available": memory.available,
            "percent": memory.percent,
        },
        "swap": {
            "total": swap.total,
            "used": swap.used,
            "percent": swap.percent,
        },
        "running_process_count": running_process_count,
        "top_cpu": top_cpu,
        "top_memory": top_memory,
    }
