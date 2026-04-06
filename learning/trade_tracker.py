from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count


@dataclass(slots=True)
class TradeRecord:
    trade_id: str
    symbol: str
    signal: str
    confidence: float
    regime: str
    entry_price: float
    exit_price: float | None = None
    pnl: float | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    reason: str = ""


_trade_id_counter = count(1)
_trade_records: list[TradeRecord] = []
_open_trade_index: dict[str, int] = {}


def record_trade_open(signal, price: float, regime: str) -> str:
    trade_id = f"T{next(_trade_id_counter):06d}"
    record = TradeRecord(
        trade_id=trade_id,
        symbol=str(getattr(signal, "symbol", "") or ""),
        signal=str(getattr(signal, "signal", "") or ""),
        confidence=float(getattr(signal, "confidence", 0.0) or 0.0),
        regime=str(regime or ""),
        entry_price=float(price),
        reason=str(getattr(signal, "reason", "") or ""),
    )
    _open_trade_index[trade_id] = len(_trade_records)
    _trade_records.append(record)
    return trade_id


def record_trade_close(trade_id: str, exit_price: float) -> TradeRecord | None:
    index = _open_trade_index.pop(trade_id, None)
    if index is None:
        return None

    record = _trade_records[index]
    final_exit_price = float(exit_price)
    pnl = _calculate_pnl(record.signal, record.entry_price, final_exit_price)
    updated_record = TradeRecord(
        trade_id=record.trade_id,
        symbol=record.symbol,
        signal=record.signal,
        confidence=record.confidence,
        regime=record.regime,
        entry_price=record.entry_price,
        exit_price=final_exit_price,
        pnl=pnl,
        timestamp=record.timestamp,
        reason=record.reason,
    )
    _trade_records[index] = updated_record

    closed_trades = [item for item in _trade_records if item.exit_price is not None]
    if closed_trades and len(closed_trades) % 10 == 0:
        stats = get_stats()
        print(
            f"[TRADE_STATS] total={stats['total_trades']} "
            f"win_rate={stats['win_rate']:.2f}% avg_pnl={stats['avg_pnl']:.2f}"
        )

    return updated_record


def get_stats() -> dict[str, float]:
    closed_trades = [record for record in _trade_records if record.pnl is not None]
    total_trades = len(closed_trades)
    if total_trades == 0:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
        }

    wins = sum(1 for record in closed_trades if (record.pnl or 0.0) > 0)
    avg_pnl = sum(record.pnl or 0.0 for record in closed_trades) / total_trades
    return {
        "total_trades": total_trades,
        "win_rate": (wins / total_trades) * 100.0,
        "avg_pnl": avg_pnl,
    }


def _calculate_pnl(signal_type: str, entry_price: float, exit_price: float) -> float:
    if signal_type == "BUY_PE":
        return round(entry_price - exit_price, 2)
    return round(exit_price - entry_price, 2)
