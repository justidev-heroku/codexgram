"""Брокер подтверждений для режима /auto выкл (approvalPolicy on-request).

Когда app-server просит подтверждение, брокер отправляет сообщение с кнопками и ждёт
ответ. Без ответа за timeout решение «отклонить». Токен короткий и одноразовый: повторный
или чужой клик ничего не делает.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Awaitable, Callable

DEFAULT_TIMEOUT = 600.0


class ApprovalBroker:
    def __init__(self, send: Callable[[str, str, dict], Awaitable[None]], timeout: float = DEFAULT_TIMEOUT):
        self._send = send
        self.timeout = timeout
        self._pending: dict[str, asyncio.Future] = {}

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def ask(self, method: str, params: dict) -> bool:
        """True — разрешить, False — отклонить (в том числе по таймауту или при ошибке отправки)."""
        token = secrets.token_hex(4)
        while token in self._pending:
            token = secrets.token_hex(4)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[token] = fut
        try:
            await self._send(token, method, params)
        except Exception:  # noqa: BLE001
            self._pending.pop(token, None)
            return False
        try:
            return bool(await asyncio.wait_for(fut, self.timeout))
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(token, None)

    def resolve(self, token: str, allow: bool) -> bool:
        fut = self._pending.get(token)
        if fut is None or fut.done():
            return False
        fut.set_result(bool(allow))
        return True

    def cancel_all(self) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result(False)
        self._pending.clear()


def describe_request(method: str, params: dict) -> tuple[str, str]:
    """(заголовок, подробности) для сообщения о подтверждении."""
    if method == "item/commandExecution/requestApproval" or method == "execCommandApproval":
        cmd = params.get("command")
        if isinstance(cmd, list):
            cmd = " ".join(str(part) for part in cmd)
        return "Запрос на выполнение команды", str(cmd or "")
    if method in ("item/fileChange/requestApproval", "applyPatchApproval"):
        return "Запрос на изменение файлов", str(params.get("grantRoot") or params.get("reason") or "")
    if method == "item/permissions/requestApproval":
        return "Запрос дополнительных прав", str(params.get("reason") or "")
    return "Запрос подтверждения", method


def request_text(method: str, params: dict) -> str:
    from codex_format import esc, truncate

    title, detail = describe_request(method, params)
    reason = params.get("reason")
    lines = [f"🔐 <b>{esc(title)}</b>"]
    if detail:
        lines.append(f"<blockquote>{esc(truncate(detail, 800))}</blockquote>")
    if reason and reason != detail:
        lines.append(esc(truncate(str(reason), 300)))
    return "\n".join(lines)
