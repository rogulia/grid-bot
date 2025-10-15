"""Order management for Grid Strategy"""

import time
from datetime import datetime
from config.constants import TradingConstants, LogMessages
from ...utils.logger import log_trade


class OrderManagementMixin:
    """Mixin for order execution and TP management"""

    def _open_initial_position(self, side: str, current_price: float, custom_margin_usd: float = None):
        """
        Open initial position for a side IN PARTS (grid levels)

        Always opens positions as sequence of grid levels (0, 1, 2, ...)
        even if total margin is large. This allows proper grid level restoration
        from order history after restart.

        Args:
            side: 'Buy' for LONG or 'Sell' for SHORT
            current_price: Current market price
            custom_margin_usd: Optional custom margin in USD (for adaptive reopen). If not provided, uses initial_size_usd

        Raises:
            Exception: If order placement fails (in live mode)
        """
        # Use custom margin if provided, otherwise use default initial size
        target_margin = custom_margin_usd if custom_margin_usd is not None else self.initial_size_usd

        # Calculate which grid levels are needed
        levels_to_open = self._calculate_grid_levels_for_margin(target_margin)

        self.logger.info(
            f"🆕 [{self.symbol}] Opening {side} position: ${target_margin:.2f} margin "
            f"in {len(levels_to_open)} parts (levels {levels_to_open})"
        )

        # Open each grid level separately
        for grid_level in levels_to_open:
            level_margin = self.initial_size_usd * (self.multiplier ** grid_level)
            level_qty = self._usd_to_qty(level_margin, current_price)

            self.logger.info(
                f"  Level {grid_level}: ${level_margin:.2f} margin ({level_qty:.1f} {self.symbol})"
            )

            order_id = None
            if not self.dry_run:
                try:
                    # Determine position_idx for hedge mode
                    position_idx = TradingConstants.POSITION_IDX_LONG if side == 'Buy' else TradingConstants.POSITION_IDX_SHORT

                    # Use limit order with retry mechanism
                    order_id = self.limit_order_manager.place_limit_order(
                        side=side,
                        qty=level_qty,
                        current_price=current_price,
                        reason=f"Initial position level {grid_level}",
                        position_idx=position_idx
                    )

                    if not order_id:
                        # Limit order failed, fallback to market immediately
                        self.logger.warning(
                            f"[{self.symbol}] Limit order failed for level {grid_level}, using market order"
                        )
                        response = self.client.place_order(
                            symbol=self.symbol,
                            side=side,
                            qty=level_qty,
                            order_type="Market",
                            category=self.category,
                            position_idx=position_idx
                        )
                        self.logger.debug(f"[{self.symbol}] Level {grid_level} market order response: {response}")

                        # Extract orderId from response
                        if response and 'result' in response:
                            order_id = response['result'].get('orderId')

                    # Small delay between orders to preserve order in history
                    time.sleep(0.1)

                except Exception as e:
                    self.logger.error(
                        f"[{self.symbol}] Failed to open {side} position level {grid_level}: {e}"
                    )
                    raise

            # Track position
            self.pm.add_position(
                side=side,
                entry_price=current_price,
                quantity=level_qty,
                grid_level=grid_level,
                order_id=order_id
            )

        self.logger.info(
            f"✅ [{self.symbol}] Opened {side}: {len(levels_to_open)} levels, "
            f"total ${target_margin:.2f} margin"
        )

        # Set TP order
        self._update_tp_order(side)

        # Log to metrics
        if self.metrics_tracker:
            # Calculate total quantity from all opened levels
            total_qty = sum(
                self._usd_to_qty(self.initial_size_usd * (self.multiplier ** level), current_price)
                for level in levels_to_open
            )

            # Determine reason based on whether custom margin was used
            if custom_margin_usd is not None:
                reason = f"Adaptive reopen (${target_margin:.2f})"
            else:
                reason = "Initial position"

            self.metrics_tracker.log_trade(
                symbol=self.symbol,
                side=side,
                action="OPEN",
                price=current_price,
                quantity=total_qty,
                reason=reason,
                pnl=None
            )

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

            # CRITICAL: Check safety reserve before averaging (account-level check)
            if not self.dry_run:
                try:
                    # Use account-level reserve check if TradingAccount is available
                    # This accounts for ALL symbols and dynamic safety reserve
                    if self.trading_account:
                        # Account-level check with safety reserve for ALL symbols
                        if not self.trading_account.check_reserve_before_averaging(
                            symbol=self.symbol,
                            side=side,
                            next_averaging_margin=new_margin_usd
                        ):
                            # Reserve check failed - don't place order
                            # Warning already logged by check_reserve_before_averaging()
                            return
                    else:
                        # Fallback: Simple balance check (for tests and standalone mode)
                        # This doesn't account for safety reserve!
                        available_balance = self.balance_manager.get_available_balance()

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
                    self.logger.error(f"[{self.symbol}] Failed to check balance/reserve before order: {e}")
                    return  # Don't place order if we can't verify balance

            self.logger.info(
                f"[{self.symbol}] Executing grid order: {side} ${new_margin_usd:.2f} MARGIN ({new_size:.6f}) "
                f"@ ${current_price:.4f} (level {grid_level})"
            )

            # Execute order (or simulate)
            order_id = None
            if not self.dry_run:
                # Determine position_idx for hedge mode
                position_idx = TradingConstants.POSITION_IDX_LONG if side == 'Buy' else TradingConstants.POSITION_IDX_SHORT

                # Use limit order with retry mechanism
                order_id = self.limit_order_manager.place_limit_order(
                    side=side,
                    qty=new_size,
                    current_price=current_price,
                    reason=f"Grid level {grid_level}",
                    position_idx=position_idx
                )

                if not order_id:
                    # Limit order failed, fallback to market immediately
                    self.logger.warning(
                        f"[{self.symbol}] Limit order failed for grid level {grid_level}, using market order"
                    )
                    response = self.client.place_order(
                        symbol=self.symbol,
                        side=side,
                        qty=new_size,
                        order_type="Market",
                        category=self.category,
                        position_idx=position_idx
                    )
                    self.logger.info(f"[{self.symbol}] Market order response: {response}")

                    # Extract orderId from response
                    if response and 'result' in response:
                        order_id = response['result'].get('orderId')

            # Add to position manager
            self.pm.add_position(
                side=side,
                entry_price=current_price,
                quantity=new_size,
                grid_level=grid_level,
                order_id=order_id
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

    def _cancel_all_reduce_only_orders(self, side: str):
        """
        Cancel ALL reduce-only orders for a side from exchange
        (doesn't rely on local tracking - reads directly from exchange)

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT) - position side, not order side

        Returns:
            Number of cancelled orders
        """
        if self.dry_run:
            return 0

        # Determine positionIdx for the position we want to close
        position_idx = TradingConstants.POSITION_IDX_LONG if side == 'Buy' else TradingConstants.POSITION_IDX_SHORT

        try:
            # Get all open orders for this symbol
            open_orders = self.client.get_open_orders(self.symbol, self.category)

            # Filter for reduce-only orders with matching positionIdx
            tp_orders = [
                order for order in open_orders
                if order.get('reduceOnly') == True and order.get('positionIdx') == position_idx
            ]

            if not tp_orders:
                self.logger.debug(f"[{self.symbol}] No reduce-only orders found for {side} position")
                return 0

            # Cancel all found TP orders
            cancelled_count = 0
            for order in tp_orders:
                order_id = order.get('orderId')
                try:
                    self.logger.info(
                        f"[{self.symbol}] 🗑️  Cancelling reduce-only order: {order_id} "
                        f"({order.get('side')} {order.get('qty')} @ ${order.get('price')})"
                    )
                    self.client.cancel_order(self.symbol, order_id, self.category)
                    cancelled_count += 1
                except Exception as e:
                    self.logger.warning(
                        f"[{self.symbol}] ⚠️  Failed to cancel order {order_id}: {e} "
                        f"(may have been already filled/cancelled)"
                    )

            if cancelled_count > 0:
                self.logger.info(
                    f"[{self.symbol}] ✅ Cancelled {cancelled_count} reduce-only order(s) for {side} position"
                )

            return cancelled_count

        except Exception as e:
            self.logger.error(
                f"[{self.symbol}] ❌ Error cancelling reduce-only orders for {side}: {e}",
                exc_info=True
            )
            return 0

    def _update_tp_order(self, side: str, force_cancel_all: bool = False):
        """
        Update Take Profit order for a side (cancel old and place new)

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)
            force_cancel_all: If True, cancel ALL reduce-only orders from exchange
                            (useful on restart when local tracking is empty)
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

        # Cancel old TP order(s)
        if force_cancel_all:
            # Force mode: Cancel ALL reduce-only orders from exchange
            # (useful on restart when local tracking is empty/stale)
            self.logger.info(
                f"[{self.symbol}] 🔄 Force cancel mode: checking exchange for reduce-only orders..."
            )
            self._cancel_all_reduce_only_orders(side)
        else:
            # Normal mode: Cancel only tracked TP order
            # Try PositionManager first (fallback for old state files)
            old_tp_id = self.pm.get_tp_order_id(side)

            # If not in PM, check Order WebSocket tracking
            if not old_tp_id:
                with self._tp_orders_lock:
                    old_tp_id = self._tp_orders.get(side)

            if old_tp_id and not self.dry_run:
                # Cancel the tracked TP order
                try:
                    self.logger.info(f"[{self.symbol}] 🔄 Attempting to cancel old TP order: {old_tp_id}")
                    self.client.cancel_order(self.symbol, old_tp_id, self.category)
                    self.logger.info(f"[{self.symbol}] ✅ Cancelled old TP order: {old_tp_id}")
                except Exception as e:
                    self.logger.warning(
                        f"[{self.symbol}] ⚠️  Failed to cancel TP order {old_tp_id}: {e} "
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

            if new_tp_id:
                # TP created successfully - save ID
                self.pm.set_tp_order_id(side, new_tp_id)
                # Also update Order WebSocket tracking
                # (WebSocket will confirm, but we pre-fill for immediate availability)
                with self._tp_orders_lock:
                    self._tp_orders[side] = new_tp_id

                self.logger.info(
                    f"[{self.symbol}] ✅ TP order created: {tp_side} {total_qty} @ ${tp_price:.4f} "
                    f"(avg entry: ${avg_entry:.4f}, ID: {new_tp_id})"
                )
                return new_tp_id
            else:
                # TP creation failed - log error but don't save None
                self.logger.error(
                    f"[{self.symbol}] ❌ Failed to create TP order for {side} - place_tp_order returned None!"
                )
                return None
        else:
            self.logger.info(
                f"[{self.symbol}] [DRY RUN] Would place TP: {tp_side} {total_qty} @ ${tp_price:.4f}"
            )
            return "DRY_RUN_TP_ID"
