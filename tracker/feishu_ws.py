from __future__ import annotations

import asyncio
import multiprocessing
import queue
import signal
import threading
from collections.abc import Callable
from typing import Any

# Import before asyncio.run(). The SDK's WebSocket transport owns a module-level
# event loop, so each bot runs in its own spawned worker process.
from lark_channel import FeishuChannel, LogLevel

from .bots import FeishuBotBinding


BoundMessageHandler = Callable[[str, str, str, str, str], dict[str, Any]]


async def _run_bot_channel(
    bot: FeishuBotBinding,
    event_queue: Any,
    stop_event: Any,
) -> None:
    channel = FeishuChannel(
        app_id=bot.app_id,
        app_secret=bot.app_secret,
        log_level=LogLevel.WARNING,
    )

    async def on_message(message: Any) -> None:
        event_queue.put(
            {
                "type": "message",
                "callback_key": bot.callback_key,
                "event_id": str(getattr(message, "message_id", "") or ""),
                "chat_id": str(getattr(message, "chat_id", "") or ""),
                "sender_id": str(getattr(message, "sender_id", "") or ""),
                "text": str(
                    getattr(message, "body_text", "")
                    or getattr(message, "content_text", "")
                    or ""
                ),
            }
        )

    channel.on("message", on_message)
    try:
        await channel.connect_until_ready(timeout=30.0)
        event_queue.put({"type": "ready", "bot_id": bot.bot_id})
        while not stop_event.is_set():
            await asyncio.sleep(0.25)
    finally:
        await channel.disconnect()


def _channel_process_main(
    bot: FeishuBotBinding,
    event_queue: Any,
    stop_event: Any,
) -> None:
    # Ctrl+C is owned by the parent server. Children exit through stop_event so
    # they cannot survive as orphan WebSocket consumers after a local restart.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        asyncio.run(_run_bot_channel(bot, event_queue, stop_event))
    except Exception as exc:
        event_queue.put(
            {"type": "failed", "bot_id": bot.bot_id, "error": str(exc)[:1000]}
        )


class FeishuLongConnectionManager:
    """Supervise one official-SDK WebSocket worker per Feishu bot."""

    def __init__(
        self,
        bots: tuple[FeishuBotBinding, ...],
        message_handler: BoundMessageHandler,
    ) -> None:
        self.bots = tuple(bot for bot in bots if bot.transport == "websocket")
        self.message_handler = message_handler
        self.status = "disabled" if not self.bots else "stopped"
        self.connected_count = 0
        self.last_error = ""
        self.last_message_error = ""
        self._context = multiprocessing.get_context("spawn")
        self._event_queue: Any = None
        self._process_stop: Any = None
        self._processes: list[Any] = []
        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._ready_event = threading.Event()
        self._ready_bots: set[str] = set()
        self._failed_bots: set[str] = set()

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "configured_count": len(self.bots),
            "connected_count": self.connected_count,
            "last_error": self.last_error,
            "last_message_error": self.last_message_error,
        }

    def start(self, wait_timeout: float = 35.0) -> dict[str, Any]:
        if not self.bots:
            return self.snapshot()
        if any(process.is_alive() for process in self._processes):
            return self.snapshot()
        self.status = "connecting"
        self.connected_count = 0
        self.last_error = ""
        self._ready_bots.clear()
        self._failed_bots.clear()
        self._ready_event.clear()
        self._reader_stop.clear()
        self._event_queue = self._context.Queue()
        self._process_stop = self._context.Event()
        self._reader_thread = threading.Thread(
            target=self._read_events,
            name="feishu-long-connection-events",
            daemon=True,
        )
        self._reader_thread.start()
        self._processes = []
        for bot in self.bots:
            process = self._context.Process(
                target=_channel_process_main,
                args=(bot, self._event_queue, self._process_stop),
                name=f"feishu-{bot.bot_id}",
                daemon=True,
            )
            process.start()
            self._processes.append(process)
        self._ready_event.wait(wait_timeout)
        if self.status == "connecting":
            self.status = "failed"
            self.last_error = "飞书长连接启动超时"
        return self.snapshot()

    def stop(self, timeout: float = 8.0) -> None:
        if self._process_stop is not None:
            self._process_stop.set()
        per_process_timeout = timeout / max(len(self._processes), 1)
        for process in self._processes:
            process.join(per_process_timeout)
            if process.is_alive():
                process.terminate()
                process.join(2.0)
        self._reader_stop.set()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(2.0)
        self.connected_count = 0
        if self.status != "disabled":
            self.status = "stopped"

    def _read_events(self) -> None:
        while not self._reader_stop.is_set():
            try:
                event = self._event_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            event_type = event.get("type")
            if event_type == "ready":
                self._ready_bots.add(str(event.get("bot_id") or ""))
                self.connected_count = len(self._ready_bots)
                if self.connected_count == len(self.bots):
                    self.status = "connected"
                    self._ready_event.set()
                continue
            if event_type == "failed":
                self._failed_bots.add(str(event.get("bot_id") or ""))
                self.last_error = str(event.get("error") or "")[:1000]
                if len(self._ready_bots) + len(self._failed_bots) == len(self.bots):
                    self.status = "partial" if self._ready_bots else "failed"
                    self._ready_event.set()
                continue
            if event_type != "message":
                continue
            try:
                self.message_handler(
                    str(event.get("callback_key") or ""),
                    str(event.get("event_id") or ""),
                    str(event.get("chat_id") or ""),
                    str(event.get("sender_id") or ""),
                    str(event.get("text") or ""),
                )
                self.last_message_error = ""
            except PermissionError:
                continue
            except Exception as exc:
                self.last_message_error = str(exc)[:1000]
