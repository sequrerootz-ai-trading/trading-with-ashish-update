from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

DEFAULT_SYMBOLS = ("NIFTY", "SENSEX", "CRUDEOIL")
STATE_FILE = Path("web_state.json")


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: set[Any] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._market_data_service: Any | None = None
        self._latest_by_symbol: dict[str, dict[str, Any]] = {
            symbol: self._default_signal_state(symbol) for symbol in DEFAULT_SYMBOLS
        }
        self._last_signal_by_symbol: dict[str, dict[str, Any]] = {
            symbol: self._default_signal_state(symbol) for symbol in DEFAULT_SYMBOLS
        }
        self._load_state()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def register_market_data_service(self, service: Any) -> None:
        self._market_data_service = service

    def get_snapshot(self) -> dict[str, Any]:
        return {
            "type": "snapshot",
            "timer": self.get_timer_text(),
            "latest": self._latest_by_symbol,
            "last_signals": self._last_signal_by_symbol,
        }

    def get_timer_text(self) -> str:
        if self._market_data_service is None:
            return "--:--"
        try:
            return self._market_data_service._time_until_next_candle_close()
        except Exception:
            return "--:--"

    async def connect(self, websocket: Any) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        await websocket.send_json(self.get_snapshot())

    def disconnect(self, websocket: Any) -> None:
        self._connections.discard(websocket)

    def publish_signal(self, symbol: str, signal: Any) -> None:
        symbol_key = str(symbol or "").strip().upper()
        payload = self._serialize_signal(symbol_key, signal)
        self._latest_by_symbol[symbol_key] = payload
        if payload["signal"] not in {"", None, "NO_TRADE"}:
            self._last_signal_by_symbol[symbol_key] = payload
        self._save_state()
        self._schedule_broadcast(
            {
                "type": "signal_update",
                "symbol": symbol_key,
                "timer": self.get_timer_text(),
                "payload": payload,
                "latest": self._latest_by_symbol,
                "last_signals": self._last_signal_by_symbol,
            }
        )

    def publish_timer(self) -> None:
        self._schedule_broadcast(
            {
                "type": "timer_update",
                "timer": self.get_timer_text(),
                "latest": self._latest_by_symbol,
                "last_signals": self._last_signal_by_symbol,
            }
        )

    async def timer_loop(self) -> None:
        while True:
            self.publish_timer()
            await asyncio.sleep(1)

    def _schedule_broadcast(self, payload: dict[str, Any]) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        disconnected: list[Any] = []
        for websocket in list(self._connections):
            try:
                await websocket.send_json(payload)
            except Exception:
                disconnected.append(websocket)
        for websocket in disconnected:
            self.disconnect(websocket)

    def _serialize_signal(self, symbol: str, signal: Any) -> dict[str, Any]:
        if signal is None:
            return self._default_signal_state(symbol)

        details = getattr(signal, "details", None)
        option = (
            getattr(details, "option_suggestion", None) if details is not None else None
        )
        return {
            "symbol": symbol,
            "signal": getattr(signal, "signal", "NO_TRADE") or "NO_TRADE",
            "reason": getattr(signal, "reason", "waiting_for_signal")
            or "waiting_for_signal",
            "summary": getattr(details, "summary", "") if details is not None else "",
            "confidence": float(getattr(signal, "confidence", 0.0) or 0.0),
            "entry_price": self._safe_number(getattr(signal, "entry_price", None)),
            "target": self._safe_number(getattr(signal, "target", None)),
            "stop_loss": self._safe_number(getattr(signal, "stop_loss", None)),
            "timestamp": getattr(signal, "timestamp", "") or "",
            "details": (
                asdict(details)
                if details is not None and is_dataclass(details)
                else None
            ),
            "option": (
                asdict(option) if option is not None and is_dataclass(option) else None
            ),
            "context": self._safe_context(getattr(signal, "context", None)),
        }

    def _load_state(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        latest = payload.get("latest") or {}
        last_signals = payload.get("last_signals") or {}
        for symbol in DEFAULT_SYMBOLS:
            if isinstance(latest.get(symbol), dict):
                self._latest_by_symbol[symbol] = latest[symbol]
            if isinstance(last_signals.get(symbol), dict):
                self._last_signal_by_symbol[symbol] = last_signals[symbol]

    def _save_state(self) -> None:
        payload = {
            "latest": self._latest_by_symbol,
            "last_signals": self._last_signal_by_symbol,
        }
        STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _safe_number(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return round(float(value), 2)
        except Exception:
            return None

    @staticmethod
    def _safe_context(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        safe: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(item, (str, int, float, bool)) or item is None:
                safe[str(key)] = item
            elif isinstance(item, list):
                safe[str(key)] = [
                    entry
                    for entry in item
                    if isinstance(entry, (str, int, float, bool)) or entry is None
                ]
        return safe

    @staticmethod
    def _default_signal_state(symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "signal": "NO_TRADE",
            "reason": "waiting_for_signal",
            "summary": "",
            "confidence": 0.0,
            "entry_price": None,
            "target": None,
            "stop_loss": None,
            "timestamp": "",
            "details": None,
            "option": None,
            "context": {},
        }


web_socket_manager = WebSocketManager()
