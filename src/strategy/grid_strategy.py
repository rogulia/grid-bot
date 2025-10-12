"""Grid trading strategy with dual-sided hedging"""

import logging
import time
from typing import Optional, TYPE_CHECKING
from .position_manager import PositionManager
from ..exchange.bybit_client import BybitClient
from ..utils.logger import log_trade

if TYPE_CHECKING:
    from ..analytics.metrics_tracker import MetricsTracker


class GridStrategy:
    """Grid strategy for dual-sided LONG/SHORT trading"""

    def __init__(
        self,
        client: BybitClient,
        position_manager: PositionManager,
        config: dict,
        dry_run: bool = True,
        metrics_tracker: Optional['MetricsTracker'] = None,
        account_id: int = 0,
        account_logger: Optional[logging.Logger] = None
    ):
        """
        Initialize grid strategy

        Args:
            client: Bybit API client
            position_manager: Position manager instance
            config: Strategy configuration
            dry_run: Dry run mode (no real orders)
            metrics_tracker: Optional metrics tracker for analytics
            account_id: Account ID (for multi-account support)
            account_logger: Logger from TradingAccount (logs to per-account files)
        """
        # Account identification
        self.account_id = account_id
        self.id_str = f"{account_id:03d}"  # Zero-padded ID for files

        # Use account's logger (writes to per-account log files)
        # If not provided, fall back to default logger
        self.logger = account_logger or logging.getLogger("sol-trader.grid_strategy")

        self.client = client
        self.pm = position_manager
        self.dry_run = dry_run
        self.metrics_tracker = metrics_tracker

        # Load config - symbol is REQUIRED (no default!)
        if 'symbol' not in config:
            raise ValueError("Trading symbol must be specified in config - cannot use hardcoded default")
        self.symbol = config['symbol']

        self.category = config.get('category', 'linear')
        self.leverage = config.get('leverage', 100)
        self.initial_size_usd = config.get('initial_position_size_usd', 1.0)  # in USD
        self.grid_step_pct = config.get('grid_step_percent', 1.0)
        self.multiplier = config.get('averaging_multiplier', 2.0)
        self.tp_pct = config.get('take_profit_percent', 1.0)
        self.max_grid_levels = config.get('max_grid_levels_per_side', 10)

        # Risk management
        self.liquidation_buffer = config.get('liquidation_buffer', 0.8)
        # max_exposure: no limit by default (works with any balance!)
        self.max_exposure = config.get('max_total_exposure', float('inf'))
        # MM rate threshold for emergency close (configurable per account)
        self.mm_rate_threshold = config.get('mm_rate_threshold', 90.0)

        # Get instrument info from Bybit API
        self._load_instrument_info()

        # Throttling for log messages (to avoid spam)
        # Track last time each warning type was logged
        self._last_warning_time = {
            'max_exposure': 0,
            'mm_rate': 0,
            'liquidation': 0
        }
        self._warning_interval = 60  # Log warnings max once per 60 seconds

        # Throttling for MM Rate checks (to avoid API rate limits)
        self._cached_mm_rate: Optional[float] = None
        self._last_mm_rate_check_time: float = 0
        self._mm_rate_check_interval: float = 30.0  # Check every 30 seconds

        # Emergency stop flag - prevents any operations after critical failure
        self.emergency_stopped = False

        self.logger.info(
            f"[{self.symbol}] Grid strategy initialized - Symbol: {self.symbol}, "
            f"Leverage: {self.leverage}x, Grid: {self.grid_step_pct}%, "
            f"Multiplier: {self.multiplier}x, Dry Run: {self.dry_run}"
        )
        self.logger.info(
            f"[{self.symbol}] Instrument limits - Min: {self.min_qty}, Step: {self.qty_step}, Max: {self.max_qty}"
        )

    def _load_instrument_info(self):
        """Load instrument trading parameters from Bybit API"""
        try:
            response = self.client.session.get_instruments_info(
                category=self.category,
                symbol=self.symbol
            )

            if response.get('retCode') != 0:
                raise RuntimeError(
                    f"[{self.symbol}] Bybit API returned error: {response.get('retMsg', 'Unknown error')}"
                )

            result = response.get('result', {})
            if not result.get('list'):
                raise RuntimeError(
                    f"[{self.symbol}] Bybit API returned empty instrument list"
                )

            instrument = result['list'][0]
            lot_filter = instrument.get('lotSizeFilter')
            if not lot_filter:
                raise RuntimeError(
                    f"[{self.symbol}] Instrument does not have lotSizeFilter"
                )

            self.min_qty = float(lot_filter['minOrderQty'])
            self.qty_step = float(lot_filter['qtyStep'])
            self.max_qty = float(lot_filter['maxOrderQty'])

            self.logger.info(
                f"[{self.symbol}] Loaded instrument info for {self.symbol}: "
                f"min={self.min_qty}, step={self.qty_step}, max={self.max_qty}"
            )

        except Exception as e:
            self.logger.error(f"[{self.symbol}] Failed to load instrument info: {e}")
            raise RuntimeError(
                f"[{self.symbol}] Cannot start bot without instrument info"
            ) from e

    def _usd_to_qty(self, usd_amount: float, price: float) -> float:
        """
        Convert USD MARGIN amount to quantity of coins with leverage applied

        Args:
            usd_amount: Amount in USD (MARGIN, not position value!)
            price: Current price per coin

        Returns:
            Quantity of coins (rounded to instrument's step, >= minimum)

        Example:
            $1 margin × 100x leverage = $100 position value
            At $220/SOL: 100 ÷ 220 = 0.454 SOL
        """
        # Apply leverage: position_value = margin × leverage
        position_value_usd = usd_amount * self.leverage

        # Calculate raw quantity from position value
        raw_qty = position_value_usd / price

        # Round to instrument's qty step
        num_steps = round(raw_qty / self.qty_step)
        rounded_qty = num_steps * self.qty_step

        # Determine decimal places from qty_step to avoid floating point errors
        # For step=0.1 -> 1 decimal, step=1.0 -> 0 decimals, step=0.01 -> 2 decimals
        if self.qty_step >= 1:
            decimal_places = 0
        else:
            # Count decimal places in qty_step
            step_str = f"{self.qty_step:.10f}".rstrip('0')
            decimal_places = len(step_str.split('.')[-1]) if '.' in step_str else 0

        # Final rounding to exact precision (fixes 2.4000000000000004 -> 2.4)
        rounded_qty = round(rounded_qty, decimal_places)

        # Ensure at least minimum quantity
        final_qty = max(rounded_qty, self.min_qty)

        # Log if rounding significantly changed the amount
        actual_position_value = final_qty * price
        actual_margin = actual_position_value / self.leverage

        if abs(final_qty - raw_qty) / raw_qty > 0.1:  # More than 10% change
            self.logger.info(
                f"[{self.symbol}] Margin ${usd_amount:.2f} × {self.leverage}x = Position ${position_value_usd:.2f} "
                f"→ ${actual_position_value:.2f} (margin ${actual_margin:.2f}) "
                f"| qty: {raw_qty:.6f} → {final_qty:.6f} (step={self.qty_step})"
            )

        return final_qty

    def _qty_to_usd(self, qty: float, price: float) -> float:
        """
        Convert quantity of coins to USD amount

        Args:
            qty: Quantity of coins
            price: Current price per coin

        Returns:
            Amount in USD
        """
        return qty * price

    def is_stopped(self) -> bool:
        """
        Check if strategy is in emergency stop state

        Returns:
            True if emergency stop was triggered, False otherwise
        """
        return self.emergency_stopped

    def _create_emergency_stop_flag(self, reason: str):
        """
        Create emergency stop flag file to prevent bot restart

        This file signals systemd/supervisor not to restart the bot
        after emergency shutdown. User must manually remove file and
        fix issues before restarting.

        Args:
            reason: Reason for emergency stop
        """
        from datetime import datetime
        from pathlib import Path

        # Per-account emergency stop file: data/.{ID}_emergency_stop
        flag_file = Path(f"data/.{self.id_str}_emergency_stop")
        flag_file.parent.mkdir(parents=True, exist_ok=True)

        from ..utils.timezone import now_helsinki
        flag_content = {
            "timestamp": now_helsinki().isoformat(),
            "account_id": self.account_id,
            "symbol": self.symbol,
            "reason": reason
        }

        import json
        with open(flag_file, 'w') as f:
            json.dump(flag_content, f, indent=2)

        self.logger.critical(
            f"🚨 [{self.symbol}] EMERGENCY STOP FLAG CREATED: {flag_file}\n"
            f"   Account ID: {self.id_str}\n"
            f"   Reason: {reason}\n"
            f"   Bot will not restart automatically.\n"
            f"   Fix issues and remove file: rm {flag_file}"
        )

    def _restore_positions_from_order_history(self, side: str, exchange_qty: float, exchange_avg_price: float) -> bool:
        """
        Restore position history from exchange order history (PRIMARY method)

        This method reconstructs the position grid levels by analyzing order history:
        1. Gets recent order history from exchange
        2. Finds last reduceOnly order (position closure)
        3. All orders after that = current position orders
        4. Restores each order as separate position with grid_level

        Args:
            side: 'Buy' or 'Sell'
            exchange_qty: Current position quantity from exchange
            exchange_avg_price: Average entry price from exchange

        Returns:
            True if successfully restored, False otherwise

        Raises:
            RuntimeError: If restoration fails (fail-fast principle)
        """
        try:
            # Get order history from exchange
            order_history = self.client.get_order_history(
                symbol=self.symbol,
                category=self.category,
                limit=50
            )

            if not order_history:
                raise RuntimeError(
                    f"[{self.symbol}] Cannot restore {side} position: order history is empty. "
                    f"Exchange shows {exchange_qty} position but no order history available."
                )

            # Filter by position index (1=LONG/Buy, 2=SHORT/Sell)
            position_idx = 1 if side == 'Buy' else 2
            relevant_orders = [
                order for order in order_history
                if int(order.get('positionIdx', 0)) == position_idx
                and order.get('orderStatus') == 'Filled'  # Only filled orders
            ]

            if not relevant_orders:
                raise RuntimeError(
                    f"[{self.symbol}] Cannot restore {side} position: no filled orders found "
                    f"with positionIdx={position_idx}. Exchange shows {exchange_qty} position."
                )

            # Sort by creation time (oldest first)
            relevant_orders.sort(key=lambda x: int(x.get('createdTime', 0)))

            # Find last reduceOnly order (position closure)
            last_reduce_idx = -1
            for i, order in enumerate(relevant_orders):
                if order.get('reduceOnly', False):
                    last_reduce_idx = i

            # Orders after last closure = current position
            if last_reduce_idx >= 0:
                # Position was closed and reopened
                position_orders = relevant_orders[last_reduce_idx + 1:]
            else:
                # No closure found - all orders are part of current position
                position_orders = relevant_orders

            # Filter out reduceOnly orders (we only want position-opening orders)
            position_orders = [
                order for order in position_orders
                if not order.get('reduceOnly', False)
            ]

            if not position_orders:
                raise RuntimeError(
                    f"[{self.symbol}] Cannot restore {side} position: no position-opening orders found "
                    f"after last closure. Exchange shows {exchange_qty} position."
                )

            # Restore each order as separate position
            total_qty = 0.0
            for grid_level, order in enumerate(position_orders):
                qty = float(order.get('cumExecQty', 0))  # Executed quantity
                price = float(order.get('avgPrice', 0))   # Average execution price

                if qty == 0 or price == 0:
                    self.logger.warning(
                        f"[{self.symbol}] Skipping order {order.get('orderId')} with qty={qty}, price={price}"
                    )
                    continue

                self.pm.add_position(
                    side=side,
                    entry_price=price,
                    quantity=qty,
                    grid_level=grid_level
                )
                total_qty += qty

                self.logger.info(
                    f"📥 [{self.symbol}] Restored {side} Grid {grid_level}: "
                    f"{qty} @ ${price:.4f} (order: {order.get('orderId')})"
                )

            # Validate total quantity matches exchange
            qty_diff = abs(total_qty - exchange_qty)
            tolerance = 0.01  # Allow 0.01 difference for rounding

            if qty_diff > tolerance:
                # Quantity mismatch - this is critical error
                raise RuntimeError(
                    f"[{self.symbol}] Position restoration FAILED: quantity mismatch!\n"
                    f"  Exchange qty: {exchange_qty}\n"
                    f"  Restored qty: {total_qty}\n"
                    f"  Difference: {qty_diff}\n"
                    f"  Orders restored: {len(position_orders)}\n"
                    f"This indicates data corruption or API inconsistency."
                )

            self.logger.info(
                f"✅ [{self.symbol}] Successfully restored {side} position from order history: "
                f"{len(position_orders)} orders, total {total_qty} @ avg ${exchange_avg_price:.4f}"
            )
            return True

        except RuntimeError:
            # Re-raise RuntimeError as-is (fail-fast)
            raise
        except Exception as e:
            # Unexpected error - also fail fast
            raise RuntimeError(
                f"[{self.symbol}] Unexpected error restoring {side} position from order history: {e}"
            ) from e

    def sync_with_exchange(self, current_price: float):
        """
        Sync local position state with exchange reality
        - Check if positions exist on exchange
        - If not, open initial position
        - If yes, update TP order if needed

        Args:
            current_price: Current market price
        """
        # Block all operations if emergency stop was triggered
        if self.emergency_stopped:
            self.logger.warning(
                f"⚠️  [{self.symbol}] Sync blocked: bot in emergency stop state. "
                f"Fix issues and restart bot manually."
            )
            return

        self.logger.debug(f"🔄 [{self.symbol}] Syncing positions with exchange...")

        # Get available balance for initial position check
        available_balance = 0.0
        if not self.dry_run:
            try:
                balance_info = self.client.get_wallet_balance(account_type="UNIFIED")
                for account in balance_info.get('list', []):
                    if account.get('accountType') == 'UNIFIED':
                        available_balance = float(account.get('totalAvailableBalance', 0))
                        break
            except Exception as e:
                self.logger.error(f"[{self.symbol}] Failed to get balance: {e}")

        for side in ['Buy', 'Sell']:
            # Check if we have local position tracked
            local_qty = self.pm.get_total_quantity(side)

            # Check what's on exchange
            exchange_pos = self.client.get_active_position(self.symbol, side, self.category)

            if exchange_pos:
                exchange_qty = float(exchange_pos.get('size', 0))
                exchange_avg_price = float(exchange_pos.get('avgPrice', current_price))

                if local_qty == 0:
                    # Exchange has position but we don't track it -> restore from order history
                    self.logger.info(
                        f"📥 [{self.symbol}] Restoring {side} position from order history: "
                        f"{exchange_qty} @ ${exchange_avg_price:.4f}"
                    )

                    # Restore positions from exchange order history (PRIMARY method - fail-fast!)
                    self._restore_positions_from_order_history(
                        side=side,
                        exchange_qty=exchange_qty,
                        exchange_avg_price=exchange_avg_price
                    )

                    # Set TP order
                    self._update_tp_order(side)
                else:
                    # Both have position -> verify TP order exists and recover ID if lost
                    self.logger.debug(f"✅ [{self.symbol}] {side} position already synced")

                    # Check if we lost TP order ID after restart
                    current_tp_id = self.pm.get_tp_order_id(side)
                    if not current_tp_id and not self.dry_run:
                        # Try to recover TP order ID from exchange
                        try:
                            open_orders = self.client.get_open_orders(self.symbol, self.category)
                            position_idx = 1 if side == 'Buy' else 2

                            for order in open_orders:
                                # Find TP order: reduce-only and correct position index
                                is_reduce_only = order.get('reduceOnly', False)
                                order_pos_idx = int(order.get('positionIdx', 0))

                                if is_reduce_only and order_pos_idx == position_idx:
                                    order_id = order.get('orderId')
                                    order_price = order.get('price')
                                    self.logger.info(
                                        f"♻️  [{self.symbol}] Recovered {side} TP order ID: {order_id} "
                                        f"@ ${order_price}"
                                    )
                                    self.pm.set_tp_order_id(side, order_id)
                                    break
                            else:
                                # No TP order found - create one
                                self.logger.warning(
                                    f"⚠️  [{self.symbol}] No TP order found for {side} position - creating new one"
                                )
                                self._update_tp_order(side)
                        except Exception as e:
                            self.logger.error(
                                f"[{self.symbol}] Failed to recover TP order ID for {side}: {e}"
                            )

            else:
                # No position on exchange
                if local_qty > 0:
                    # Position was closed on exchange (TP execution, liquidation, or manual close)
                    avg_entry = self.pm.get_average_entry_price(side)

                    self.logger.warning(
                        f"⚠️  [{self.symbol}] {side} position closed on exchange! "
                        f"Entry: ${avg_entry:.4f}, Qty: {local_qty}, Current: ${current_price:.4f}"
                    )

                    # Get REAL PnL from exchange
                    actual_pnl = None
                    open_fee = 0.0
                    close_fee = 0.0
                    funding_fee = 0.0
                    close_reason = "Unknown (exchange close)"
                    close_timestamp = None  # Will be set from Bybit if available

                    if not self.dry_run:
                        try:
                            # Wait for exchange to process (increased for reliability)
                            import time
                            time.sleep(2)

                            # Get recent closed PnL records (limit=20 to find correct match)
                            closed_records = self.client.get_closed_pnl(self.symbol, limit=20)

                            record = None
                            record_index = -1
                            avg_exit = current_price  # Fallback to WebSocket price

                            if closed_records:
                                import time as time_module
                                current_time = time_module.time() * 1000  # milliseconds

                                # Find matching record by side, quantity, and recency
                                for idx, rec in enumerate(closed_records):
                                    rec_side = rec.get('side')
                                    rec_qty = float(rec.get('closedSize', 0))
                                    rec_time = int(rec.get('updatedTime', 0))

                                    # Check: same side, same qty (with tolerance), within 60 seconds
                                    time_diff_sec = (current_time - rec_time) / 1000

                                    if (rec_side == side and
                                        abs(rec_qty - local_qty) < 0.01 and
                                        time_diff_sec < 60):
                                        record = rec
                                        record_index = idx
                                        break

                                # Fallback to first record if no exact match
                                if record is None:
                                    self.logger.warning(
                                        f"⚠️  [{self.symbol}] No exact match in closed PnL (side={side}, qty={local_qty}), "
                                        f"using most recent record"
                                    )
                                    record = closed_records[0]
                                    record_index = 0

                            if record:
                                actual_pnl = float(record.get('closedPnl', 0))
                                open_fee = float(record.get('openFee', 0))
                                close_fee = float(record.get('closeFee', 0))
                                avg_entry = float(record.get('avgEntryPrice', 0))
                                avg_exit = float(record.get('avgExitPrice', 0))

                                # Extract timestamp from Bybit (updatedTime in milliseconds)
                                updated_time_ms = int(record.get('updatedTime', 0))
                                if updated_time_ms > 0:
                                    # Convert to datetime and format in Helsinki timezone
                                    from datetime import datetime
                                    import pytz
                                    from ..utils.timezone import format_helsinki

                                    utc_dt = datetime.fromtimestamp(updated_time_ms / 1000, tz=pytz.UTC)
                                    close_timestamp = format_helsinki(utc_dt)
                                else:
                                    close_timestamp = None  # Fallback to current time in log_trade

                                # Determine close reason using actual PnL (most reliable)
                                if actual_pnl > 0:
                                    price_diff_pct = abs((avg_exit - avg_entry) / avg_entry * 100)
                                    close_reason = f"Take Profit ({price_diff_pct:.2f}%)"
                                else:
                                    close_reason = "Stop-Loss or Liquidation"

                                self.logger.info(
                                    f"💰 [{self.symbol}] {side} CLOSED ON EXCHANGE (record #{record_index}): "
                                    f"Entry: ${avg_entry:.4f}, Exit: ${avg_exit:.4f}, "
                                    f"PnL=${actual_pnl:.4f} ({close_reason}), "
                                    f"Open Fee=${open_fee:.4f}, Close Fee=${close_fee:.4f}"
                                )
                        except Exception as e:
                            self.logger.error(f"Failed to get closed PnL from exchange: {e}")
                            raise RuntimeError(
                                f"[{self.symbol}] Cannot get closed PnL after position close - data integrity compromised"
                            ) from e

                    # Log the close to metrics
                    if self.metrics_tracker and actual_pnl is not None:
                        close_side_name = "Sell" if side == "Buy" else "Buy"
                        self.metrics_tracker.log_trade(
                            symbol=self.symbol,
                            side=close_side_name,
                            action="CLOSE",
                            price=avg_exit,  # Use real exit price from Bybit, not WebSocket price
                            quantity=local_qty,
                            reason=close_reason,
                            pnl=actual_pnl,
                            open_fee=open_fee,
                            close_fee=close_fee,
                            funding_fee=funding_fee,
                            timestamp=close_timestamp  # Use Bybit close time, not detection time
                        )

                    # NOW remove local tracking
                    self.pm.remove_all_positions(side)
                    self.pm.set_tp_order_id(side, None)

                # Check balance before opening initial position
                if not self.dry_run and available_balance < self.initial_size_usd:
                    reason = (
                        f"Insufficient balance to start trading: "
                        f"need ${self.initial_size_usd:.2f} MARGIN for initial position, "
                        f"available ${available_balance:.2f}"
                    )
                    self.logger.error(f"❌ [{self.symbol}] {reason}")

                    # Create emergency stop flag
                    self._create_emergency_stop_flag(reason)
                    self.emergency_stopped = True

                    # Stop bot - raise RuntimeError to prevent further operations
                    raise RuntimeError(f"[{self.symbol}] {reason}")

                # Open initial position
                initial_qty = self._usd_to_qty(self.initial_size_usd, current_price)
                self.logger.info(
                    f"🆕 [{self.symbol}] Opening initial {side} position: ${self.initial_size_usd} "
                    f"({initial_qty:.6f} {self.symbol}) @ ${current_price:.4f}"
                )

                if not self.dry_run:
                    try:
                        response = self.client.place_order(
                            symbol=self.symbol,
                            side=side,
                            qty=initial_qty,
                            order_type="Market",
                            category=self.category
                        )
                        self.logger.info(f"[{self.symbol}] Order response: {response}")
                    except Exception as e:
                        self.logger.error(f"[{self.symbol}] Failed to open {side} position: {e}")
                        continue

                # Track position
                self.pm.add_position(
                    side=side,
                    entry_price=current_price,
                    quantity=initial_qty,
                    grid_level=0
                )

                # Set TP order
                self._update_tp_order(side)

                # Log to metrics
                if self.metrics_tracker:
                    self.metrics_tracker.log_trade(
                        symbol=self.symbol,
                        side=side,
                        action="OPEN",
                        price=current_price,
                        quantity=initial_qty,
                        reason="Initial position (sync)",
                        pnl=None
                    )

        # Log MM Rate once per sync (every 60s) - critical safety metric
        if not self.dry_run:
            try:
                balance_info = self.client.get_wallet_balance(account_type="UNIFIED")
                for account in balance_info.get('list', []):
                    if account.get('accountType') == 'UNIFIED':
                        account_mm_rate_str = account.get('accountMMRate', '')
                        if account_mm_rate_str:
                            account_mm_rate = float(account_mm_rate_str) * 100
                            available_balance = float(account.get('totalAvailableBalance', 0))
                            self.logger.info(
                                f"[{self.symbol}] 💎 Balance: ${available_balance:.2f}, "
                                f"Account MM Rate: {account_mm_rate:.4f}%"
                            )
                        break
            except Exception as e:
                self.logger.warning(f"[{self.symbol}] Could not log MM Rate: {e}")

    def on_execution(self, exec_data: dict):
        """
        Handle execution event from Private WebSocket

        Processes real-time execution data from Bybit. Replaces polling-based
        detection from sync_with_exchange().

        Args:
            exec_data: Execution data from Bybit WebSocket
        """
        try:
            # Validate required fields (FAIL-FAST)
            required_fields = ['symbol', 'side', 'execPrice', 'execQty', 'execTime']
            missing = [f for f in required_fields if f not in exec_data]
            if missing:
                raise ValueError(f"Execution event missing required fields {missing}: {exec_data}")

            symbol = exec_data['symbol']
            side = exec_data['side']  # Buy or Sell (order side, not position side)
            exec_price = float(exec_data['execPrice'])
            exec_qty = float(exec_data['execQty'])
            exec_time_ms = int(exec_data['execTime'])
            closed_size = float(exec_data.get('closedSize', 0))
            closed_pnl = float(exec_data.get('execPnl', 0))  # Use execPnl, not closedPnl!
            exec_fee = float(exec_data.get('execFee', 0))

            # Extract execution type and order details for close reason detection
            exec_type = exec_data.get('execType', '')
            order_type = exec_data.get('orderType', '')
            stop_order_type = exec_data.get('stopOrderType', '')
            order_link_id = exec_data.get('orderLinkId', '')

            # Log execution details for debugging
            self.logger.debug(
                f"🔍 [{symbol}] Execution: execType={exec_type}, orderType={order_type}, "
                f"stopOrderType={stop_order_type}, execPnl={closed_pnl:.4f}, "
                f"closedSize={closed_size}, orderLinkId={order_link_id}"
            )

            # Check if this is a position close
            is_close = closed_size > 0 or closed_pnl != 0

            if not is_close:
                # Position open/add - just log
                self.logger.debug(
                    f"📝 [{symbol}] {side} OPEN: qty={exec_qty} price={exec_price}"
                )
                return

            # POSITION CLOSE - process with real data from exchange
            self.logger.info(
                f"💰 [{symbol}] {side} CLOSED via WebSocket: "
                f"qty={closed_size} price={exec_price} pnl=${closed_pnl:.4f}"
            )

            # Convert timestamp to Helsinki timezone
            from datetime import datetime
            import pytz
            from ..utils.timezone import format_helsinki

            utc_dt = datetime.fromtimestamp(exec_time_ms / 1000, tz=pytz.UTC)
            close_timestamp = format_helsinki(utc_dt)

            # Determine which position side was closed
            # Order side 'Sell' closes LONG, 'Buy' closes SHORT
            closed_position_side = 'Buy' if side == 'Sell' else 'Sell'

            # Get average entry price for percentage calculation
            avg_entry = self.pm.get_average_entry_price(closed_position_side)
            if not avg_entry:
                avg_entry = exec_price  # Fallback if position not tracked

            # Determine close reason using execType and other fields
            if exec_type == 'BustTrade':
                close_reason = "Liquidation"
            elif exec_type == 'AdlTrade':
                close_reason = "ADL (Auto-Deleveraging)"
            elif exec_type == 'Funding':
                # Funding fee - ignore, not a position close
                self.logger.debug(f"🔔 [{symbol}] Funding fee event, ignoring")
                return
            elif exec_type == 'Trade':
                # Regular trade - need to distinguish TP vs SL
                if stop_order_type in ['StopLoss', 'TrailingStop']:
                    close_reason = "Stop-Loss"
                elif stop_order_type == 'TakeProfit':
                    close_reason = "Take Profit (WebSocket)"
                elif order_type == 'Limit' and closed_pnl > 0:
                    # Our TP orders are Limit orders with positive PnL
                    price_diff_pct = abs((exec_price - avg_entry) / avg_entry * 100)
                    close_reason = f"Take Profit ({price_diff_pct:.2f}%)"
                elif closed_pnl < 0:
                    close_reason = "Stop-Loss or Manual Close"
                else:
                    close_reason = "Manual Close"
            else:
                # Unknown execType (Delivery, Settle, BlockTrade, etc.)
                close_reason = f"Unknown ({exec_type})"

            # Log to metrics tracker
            if self.metrics_tracker:
                self.metrics_tracker.log_trade(
                    symbol=symbol,
                    side=side,  # Order side (Sell for LONG close, Buy for SHORT close)
                    action="CLOSE",
                    price=exec_price,  # Real execution price from exchange
                    quantity=closed_size,
                    reason=close_reason,
                    pnl=closed_pnl,  # Real PnL from exchange
                    open_fee=0.0,  # Not provided in execution event
                    close_fee=exec_fee,
                    funding_fee=0.0,  # Not provided in execution event
                    timestamp=close_timestamp  # Real timestamp from exchange
                )

            # 🚨 CRITICAL: Check if this was a liquidation or ADL
            if close_reason == "Liquidation" or close_reason.startswith("ADL"):
                reason = (
                    f"Position liquidated! {closed_position_side} position on {symbol} was forcibly closed by exchange. "
                    f"Liquidation PnL: ${closed_pnl:.2f}. Account balance critically low. "
                    f"Review risk management and account balance before restarting."
                )

                self.logger.error(f"🚨 [{symbol}] LIQUIDATION DETECTED: {reason}")

                # Create emergency stop flag to prevent restart
                self._create_emergency_stop_flag(reason)
                self.emergency_stopped = True

                # Update local state - remove closed positions
                self.pm.remove_all_positions(closed_position_side)
                self.pm.set_tp_order_id(closed_position_side, None)

                # STOP BOT IMMEDIATELY - this is critical!
                raise RuntimeError(f"[{symbol}] {reason}")

            # Update local state - remove closed positions
            self.pm.remove_all_positions(closed_position_side)
            self.pm.set_tp_order_id(closed_position_side, None)

            # Reopen initial position if needed
            # Get current price (could be from WebSocket or this execution price)
            current_price = exec_price

            # Check if we should reopen
            if not self.emergency_stopped and not self.dry_run:
                try:
                    # Open new initial position at current market
                    self.logger.info(
                        f"🆕 [{symbol}] Reopening {closed_position_side} position after TP..."
                    )

                    qty = self._usd_to_qty(self.initial_size_usd, current_price)

                    order_response = self.client.place_order(
                        symbol=symbol,
                        side=closed_position_side,
                        order_type="Market",
                        qty=qty,
                        category=self.category
                    )

                    if order_response:
                        self.pm.add_position(closed_position_side, current_price, qty, 0)
                        self.logger.info(
                            f"✅ [{symbol}] Reopened {closed_position_side}: "
                            f"{qty} @ ${current_price:.4f}"
                        )

                        # Update TP order
                        self._update_tp_order(closed_position_side)

                        # Log to metrics
                        if self.metrics_tracker:
                            self.metrics_tracker.log_trade(
                                symbol=symbol,
                                side=closed_position_side,
                                action="OPEN",
                                price=current_price,
                                quantity=qty,
                                reason="Reopen after TP"
                            )
                except Exception as e:
                    self.logger.error(f"❌ [{symbol}] Failed to reopen position: {e}")

        except Exception as e:
            self.logger.error(
                f"❌ [{symbol}] Error processing execution event: {e}",
                exc_info=True
            )

    def _update_tp_order(self, side: str):
        """
        Update Take Profit order for a side (cancel old and place new)

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)
        """
        # Get current average entry and total quantity
        avg_entry = self.pm.get_average_entry_price(side)
        total_qty = self.pm.get_total_quantity(side)

        if not avg_entry or total_qty == 0:
            self.logger.warning(f"[{self.symbol}] No {side} position to set TP for")
            return

        # Calculate TP price from average entry
        if side == 'Buy':  # LONG: TP above entry
            tp_price = avg_entry * (1 + self.tp_pct / 100)
            tp_side = 'Sell'  # Close with Sell
        else:  # SHORT: TP below entry
            tp_price = avg_entry * (1 - self.tp_pct / 100)
            tp_side = 'Buy'  # Close with Buy

        # Cancel old TP order if exists
        old_tp_id = self.pm.get_tp_order_id(side)

        if old_tp_id and not self.dry_run:
            # We know the ID - cancel it directly
            self.client.cancel_order(self.symbol, old_tp_id, self.category)
            self.logger.debug(f"[{self.symbol}] Cancelled old TP order: {old_tp_id}")

        elif not old_tp_id and not self.dry_run:
            # We DON'T know the ID (e.g., after restart)
            # Find and cancel ALL old TP orders for this side to avoid duplicates
            try:
                open_orders = self.client.get_open_orders(self.symbol, self.category)
                position_idx = 1 if side == 'Buy' else 2

                cancelled_count = 0
                for order in open_orders:
                    is_reduce_only = order.get('reduceOnly', False)
                    order_pos_idx = int(order.get('positionIdx', 0))

                    if is_reduce_only and order_pos_idx == position_idx:
                        order_id = order.get('orderId')
                        order_price = order.get('price')
                        self.logger.info(
                            f"🗑️  [{self.symbol}] Cancelling old {side} TP: {order_id} @ ${order_price}"
                        )
                        self.client.cancel_order(self.symbol, order_id, self.category)
                        cancelled_count += 1

                if cancelled_count > 0:
                    self.logger.info(
                        f"[{self.symbol}] Cancelled {cancelled_count} old TP order(s) for {side}"
                    )
            except Exception as e:
                self.logger.error(
                    f"[{self.symbol}] Failed to cancel old TP orders for {side}: {e}"
                )

        # Calculate positionIdx for the position we're closing
        # In Hedge Mode: positionIdx indicates which position to close
        # Buy position (LONG) = 1, Sell position (SHORT) = 2
        position_idx = 1 if side == 'Buy' else 2

        # Place new TP order
        if not self.dry_run:
            new_tp_id = self.client.place_tp_order(
                symbol=self.symbol,
                side=tp_side,
                qty=total_qty,
                tp_price=tp_price,
                category=self.category,
                position_idx=position_idx  # Explicitly specify which position to close
            )
            self.pm.set_tp_order_id(side, new_tp_id)
        else:
            self.logger.info(
                f"[{self.symbol}] [DRY RUN] Would place TP: {tp_side} {total_qty} @ ${tp_price:.4f}"
            )

        self.logger.info(
            f"[{self.symbol}] 🎯 Updated TP for {side}: {tp_side} {total_qty} @ ${tp_price:.4f} "
            f"(avg entry: ${avg_entry:.4f})"
        )

    def on_price_update(self, current_price: float):
        """
        Handle price update and execute strategy logic

        Args:
            current_price: Current market price
        """
        # Block all operations if emergency stop was triggered
        if self.emergency_stopped:
            return

        try:
            # Check risk limits first
            if not self._check_risk_limits(current_price):
                return

            # Check for grid entries (averaging)
            self._check_grid_entries(current_price)

            # Take profit is now handled via Private WebSocket execution stream
            # (on_execution() method) - NO FALLBACKS!
            # Old polling-based _check_take_profit() is DISABLED

        except RuntimeError as e:
            # RuntimeError indicates critical failure - re-raise to stop bot
            self.logger.error(f"[{self.symbol}] Critical error in strategy execution: {e}", exc_info=True)
            raise
        except Exception as e:
            # Other exceptions are logged but don't stop the bot
            self.logger.error(f"[{self.symbol}] Error in strategy execution: {e}", exc_info=True)

    def _check_grid_entries(self, current_price: float):
        """Check if we should add positions at grid levels"""

        # Check LONG side
        if self.pm.get_position_count('Buy') < self.max_grid_levels:
            if self._should_add_position('Buy', current_price):
                self._execute_grid_order('Buy', current_price)

        # Check SHORT side
        if self.pm.get_position_count('Sell') < self.max_grid_levels:
            if self._should_add_position('Sell', current_price):
                self._execute_grid_order('Sell', current_price)

    def _should_add_position(self, side: str, current_price: float) -> bool:
        """
        Check if we should add a position at this price level

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)
            current_price: Current market price

        Returns:
            True if should add position
        """
        last_entry = (self.pm.last_long_entry if side == 'Buy'
                     else self.pm.last_short_entry)

        if last_entry is None:
            return False  # Will be opened in main.py initially

        # Calculate price change percentage
        if side == 'Buy':
            # LONG: add when price goes DOWN by grid_step_pct
            price_change_pct = (last_entry - current_price) / last_entry * 100
        else:
            # SHORT: add when price goes UP by grid_step_pct
            price_change_pct = (current_price - last_entry) / last_entry * 100

        # Check if we hit the grid step
        if price_change_pct >= self.grid_step_pct:
            # Don't log here - will log in _execute_grid_order() if order actually places
            # This prevents log spam when conditions are met but order doesn't execute (e.g., insufficient balance)
            return True

        return False

    def _execute_grid_order(self, side: str, current_price: float):
        """
        Execute grid order (add to position)

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)
            current_price: Current market price
        """
        try:
            # Calculate MARGIN used (not position value)
            current_qty = self.pm.get_total_quantity(side)

            if current_qty > 0:
                # Position value = qty × price
                position_value = self._qty_to_usd(current_qty, current_price)
                # MARGIN = position value / leverage
                current_margin_usd = position_value / self.leverage
            else:
                current_margin_usd = 0

            # Calculate new margin to add
            if current_margin_usd == 0:
                # First position: use initial size
                new_margin_usd = self.initial_size_usd
            else:
                # Classic martingale: multiply LAST position size by multiplier
                # With multiplier=2.0: Grid 1 adds $2, Grid 2 adds $4, Grid 3 adds $8, etc.
                # Sequence: 1, 2, 4, 8, 16... (each position = previous × multiplier)
                positions = self.pm.long_positions if side == 'Buy' else self.pm.short_positions
                if not positions:
                    raise RuntimeError(
                        f"[{self.symbol}] Inconsistent state: current_margin > 0 but no positions found for {side}"
                    )

                last_position = positions[-1]
                last_position_value = last_position.quantity * current_price
                last_position_margin = last_position_value / self.leverage
                new_margin_usd = last_position_margin * self.multiplier

            # Convert margin to qty (with leverage applied)
            new_size = self._usd_to_qty(new_margin_usd, current_price)
            grid_level = self.pm.get_position_count(side)

            # Check if we have enough available balance (use exchange data directly)
            if not self.dry_run:
                try:
                    balance_info = self.client.get_wallet_balance(account_type="UNIFIED")
                    if balance_info and 'list' in balance_info:
                        for account in balance_info['list']:
                            if account.get('accountType') == 'UNIFIED':
                                available_balance = float(account.get('totalAvailableBalance', 0))

                                # Check if we have enough balance for this order
                                if new_margin_usd > available_balance:
                                    # Throttle warning to avoid spam (max once per minute)
                                    current_time = time.time()
                                    warning_key = f'insufficient_balance_{side}'
                                    if warning_key not in self._last_warning_time:
                                        self._last_warning_time[warning_key] = 0

                                    if current_time - self._last_warning_time[warning_key] >= self._warning_interval:
                                        self.logger.warning(
                                            f"⚠️ [{self.symbol}] Insufficient balance for {side} averaging: "
                                            f"need ${new_margin_usd:.2f} MARGIN, available ${available_balance:.2f}"
                                        )
                                        self._last_warning_time[warning_key] = current_time
                                    return  # Don't place order
                                break
                except Exception as e:
                    self.logger.error(f"[{self.symbol}] Failed to check balance before order: {e}")
                    return  # Don't place order if we can't verify balance

            self.logger.info(
                f"[{self.symbol}] Executing grid order: {side} ${new_margin_usd:.2f} MARGIN ({new_size:.6f}) "
                f"@ ${current_price:.4f} (level {grid_level})"
            )

            # Execute order (or simulate)
            if not self.dry_run:
                response = self.client.place_order(
                    symbol=self.symbol,
                    side=side,
                    qty=new_size,
                    order_type="Market",
                    category=self.category
                )
                self.logger.info(f"[{self.symbol}] Order response: {response}")

            # Add to position manager
            self.pm.add_position(
                side=side,
                entry_price=current_price,
                quantity=new_size,
                grid_level=grid_level
            )

            # Log trade
            log_trade(
                self.logger,
                side=side,
                price=current_price,
                qty=new_size,
                reason=f"Grid level {grid_level}",
                dry_run=self.dry_run,
                account_prefix=f"{self.id_str}_"
            )

            # Log to metrics tracker
            if self.metrics_tracker:
                self.metrics_tracker.log_trade(
                    symbol=self.symbol,
                    side=side,
                    action="OPEN",
                    price=current_price,
                    quantity=new_size,
                    reason=f"Grid level {grid_level}",
                    pnl=None
                )

            # Update TP order with new average entry
            self._update_tp_order(side)

        except Exception as e:
            self.logger.error(f"[{self.symbol}] Failed to execute grid order: {e}")

    def _check_take_profit(self, current_price: float):
        """Check if we should take profit on any side"""

        # Check LONG take profit
        if self.pm.long_positions:
            avg_long = self.pm.get_average_entry_price('Buy')
            if avg_long:
                price_change_pct = (current_price - avg_long) / avg_long * 100

                if price_change_pct >= self.tp_pct:
                    self._execute_take_profit('Buy', current_price, price_change_pct)

        # Check SHORT take profit
        if self.pm.short_positions:
            avg_short = self.pm.get_average_entry_price('Sell')
            if avg_short:
                price_change_pct = (avg_short - current_price) / avg_short * 100

                if price_change_pct >= self.tp_pct:
                    self._execute_take_profit('Sell', current_price, price_change_pct)

    def _execute_take_profit(self, side: str, current_price: float, profit_pct: float):
        """
        Execute take profit order

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)
            current_price: Current market price
            profit_pct: Profit percentage
        """
        try:
            total_qty = self.pm.get_total_quantity(side)
            pnl = self.pm.calculate_pnl(current_price, side)

            self.logger.info(
                f"[{self.symbol}] 💰 Take Profit triggered on {side}: {profit_pct:.2f}% gain, "
                f"PnL: ${pnl:.2f}"
            )

            # NOTE: TP limit order already executed automatically, position is closed
            # Just clean up our local state

            # Clear TP order ID (it was already filled)
            self.pm.set_tp_order_id(side, None)

            # Remove local position tracking
            self.pm.remove_all_positions(side)

            # Получить реальные данные о закрытой позиции с биржи (REQUIRED - no fallback!)
            if not self.dry_run:
                # Подождать 2 секунды для обработки биржей
                import time
                time.sleep(2)

                # Получить последнюю запись closed PnL с биржи
                closed_records = self.client.get_closed_pnl(self.symbol, limit=1)
                if not closed_records:
                    raise RuntimeError(
                        f"[{self.symbol}] No closed PnL records found after TP execution"
                    )

                record = closed_records[0]
                if 'closedPnl' not in record:
                    raise RuntimeError(
                        f"[{self.symbol}] Closed PnL record missing 'closedPnl' field"
                    )

                actual_pnl = float(record['closedPnl'])
                open_fee = float(record.get('openFee', 0))
                close_fee = float(record.get('closeFee', 0))
                funding_fee = 0.0

                self.logger.info(
                    f"[{self.symbol}] 📊 Exchange confirmed: "
                    f"PnL=${actual_pnl:.4f}, "
                    f"Open Fee=${open_fee:.4f}, "
                    f"Close Fee=${close_fee:.4f}"
                )
            else:
                # In dry run, use calculated PnL
                actual_pnl = pnl
                open_fee = 0.0
                close_fee = 0.0
                funding_fee = 0.0

            # Log trade (closing)
            close_side = "Sell" if side == "Buy" else "Buy"
            log_trade(
                self.logger,
                side=close_side,
                price=current_price,
                qty=total_qty,
                reason=f"Take Profit ({profit_pct:.2f}%, PnL: ${pnl:.2f})",
                dry_run=self.dry_run,
                account_prefix=f"{self.id_str}_"
            )

            # Log to metrics tracker with real PnL and fees from exchange
            if self.metrics_tracker:
                self.metrics_tracker.log_trade(
                    symbol=self.symbol,
                    side=close_side,
                    action="CLOSE",
                    price=current_price,
                    quantity=total_qty,
                    reason=f"Take Profit ({profit_pct:.2f}%)",
                    pnl=actual_pnl,  # Real PnL from exchange
                    open_fee=open_fee,  # Real opening fee
                    close_fee=close_fee,  # Real closing fee
                    funding_fee=funding_fee  # Funding fees (if available)
                )

            # 🔄 ВАЖНО: Сразу открыть новую начальную позицию
            # Convert USD to qty
            initial_qty = self._usd_to_qty(self.initial_size_usd, current_price)

            self.logger.info(
                f"[{self.symbol}] ♻️  Переоткрываю начальную {side} позицию: ${self.initial_size_usd} "
                f"({initial_qty:.6f} {self.symbol}) @ ${current_price:.4f}"
            )

            # Открыть новый ордер
            if not self.dry_run:
                response = self.client.place_order(
                    symbol=self.symbol,
                    side=side,
                    qty=initial_qty,
                    order_type="Market",
                    category=self.category
                )
                self.logger.info(f"[{self.symbol}] Reopen order response: {response}")

            # Добавить новую начальную позицию в manager
            self.pm.add_position(
                side=side,
                entry_price=current_price,
                quantity=initial_qty,
                grid_level=0
            )

            log_trade(
                self.logger,
                side=side,
                price=current_price,
                qty=initial_qty,
                reason="Reopen after Take Profit",
                dry_run=self.dry_run,
                account_prefix=f"{self.id_str}_"
            )

            # Log to metrics tracker
            if self.metrics_tracker:
                self.metrics_tracker.log_trade(
                    symbol=self.symbol,
                    side=side,
                    action="OPEN",
                    price=current_price,
                    quantity=initial_qty,
                    reason="Reopen after TP",
                    pnl=None
                )

            # Place new TP order for reopened position
            self._update_tp_order(side)

            self.logger.info(
                f"[{self.symbol}] ✅ Новая {side} позиция открыта: {initial_qty} @ ${current_price:.4f}"
            )

        except Exception as e:
            self.logger.error(f"[{self.symbol}] Failed to execute take profit: {e}")

    def _check_risk_limits(self, current_price: float) -> bool:
        """
        Check risk limits (Account MM Rate only - balance checked before averaging)

        For hedged positions (LONG+SHORT), monitors Account Maintenance Margin Rate
        to detect liquidation risk. Checks are throttled to every 30 seconds to avoid
        API rate limits (balance changes only on execution events, not price ticks).

        Args:
            current_price: Current market price

        Returns:
            True if safe to continue, False if risk limits hit
        """
        # In dry run, use mock value
        if self.dry_run:
            account_mm_rate = 0.17  # Mock: safe value
        else:
            # Throttled check: only fetch from API every 30 seconds
            current_time = time.time()
            if current_time - self._last_mm_rate_check_time >= self._mm_rate_check_interval:
                # Fetch fresh data from exchange
                balance_info = self.client.get_wallet_balance(account_type="UNIFIED")
                if balance_info and 'list' in balance_info:
                    for account in balance_info['list']:
                        if account.get('accountType') == 'UNIFIED':
                            # accountMMRate = Account Maintenance Margin Rate (%)
                            # This is the ONLY metric that matters for hedged positions!
                            account_mm_rate_str = account.get('accountMMRate', '')
                            if account_mm_rate_str and account_mm_rate_str != '':
                                # Convert to percentage (Bybit returns as decimal, e.g. "0.0017" = 0.17%)
                                self._cached_mm_rate = float(account_mm_rate_str) * 100
                            else:
                                self._cached_mm_rate = None
                            break
                else:
                    # API failure - fail-fast (no fallbacks!)
                    raise RuntimeError(
                        f"[{self.symbol}] Failed to get wallet balance from exchange - cannot check risk limits"
                    )

                # Update last check time
                self._last_mm_rate_check_time = current_time

            # Use cached value between checks
            account_mm_rate = self._cached_mm_rate

        # Check Account Maintenance Margin Rate (for hedged positions)
        # This is the CORRECT way to check liquidation risk for LONG+SHORT strategy
        if not self.dry_run and account_mm_rate is not None:
            # Log current MM rate for monitoring (throttled to avoid spam)
            if account_mm_rate > 50.0:  # Warning if > 50%
                current_time = time.time()
                if current_time - self._last_warning_time['mm_rate'] >= self._warning_interval:
                    self.logger.warning(
                        f"⚠️ [{self.symbol}] Account Maintenance Margin Rate: {account_mm_rate:.2f}% (caution!)"
                    )
                    self._last_warning_time['mm_rate'] = current_time

            # Emergency close if >= threshold (use instance variable set in __init__)
            if account_mm_rate >= self.mm_rate_threshold:
                self.logger.error(
                    f"💥 CRITICAL: Account MM Rate {account_mm_rate:.2f}% >= {self.mm_rate_threshold}%! "
                    f"EMERGENCY CLOSE ALL POSITIONS!"
                )

                # Close ALL positions (both LONG and SHORT)
                for side in ['Buy', 'Sell']:
                    total_qty = self.pm.get_total_quantity(side)
                    if total_qty > 0:
                        self._emergency_close(
                            side, current_price,
                            f"Account MM Rate {account_mm_rate:.2f}% >= {self.mm_rate_threshold}%"
                        )

                # Set emergency stop flag to prevent further operations
                self.emergency_stopped = True

                # Create emergency stop flag file to prevent systemd restart
                reason = (
                    f"Account Maintenance Margin Rate {account_mm_rate:.2f}% "
                    f"reached critical level (>= {self.mm_rate_threshold}%). All positions closed. "
                    f"Review account and fix issues before restarting."
                )
                self._create_emergency_stop_flag(reason)

                # STOP BOT - this is critical!
                raise RuntimeError(f"[{self.symbol}] {reason}")

        # Available balance is checked in _execute_grid_order() before averaging
        # (balance changes only on execution events, not price ticks)
        return True

    def _emergency_close(self, side: str, current_price: float, reason: str):
        """Emergency close all positions for a side"""
        try:
            total_qty = self.pm.get_total_quantity(side)

            if not self.dry_run and total_qty > 0:
                self.client.close_position(
                    symbol=self.symbol,
                    side=side,
                    qty=total_qty,
                    category=self.category
                )

            # Calculate PnL before removing positions
            pnl = self.pm.calculate_pnl(current_price, side)

            self.pm.remove_all_positions(side)

            close_side = "Sell" if side == "Buy" else "Buy"
            log_trade(
                self.logger,
                side=close_side,
                price=current_price,
                qty=total_qty,
                reason=f"EMERGENCY: {reason}",
                dry_run=self.dry_run,
                account_prefix=f"{self.id_str}_"
            )

            # Log to metrics tracker
            if self.metrics_tracker:
                self.metrics_tracker.log_trade(
                    symbol=self.symbol,
                    side=close_side,
                    action="CLOSE",
                    price=current_price,
                    quantity=total_qty,
                    reason=f"EMERGENCY: {reason}",
                    pnl=pnl
                )

        except Exception as e:
            self.logger.error(f"[{self.symbol}] Failed to emergency close: {e}")
