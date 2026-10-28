"""CodexBridge: asyncio-клиент для `codex app-server` (JSON-RPC 2.0 поверх stdio).

Отвечает за:
- запуск дочернего процесса `codex app-server`, handshake (initialize/initialized);
- хранение и возобновление одного активного треда (файл STATE_DIR/active_session_id);
- отправку пользовательских сообщений: turn/start, либо turn/steer, если ход уже идёт;
  при ошибке steer сообщение откладывается и уходит после turn/completed;
- ответы на server-initiated запросы (approvals и пр.) — на каждый запрос обязательно отвечаем;
- автоперезапуск дочернего процесса с backoff и возобновление треда.

Модуль не импортирует server.py и ничего не знает о Telegram.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Optional, Sequence, Union

from codex_settings import AUTO_ON_REQUEST, approval_policy, effort_setting, load_settings, model_setting

CLIENT_INFO = {"name": "codex-gram", "version": "0.1.0"}
# Имя сервиса в thread/start: по нему (и по превью с chat_id) отбираются треды для /resume.
SERVICE_NAME = "codex-gram"
SANDBOX_MODE = "danger-full-access"
CODEX_HOME_ENV = "CODEX_GRAM_CODEX_HOME"
APPROVAL_METHODS = frozenset({
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "execCommandApproval",
    "applyPatchApproval",
})
# Один JSON-объект может быть очень большим (вывод команд, полные items) — дефолтный
# лимит asyncio StreamReader (64 KiB) оборвал бы чтение, поэтому поднимаем его.
STDOUT_LIMIT = 64 * 1024 * 1024
DONE_TURNS_MAX = 1000
STABLE_UPTIME_SEC = 60.0


def child_env() -> dict:
    """Окружение app-server. CODEX_GRAM_CODEX_HOME задаёт CODEX_HOME (аккаунт и тесты)."""
    env = dict(os.environ)
    home = os.environ.get(CODEX_HOME_ENV)
    if home:
        env["CODEX_HOME"] = home
    return env


class BridgeError(RuntimeError):
    """Соединение с app-server недоступно (процесс умер, не подключены и т.п.)."""


class RPCError(BridgeError):
    """app-server вернул JSON-RPC error на наш запрос."""

    def __init__(self, code: Any, message: str):
        super().__init__(f"rpc error {code}: {message}")
        self.code = code
        self.message = message


def _default_log(msg: str) -> None:
    sys.stderr.write(f"codex bridge: {msg}\n")
    sys.stderr.flush()


def build_input(text: str = "", images: Iterable[str] = ()) -> list[dict]:
    """Собирает массив UserInput для turn/start и turn/steer."""
    items: list[dict] = []
    if text:
        items.append({"type": "text", "text": text})
    for path in images:
        if path:
            items.append({"type": "localImage", "path": str(path)})
    return items


def approval_result(method: str, params: dict, allow: bool) -> dict:
    """Ответ на server-запрос подтверждения. allow=False — отказ (без выдачи прав)."""
    if method == "item/permissions/requestApproval":
        granted = params.get("permissions") if allow and isinstance(params.get("permissions"), dict) else {}
        return {"permissions": granted, "scope": "turn"}
    if method in ("execCommandApproval", "applyPatchApproval"):
        return {"decision": "approved" if allow else "denied"}
    return {"decision": "accept" if allow else "decline"}


class CodexBridge:
    def __init__(
        self,
        *,
        state_dir: Path,
        cwd: str,
        instructions: Union[str, Callable[[], str]] = "",
        mcp_servers: Union[None, dict, Callable[[], Optional[dict]]] = None,
        cmd: Optional[Sequence[str]] = None,
        log: Optional[Callable[[str], None]] = None,
        restart_backoff: tuple[float, float] = (1.0, 30.0),
        request_timeout: float = 60.0,
        ephemeral: bool = False,
        env: Optional[Callable[[], dict]] = None,
    ):
        self.state_dir = Path(state_dir)
        self.cwd = cwd
        self.instructions = instructions
        self.mcp_servers = mcp_servers
        self.cmd = list(cmd) if cmd else ["codex", "app-server"]
        self._log = log or _default_log
        self._backoff_min, self._backoff_max = restart_backoff
        self._request_timeout = request_timeout
        self.ephemeral = ephemeral
        # Окружение дочернего app-server; вызывается при каждом запуске (смена аккаунта гостя
        # меняет CODEX_HOME без пересоздания моста).
        self.env_factory = env or child_env

        self.session_file = self.state_dir / "active_session_id"
        self.thread_id: Optional[str] = None
        self.active_turn_id: Optional[str] = None

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._supervisor: Optional[asyncio.Task] = None
        self._pending: dict[Any, asyncio.Future] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._child_exited = asyncio.Event()
        self._ready = asyncio.Event()
        self._stopping = False
        self._queue: list[list[dict]] = []
        self._done_turns: set[str] = set()
        self._listeners: list[Callable[[str, dict], Any]] = []
        self._approval_handler: Optional[Callable[[str, dict], Awaitable[bool]]] = None

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def queued_count(self) -> int:
        return len(self._queue)

    def add_listener(self, cb: Callable[[str, dict], Any]) -> None:
        """cb(method, params) вызывается для каждой нотификации app-server."""
        self._listeners.append(cb)

    def set_approval_handler(self, handler: Optional[Callable[[str, dict], Awaitable[bool]]]) -> None:
        """В режиме on-request спрашивает владельца: True — разрешить, False — отклонить."""
        self._approval_handler = handler

    def settings(self) -> dict:
        return load_settings(self.state_dir)

    async def start(self, wait_ready: bool = True, timeout: float = 60.0) -> bool:
        """Запускает супервизор. Возвращает True, если первое подключение состоялось."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._stopping = False
        if self._supervisor is None or self._supervisor.done():
            self._supervisor = asyncio.create_task(self._supervise())
        if not wait_ready:
            return False
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            self._log(f"app-server not ready after {timeout:.0f}s; will keep retrying")
            return False

    async def stop(self) -> None:
        self._stopping = True
        if self._supervisor is not None:
            self._supervisor.cancel()
            await asyncio.gather(self._supervisor, return_exceptions=True)
            self._supervisor = None
        await self._kill_child()
        self._ready.clear()

    async def submit(self, text: str, images: Sequence[str] = ()) -> str:
        """Отправляет сообщение пользователя.

        Возвращает: "started" (новый ход), "steered" (добавлено в идущий ход),
        "queued" (отложено до конца хода / переподключения), "empty" (нечего отправлять).
        """
        items = build_input(text, images)
        if not items:
            return "empty"
        async with self._turn_lock:
            if not self._ready.is_set() or self.thread_id is None:
                self._queue.append(items)
                return "queued"
            if self.active_turn_id is not None:
                try:
                    await self._request(
                        "turn/steer",
                        {"threadId": self.thread_id, "expectedTurnId": self.active_turn_id, "input": items},
                    )
                    return "steered"
                except BridgeError as e:
                    self._log(f"turn/steer failed, queueing message: {e}")
                    self._queue.append(items)
                    return "queued"
            merged = self._take_queue() + items
            ok = await self._start_turn_locked(merged)
            return "started" if ok else "queued"

    async def restart_child(self, wait_timeout: float = 60.0) -> bool:
        """Перезапускает дочерний app-server (например, после смены аккаунта).

        Убиваем процесс, супервизор поднимает новый и возобновляет тред. Ход, который шёл,
        теряется: его turn/completed уже не придёт, поэтому active_turn_id сбрасываем.
        """
        self.active_turn_id = None
        self._ready.clear()
        await self._kill_child()
        if self._supervisor is None or self._supervisor.done():
            await self.start(wait_ready=False)
        try:
            await asyncio.wait_for(self._ready.wait(), wait_timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def list_threads(self, limit: int = 10, cwd: Optional[str] = None) -> list[dict]:
        """thread/list. cwd — фильтр на стороне сервера (точное совпадение cwd сессии)."""
        self._require_ready()
        params: dict = {"archived": False, "limit": limit, "sortKey": "updated_at", "sortDirection": "desc"}
        if cwd:
            params["cwd"] = cwd
        result = await self._request("thread/list", params)
        return [t for t in result.get("data") or [] if isinstance(t, dict) and t.get("id")]

    async def read_thread(self, thread_id: str) -> dict:
        """thread/read без истории ходов: метаданные треда (preview, cwd, ...)."""
        self._require_ready()
        result = await self._request("thread/read", {"threadId": thread_id, "includeTurns": False})
        thread = result.get("thread")
        return thread if isinstance(thread, dict) else {}

    async def resume_thread(self, thread_id: str) -> str:
        """Делает существующий тред активным. Нельзя, пока идёт ход."""
        if not isinstance(thread_id, str) or not thread_id:
            raise BridgeError("empty thread id")
        async with self._turn_lock:
            if self.active_turn_id is not None:
                raise BridgeError("ход ещё идёт")
            self._require_ready()
            result = await self._request("thread/resume", {"threadId": thread_id, **self._thread_params()})
            self._adopt_thread(result)
            self._queue.clear()
            return self.thread_id  # type: ignore[return-value]

    async def archive_thread(self, thread_id: str) -> None:
        self._require_ready()
        await self._request("thread/archive", {"threadId": thread_id})

    async def list_models(self) -> list[dict]:
        self._require_ready()
        result = await self._request("model/list", {"includeHidden": False, "limit": 100})
        return [m for m in result.get("data") or [] if isinstance(m, dict) and m.get("model")]

    async def rate_limits(self) -> dict:
        self._require_ready()
        return await self._request("account/rateLimits/read", {})

    async def account(self) -> dict:
        self._require_ready()
        return await self._request("account/read", {})

    async def goal_get(self) -> Optional[dict]:
        self._require_ready()
        if self.thread_id is None:
            return None
        result = await self._request("thread/goal/get", {"threadId": self.thread_id})
        return result.get("goal")

    async def goal_set(self, objective: str) -> dict:
        self._require_ready()
        result = await self._request("thread/goal/set", {
            "threadId": self.thread_id, "objective": objective, "status": "active", "origin": "user",
        })
        return result.get("goal") or {}

    async def goal_clear(self) -> None:
        self._require_ready()
        await self._request("thread/goal/clear", {"threadId": self.thread_id, "origin": "user"})

    def _require_ready(self) -> None:
        if not self._ready.is_set() or self.thread_id is None:
            raise BridgeError("app-server не готов")

    async def interrupt(self) -> bool:
        """turn/interrupt для текущего хода. False, если хода нет."""
        turn_id = self.active_turn_id
        if turn_id is None or self.thread_id is None:
            return False
        try:
            await self._request("turn/interrupt", {"threadId": self.thread_id, "turnId": turn_id})
            return True
        except BridgeError as e:
            self._log(f"turn/interrupt failed: {e}")
            return False

    async def new_thread(self) -> str:
        """Создаёт новый тред и делает его активным (сохраняется в active_session_id)."""
        async with self._turn_lock:
            result = await self._request("thread/start", self._start_params())
            self._adopt_thread(result)
            self._queue.clear()
            self.active_turn_id = None
            return self.thread_id  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Параметры и состояние треда
    # ------------------------------------------------------------------

    def _thread_params(self) -> dict:
        params: dict = {
            "cwd": self.cwd,
            "approvalPolicy": approval_policy(self.settings()),
            "sandbox": SANDBOX_MODE,
        }
        text = self.instructions() if callable(self.instructions) else self.instructions
        if text:
            params["developerInstructions"] = text
        servers = self.mcp_servers() if callable(self.mcp_servers) else self.mcp_servers
        if servers:
            params["config"] = {"mcp_servers": servers}
        return params

    def _start_params(self) -> dict:
        """Параметры thread/start: serviceName для отбора тредов в /resume, ephemeral для смоука."""
        params = {**self._thread_params(), "serviceName": SERVICE_NAME}
        if self.ephemeral:
            params["ephemeral"] = True
        return params

    def _read_stored_id(self) -> Optional[str]:
        try:
            value = self.session_file.read_text("utf-8").strip()
        except OSError:
            return None
        if not value or value == "new":
            return None
        return value

    def _write_stored_id(self, thread_id: str) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.session_file.with_suffix(".tmp")
        tmp.write_text(thread_id + "\n", "utf-8")
        os.replace(tmp, self.session_file)

    def _adopt_thread(self, result: dict) -> None:
        thread = result.get("thread") or {}
        thread_id = thread.get("id")
        if not thread_id:
            raise BridgeError(f"app-server returned no thread id: {result!r}")
        self.thread_id = thread_id
        self._write_stored_id(thread_id)

    async def _ensure_thread(self) -> None:
        stored = self._read_stored_id()
        if stored:
            try:
                result = await self._request("thread/resume", {"threadId": stored, **self._thread_params()})
                self._adopt_thread(result)
                self._log(f"resumed thread {self.thread_id}")
                return
            except RPCError as e:
                # Сервер ответил ошибкой: тред удалён или не открывается. Стартуем новый.
                self._log(f"thread/resume {stored} rejected ({e}); starting a new thread")
        result = await self._request("thread/start", self._start_params())
        self._adopt_thread(result)
        self._log(f"started thread {self.thread_id}")

    # ------------------------------------------------------------------
    # Ходы
    # ------------------------------------------------------------------

    def _take_queue(self) -> list[dict]:
        merged: list[dict] = []
        for items in self._queue:
            merged.extend(items)
        self._queue.clear()
        return merged

    async def _start_turn_locked(self, items: list[dict]) -> bool:
        """Вызывать под _turn_lock. При ошибке items возвращаются в начало очереди."""
        settings = self.settings()
        params: dict = {
            "threadId": self.thread_id,
            "input": items,
            "approvalPolicy": approval_policy(settings),
        }
        if model_setting(settings):
            params["model"] = model_setting(settings)
        if effort_setting(settings):
            params["effort"] = effort_setting(settings)
        try:
            result = await self._request("turn/start", params)
        except BridgeError as e:
            self._log(f"turn/start failed, message kept in queue: {e}")
            self._queue.insert(0, items)
            return False
        turn_id = (result.get("turn") or {}).get("id")
        if turn_id and turn_id not in self._done_turns:
            self.active_turn_id = turn_id
        return True

    def _schedule_flush(self) -> None:
        if self._queue and self.active_turn_id is None and self._ready.is_set():
            asyncio.create_task(self._flush_queue())

    async def _flush_queue(self) -> None:
        async with self._turn_lock:
            if not self._queue or self.active_turn_id is not None or not self._ready.is_set():
                return
            if self.thread_id is None:
                return
            await self._start_turn_locked(self._take_queue())

    def _on_turn_started(self, params: dict) -> None:
        turn_id = (params.get("turn") or {}).get("id")
        if turn_id and turn_id not in self._done_turns:
            self.active_turn_id = turn_id

    def _on_turn_completed(self, params: dict) -> None:
        turn_id = (params.get("turn") or {}).get("id")
        if turn_id:
            self._done_turns.add(turn_id)
            if len(self._done_turns) > DONE_TURNS_MAX:
                self._done_turns.clear()
                self._done_turns.add(turn_id)
        if turn_id is None or self.active_turn_id == turn_id:
            self.active_turn_id = None
        self._schedule_flush()

    # ------------------------------------------------------------------
    # Транспорт
    # ------------------------------------------------------------------

    async def _send(self, obj: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            raise BridgeError("app-server is not running")
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            try:
                proc.stdin.write(data)
                await proc.stdin.drain()
            except (ConnectionError, RuntimeError) as e:
                raise BridgeError(f"write to app-server failed: {e}") from e

    async def _request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            msg = await asyncio.wait_for(fut, self._request_timeout)
        except asyncio.TimeoutError as e:
            raise BridgeError(f"{method} timed out") from e
        finally:
            self._pending.pop(rid, None)
        if "error" in msg:
            err = msg["error"] or {}
            raise RPCError(err.get("code"), str(err.get("message", "")))
        return msg.get("result") or {}

    async def _notify(self, method: str, params: Optional[dict] = None) -> None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        await self._send(msg)

    async def _connect(self) -> None:
        self._child_exited.clear()
        proc = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,
            cwd=self.cwd if os.path.isdir(self.cwd) else None,
            limit=STDOUT_LIMIT,
            env=self.env_factory(),
        )
        self._proc = proc
        self._reader_task = asyncio.create_task(self._read_loop(proc))
        try:
            await self._request("initialize", {"clientInfo": CLIENT_INFO})
            await self._notify("initialized")
            await self._ensure_thread()
        except BaseException:
            await self._kill_child()
            raise

    async def _kill_child(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        if self._reader_task is not None:
            try:
                await asyncio.wait_for(self._reader_task, 10)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
        if proc is not None and proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
        self._proc = None

    async def _read_loop(self, proc: asyncio.subprocess.Process) -> None:
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    self._log(f"non-JSON line from app-server: {line[:200]!r}")
                    continue
                if not isinstance(msg, dict):
                    continue
                if "method" in msg and "id" in msg:
                    asyncio.create_task(self._answer_server_request(msg))
                elif "method" in msg:
                    self._dispatch_notification(msg)
                elif "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self._log(f"reader failed: {e}")
        finally:
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(BridgeError("app-server connection lost"))
            self._pending.clear()
            self._ready.clear()
            self._child_exited.set()

    def _dispatch_notification(self, msg: dict) -> None:
        method = msg.get("method") or ""
        params = msg.get("params") or {}
        if method == "turn/started":
            self._on_turn_started(params)
        elif method == "turn/completed":
            self._on_turn_completed(params)
        for cb in list(self._listeners):
            try:
                res = cb(method, params)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            except Exception as e:  # noqa: BLE001
                self._log(f"listener error on {method}: {e}")

    async def _answer_server_request(self, msg: dict) -> None:
        """Сервер просит решение (approval, ввод и т.п.). Отвечаем всегда."""
        rid = msg.get("id")
        method = msg.get("method") or ""
        try:
            params = msg.get("params") or {}
            if method in APPROVAL_METHODS and approval_policy(self.settings()) == AUTO_ON_REQUEST:
                allow = await self._ask_owner(method, params)
                result = approval_result(method, params, allow)
            else:
                result = self._server_request_result(method, params)
            if result is None:
                reply = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unsupported: {method}"}}
            else:
                reply = {"jsonrpc": "2.0", "id": rid, "result": result}
            self._log(f"server request {method} -> {'error' if result is None else 'answered'}")
            await self._send(reply)
        except BridgeError as e:
            self._log(f"could not answer server request {method}: {e}")

    async def _ask_owner(self, method: str, params: dict) -> bool:
        handler = self._approval_handler
        if handler is None:
            return False  # без обработчика на подтверждения отвечаем отказом
        try:
            return bool(await handler(method, params))
        except Exception as e:  # noqa: BLE001
            self._log(f"approval handler failed: {e}")
            return False

    @staticmethod
    def _server_request_result(method: str, params: dict) -> Optional[dict]:
        if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            return {"decision": "accept"}
        if method in ("execCommandApproval", "applyPatchApproval"):
            return {"decision": "approved"}
        if method == "item/permissions/requestApproval":
            # В режиме never дополнительных прав не выдаём: danger-full-access их не требует.
            return {"permissions": {}, "scope": "turn"}
        if method == "item/tool/requestUserInput":
            return {"answers": {}}
        if method == "mcpServer/elicitation/request":
            return {"action": "decline"}
        if method == "item/tool/call":
            return {
                "success": False,
                "contentItems": [{"type": "inputText", "text": "dynamic tools are not supported by codex-gram"}],
            }
        return None

    # ------------------------------------------------------------------
    # Супервизор
    # ------------------------------------------------------------------

    async def _supervise(self) -> None:
        backoff = self._backoff_min
        while not self._stopping:
            started = time.monotonic()
            try:
                await self._connect()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._log(f"connect failed: {e}; retry in {backoff:.1f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._backoff_max)
                continue
            self._ready.set()
            self._schedule_flush()
            await self._child_exited.wait()
            if self._stopping:
                break
            self._ready.clear()
            self.active_turn_id = None
            if time.monotonic() - started >= STABLE_UPTIME_SEC:
                backoff = self._backoff_min
            rc = self._proc.returncode if self._proc is not None else None
            self._log(f"app-server exited (rc={rc}); restarting in {backoff:.1f}s")
            await self._kill_child()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._backoff_max)
