from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

os.environ.setdefault("HERMES_PROFILE", "hermiss")
os.environ.setdefault("HERMES_HOME", "/root/.hermes/profiles/hermiss")
os.environ.setdefault("LANG", "C.UTF-8")
os.environ.setdefault("LC_ALL", "C.UTF-8")
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["HERMES_YOLO_MODE"] = "1"
os.environ["HERMES_ACCEPT_HOOKS"] = "1"
os.environ["HERMES_SESSION_SOURCE"] = "panel"

from hermes_cli.fallback_config import get_fallback_chain
from hermes_cli.models import detect_provider_for_model
from hermes_cli.oneshot import _create_session_db_for_oneshot, _normalize_toolsets, _oneshot_clarify_callback
from hermes_cli.runtime_provider import resolve_runtime_provider
from hermes_cli.tools_config import _get_platform_tools

HOST = os.getenv("HERMISS_PANEL_CHAT_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.getenv("HERMISS_PANEL_CHAT_BRIDGE_PORT", "8799"))

logging.disable(logging.CRITICAL)


class PanelAgent:
    def __init__(self) -> None:
        self._agent = None
        self._lock = threading.Lock()
        self._config_signature: Optional[tuple] = None

    def _signature(self) -> tuple:
        paths = [
            "/root/.hermes/profiles/hermiss/config.yaml",
            "/root/.hermes/profiles/hermiss/.env",
            "/root/.hermes/profiles/hermiss/SOUL.md",
            "/root/.hermes/profiles/hermiss/memories/USER.md",
        ]
        signature = []
        for path in paths:
            try:
                stat = os.stat(path)
                signature.append((path, stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                signature.append((path, 0, 0))
        return tuple(signature)

    def _build_agent(self):
        from hermes_cli.config import load_config
        from run_agent import AIAgent

        cfg = load_config()
        model_cfg = cfg.get("model") or {}
        if isinstance(model_cfg, str):
            cfg_model = model_cfg
            cfg_provider = ""
        else:
            cfg_model = model_cfg.get("default") or model_cfg.get("model") or ""
            cfg_provider = str(model_cfg.get("provider") or "").strip().lower()

        effective_model = os.getenv("HERMES_INFERENCE_MODEL", "").strip() or cfg_model
        effective_provider = os.getenv("HERMES_INFERENCE_PROVIDER", "").strip() or cfg_provider or None
        explicit_base_url = None
        if effective_provider is None and effective_model:
            detected = detect_provider_for_model(effective_model, "auto")
            if detected:
                effective_provider, effective_model = detected

        runtime = resolve_runtime_provider(
            requested=effective_provider,
            target_model=effective_model or None,
            explicit_base_url=explicit_base_url,
        )
        toolsets = _normalize_toolsets(None)
        if toolsets is None:
            toolsets = sorted(_get_platform_tools(cfg, "cli"))

        agent = AIAgent(
            api_key=runtime.get("api_key"),
            base_url=runtime.get("base_url"),
            provider=runtime.get("provider"),
            api_mode=runtime.get("api_mode"),
            model=effective_model,
            enabled_toolsets=toolsets,
            quiet_mode=True,
            platform="panel",
            session_db=_create_session_db_for_oneshot(),
            credential_pool=runtime.get("credential_pool"),
            fallback_model=get_fallback_chain(cfg) or None,
            clarify_callback=_oneshot_clarify_callback,
        )
        agent.suppress_status_output = True
        agent.stream_delta_callback = None
        agent.tool_gen_callback = None
        return agent

    def chat(self, message: str) -> str:
        with self._lock:
            signature = self._signature()
            if self._agent is None or signature != self._config_signature:
                self._agent = self._build_agent()
                self._config_signature = signature
            try:
                return self._agent.chat(message) or ""
            except Exception:
                self._agent = self._build_agent()
                self._config_signature = self._signature()
                return self._agent.chat(message) or ""


panel_agent = PanelAgent()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        if self.path.rstrip("/") != "/health":
            self._send(404, {"ok": False, "error": "not found"})
            return
        self._send(200, {"ok": True, "agent_ready": panel_agent._agent is not None, "pid": os.getpid()})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/chat":
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            message = str(data.get("message") or "").strip()
            if not message:
                self._send(400, {"ok": False, "error": "消息不能为空"})
                return
            reply = panel_agent.chat(message)
            self._send(200, {"ok": True, "reply": reply})
        except Exception as exc:
            self._send(500, {"ok": False, "error": str(exc)})


if __name__ == "__main__":
    os.chdir("/root")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()
