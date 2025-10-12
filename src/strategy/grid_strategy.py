"""Grid trading strategy with dual-sided hedging"""

import logging
import time
import threading
from typing import Optional, TYPE_CHECKING
from .position_manager import PositionManager
from ..exchange.bybit_client import BybitClient
from ..utils.logger import log_trade
from ..utils.balance_manager import BalanceManager
from ..utils.timestamp_converter import TimestampConverter
from ..utils.emergency_stop_manager import EmergencyStopManager
from config.constants import TradingConstants, LogMessages

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
        account_logger: Optional[logging.Logger] = None,
        balance_manager: Optional['BalanceManager'] = None
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
            balance_manager: Optional shared balance manager (for multi-strategy accounts)
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

        # Balance manager (use shared if provided, otherwise create new)
        if balance_manager:
            self.balance_manager = balance_manager
        else:
            # Create new balance manager (for tests and standalone use)
            self.balance_manager = BalanceManager(
                client,
                cache_ttl_seconds=TradingConstants.BALANCE_CACHE_TTL_SEC
            )

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
        # MM rate threshold for emergency close (configurable per account)
        self.mm_rate_threshold = config.get('mm_rate_threshold', 90.0)

        # Get instrument info from Bybit API
        self._load_instrument_info()

        # Throttling for log messages (to avoid spam)
        # Track last time each warning type was logged
        self._last_warning_time = {
            'mm_rate': 0,
            'insufficient_balance': 0
        }
        self._warning_interval = TradingConstants.WARNING_LOG_INTERVAL_SEC

        # Emergency stop flag - prevents any operations after critical failure
        self.emergency_stopped = False

        # Thread safety locks (WebSocket callbacks run in separate threads)
        self._tp_orders_lock = threading.Lock()
        self._pnl_lock = threading.Lock()

        # Track cumulative realized PnL for each side (to calculate delta on position close)
        self._last_cum_realised_pnl = {'Buy': 0.0, 'Sell': 0.0}

        # Track TP order IDs for each side (updated via Order WebSocket)
        self._tp_orders = {'Buy': None, 'Sell': None}

        # Track current price from WebSocket (eliminates need for REST get_ticker calls)
        self.current_price: float = 0.0

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

    def _open_initial_position(self, side: str, current_price: float):
        """
        Open initial position for a side

        Args:
            side: 'Buy' for LONG or 'Sell' for SHORT
            current_price: Current market price

        Raises:
            Exception: If order placement fails (in live mode)
        """
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
                raise

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
                reason="Initial position",
                pnl=None
            )

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
        emergency_manager = EmergencyStopManager(logger=self.logger)
        emergency_manager.create(
            account_id=self.account_id,
            symbol=self.symbol,
            reason=reason
        )

    def sync_with_exchange(self, current_price: float):
        """
        Sync local position state with exchange reality

        **WebSocket-First Architecture:**
        - Position WebSocket restores positions automatically on connect (snapshot)
        - Position WebSocket keeps local tracking in sync in real-time (updates)
        - This method checks if TP orders exist and opens initial positions if needed
        - No REST API calls for monitoring - all data from WebSocket

        **Position Restoration Flow:**
        1. Bot starts, Position WebSocket connects
        2. WebSocket sends snapshot of all open positions
        3. on_position_update() detects missing local positions and restores them
        4. State file updates automatically via pm.add_position()

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

        self.logger.debug(LogMessages.SYNC_START.format(symbol=self.symbol))

        # Get available balance for initial position check (using BalanceManager)
        available_balance = 0.0
        if not self.dry_run:
            try:
                available_balance = self.balance_manager.get_available_balance()
            except Exception as e:
                self.logger.error(f"[{self.symbol}] Failed to get balance: {e}")

        for side in ['Buy', 'Sell']:
            # Check if we have local position tracked (updated by Position WebSocket)
            local_qty = self.pm.get_total_quantity(side)

            if local_qty > 0:
                # We have local position tracked by Position WebSocket
                # Position WebSocket keeps this in sync - no need to check exchange
                # Only verify TP order exists (tracked by Order WebSocket)
                with self._tp_orders_lock:
                    tp_order_id = self._tp_orders.get(side) or self.pm.get_tp_order_id(side)

                if not tp_order_id and not self.dry_run:
                    # TP order missing - create one
                    self.logger.warning(
                        f"⚠️  [{self.symbol}] TP order missing for {side} position (qty={local_qty}) - creating new one"
                    )
                    self._update_tp_order(side)
                else:
                    self.logger.debug(f"✅ [{self.symbol}] {side} position synced (qty={local_qty}, TP order tracked)")

            else:
                # No local position tracked
                # Note: Position WebSocket sends snapshot on connect, so any existing positions
                # will be restored via on_position_update() callback automatically.
                # If we reach here, it means no position exists on exchange.
                # Open initial position:

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

        # Log MM Rate once per sync (every 60s) - critical safety metric (using BalanceManager)
        if not self.dry_run:
            try:
                available_balance = self.balance_manager.get_available_balance()
                account_mm_rate = self.balance_manager.get_mm_rate()

                if account_mm_rate is not None:
                    self.logger.info(
                        LogMessages.BALANCE_UPDATE.format(
                            symbol=self.symbol,
                            balance=available_balance,
                            mm_rate=account_mm_rate
                        )
                    )
                else:
                    self.logger.info(
                        LogMessages.BALANCE_ONLY.format(
                            symbol=self.symbol,
                            balance=available_balance
                        )
                    )
            except Exception as e:
                self.logger.warning(f"[{self.symbol}] Could not log balance/MM Rate: {e}")

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
            close_timestamp = TimestampConverter.exchange_ms_to_helsinki(exec_time_ms)

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

    def _calculate_honest_tp_price(self, side: str, avg_entry: float) -> float:
        """
        Calculate honest TP price that accounts for all fees (opens + averagings + close)

        Formula:
        - Total fees = (num_positions × taker_fee) + maker_fee
        - Honest TP percent = tp_percent + total_fees_percent

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)
            avg_entry: Average entry price

        Returns:
            TP price adjusted for fees to achieve true profit target
        """
        # Get number of positions (each opened with market order = taker fee)
        num_positions = self.pm.get_position_count(side)

        # Calculate total fees as percentage
        # Opens/averages: each position × taker fee (0.055%)
        # Close: 1 × maker fee (0.020% for limit/TP orders)
        open_fees_pct = num_positions * TradingConstants.BYBIT_TAKER_FEE_RATE * 100
        close_fee_pct = TradingConstants.BYBIT_MAKER_FEE_RATE * 100
        total_fees_pct = open_fees_pct + close_fee_pct

        # Honest TP = user's TP + fees to cover
        honest_tp_pct = self.tp_pct + total_fees_pct

        # Calculate TP price
        if side == 'Buy':  # LONG: TP above entry
            tp_price = avg_entry * (1 + honest_tp_pct / 100)
        else:  # SHORT: TP below entry
            tp_price = avg_entry * (1 - honest_tp_pct / 100)

        self.logger.debug(
            f"[{self.symbol}] Honest TP calc for {side}: "
            f"positions={num_positions}, fees={total_fees_pct:.3f}%, "
            f"target={self.tp_pct}%, honest={honest_tp_pct:.3f}%"
        )

        return tp_price

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

        # Calculate honest TP price (accounts for all fees)
        tp_price = self._calculate_honest_tp_price(side, avg_entry)
        tp_side = 'Sell' if side == 'Buy' else 'Buy'  # Close with opposite side

        # Cancel old TP order if exists
        # Try PositionManager first (fallback for old state files)
        old_tp_id = self.pm.get_tp_order_id(side)

        # If not in PM, check Order WebSocket tracking
        if not old_tp_id:
            with self._tp_orders_lock:
                old_tp_id = self._tp_orders.get(side)

        if old_tp_id and not self.dry_run:
            # Cancel the tracked TP order
            try:
                self.client.cancel_order(self.symbol, old_tp_id, self.category)
                self.logger.debug(f"[{self.symbol}] Cancelled old TP order: {old_tp_id}")
            except Exception as e:
                self.logger.warning(
                    f"[{self.symbol}] Failed to cancel TP order {old_tp_id}: {e} "
                    f"(may have been already filled/cancelled)"
                )

        # Calculate positionIdx for the position we're closing
        # In Hedge Mode: positionIdx indicates which position to close
        # Buy position (LONG) = 1, Sell position (SHORT) = 2
        position_idx = TradingConstants.POSITION_IDX_LONG if side == 'Buy' else TradingConstants.POSITION_IDX_SHORT

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
            # Also update Order WebSocket tracking
            # (WebSocket will confirm, but we pre-fill for immediate availability)
            with self._tp_orders_lock:
                self._tp_orders[side] = new_tp_id
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
        # Store current price (from WebSocket)
        self.current_price = current_price

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

            # Check if we have enough available balance (using BalanceManager)
            if not self.dry_run:
                try:
                    available_balance = self.balance_manager.get_available_balance()

                    # Check if we have enough balance for this order
                    if new_margin_usd > available_balance:
                        # Throttle warning to avoid spam (max once per minute)
                        current_time = time.time()
                        warning_key = f'insufficient_balance_{side}'
                        if warning_key not in self._last_warning_time:
                            self._last_warning_time[warning_key] = 0

                        if current_time - self._last_warning_time[warning_key] >= self._warning_interval:
                            self.logger.warning(
                                LogMessages.INSUFFICIENT_BALANCE.format(
                                    symbol=self.symbol,
                                    side=side,
                                    needed=new_margin_usd,
                                    available=available_balance
                                )
                            )
                            self._last_warning_time[warning_key] = current_time
                        return  # Don't place order
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

    def _check_risk_limits(self, current_price: float) -> bool:
        """
        Check risk limits (Account MM Rate only - balance checked before averaging)

        For hedged positions (LONG+SHORT), monitors Account Maintenance Margin Rate
        to detect liquidation risk. Uses BalanceManager with caching to avoid API rate limits.

        Args:
            current_price: Current market price

        Returns:
            True if safe to continue, False if risk limits hit
        """
        # In dry run, use mock value
        if self.dry_run:
            account_mm_rate = 0.17  # Mock: safe value
        else:
            # Get MM Rate from BalanceManager (with caching)
            try:
                account_mm_rate = self.balance_manager.get_mm_rate()
            except Exception as e:
                # API failure - fail-fast (no fallbacks!)
                raise RuntimeError(
                    f"[{self.symbol}] Failed to get wallet balance from exchange - cannot check risk limits"
                ) from e

        # Check Account Maintenance Margin Rate (for hedged positions)
        # This is the CORRECT way to check liquidation risk for LONG+SHORT strategy
        if not self.dry_run and account_mm_rate is not None:
            # Log current MM rate for monitoring (throttled to avoid spam)
            if account_mm_rate > TradingConstants.MM_RATE_WARNING_THRESHOLD:
                current_time = time.time()
                if current_time - self._last_warning_time['mm_rate'] >= self._warning_interval:
                    self.logger.warning(
                        LogMessages.HIGH_MM_RATE.format(
                            symbol=self.symbol,
                            mm_rate=account_mm_rate
                        )
                    )
                    self._last_warning_time['mm_rate'] = current_time

            # Emergency close if >= threshold (use instance variable set in __init__)
            if account_mm_rate >= self.mm_rate_threshold:
                reason = f"Account MM Rate {account_mm_rate:.2f}% >= {self.mm_rate_threshold}%"
                self.logger.error(
                    LogMessages.EMERGENCY_CLOSE.format(reason=reason)
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

    def on_position_update(self, position_data: dict):
        """
        Handle position update from WebSocket

        Called when position stream sends an update. Detects position closures
        and calculates realized PnL from cumRealisedPnl delta.

        Args:
            position_data: Position data from Bybit WebSocket
        """
        try:
            side = position_data.get('side')  # 'Buy' or 'Sell'
            size = position_data.get('size')  # Position size (string)
            cum_realised_pnl = position_data.get('cumRealisedPnl')  # Cumulative realized PnL
            avg_price = position_data.get('avgPrice')  # Average entry price

            # Convert size to float
            size_float = float(size) if size else 0.0

            # Log position update
            self.logger.debug(
                f"[{self.symbol}] Position update: {side} size={size} "
                f"avgPrice={avg_price} cumPnL={cum_realised_pnl}"
            )

            # Detect position closure: size becomes "0" or close to 0
            if size_float < 0.001:
                self.logger.info(
                    f"[{self.symbol}] Position CLOSED detected via WebSocket: {side}"
                )

                # Calculate realized PnL from cumulative delta
                if cum_realised_pnl:
                    cum_pnl_float = float(cum_realised_pnl)
                    with self._pnl_lock:
                        last_cum_pnl = self._last_cum_realised_pnl.get(side, 0.0)
                        realized_pnl = cum_pnl_float - last_cum_pnl

                        # Update tracked cumulative PnL
                        self._last_cum_realised_pnl[side] = cum_pnl_float

                    self.logger.info(
                        f"💰 [{self.symbol}] {side} position closed - "
                        f"Realized PnL: ${realized_pnl:.4f} "
                        f"(cumPnL: {last_cum_pnl:.4f} → {cum_pnl_float:.4f})"
                    )

                    # Log to metrics
                    if self.metrics_tracker:
                        # Determine close reason (positive = TP, negative = loss/liquidation)
                        close_reason = "Take Profit" if realized_pnl > 0 else "Loss/Liquidation"

                        self.metrics_tracker.log_trade(
                            symbol=self.symbol,
                            side='Sell' if side == 'Buy' else 'Buy',  # Opposite side for close
                            action="CLOSE",
                            price=float(avg_price) if avg_price else 0.0,
                            quantity=0.0,  # Closed, qty unknown from this message
                            reason=close_reason,
                            pnl=realized_pnl
                        )

                    # Clear local positions for this side
                    self.pm.remove_all_positions(side)

                    # Reopen position if not in emergency stop
                    if not self.emergency_stopped and not self.dry_run:
                        current_price = float(avg_price) if avg_price else 0.0
                        if current_price > 0:
                            try:
                                self._open_initial_position(side, current_price)
                            except Exception as e:
                                self.logger.error(
                                    f"[{self.symbol}] Failed to reopen {side} position after WebSocket close: {e}"
                                )

            else:
                # Position still open (size > 0)

                # Check if we have this position tracked locally
                local_qty = self.pm.get_total_quantity(side)

                if local_qty == 0:
                    # Position exists on exchange but not tracked locally
                    # This happens on bot restart - restore from Position WebSocket snapshot
                    self.logger.info(
                        f"📥 [{self.symbol}] Restoring {side} position from Position WebSocket: "
                        f"{size_float} @ ${avg_price}"
                    )

                    # Restore position as single entry (grid_level=0)
                    # Note: We lose grid level details, but exchange avgPrice is accurate
                    self.pm.add_position(
                        side=side,
                        entry_price=float(avg_price) if avg_price else 0.0,
                        quantity=size_float,
                        grid_level=0  # Restored position (no grid history)
                    )

                    # Create TP order for restored position
                    if not self.dry_run:
                        try:
                            self._update_tp_order(side)
                            self.logger.info(
                                f"✅ [{self.symbol}] {side} position restored and TP order created"
                            )
                        except Exception as e:
                            self.logger.error(
                                f"[{self.symbol}] Failed to create TP order for restored {side} position: {e}"
                            )

                    # Log to metrics
                    if self.metrics_tracker:
                        self.metrics_tracker.log_trade(
                            symbol=self.symbol,
                            side=side,
                            action="RESTORE",
                            price=float(avg_price) if avg_price else 0.0,
                            quantity=size_float,
                            reason="Position restored from WebSocket snapshot",
                            pnl=None
                        )

                # Update cumulative PnL tracking if provided
                if cum_realised_pnl:
                    with self._pnl_lock:
                        self._last_cum_realised_pnl[side] = float(cum_realised_pnl)

        except Exception as e:
            self.logger.error(
                f"[{self.symbol}] Error processing position update: {e}",
                exc_info=True
            )

    def on_wallet_update(self, wallet_data: dict):
        """
        Handle wallet update from WebSocket

        Called when wallet stream sends an update. Updates balance and MM Rate cache.

        Args:
            wallet_data: Wallet data from Bybit WebSocket
        """
        try:
            # Extract balance and MM Rate
            total_available = wallet_data.get('totalAvailableBalance')
            account_mm_rate = wallet_data.get('accountMMRate')

            if total_available is not None:
                balance = float(total_available)

                # Update BalanceManager cache from WebSocket
                if self.balance_manager:
                    # Convert MM Rate from decimal to percentage (e.g., 0.0017 -> 0.17%)
                    mm_rate_pct = None
                    if account_mm_rate and account_mm_rate != '':
                        mm_rate_pct = float(account_mm_rate) * 100

                    self.balance_manager.update_from_websocket(balance, mm_rate_pct)

                    self.logger.debug(
                        f"[{self.symbol}] Wallet update: ${balance:.2f}, "
                        f"MM Rate: {mm_rate_pct:.4f}%" if mm_rate_pct else f"${balance:.2f}"
                    )

        except Exception as e:
            self.logger.error(
                f"[{self.symbol}] Error processing wallet update: {e}",
                exc_info=True
            )

    def on_order_update(self, order_data: dict):
        """
        Handle order update from WebSocket

        Called when order stream sends an update. Tracks TP order IDs automatically.

        Args:
            order_data: Order data from Bybit WebSocket
        """
        try:
            order_id = order_data.get('orderId')
            order_status = order_data.get('orderStatus')
            order_type = order_data.get('orderType')
            side = order_data.get('side')
            position_idx = order_data.get('positionIdx')

            # Only track Take Profit orders
            if order_type == 'Market' and order_status in ['New', 'Filled', 'Cancelled']:
                # Determine which side this TP order belongs to
                # positionIdx: 1=LONG, 2=SHORT
                if position_idx == '1':  # LONG TP (closes Buy position)
                    track_side = 'Buy'
                elif position_idx == '2':  # SHORT TP (closes Sell position)
                    track_side = 'Sell'
                else:
                    return  # Unknown position index

                if order_status == 'New':
                    # New TP order created - track it
                    with self._tp_orders_lock:
                        self._tp_orders[track_side] = order_id
                    self.logger.debug(
                        f"[{self.symbol}] TP order tracked: {track_side} -> {order_id}"
                    )

                elif order_status == 'Filled':
                    # TP order filled - remove from tracking
                    with self._tp_orders_lock:
                        if track_side in self._tp_orders:
                            del self._tp_orders[track_side]
                    self.logger.info(
                        f"[{self.symbol}] TP order filled: {track_side} orderId={order_id}"
                    )

                elif order_status == 'Cancelled':
                    # TP order cancelled - remove from tracking
                    with self._tp_orders_lock:
                        if track_side in self._tp_orders:
                            del self._tp_orders[track_side]
                    self.logger.debug(
                        f"[{self.symbol}] TP order cancelled: {track_side} orderId={order_id}"
                    )

        except Exception as e:
            self.logger.error(
                f"[{self.symbol}] Error processing order update: {e}",
                exc_info=True
            )
