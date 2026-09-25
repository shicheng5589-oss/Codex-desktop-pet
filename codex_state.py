"""把 Codex 正在做的事情，翻译成桌宠能演的一个状态。

数据来自两处，都是 Codex 自己写的、只读的本地文件：

* ``~/.codex/thread_history_1.sqlite`` —— 每个回合的状态、每个步骤的类型与时间
* ``~/.codex/sessions/**/*.jsonl``    —— 逐条事件流，用来判断"某个工具是不是还在跑"

这里不解析任何密钥，也不联网。
"""

from __future__ import annotations

import glob
import html
import json
import os
import re
import sqlite3
import time

CODEX_HOME = os.path.expanduser("~/.codex")
SESSIONS_DIR = os.path.join(CODEX_HOME, "sessions")
HISTORY_DB = os.path.join(CODEX_HOME, "thread_history_1.sqlite")
STATE_DB = os.path.join(CODEX_HOME, "state_5.sqlite")

TOOL_LABELS = {
    "commandExecution": "运行命令",
    "fileChange": "修改文件",
    "mcpToolCall": "调用工具",
    "webSearch": "搜索网页",
    "toolSearch": "查找工具",
    "reasoning": "思考中",
    "agentMessage": "整理结果",
    "userMessage": "接收指令",
    "todoList": "规划步骤",
}

SECRET_HINTS = ("key", "token", "secret", "password", "authorization", "cookie")


def _connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=1.5)
    con.row_factory = sqlite3.Row
    return con


def _read_tail(path: str, max_bytes: int = 900_000) -> list[dict]:
    """Read the last chunk of a rollout file and parse whatever complete lines it holds."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # drop the partial line
            raw = fh.read()
    except OSError:
        return []

    events = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def _newest_rollout() -> str | None:
    files = glob.glob(os.path.join(SESSIONS_DIR, "**", "*.jsonl"), recursive=True)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def _iso_seconds(stamp: str) -> float:
    try:
        text = stamp.replace("Z", "+00:00")
        return time.mktime(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except Exception:
        return 0.0


def _shorten(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _command_hint(arguments: str) -> str:
    """Show which program ran, never the full command line (it can carry credentials)."""
    try:
        args = json.loads(arguments)
    except Exception:
        return ""
    if not isinstance(args, dict):
        return ""
    cmd = args.get("cmd") or args.get("command") or ""
    if isinstance(cmd, list):
        cmd = " ".join(str(part) for part in cmd)
    cmd = str(cmd)
    lowered = cmd.lower()
    if any(hint in lowered for hint in SECRET_HINTS):
        return ""
    match = re.search(r"([\w.-]+\.(?:exe|cmd|bat|ps1)|[\w./\\-]+)$", cmd.split(" ")[0].strip("\"'"))
    token = match.group(1) if match else cmd.split(" ")[0]
    return os.path.basename(token.strip("\"'"))[:24]


def _item_activity(item: dict) -> str:
    kind = item.get("type") or ""
    label = TOOL_LABELS.get(kind, "处理中")
    if kind == "commandExecution":
        hint = _command_hint(item.get("command", "") if isinstance(item.get("command"), str) else json.dumps(item.get("command", "")))
        return f"{label} · {hint}" if hint else label
    if kind == "fileChange":
        changes = item.get("changes") or []
        names = []
        for change in changes[:2]:
            path = change.get("path") or ""
            if isinstance(path, str) and path:
                names.append(os.path.basename(path))
        return f"{label} · {', '.join(names)}" if names else label
    if kind == "mcpToolCall":
        name = item.get("tool") or item.get("name") or ""
        return f"{label} · {_shorten(str(name), 24)}" if name else label
    if kind == "webSearch":
        query = item.get("query") or ""
        return f"{label} · {_shorten(str(query), 24)}" if query else label
    return label


def _thread_meta() -> dict:
    """Pick the thread the user is actually watching: the running one wins over recency."""
    try:
        con = _connect(STATE_DB)
    except sqlite3.Error:
        return {}
    try:
        rows = con.execute(
            "select id, name, title, first_user_message, cwd, tokens_used, updated_at, rollout_path "
            "from threads where archived = 0 and thread_source = 'user' "
            "order by updated_at desc limit 6"
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        con.close()

    if not rows:
        return {}
    running = _running_thread_ids()
    chosen = next((row for row in rows if row["id"] in running), rows[0])
    meta = dict(chosen)
    label = meta.get("name") or meta.get("title") or meta.get("first_user_message") or ""
    meta["label"] = html.unescape(label).strip()
    return meta


def _running_thread_ids() -> set[str]:
    try:
        con = _connect(HISTORY_DB)
    except sqlite3.Error:
        return set()
    try:
        return {
            row["thread_id"]
            for row in con.execute("select thread_id from thread_turns where status = 'inProgress'")
        }
    except sqlite3.Error:
        return set()
    finally:
        con.close()


def _turn_status(thread_id: str) -> dict:
    try:
        con = _connect(HISTORY_DB)
    except sqlite3.Error:
        return {}
    try:
        row = con.execute(
            "select status, started_at, completed_at, duration_ms, turn_id, error_json "
            "from thread_turns where thread_id=? order by started_at desc limit 1",
            (thread_id,),
        ).fetchone()
        return dict(row) if row else {}
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def _last_items(thread_id: str, limit: int = 6) -> list[dict]:
    try:
        con = _connect(HISTORY_DB)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute(
            "select item_type, item_json, rollout_ordinal from thread_items "
            "where thread_id=? order by rollout_ordinal desc limit ?",
            (thread_id, limit),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()

    items = []
    for row in rows:
        try:
            item = json.loads(row["item_json"])
        except Exception:
            continue
        item["type"] = item.get("type") or row["item_type"]
        item["_ordinal"] = row["rollout_ordinal"]
        items.append(item)
    return items


def _thread_id_from_rollout(path: str) -> str | None:
    match = re.search(r"rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", path)
    return match.group(1) if match else None


def snapshot() -> dict:
    """Current pet state. Always returns something usable, even with no Codex running."""
    now = time.time()
    meta = _thread_meta()
    rollout = (meta.get("rollout_path") or "").lstrip("\\?")
    if not rollout or not os.path.exists(rollout):
        rollout = _newest_rollout()
    thread_id = meta.get("id") or (_thread_id_from_rollout(rollout) if rollout else None)

    idle_payload = {
        "state": "idle",
        "label": "空闲",
        "title": "",
        "activity": "",
        "say": "",
        "elapsed": 0,
        "tools": 0,
        "tokens": "",
        "source": "Codex",
        "threadId": thread_id or "",
    }
    if not rollout or not thread_id:
        return idle_payload

    events = _read_tail(rollout)
    if not events:
        return idle_payload

    # Find where the current turn begins.
    start = 0
    for index, event in enumerate(events):
        payload = event.get("payload") or {}
        kind = payload.get("type")
        if event.get("type") == "event_msg" and kind in ("task_started", "user_message"):
            start = index
        elif event.get("type") == "response_item" and kind == "message" and (payload.get("role") == "user"):
            start = index

    turn_events = events[start:]
    tool_calls: dict[str, str] = {}
    completed: set[str] = set()
    pending: dict | None = None
    tools = 0
    last_reasoning = 0.0
    last_message = ""
    last_stamp = 0.0
    tokens_text = ""
    aborted = False
    finished = False

    for event in turn_events:
        stamp = _iso_seconds(event.get("timestamp", "")) or last_stamp
        last_stamp = stamp or last_stamp
        kind = event.get("type")
        payload = event.get("payload") or {}
        ptype = payload.get("type")

        if kind == "event_msg":
            if ptype == "turn_aborted":
                aborted = True
            elif ptype == "task_complete":
                finished = True
            elif ptype == "token_count":
                info = payload.get("info") or {}
                usage = info.get("last_token_usage") or info.get("total_token_usage") or {}
                window = info.get("model_context_window") or 0
                total = usage.get("total_tokens") or 0
                if window:
                    tokens_text = "%d%% · %s/%s" % (
                        round(total * 100.0 / window),
                        _compact(total),
                        _compact(window),
                    )
            elif ptype == "item_completed":
                item = payload.get("item") or {}
                if item.get("type") == "Reasoning":
                    last_reasoning = stamp or last_reasoning
                elif item.get("type") == "AgentMessage":
                    last_message = item.get("text") or last_message
            continue

        if kind != "response_item":
            continue

        if ptype in ("function_call", "custom_tool_call"):
            tools += 1
            call_id = payload.get("call_id") or payload.get("id") or ""
            name = payload.get("name") or ""
            args = payload.get("arguments") or payload.get("input") or ""
            tool_calls[call_id] = name
            pending = {"call_id": call_id, "name": name, "arguments": args, "stamp": stamp}
        elif ptype in ("function_call_output", "custom_tool_call_output"):
            completed.add(payload.get("call_id") or "")
        elif ptype == "reasoning":
            last_reasoning = stamp or last_reasoning
        elif ptype == "message":
            if payload.get("role") == "assistant":
                texts = []
                for chunk in payload.get("content") or []:
                    if isinstance(chunk, dict) and chunk.get("text"):
                        texts.append(chunk["text"])
                if texts:
                    last_message = "\n".join(texts)

    pending_open = bool(pending and pending["call_id"] not in completed)
    quiet_for = now - (last_stamp or now)

    items = _last_items(thread_id)
    latest_item = items[0] if items else {}
    latest_activity = _item_activity(latest_item) if latest_item else ""
    activity_stamp = latest_item.get("_ordinal")

    turn = _turn_status(thread_id)
    turn_status = turn.get("status") or ""
    started_at = turn.get("started_at") or 0
    elapsed = max(0, int(now - started_at)) if started_at and turn_status == "inProgress" else 0

    # ---- decide the state -------------------------------------------------
    if aborted or turn_status == "interrupted":
        state, label = "interrupted", "被打断"
    elif finished or turn_status == "completed":
        if quiet_for < 90:
            state, label = "done", "完成了"
        else:
            return {**idle_payload, "title": meta.get("label") or "", "tokens": tokens_text}
    elif pending_open:
        name = pending["name"] or ""
        if name in ("tool_search", "tool_search_tool"):
            state, label = "review", "找工具"
        else:
            state, label = "working", "执行中"
    elif quiet_for > 45:
        state, label = "waiting", "等待你"
    else:
        state, label = "thinking", "思考中"

    activity = latest_activity
    if state == "thinking":
        activity = "正在思考下一步" if not activity else activity
    if state == "waiting":
        activity = activity or "等你确认或回复"

    return {
        "state": state,
        "label": label,
        "title": _shorten(meta.get("label") or "Codex 任务", 60),
        "activity": activity,
        "say": _shorten(last_message, 110),
        "elapsed": elapsed,
        "tools": tools,
        "tokens": tokens_text,
        "source": "Codex",
        "threadId": thread_id,
        "cwd": meta.get("cwd") or "",
        "ordinal": activity_stamp or 0,
    }


def _compact(value: int) -> str:
    if value >= 1_000_000:
        return "%.1fM" % (value / 1_000_000.0)
    if value >= 1_000:
        return "%dk" % round(value / 1000.0)
    return str(value)


if __name__ == "__main__":
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
