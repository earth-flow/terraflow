"""Conversation agent behind the workspace chat.

Each user message is first classified by the local model: answer directly
(from the conversation, the data catalogue and earlier results) or propose a
run. A run is shown as a plan the user confirms; only then the runner executes
exactly that plan. Execution, the model's text and the final explanation are
streamed to the page as server-sent events.

The agent is attached to the web adapter module (``web``) and uses its data
catalogue, run lock, runner import and preview URLs. Standard library only.
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator
from urllib.request import Request, urlopen

import workspace_scenarios as scenarios

Event = tuple[str, dict[str, Any]]
_CONVERSATION_ID = re.compile(r"^c_[0-9a-f]{12}$")
_MESSAGE_ID = re.compile(r"^m_[0-9a-f]{12}$")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
HISTORY_TURNS = 8


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ConversationStore:
    """One JSON file per conversation under runtime/conversations/."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def _path(self, conversation_id: str) -> Path | None:
        return self.root / f"{conversation_id}.json" if _CONVERSATION_ID.fullmatch(conversation_id) else None

    def create(self, title: str | None = None) -> dict[str, Any]:
        conversation = {
            "id": f"c_{uuid.uuid4().hex[:12]}",
            "title": (title or "新对话")[:40],
            "created_at": now(),
            "updated_at": now(),
            "messages": [],
        }
        self.save(conversation)
        return conversation

    def load(self, conversation_id: str) -> dict[str, Any] | None:
        path = self._path(conversation_id)
        if path is None or not path.is_file():
            return None
        with self.lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def save(self, conversation: dict[str, Any]) -> None:
        path = self._path(conversation["id"])
        if path is None:
            raise ValueError("invalid conversation id")
        conversation["updated_at"] = now()
        with self.lock:
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(conversation, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)

    def list(self) -> list[dict[str, Any]]:
        rows = []
        for path in self.root.glob("c_*.json"):
            try:
                conversation = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not conversation.get("messages"):
                continue  # created but never used
            rows.append(
                {
                    "id": conversation["id"],
                    "title": conversation.get("title"),
                    "updated_at": conversation.get("updated_at"),
                    "message_count": len(conversation.get("messages") or []),
                }
            )
        return sorted(rows, key=lambda row: str(row.get("updated_at") or ""), reverse=True)

    def delete(self, conversation_id: str) -> bool:
        path = self._path(conversation_id)
        if path is None or not path.is_file():
            return False
        with self.lock:
            path.unlink()
        return True

    def append(self, conversation_id: str, message: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                raise KeyError(conversation_id)
            message = {"id": f"m_{uuid.uuid4().hex[:12]}", "created_at": now(), **message}
            conversation["messages"].append(message)
            if message.get("role") == "user" and conversation.get("title") in (None, "", "新对话"):
                conversation["title"] = str(message.get("content") or "新对话")[:40]
            self.save(conversation)
            return message

    def update(self, conversation_id: str, message_id: str, **fields: Any) -> dict[str, Any] | None:
        with self.lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                return None
            for message in conversation["messages"]:
                if message["id"] == message_id:
                    message.update(fields)
                    self.save(conversation)
                    return message
            return None


class WorkspaceAgent:
    def __init__(self, web: Any) -> None:
        self.web = web
        self.store = ConversationStore(web.RUNTIME_DIR / "conversations")

    # ---------------------------------------------------------------- model calls

    def _llm_json(self, request: dict[str, Any], max_tokens: int = 400) -> dict[str, Any]:
        runner = self.web._RUNNER
        response = runner.request_json(
            f"{runner.DEFAULT_LLM_URL}/v1/chat/completions",
            method="POST",
            payload={
                "model": self.web.llm_model_id(),
                "temperature": 0,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [
                    {"role": "system", "content": "Return only valid JSON. Do not include a markdown fence."},
                    {"role": "user", "content": "/no_think\n" + json.dumps(request, ensure_ascii=False)},
                ],
            },
            timeout=120,
        )
        return runner.parse_json_content(str(response["choices"][0]["message"].get("content") or ""))

    def _llm_stream(self, messages: list[dict[str, str]], max_tokens: int = 700) -> Iterator[str]:
        runner = self.web._RUNNER
        body = json.dumps(
            {
                "model": self.web.llm_model_id(),
                "stream": True,
                "temperature": 0.3,
                "max_tokens": max_tokens,
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": messages,
            }
        ).encode()
        request = Request(
            f"{runner.DEFAULT_LLM_URL}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        )
        with urlopen(request, timeout=300) as response:  # noqa: S310 - local llama.cpp endpoint
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content")
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta

    # ---------------------------------------------------------------- context

    def _datasets(self, *, include_developer: bool = True) -> list[dict[str, Any]]:
        return [
            {key: option.get(key) for key in ("id", "title", "place", "period", "sensor", "category", "input_kind")}
            for option in self.web.fixture_options()
            if include_developer or option.get("audience") != "developer"
        ]

    def _history(self, conversation: dict[str, Any]) -> list[dict[str, Any]]:
        turns = []
        for message in conversation["messages"][-HISTORY_TURNS:]:
            kind = message.get("kind", "text")
            if kind == "plan":
                text = f"[提出计划] 场景 {message['plan']['scenario_title']}，数据 {message['plan']['input_title']}，状态 {message.get('status')}"
            elif kind == "run":
                result = message.get("result") or {}
                metrics = "；".join(f"{m['label']} {m['value']} {m['unit']}" for m in result.get("metrics") or [])
                text = f"[运行结果] 数据 {message.get('input_title')}：{result.get('headline')} {metrics}"
            else:
                text = str(message.get("content") or "")
            turns.append({"role": message.get("role", "assistant"), "text": text[:600]})
        return turns

    def _last_run(self, conversation: dict[str, Any]) -> dict[str, Any] | None:
        return next((m for m in reversed(conversation["messages"]) if m.get("kind") == "run"), None)

    # ---------------------------------------------------------------- one turn

    def decide(self, conversation: dict[str, Any], content: str, attachments: list[str]) -> dict[str, Any]:
        datasets = self._datasets()
        ids = {item["id"] for item in datasets}
        catalog = [
            {key: spec[key] for key in ("id", "title", "summary", "inputs", "input_kinds")}
            for spec in scenarios.scenario_catalog()
        ]
        last = self._last_run(conversation)
        request = {
            "latest_message": content,
            "attached_datasets": [item for item in datasets if item["id"] in attachments],
            "conversation": self._history(conversation),
            "datasets": datasets,
            "scenarios": catalog,
            "previous_run_dataset": last.get("input_set") if last else None,
            "instructions": [
                "Decide whether the latest message needs a new analysis run (action=run) or can be answered "
                "from the conversation, the dataset list and previous results (action=answer).",
                "Choose run only when the user asks to analyse, compute or compare data and a scenario fits. "
                "Questions about earlier results, explanations, available data or capabilities are answer.",
                "input_set: the dataset to analyse - an attached dataset, a dataset the message names (copy the "
                "naming words exactly into mentioned), or previous_run_dataset when the message refers to the "
                "same data. null when unknown.",
                "If the message names a place or event that matches no dataset, set unmatched true.",
            ],
            "return_schema": {
                "action": "answer or run",
                "scenario": "scenario id or null",
                "input_set": "dataset id or null",
                "mentioned": "exact words from the message, or null",
                "unmatched": False,
            },
        }
        parsed = self._llm_json(request)
        action = "run" if parsed.get("action") == "run" else "answer"
        scenario = parsed.get("scenario") if parsed.get("scenario") in scenarios.TASK_SCENARIOS else None
        mentioned = parsed.get("mentioned") if isinstance(parsed.get("mentioned"), str) else None
        mentioned = mentioned.strip() if mentioned and mentioned.strip() in content else None
        chosen = parsed.get("input_set") if parsed.get("input_set") in ids else None
        by_id = {option["id"]: option for option in self.web.fixture_options()}
        # Accept the model's dataset only when it is grounded: attached, named in
        # the message (checked against the dataset description), or the previous run's.
        grounded = (
            chosen in attachments
            or (chosen and mentioned and self.web._mentions_candidate(mentioned, by_id[chosen]))
            or (chosen and last and chosen == last.get("input_set"))
        )
        source = None
        if grounded:
            source = "attachment" if chosen in attachments else ("message" if mentioned else "previous_run")
        else:
            chosen = None
        if chosen is None and attachments:
            chosen = next((item for item in attachments if item in ids), None)
            source = "attachment" if chosen else None
        if chosen is None and any(word in content.lower() for word in ("合成", "synthetic")):
            chosen = next(
                (option["id"] for option in self.web.fixture_options() if option.get("category") == "synthetic"), None
            )
            source = "keyword" if chosen else None
        return {
            "action": action,
            "scenario": scenario or (next(iter(scenarios.TASK_SCENARIOS)) if action == "run" else None),
            "input_set": chosen,
            "mentioned": mentioned,
            "matched_by": source,
            "unmatched": bool(parsed.get("unmatched")) and chosen is None and bool(mentioned),
        }

    def handle_message(self, conversation_id: str, content: str, attachments: list[str]) -> Iterator[Event]:
        user = self.store.append(
            conversation_id, {"role": "user", "kind": "text", "content": content, "attachments": attachments}
        )
        yield "message", user
        conversation = self.store.load(conversation_id) or {"messages": []}
        yield "status", {"text": "正在理解任务…"}
        try:
            decision = self.decide(conversation, content, attachments)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as text
            yield from self._say(conversation_id, f"模型暂时不可用，没能理解这条消息：{exc}")
            return
        if decision["action"] == "run":
            yield from self._propose(conversation_id, content, decision)
        else:
            yield from self._answer(conversation_id, conversation, content)

    def _say(self, conversation_id: str, text: str, **extra: Any) -> Iterator[Event]:
        message = self.store.append(conversation_id, {"role": "assistant", "kind": "text", "content": text, **extra})
        yield "message", message
        yield "done", {}

    def _propose(self, conversation_id: str, content: str, decision: dict[str, Any]) -> Iterator[Event]:
        spec = scenarios.TASK_SCENARIOS[decision["scenario"]]
        if decision["input_set"] is None:
            if decision["unmatched"]:
                text = (
                    f"现有数据里没有「{decision['mentioned']}」。可以到“数据”上传对应影像，"
                    f"或者用“＋ 添加数据”选一组现有数据再试。"
                )
            else:
                needs = "、".join(item["label"] for item in spec["inputs"])
                text = f"要运行「{spec['title']}」，需要指定一组数据（{needs}）。请用“＋ 添加数据”选择，或在任务里说明地点。"
            yield from self._say(conversation_id, text)
            return
        dataset = self.web.resolve_fixture(decision["input_set"])
        if dataset["input_kind"] not in spec["input_kinds"]:
            yield from self._say(conversation_id, f"「{dataset['title']}」的数据类型不适用于「{spec['title']}」。")
            return
        yield "status", {"text": "正在规划工具顺序…"}
        runner = self.web._RUNNER
        try:
            api_key = runner.load_api_key(Path("/tmp/terrabox-api-key"), None)
            _, tools = runner.discover_catalog(runner.DEFAULT_TERRABOX_URL, api_key)
            model = runner.detect_model(runner.DEFAULT_LLM_URL, runner.DEFAULT_REQUESTED_MODEL)
            plan = runner.plan_with_model(
                runner.DEFAULT_LLM_URL, model, tools, user_prompt=content, input_kind=dataset["input_kind"]
            )
        except runner.DemoError as exc:
            yield from self._say(conversation_id, f"没能生成可执行的计划：{exc}")
            return
        message = self.store.append(
            conversation_id,
            {
                "role": "assistant",
                "kind": "plan",
                "status": "pending",
                "content": plan.get("reason") or "",
                "request": content,
                "input_set": dataset["id"],
                "plan": {
                    "scenario": spec["id"],
                    "scenario_title": spec["title"],
                    "input_set": dataset["id"],
                    "input_title": dataset["title"],
                    "input_kind": dataset["input_kind"],
                    "has_self_check_sample": bool(dataset.get("has_mismatch")),
                    "execution_order": plan["execution_order"],
                    "removed_duplicates": plan.get("removed_duplicates") or [],
                    "reason": plan.get("reason"),
                    "model": model["model_used"],
                    "matched_by": decision.get("matched_by"),
                    "mentioned": decision.get("mentioned"),
                },
            },
        )
        yield "message", message
        yield "done", {}

    def _answer(self, conversation_id: str, conversation: dict[str, Any], content: str) -> Iterator[Event]:
        last = self._last_run(conversation)
        facts = {
            "scenarios": [
                {key: spec[key] for key in ("title", "summary", "requirements", "outputs", "limitations")}
                for spec in scenarios.scenario_catalog()
            ],
            # Test fixtures and test uploads are not offered to users unless they ask for them.
            "datasets": self._datasets(include_developer=any(w in content for w in ("合成", "测试", "开发"))),
            "latest_result": {
                "dataset": last.get("input_title"),
                "headline": (last.get("result") or {}).get("headline"),
                "metrics": (last.get("result") or {}).get("metrics"),
                "boundaries": (last.get("result") or {}).get("boundaries"),
                "gate": (last.get("result") or {}).get("gate"),
            }
            if last
            else None,
        }
        facts_text = json.dumps(facts, ensure_ascii=False)
        system = (
            "你是 Terraflow Agent Workspace 中的遥感分析助手。根据对话和下面的事实回答。"
            "只使用事实中出现的数字，不要编造数值；涉及结果时说明适用边界；"
            "不知道就直说，并建议可以运行的场景；回答简洁，使用简体中文。\n事实：" + facts_text
        )
        messages = [{"role": "system", "content": system}]
        for turn in self._history(conversation)[:-1]:
            messages.append({"role": "user" if turn["role"] == "user" else "assistant", "content": turn["text"]})
        messages.append({"role": "user", "content": content})
        yield "status", {"text": "正在回答…"}
        text = ""
        try:
            for delta in self._llm_stream(messages):
                text += delta
                yield "token", {"text": delta}
        except OSError as exc:
            text += f"\n（回答中断：{exc}）"
        warnings = unsupported_numbers(
            text, facts_text + " " + content + " " + json.dumps(self._history(conversation), ensure_ascii=False)
        )
        message = self.store.append(
            conversation_id,
            {"role": "assistant", "kind": "text", "content": text, "warnings": warnings},
        )
        yield "message", message
        yield "done", {}

    # ---------------------------------------------------------------- execution

    def cancel(self, conversation_id: str, message_id: str) -> dict[str, Any] | None:
        conversation = self.store.load(conversation_id)
        message = next((m for m in (conversation or {}).get("messages", []) if m["id"] == message_id), None)
        if not message or message.get("kind") != "plan" or message.get("status") != "pending":
            return None
        return self.store.update(conversation_id, message_id, status="cancelled")

    def execute(self, conversation_id: str, message_id: str, self_check: bool) -> Iterator[Event]:
        if not _MESSAGE_ID.fullmatch(message_id):
            yield "error", {"text": "计划不存在。"}
            return
        conversation = self.store.load(conversation_id)
        plan_message = next((m for m in (conversation or {}).get("messages", []) if m["id"] == message_id), None)
        if not plan_message or plan_message.get("kind") != "plan":
            yield "error", {"text": "计划不存在。"}
            return
        if plan_message.get("status") != "pending":
            yield "error", {"text": "这个计划已经执行或取消过了。"}
            return
        if not self.web._RUN_LOCK.acquire(blocking=False):
            yield "error", {"text": "已有任务在运行，请稍后再执行。"}
            return
        self.store.update(conversation_id, message_id, status="running")
        events: queue.Queue[Event | None] = queue.Queue()
        worker = threading.Thread(
            target=self._execute_worker,
            args=(conversation_id, plan_message, self_check, events),
            daemon=True,
            name=f"workspace-run-{message_id}",
        )
        worker.start()
        while True:
            item = events.get()
            if item is None:
                return
            yield item

    def _execute_worker(
        self, conversation_id: str, plan_message: dict[str, Any], self_check: bool, events: queue.Queue
    ) -> None:
        """Runs in its own thread so a closed browser tab does not lose the result."""
        plan = plan_message["plan"]
        try:
            dataset = self.web.resolve_fixture(plan["input_set"])
            job_dir = self.web.JOB_DIR
            plan_file = job_dir / f"{plan_message['id']}.plan.json"
            events_file = job_dir / f"{plan_message['id']}.events.jsonl"
            plan_file.write_text(
                json.dumps({"plan_id": plan_message["id"], **plan}, ensure_ascii=False), encoding="utf-8"
            )
            events_file.write_text("", encoding="utf-8")
            command = [
                sys.executable,
                str(self.web.RUNNER),
                "--prompt",
                plan_message.get("request") or "",
                "--fixtures-dir",
                str(dataset["path"]),
                "--plan-file",
                str(plan_file),
                "--events-file",
                str(events_file),
            ]
            if self_check and dataset.get("has_mismatch"):
                command.append("--self-check")
            events.put(("status", {"text": "开始执行"}))
            process = subprocess.Popen(
                command, cwd=self.web.ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            offset = 0
            deadline = time.monotonic() + self.web.RUN_TIMEOUT_SECONDS
            while True:
                finished = process.poll() is not None
                with events_file.open("r", encoding="utf-8") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                    offset = handle.tell()
                for line in chunk.splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    translated = translate_event(event)
                    if translated:
                        events.put(translated)
                if finished:
                    break
                if time.monotonic() > deadline:
                    process.kill()
                    raise RuntimeError("执行超时")
                time.sleep(0.2)
            stdout, stderr = process.communicate()
            cli = self.web.parse_last_json(stdout) or self.web.parse_last_json(stderr) or {}
            if process.returncode != 0 or not cli.get("summary_path"):
                raise RuntimeError(cli.get("error") or "执行失败")
            summary_path = Path(cli["summary_path"])
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["web_request"] = {
                "prompt": plan_message.get("request"),
                "entrypoint": "agent-workspace",
                "fixture_id": dataset["id"],
                "conversation_id": conversation_id,
                "plan_message_id": plan_message["id"],
                "self_check": bool(self_check),
            }
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            run_id = summary["run_id"]
            result = scenarios.build_result(summary, lambda name: f"/api/workspace/runs/{run_id}/preview/{name}")
            events.put(
                ("result", {"run_id": run_id, "result": result, "model": summary.get("model", {}).get("model_used")})
            )
            explanation, warnings = "", []
            facts = {key: result.get(key) for key in ("headline", "metrics", "gate", "self_check", "boundaries")}
            facts_text = json.dumps(facts, ensure_ascii=False)
            events.put(("status", {"text": "正在解读结果…"}))
            try:
                for delta in self._llm_stream(
                    [
                        {
                            "role": "system",
                            "content": "你是遥感分析助手。用户的问题和本次运行结果如下。用 2 到 4 句简体中文回答用户的问题，"
                            "只能使用结果中出现的数字（可以四舍五入），说明结论的适用边界，不要编造。\n结果："
                            + facts_text,
                        },
                        {"role": "user", "content": plan_message.get("request") or "解读这次运行的结果。"},
                    ],
                    max_tokens=400,
                ):
                    explanation += delta
                    events.put(("token", {"text": delta}))
                warnings = unsupported_numbers(explanation, facts_text + " " + (plan_message.get("request") or ""))
            except OSError as exc:
                explanation += f"（解读中断：{exc}）"
            message = self.store.append(
                conversation_id,
                {
                    "role": "assistant",
                    "kind": "run",
                    "run_id": run_id,
                    "plan_message_id": plan_message["id"],
                    "input_set": dataset["id"],
                    "input_title": dataset["title"],
                    "scenario": plan["scenario"],
                    "result": result,
                    "content": explanation,
                    "warnings": warnings,
                    "model": summary.get("model", {}).get("model_used"),
                },
            )
            self.store.update(conversation_id, plan_message["id"], status="done", run_id=run_id)
            events.put(("message", message))
            events.put(("done", {}))
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self.store.update(conversation_id, plan_message["id"], status="failed", error=str(exc))
            message = self.store.append(
                conversation_id, {"role": "assistant", "kind": "text", "content": f"执行没有完成：{exc}"}
            )
            events.put(("error", {"text": str(exc)}))
            events.put(("message", message))
        finally:
            self.web._RUN_LOCK.release()
            events.put(None)


def translate_event(event: dict[str, Any]) -> Event | None:
    kind = event.get("type")
    if kind == "stage" and event.get("stage") not in ("executing_success", "executing_rejection"):
        return "status", {"text": event.get("message") or ""}
    if kind in ("tool_started", "tool_finished"):
        state = "running"
        if kind == "tool_finished":
            state = "blocked" if event.get("blocked") else ("done" if event.get("success") else "failed")
        return "step", {
            "id": event.get("execution_id"),
            "tool": event.get("tool"),
            "chain": "self_check" if event.get("chain") == "reject" else "analysis",
            "step": event.get("step"),
            "target": event.get("target"),
            "state": state,
            "reason_code": event.get("reason_code"),
            "outputs": event.get("outputs") or {},
        }
    if kind == "run_failed":
        return "status", {"text": "执行失败：" + str(event.get("error") or "")}
    return None


def unsupported_numbers(text: str, allowed_source: str) -> list[str]:
    """Numbers in the model's text that do not appear (even rounded) in the facts it was given."""
    allowed: set[str] = set()
    for token in _NUMBER.findall(allowed_source):
        allowed.add(token)
        try:
            value = float(token)
        except ValueError:
            continue
        for digits in (0, 1, 2):
            rounded = round(value, digits)
            allowed.add(f"{rounded:.{digits}f}")
            if digits == 0:
                allowed.add(str(int(rounded)))
    # Ratios, percentages and differences of the given numbers count as supported.
    values = []
    for token in list(dict.fromkeys(_NUMBER.findall(allowed_source)))[:60]:
        try:
            values.append(float(token))
        except ValueError:
            continue
    for a in values:
        for b in values:
            derived = [a - b]
            if b:
                derived += [a / b, 100 * a / b]
            for value in derived:
                for digits in (0, 1, 2, 3):
                    allowed.add(f"{round(value, digits):.{digits}f}")
                allowed.add(str(int(round(value))))
    found = []
    for token in _NUMBER.findall(text):
        if token in allowed or (token.lstrip("-").isdigit() and abs(int(token)) <= 10):
            continue
        found.append(token)
    return [f"解读中出现了结果里没有的数字：{', '.join(dict.fromkeys(found))}"] if found else []
