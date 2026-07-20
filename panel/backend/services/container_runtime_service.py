# backend/services/container_runtime_service.py
import json
import os
import shutil
import subprocess
import time
from typing import Optional

import docker

from services.docker_client import get_docker_client

SERVER_STATS_CACHE_SECONDS = 5
_server_stats_cache = {"ts": 0.0, "data": None}


def _host_memory_stats() -> list[str]:
    try:
        mem = subprocess.run(["free", "-m"], capture_output=True, text=True)
        rows = mem.stdout.strip().split(chr(10))
        raw = rows[1].split() if len(rows) > 1 and len(rows[1].split()) > 6 else []
        if raw and raw[0] != "?":
            raw[1] = f"{int(raw[1])/1024:.1f}G"
            raw[2] = f"{int(raw[2])/1024:.1f}G"
            raw[6] = f"{int(raw[6])/1024:.1f}G"
            return raw
    except FileNotFoundError:
        pass
    except Exception:
        pass

    try:
        values = {}
        for line in open("/proc/meminfo", encoding="utf-8"):
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0])
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        used = max(0, total - available)
        return [
            "Mem:",
            f"{total / 1024 / 1024:.1f}G",
            f"{used / 1024 / 1024:.1f}G",
            "?",
            "?",
            "?",
            f"{available / 1024 / 1024:.1f}G",
        ]
    except Exception:
        return ["?"] * 7


def _host_disk_stats() -> list[str]:
    try:
        disk = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
        rows = disk.stdout.strip().split(chr(10))
        parts = rows[-1].split() if rows else []
        if len(parts) > 4:
            return parts
    except FileNotFoundError:
        pass
    except Exception:
        pass

    try:
        usage = shutil.disk_usage("/")
        used = usage.used / 1024 / 1024 / 1024
        free = usage.free / 1024 / 1024 / 1024
        total = usage.total / 1024 / 1024 / 1024
        pct = f"{(usage.used / usage.total * 100):.0f}%" if usage.total else "?"
        return ["/", f"{total:.1f}G", f"{used:.1f}G", f"{free:.1f}G", pct, "/"]
    except Exception:
        return ["?"] * 6


def _host_load_average() -> str:
    try:
        load = subprocess.run(["uptime"], capture_output=True, text=True)
        if "load average:" in load.stdout:
            return load.stdout.split("load average:")[-1].strip()
    except FileNotFoundError:
        pass
    except Exception:
        pass
    try:
        return ", ".join(f"{value:.2f}" for value in os.getloadavg())
    except Exception:
        return "?"


def restart_container(container_name: str) -> Optional[str]:
    try:
        container = get_docker_client().containers.get(container_name)
        container.restart(timeout=1)
        return container.status
    except docker.errors.NotFound:
        return None


def stop_container(container_name: str) -> Optional[str]:
    try:
        container = get_docker_client().containers.get(container_name)
        container.stop(timeout=5)
        return container.status
    except Exception:
        return None


def start_container(container_name: str) -> Optional[str]:
    try:
        container = get_docker_client().containers.get(container_name)
        container.start()
        return container.status
    except Exception:
        return None


def delete_container(container_name: str) -> bool:
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    subprocess.run(["docker", "volume", "rm", "-f", f"hermiss-data-{container_name}"], capture_output=True)
    return True


def get_container_status(container_name: str) -> dict:
    try:
        container = get_docker_client().containers.get(container_name)
        return {
            "name": container.name,
            "status": container.status,
            "image": container.image.tags[0] if container.image.tags else "unknown",
        }
    except docker.errors.NotFound:
        return {"name": container_name, "status": "not_created"}


def get_container_logs(container_name: str, tail: int = 50) -> str:
    try:
        return get_docker_client().containers.get(container_name).logs(tail=tail).decode("utf-8", errors="replace")
    except docker.errors.NotFound:
        return "容器不存在"


def get_server_stats() -> dict:
    now = time.time()
    cached = _server_stats_cache.get("data")
    if cached and now - float(_server_stats_cache.get("ts") or 0) < SERVER_STATS_CACHE_SECONDS:
        return cached

    dps = _host_disk_stats()
    mp = _host_memory_stats()
    ld = _host_load_average()

    live_stats = {}
    try:
        stats_result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=4,
        )
        for line in stats_result.stdout.strip().splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            name = item.get("Name") or item.get("Container") or item.get("ID")
            if not name:
                continue
            live_stats[name] = {
                "cpu": item.get("CPUPerc") or "-",
                "memory": item.get("MemUsage") or "-",
                "memory_percent": item.get("MemPerc") or "-",
                "net_io": item.get("NetIO") or "-",
                "block_io": item.get("BlockIO") or "-",
                "pids": item.get("PIDs") or "-",
            }
    except Exception:
        live_stats = {}

    containers = []
    try:
        ps_result = subprocess.run(
            ["docker", "ps", "-a", "--size", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        for line in ps_result.stdout.strip().splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            status_text = str(item.get("Status") or "")
            if status_text.lower().startswith("up"):
                status = "running"
            elif status_text.lower().startswith("exited"):
                status = "exited"
            elif status_text:
                status = status_text.split()[0].lower()
            else:
                status = "unknown"
            stats = live_stats.get(item.get("Names") or item.get("Name") or "?") or {}
            containers.append({
                "name": item.get("Names") or item.get("Name") or "?",
                "status": status,
                "image": item.get("Image") or "?",
                "ports": item.get("Ports") or "-",
                "size": item.get("Size") or "-",
                "cpu": stats.get("cpu", "-"),
                "memory": stats.get("memory", "-"),
                "memory_percent": stats.get("memory_percent", "-"),
                "net_io": stats.get("net_io", "-"),
                "block_io": stats.get("block_io", "-"),
                "pids": stats.get("pids", "-"),
            })
    except Exception:
        pass

    if not containers:
        try:
            for container in get_docker_client().containers.list(all=True):
                try:
                    status = container.status or "unknown"
                    stats = {}
                    if status == "running":
                        try:
                            raw_stats = container.stats(stream=False)
                            cpu_total = raw_stats.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0)
                            pre_cpu_total = raw_stats.get("precpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0)
                            system_total = raw_stats.get("cpu_stats", {}).get("system_cpu_usage", 0)
                            pre_system_total = raw_stats.get("precpu_stats", {}).get("system_cpu_usage", 0)
                            cpu_delta = cpu_total - pre_cpu_total
                            system_delta = system_total - pre_system_total
                            cpu_count = len(raw_stats.get("cpu_stats", {}).get("cpu_usage", {}).get("percpu_usage") or []) or 1
                            cpu_percent = (cpu_delta / system_delta * cpu_count * 100) if system_delta > 0 else 0
                            memory_stats = raw_stats.get("memory_stats", {}) or {}
                            mem_usage = int(memory_stats.get("usage") or 0)
                            mem_limit = int(memory_stats.get("limit") or 0)
                            mem_percent = (mem_usage / mem_limit * 100) if mem_limit > 0 else 0
                            stats = {
                                "cpu": f"{cpu_percent:.2f}%",
                                "memory": f"{mem_usage / 1024 / 1024:.1f}MiB / {mem_limit / 1024 / 1024:.1f}MiB" if mem_limit else "-",
                                "memory_percent": f"{mem_percent:.2f}%" if mem_limit else "-",
                            }
                        except Exception:
                            stats = {}
                    try:
                        image = container.image.tags[0] if container.image.tags else "unknown"
                    except Exception:
                        image = "unknown"
                    containers.append({
                        "name": container.name,
                        "status": status,
                        "image": image,
                        "ports": "-",
                        "size": "-",
                        "cpu": stats.get("cpu", "-"),
                        "memory": stats.get("memory", "-"),
                        "memory_percent": stats.get("memory_percent", "-"),
                        "net_io": "-",
                        "block_io": "-",
                        "pids": "-",
                    })
                except Exception:
                    continue
        except Exception:
            containers = []

    data = {
        "disk": {
            "used": dps[2] if len(dps) > 2 else "?",
            "avail": dps[3] if len(dps) > 3 else "?",
            "pct": dps[4] if len(dps) > 4 else "?",
        },
        "memory": {
            "total": mp[1] if len(mp) > 0 else "?",
            "used": mp[2] if len(mp) > 1 else "?",
            "avail": mp[6] if len(mp) > 6 else "?",
        },
        "load": ld,
        "containers": containers,
    }
    _server_stats_cache["ts"] = now
    _server_stats_cache["data"] = data
    return data
