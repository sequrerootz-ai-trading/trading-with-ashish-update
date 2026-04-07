from __future__ import annotations

import logging
import sys

from data.candle_manager import CandleManager
from engine.trade_utils import (
    _handle_live_stop_loss_completion,
    _safe_exit_position,
    _trail_active_trade_if_needed,
    _try_execute_entry_if_needed,
)
from execution.order_manager import OrderManager
from execution.trade_manager import TradeManager
from utils.runtime_helpers import (
    _fmt_rupee,
    _print_order_error,
    _print_stop_loss_hit,
    _print_time_exit,
    _print_trade_running,
    _print_trade_waiting,
)
from utils_console import RED, colorize


def _root_module():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _reset_daily_state_if_needed(market_type: str):
    return _root_module()._reset_daily_state_if_needed(market_type)


def _refresh_trade_extremes(
    active_trade, current_price: float, trade_manager: TradeManager
):
    return _root_module()._refresh_trade_extremes(
        active_trade, current_price, trade_manager
    )


def _entry_confirmation_passed(active_trade, candle_manager: CandleManager) -> bool:
    return _root_module()._entry_confirmation_passed(active_trade, candle_manager)


def _handle_partial_profit(
    symbol: str,
    active_trade,
    current_price: float,
    trade_manager: TradeManager,
    order_manager: OrderManager,
    market_type: str,
):
    return _root_module()._handle_partial_profit(
        symbol, active_trade, current_price, trade_manager, order_manager, market_type
    )


def _should_time_exit(active_trade, current_price: float) -> bool:
    return _root_module()._should_time_exit(active_trade, current_price)


def _extract_order_price(order: dict[str, object], fallback_price: float) -> float:
    return _root_module()._extract_order_price(order, fallback_price)


def _record_trade_result(active_trade, exit_price: float, market_type: str) -> None:
    return _root_module()._record_trade_result(active_trade, exit_price, market_type)


def manage_nifty_trade(
    symbol: str,
    trade_manager: TradeManager,
    premium_service,
    order_manager: OrderManager,
    candle_manager: CandleManager,
) -> bool:
    _reset_daily_state_if_needed("EQUITY")
    active_trade = trade_manager.get_active_trade(symbol)
    if active_trade is None:
        return False

    try:
        premium = premium_service.get_contract_quote(
            active_trade.trading_symbol, active_trade.exchange
        )
    except Exception as exc:
        logging.warning(
            "Unable to refresh premium for active trade %s: %s",
            active_trade.trading_symbol,
            exc,
        )
        _print_order_error(
            f"Premium refresh failed for {active_trade.trading_symbol}: {exc}"
        )
        return True

    if premium is None:
        logging.warning(
            "Unable to refresh premium for active trade %s", active_trade.trading_symbol
        )
        return True

    current_price = premium.last_price
    if active_trade.status == "OPEN":
        active_trade = (
            _refresh_trade_extremes(active_trade, current_price, trade_manager)
            or active_trade
        )

    if active_trade.status == "PENDING_ENTRY":
        if current_price < active_trade.initial_stop_loss:
            closed_trade = trade_manager.close_active_trade(
                symbol, "entry_failed_below_stop", current_price
            )
            if closed_trade is not None:
                print(
                    colorize(
                        "\n".join(
                            [
                                "[ENTRY CANCELLED]",
                                f"{closed_trade.trading_symbol} slipped below stop before entry.",
                                f"LTP: {_fmt_rupee(current_price)} | Planned SL: {_fmt_rupee(closed_trade.initial_stop_loss)}",
                            ]
                        ),
                        RED,
                        bold=True,
                    )
                )
            return True

        if not _entry_confirmation_passed(active_trade, candle_manager):
            _print_trade_waiting(active_trade, current_price)
            return True

        if active_trade.entry_low <= current_price <= active_trade.entry_high:
            updated_trade = _try_execute_entry_if_needed(
                symbol,
                active_trade,
                current_price,
                trade_manager,
                order_manager,
                "EQUITY",
            )
            if updated_trade is not None and updated_trade.status == "OPEN":
                _print_trade_running(updated_trade, current_price)
            return True

        _print_trade_waiting(active_trade, current_price)
        return True

    if _handle_live_stop_loss_completion(
        symbol, active_trade, current_price, trade_manager, order_manager, "EQUITY"
    ):
        return True

    latest_candle = candle_manager.get_last_completed_candle(symbol)
    partial_trade = _handle_partial_profit(
        symbol, active_trade, current_price, trade_manager, order_manager, "EQUITY"
    )
    active_trade = (
        partial_trade or trade_manager.get_active_trade(symbol) or active_trade
    )

    if _should_time_exit(active_trade, current_price):
        exit_order = _safe_exit_position(
            active_trade,
            current_price,
            order_manager,
            quantity=active_trade.remaining_quantity or active_trade.quantity,
            reason="time_exit",
        )
        if exit_order is None:
            return True
        exit_price = _extract_order_price(exit_order, current_price)
        closed_trade = trade_manager.close_active_trade(symbol, "time_exit", exit_price)
        if closed_trade is not None:
            _record_trade_result(closed_trade, exit_price, "EQUITY")
            _print_time_exit(closed_trade, exit_price)
        return True

    if current_price <= active_trade.stop_loss:
        exit_order = _safe_exit_position(
            active_trade,
            current_price,
            order_manager,
            quantity=active_trade.remaining_quantity or active_trade.quantity,
            reason="stop_loss_hit",
        )
        if exit_order is None:
            return True
        exit_price = _extract_order_price(exit_order, current_price)
        closed_trade = trade_manager.close_active_trade(
            symbol, "stop_loss_hit", exit_price
        )
        if closed_trade is not None:
            _record_trade_result(closed_trade, exit_price, "EQUITY")
            _print_stop_loss_hit(closed_trade, exit_price)
        return True

    updated_trade = _trail_active_trade_if_needed(
        active_trade, current_price, trade_manager, order_manager, latest_candle
    )
    _print_trade_running(updated_trade or active_trade, current_price)
    return True
