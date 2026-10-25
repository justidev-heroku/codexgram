"""Фейковый `codex app-server` для тестов CodexBridge.

Говорит тем же JSON-RPC (newline-delimited) протоколом, но вместо модели
управляется текстом сообщения пользователя:

- обычный текст        — ход завершается почти сразу;
- содержит [slow]      — ход идёт ~1.5 с (можно успеть отправить steer/очередь);
- содержит [nosteer]   — turn/steer отвечает ошибкой (проверка очереди);
- содержит [approval]  — сервер шлёт server-request requestApproval и ждёт ответ;
- содержит [crash]     — процесс завершается (проверка перезапуска и resume).

Аргументы: fake_app_server.py <state.json> <log.jsonl>
state.json хранит известные треды (переживает перезапуск процесса).
log.jsonl — все входящие от клиента сообщения и ответы на server-requests.
"""

import json
import os
import sys
import threading
import time

STATE_PATH = sys.argv[1]
LOG_PATH = sys.argv[2]

_lock = threading.Lock()
_next_req = 1000


def _load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"threads": [], "counter": 0}


def _save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def _log(obj):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def send(obj):
    with _lock:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def reply(rid, result=None, error=None):
    if error is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": error})
    else:
        send({"jsonrpc": "2.0", "id": rid, "result": result or {}})


def text_of(params):
    parts = []
    for item in params.get("input") or []:
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "\n".join(parts)


class Turn:
    current = None  # id активного хода (один за раз)
    counter = 0


def run_turn(turn_id, thread_id, text):
    duration = 1.5 if "[slow]" in text else 0.05
    if "[approval]" in text:
        global _next_req
        with _lock:
            _next_req += 1
            req_id = _next_req
        send({
            "jsonrpc": "2.0", "id": req_id, "method": "item/commandExecution/requestApproval",
            "params": {"threadId": thread_id, "turnId": turn_id, "itemId": "item-1", "command": "ls"},
        })
        # Ждём ответа клиента; читает основной цикл и кладёт в лог.
        deadline = time.time() + 5
        while time.time() < deadline and not _approval_answered(req_id):
            time.sleep(0.02)
    if "[crash]" in text:
        time.sleep(0.05)
        os._exit(1)
    time.sleep(duration)
    send({"jsonrpc": "2.0", "method": "turn/completed",
          "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": "completed", "items": []}}})
    Turn.current = None


def _approval_answered(req_id):
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("kind") == "server_reply" and rec.get("id") == req_id:
                    return True
    except (OSError, ValueError):
        pass
    return False


def main():
    st = _load_state()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        meth = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or {}

        if mid is not None and meth is None:
            # ответ клиента на наш server-request
            _log({"kind": "server_reply", "id": mid, "result": msg.get("result"), "error": msg.get("error")})
            continue

        _log({"kind": "request", "method": meth, "params": params, "id": mid})

        if meth == "initialize":
            reply(mid, {"userAgent": "fake-codex/0", "codexHome": "/nonexistent", "platformOs": "linux"})
        elif meth == "initialized":
            pass
        elif meth == "thread/start":
            st["counter"] += 1
            tid = f"thr-{st['counter']}"
            st["threads"].append(tid)
            _save_state(st)
            reply(mid, {"thread": {"id": tid}, "model": "fake", "modelProvider": "fake", "cwd": params.get("cwd", "/"),
                        "approvalPolicy": "never", "approvalsReviewer": "user", "sandbox": {"type": "dangerFullAccess"}})
        elif meth == "thread/resume":
            tid = params.get("threadId")
            if tid in st["threads"]:
                reply(mid, {"thread": {"id": tid}, "model": "fake", "modelProvider": "fake", "cwd": "/",
                            "approvalPolicy": "never", "approvalsReviewer": "user", "sandbox": {"type": "dangerFullAccess"}})
            else:
                reply(mid, error={"code": -32600, "message": f"no such thread {tid}"})
        elif meth == "turn/start":
            text = text_of(params)
            if Turn.current is not None:
                reply(mid, error={"code": -32000, "message": "a turn is already running"})
                continue
            Turn.counter += 1
            turn_id = f"turn-{Turn.counter}"
            Turn.current = turn_id
            reply(mid, {"turn": {"id": turn_id, "status": "inProgress", "items": []}})
            send({"jsonrpc": "2.0", "method": "turn/started",
                  "params": {"threadId": params["threadId"], "turn": {"id": turn_id, "status": "inProgress", "items": []}}})
            threading.Thread(target=run_turn, args=(turn_id, params["threadId"], text), daemon=True).start()
        elif meth == "turn/steer":
            text = text_of(params)
            if "[nosteer]" in text:
                reply(mid, error={"code": -32001, "message": "steer rejected for test"})
            elif Turn.current is None or params.get("expectedTurnId") != Turn.current:
                reply(mid, error={"code": -32002, "message": "expectedTurnId mismatch"})
            else:
                reply(mid, {"turnId": Turn.current})
        elif meth == "turn/interrupt":
            reply(mid, {})
        else:
            if mid is not None:
                reply(mid, error={"code": -32601, "message": f"unknown method {meth}"})


if __name__ == "__main__":
    main()
