from __future__ import annotations

from datetime import datetime, time as dt_time

from config import get_mode
from utils_console import CYAN, GREEN, RED, YELLOW, colorize


def _fmt_rupee(value: float) -> str:
    return f"Rs {value:.2f}"


def _format_pnl_pct(entry_price: float | None, current_price: float) -> str:
    if entry_price is None or entry_price <= 0:
        return "NA"
    pnl_pct = ((current_price - entry_price) / entry_price) * 100
    return f"{pnl_pct:+.2f}%"


def _parse_time(value: str) -> dt_time:
    return datetime.strptime(value.strip(), "%H:%M").time()


def _time_in_range(value: dt_time, start: dt_time, end: dt_time) -> bool:
    return start <= value <= end


def _mode_label() -> str:
    return get_mode().upper()


def _mode_color() -> str:
    return GREEN if _mode_label() == "LIVE" else CYAN


def _contract_label(active_trade) -> str:
    return f"{active_trade.symbol} {active_trade.option_type}".strip()


def _print_trade_started(active_trade) -> None:
    lines = [
        f"[{_mode_label()} TRADE STARTED]",
        f"Bought {active_trade.trading_symbol} at {_fmt_rupee(active_trade.entry_price or active_trade.entry_high)}",
        f"Qty: {active_trade.quantity}",
        f"Initial SL: {_fmt_rupee(active_trade.stop_loss)}",
        (
            f"Target: {_fmt_rupee(active_trade.target_price)}"
            if active_trade.target_price is not None
            else "Target: Open"
        ),
        f"Regime: {active_trade.regime} | RR: {active_trade.rr_ratio:.2f}",
    ]
    print(colorize("\n".join(lines), _mode_color(), bold=True))


def _print_trade_waiting(active_trade, current_price: float) -> None:
    lines = [
        f"[{_mode_label()} WAITING FOR ENTRY]",
        f"{_contract_label(active_trade)} is inside watch mode.",
        f"Entry Range: {_fmt_rupee(active_trade.entry_low)} to {_fmt_rupee(active_trade.entry_high)}",
        f"Current LTP: {_fmt_rupee(current_price)} | Planned SL: {_fmt_rupee(active_trade.initial_stop_loss)}",
    ]
    print(colorize("\n".join(lines), _mode_color(), bold=True))


def _print_trail_update(active_trade, current_price: float) -> None:
    lines = [
        f"[{_mode_label()} TRAIL SL UPDATED]",
        f"Price moved to {_fmt_rupee(current_price)}",
        f"New SL: {_fmt_rupee(active_trade.stop_loss)}",
    ]
    print(colorize("\n".join(lines), _mode_color(), bold=True))


def _print_trade_running(active_trade, current_price: float) -> None:
    pnl_text = _format_pnl_pct(active_trade.entry_price, current_price)
    lines = [
        f"[{_mode_label()} TRADE RUNNING]",
        f"LTP: {_fmt_rupee(current_price)} | SL: {_fmt_rupee(active_trade.stop_loss)} | PnL: {pnl_text}",
    ]
    if active_trade.target_price is not None:
        lines.append(
            f"Target: {_fmt_rupee(active_trade.target_price)} | Realized: {_fmt_rupee(active_trade.realized_pnl)}"
        )
    print(colorize("\n".join(lines), _mode_color(), bold=True))


def _print_stop_loss_hit(active_trade, exit_price: float) -> None:
    pnl_text = _format_pnl_pct(active_trade.entry_price, exit_price)
    lines = [
        f"[{_mode_label()} STOP LOSS HIT]",
        f"Exited at {_fmt_rupee(exit_price)}",
        f"PnL: {pnl_text}",
    ]
    print(colorize("\n".join(lines), _mode_color(), bold=True))


def _print_order_error(message: str) -> None:
    print(colorize(f"[{_mode_label()} ERROR] {message}", RED, bold=True))


def _print_partial_exit(active_trade, exit_price: float, quantity: int) -> None:
    lines = [
        f"[{_mode_label()} PARTIAL EXIT]",
        f"Booked {quantity} at {_fmt_rupee(exit_price)}",
        f"Remaining Qty: {active_trade.remaining_quantity} | New SL: {_fmt_rupee(active_trade.stop_loss)}",
    ]
    print(colorize("\n".join(lines), _mode_color(), bold=True))


def _print_time_exit(active_trade, exit_price: float) -> None:
    lines = [
        f"[{_mode_label()} TIME EXIT]",
        f"Exited {active_trade.trading_symbol} at {_fmt_rupee(exit_price)}",
        f"PnL: {_format_pnl_pct(active_trade.entry_price, exit_price)}",
    ]
    print(colorize("\n".join(lines), _mode_color(), bold=True))


def _print_skip(message: str) -> None:
    print(colorize(f"[{_mode_label()} SKIPPED] {message}", YELLOW, bold=True))


def _print_blocked(message: str) -> None:
    print(colorize(f"[{_mode_label()} BLOCKED] {message}", RED, bold=True))
