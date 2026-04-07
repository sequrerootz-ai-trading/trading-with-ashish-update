from __future__ import annotations

import logging
import sys
from datetime import datetime

from execution.order_manager import OrderManager
from execution.trade_manager import ActiveTrade, TradeManager
from utils.runtime_helpers import (
    _print_order_error,
    _print_stop_loss_hit,
    _print_trade_started,
    _print_trail_update,
)


def _root_module():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _adjusted_entry_price(active_trade: ActiveTrade, current_price: float) -> float:
    return _root_module()._adjusted_entry_price(active_trade, current_price)


def _compute_entry_quantity(
    order_manager: OrderManager,
    active_trade: ActiveTrade,
    execution_price: float,
    market_type: str,
) -> int:
    return _root_module()._compute_entry_quantity(
        order_manager, active_trade, execution_price, market_type
    )


def _extract_order_price(order: dict[str, object], fallback_price: float) -> float:
    return _root_module()._extract_order_price(order, fallback_price)


def _record_trade_result(
    active_trade: ActiveTrade, exit_price: float, market_type: str
) -> None:
    return _root_module()._record_trade_result(active_trade, exit_price, market_type)


def _execution_settings():
    return _root_module()._execution_settings()


def _safe_exit_position(
    active_trade: ActiveTrade,
    current_price: float,
    order_manager: OrderManager,
    quantity: int | None = None,
    reason: str = "manual_exit",
):
    try:
        exit_quantity = (
            quantity or active_trade.remaining_quantity or active_trade.quantity
        )
        if order_manager.mode == "LIVE" and active_trade.stop_loss_order_id:
            try:
                order_manager.cancel_order(active_trade.stop_loss_order_id)
            except Exception as exc:
                logging.warning(
                    "Unable to cancel stop-loss order for %s before exit: %s",
                    active_trade.trading_symbol,
                    exc,
                )
        return order_manager.exit_position(
            trading_symbol=active_trade.trading_symbol,
            exchange=active_trade.exchange,
            quantity=exit_quantity,
            last_price=current_price,
            reason=reason,
        )
    except Exception as exc:
        logging.exception("Exit order failed for %s", active_trade.trading_symbol)
        _print_order_error(f"Exit failed for {active_trade.trading_symbol}: {exc}")
        return None


def _try_execute_entry_if_needed(
    symbol: str,
    active_trade: ActiveTrade,
    current_price: float,
    trade_manager: TradeManager,
    order_manager: OrderManager,
    market_type: str,
) -> ActiveTrade | None:
    latest_trade = trade_manager.get_active_trade(symbol) or active_trade
    if latest_trade.order_placed or latest_trade.status != "PENDING_ENTRY":
        return latest_trade
    if not (latest_trade.entry_low <= current_price <= latest_trade.entry_high):
        return latest_trade

    try:
        execution_price = _adjusted_entry_price(latest_trade, current_price)
        quantity = _compute_entry_quantity(
            order_manager, latest_trade, execution_price, market_type
        )
        managed_order = order_manager.place_market_buy(
            trading_symbol=latest_trade.trading_symbol,
            exchange=latest_trade.exchange,
            last_price=execution_price,
            stop_loss_price=latest_trade.stop_loss,
            quantity_override=quantity,
        )
        updated_trade = trade_manager.update_active_trade(
            symbol=symbol,
            status="OPEN",
            entry_price=managed_order.entry_price,
            stop_loss=managed_order.stop_loss_price,
            initial_stop_loss=latest_trade.initial_stop_loss,
            quantity=managed_order.quantity,
            remaining_quantity=managed_order.quantity,
            highest_price=managed_order.entry_price,
            mfe_price=managed_order.entry_price,
            mae_price=managed_order.entry_price,
            order_placed=True,
            entry_order_id=managed_order.entry_order_id,
            stop_loss_order_id=managed_order.stop_loss_order_id,
            opened_at=datetime.now().isoformat(),
        )
        if updated_trade is not None:
            _print_trade_started(updated_trade)
        return updated_trade
    except Exception as exc:
        logging.exception("Entry order failed for %s", latest_trade.trading_symbol)
        _print_order_error(f"Order failed for {latest_trade.trading_symbol}: {exc}")
        trade_manager.close_active_trade(symbol, "entry_order_failed", current_price)
        return None


def _handle_live_stop_loss_completion(
    symbol: str,
    active_trade: ActiveTrade,
    current_price: float,
    trade_manager: TradeManager,
    order_manager: OrderManager,
    market_type: str,
) -> bool:
    if order_manager.mode != "LIVE" or not active_trade.stop_loss_order_id:
        return False

    try:
        stop_order = order_manager.check_order_status(active_trade.stop_loss_order_id)
    except Exception as exc:
        logging.warning(
            "Unable to check stop-loss order status for %s: %s",
            active_trade.trading_symbol,
            exc,
        )
        _print_order_error(
            f"Could not verify stop-loss order for {active_trade.trading_symbol}: {exc}"
        )
        return False

    stop_status = str(stop_order.get("status", "UNKNOWN")).upper()
    if stop_status == "COMPLETE":
        exit_price = _extract_order_price(stop_order, active_trade.stop_loss)
        order_manager.trade_manager.record_trade(
            mode="LIVE",
            symbol=active_trade.trading_symbol,
            side="SELL",
            quantity=active_trade.remaining_quantity or active_trade.quantity,
            price=exit_price,
            status="STOP_LOSS_FILLED",
            reason="stop_loss_hit",
            order_id=active_trade.stop_loss_order_id,
        )
        closed_trade = trade_manager.close_active_trade(
            symbol, "stop_loss_hit", exit_price
        )
        if closed_trade is not None:
            _record_trade_result(closed_trade, exit_price, market_type)
            _print_stop_loss_hit(closed_trade, exit_price)
        return True

    if (
        stop_status in {"REJECTED", "CANCELLED"}
        and current_price <= active_trade.stop_loss
    ):
        _print_order_error(
            f"Stop-loss order {stop_status.lower()} for {active_trade.trading_symbol}. Exiting at market."
        )
        exit_order = _safe_exit_position(
            active_trade,
            current_price,
            order_manager,
            quantity=active_trade.remaining_quantity or active_trade.quantity,
            reason="emergency_stop_loss_exit",
        )
        if exit_order is None:
            return True
        exit_price = _extract_order_price(exit_order, current_price)
        closed_trade = trade_manager.close_active_trade(
            symbol, "emergency_stop_loss_exit", exit_price
        )
        if closed_trade is not None:
            _record_trade_result(closed_trade, exit_price, market_type)
            _print_stop_loss_hit(closed_trade, exit_price)
        return True

    return False


def _trail_active_trade_if_needed(
    active_trade: ActiveTrade,
    current_price: float,
    trade_manager: TradeManager,
    order_manager: OrderManager,
    latest_candle=None,
) -> ActiveTrade | None:
    entry_price = active_trade.entry_price
    if entry_price is None or active_trade.quantity <= 0:
        return active_trade

    settings = _execution_settings()
    highest_price = max(active_trade.highest_price or entry_price, current_price)
    initial_risk = max(entry_price - active_trade.initial_stop_loss, 0.01)
    reward_multiple = max((highest_price - entry_price) / initial_risk, 0.0)

    locked_stop = active_trade.stop_loss
    if reward_multiple >= 1.0:
        locked_stop = max(locked_stop, entry_price)
    if reward_multiple >= 1.5:
        locked_stop = max(locked_stop, entry_price + (initial_risk * 0.5))
    if reward_multiple >= 2.0:
        locked_stop = max(locked_stop, entry_price + initial_risk)

    if settings.trailing_mode == "FIXED_STEP":
        trailing_candidate = highest_price * (1.0 - settings.fixed_trail_step_pct)
    else:
        trailing_buffer = max(
            highest_price * settings.trailing_buffer_pct,
            initial_risk * settings.trailing_rr_lock_step,
        )
        trailing_candidate = highest_price - trailing_buffer

    new_stop_loss = max(active_trade.stop_loss, locked_stop, trailing_candidate)
    new_stop_loss = min(new_stop_loss, current_price * 0.995)

    if (
        highest_price <= (active_trade.highest_price or entry_price)
        and new_stop_loss <= active_trade.stop_loss
    ):
        return active_trade

    if new_stop_loss <= active_trade.stop_loss:
        return trade_manager.update_active_trade(
            symbol=active_trade.symbol, highest_price=highest_price
        )

    broker_stop_loss = new_stop_loss
    if order_manager.mode == "LIVE" and active_trade.stop_loss_order_id:
        try:
            broker_stop_loss = order_manager.trail_stop_loss_to_price(
                trading_symbol=active_trade.trading_symbol,
                exchange=active_trade.exchange,
                quantity=active_trade.remaining_quantity or active_trade.quantity,
                stop_loss_order_id=active_trade.stop_loss_order_id,
                current_stop_loss_price=active_trade.stop_loss,
                new_stop_loss_price=new_stop_loss,
            )
        except Exception as exc:
            logging.exception(
                "Failed to trail stop for %s", active_trade.trading_symbol
            )
            _print_order_error(
                f"Stop-loss modify failed for {active_trade.trading_symbol}: {exc}"
            )
            return trade_manager.update_active_trade(
                symbol=active_trade.symbol, highest_price=highest_price
            )

    updated = trade_manager.update_active_trade(
        symbol=active_trade.symbol,
        stop_loss=broker_stop_loss,
        highest_price=highest_price,
    )
    if updated is not None:
        _print_trail_update(updated, current_price)
    return updated
