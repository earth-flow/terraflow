#!/usr/bin/env python3
"""Small same-host HTTP adapter for the Terraflow water demo.

The browser talks to this adapter instead of reaching Terrabox, llama.cpp, or
the fixture filesystem directly. The adapter reuses the existing CLI runner
so the web demo and SSH demo exercise the same chain.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "terraflow_water_demo.py"
HOST = os.environ.get("TERRAFLOW_WATER_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("TERRAFLOW_WATER_WEB_PORT", "7862"))
MAX_PROMPT_LENGTH = 1200
RUN_TIMEOUT_SECONDS = int(os.environ.get("TERRAFLOW_WATER_WEB_TIMEOUT", "300"))
_RUN_LOCK = threading.Lock()


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def read_json(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def parse_last_json(output: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    return candidates[-1] if candidates else None


def run_demo(prompt: str) -> tuple[int, dict[str, Any]]:
    command = [sys.executable, str(RUNNER), "--prompt", prompt]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return 504, {
            "status": "blocked",
            "error": f"水体 Agent 执行超过 {RUN_TIMEOUT_SECONDS} 秒，已停止等待。",
            "stdout": (exc.stdout or "")[-2000:],
            "stderr": (exc.stderr or "")[-2000:],
        }
    cli = parse_last_json(completed.stdout) or parse_last_json(completed.stderr)
    if completed.returncode != 0:
        return 422, {
            "status": "blocked",
            "error": (cli or {}).get("error") or "水体 Agent 执行失败",
            "cli": cli,
            "stderr": completed.stderr[-2000:],
        }
    summary = read_json((cli or {}).get("summary_path"))
    if summary is None:
        return 502, {
            "status": "blocked",
            "error": "runner 已结束，但没有找到可读取的运行摘要。",
            "cli": cli,
        }
    summary["web_request"] = {
        "prompt": prompt,
        "entrypoint": "terraflow-water-web-api",
    }
    summary_path = Path(summary.get("summary_path", (cli or {}).get("summary_path", "")))
    if summary_path:
        try:
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
    return 200, {"status": "complete", "summary": summary, "cli": cli}


class Handler(BaseHTTPRequestHandler):
    server_version = "TerraflowWaterWeb/0.1"

    def _send(self, status: int, payload: Any) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/api/water-demo/health":
            self._send(200, {"status": "ok", "runner": str(RUNNER), "busy": _RUN_LOCK.locked()})
            return
        self._send(404, {"status": "not_found", "error": "unknown endpoint"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/api/water-demo/run":
            self._send(404, {"status": "not_found", "error": "unknown endpoint"})
            return
        if not _RUN_LOCK.acquire(blocking=False):
            self._send(409, {"status": "busy", "error": "已有水体 Agent 任务正在运行，请等待当前任务完成。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(min(length, 32_000))
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send(400, {"status": "invalid_request", "error": "请求体必须是 JSON。"})
                return
            prompt = str(payload.get("prompt") or "请分析这组前后栅格，检查水体变化并给出可追溯证据。")
            prompt = prompt[:MAX_PROMPT_LENGTH]
            status, response = run_demo(prompt)
            self._send(status, response)
        finally:
            _RUN_LOCK.release()

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("[water-web] " + (format % args) + "\n")


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Terraflow water web adapter listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
