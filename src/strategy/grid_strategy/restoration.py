"""State restoration for Grid Strategy"""

import time
from datetime import datetime
from config.constants import TradingConstants, LogMessages
from ...utils.timezone import now_helsinki


class RestorationMixin:
    """Mixin for state restoration from exchange"""

    def restore_state_from_exchange(self, current_price: float):
        """
        Restore bot state from exchange at startup (called BEFORE WebSocket start)

        This is the FIRST step after bot initialization:
        1. Fetch real position state from exchange via REST API (source of truth)
        2. Compare with local state (should be empty at startup)
        3. RESTORE positions if exchange has them (bot restart scenario)
        4. OPEN initial positions if both exchange and local are empty
        5. Create TP orders for all positions
        6. FAIL-FAST if unexplained mismatch detected OR after 3 retry attempts

        WebSocket updates during restore trigger re-sync instead of emergency stop.

        After this method completes successfully, WebSockets can be started safely.

        Args:
            current_price: Current market price (from REST API get_market_price)

        Raises:
            RuntimeError: If state cannot be restored or critical mismatch detected
        """
        # Block if emergency stop was triggered
        if self.emergency_stopped:
            raise RuntimeError(
                f"[{self.symbol}] Cannot restore state: bot in emergency stop state. "
                f"Remove emergency stop flag and restart."
            )

        self.logger.info(f"🔄 [{self.symbol}] Restoring state from exchange @ ${current_price:.4f}...")

        # CRITICAL: Set current_price BEFORE any operations that need it (e.g., reserve check)
        self.current_price = current_price

        # Retry loop - WebSocket updates during restore trigger re-sync
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            # Reset resync flag before each attempt
            with self._resync_lock:
                self._needs_resync = False
                if retry_count == 0:
                    self._resync_triggers = []  # Clear triggers on first attempt only

            # Log retry attempt
            if retry_count > 0:
                trigger_summary = ', '.join(t['event'] for t in self._resync_triggers[-3:])
                self.logger.warning(
                    f"⚠️ [{self.symbol}] Position changed during restore (attempt {retry_count + 1}/{max_retries}), "
                    f"retrying... Last triggers: {trigger_summary}"
                )

            # Set syncing flag
            with self._sync_lock:
                self._is_syncing = True

            try:
                # Get available balance for position checks
                available_balance = 0.0
                if not self.dry_run:
                    try:
                        available_balance = self.balance_manager.get_available_balance()
                        self.logger.debug(f"[{self.symbol}] Available balance: ${available_balance:.2f}")
                    except Exception as e:
                        self.logger.error(f"[{self.symbol}] Failed to get balance: {e}")
                        raise

                for side in ['Buy', 'Sell']:
                    # Get REAL position state from exchange (source of truth)
                    exchange_position = None
                    exchange_qty = 0.0

                    if not self.dry_run:
                        try:
                            exchange_position = self.client.get_active_position(
                                symbol=self.symbol,
                                side=side,
                                category=self.category
                            )
                            exchange_qty = float(exchange_position.get('size', 0)) if exchange_position else 0.0
                        except Exception as e:
                            # Fail-fast: cannot verify exchange state
                            reason = f"Failed to get {side} position from exchange: {e}"
                            self.logger.error(f"❌ [{self.symbol}] {reason}")
                            raise RuntimeError(f"[{self.symbol}] {reason}") from e

                    # Get local position state (should be empty at startup!)
                    local_qty = self.pm.get_total_quantity(side)

                    # Calculate difference
                    qty_diff = abs(exchange_qty - local_qty)
                    tolerance = 0.001  # Only rounding errors allowed

                    self.logger.debug(
                        f"[{self.symbol}] {side} position check: "
                        f"exchange={exchange_qty}, local={local_qty}, diff={qty_diff:.6f}"
                    )

                    # SCENARIO 1: No positions anywhere - open initial
                    if exchange_qty == 0 and local_qty == 0:
                        self.logger.info(
                            f"🆕 [{self.symbol}] No {side} position exists - opening initial position"
                        )

                        # ADAPTIVE REOPEN: Check if opposite side has positions
                        # If yes: use adaptive reopen (minus two steps)
                        # If no: use initial size
                        opposite_side = 'Sell' if side == 'Buy' else 'Buy'
                        opposite_positions = (self.pm.long_positions if opposite_side == 'Buy'
                                            else self.pm.short_positions)

                        if opposite_positions:
                            # Opposite side exists - use adaptive reopen logic
                            reopen_margin = self.calculate_reopen_size(side, opposite_side)
                            self.logger.info(
                                f"🔧 [{self.symbol}] ADAPTIVE REOPEN: {side} with ${reopen_margin:.2f} margin "
                                f"(opposite has {len(opposite_positions)} levels)"
                            )
                        else:
                            # No opposite - just use initial size
                            reopen_margin = self.initial_size_usd
                            self.logger.info(
                                f"🆕 [{self.symbol}] INITIAL OPEN: {side} with ${reopen_margin:.2f} margin"
                            )

                        # Use _open_initial_position() which handles:
                        # - Reference qty for perfect symmetry ✅
                        # - Balance checks with buffer ✅
                        # - Opening positions by grid levels ✅
                        # - Creating TP orders ✅
                        # - Placing pending for symmetry ✅
                        # - Logging to metrics ✅
                        success = self._open_initial_position(
                            side=side,
                            current_price=current_price,
                            custom_margin_usd=reopen_margin
                        )

                        if not success:
                            # _open_initial_position failed (balance check or exception)
                            reason = (
                                f"Failed to open initial {side} position - "
                                f"insufficient balance or exception occurred"
                            )
                            self.logger.error(f"❌ [{self.symbol}] {reason}")
                            self._create_emergency_stop_flag(reason)
                            self.emergency_stopped = True
                            raise RuntimeError(f"[{self.symbol}] {reason}")

                    # SCENARIO 2: Positions synced
                    elif qty_diff <= tolerance:
                        self.logger.info(
                            f"✅ [{self.symbol}] {side} position SYNCED: "
                            f"exchange={exchange_qty}, local={local_qty}"
                        )

                        # Create TP order if position exists but no TP
                        if local_qty > 0:
                            tp_order_id = self.pm.get_tp_order_id(side)
                            if not tp_order_id and not self.dry_run:
                                self.logger.info(
                                    f"🎯 [{self.symbol}] Creating TP order for {side} position (qty={local_qty})"
                                )
                                self._update_tp_order(side)

                    # SCENARIO 3: Exchange has position, local empty - RESTORE
                    elif exchange_qty > 0 and local_qty == 0:
                        self.logger.warning(
                            f"📥 [{self.symbol}] Position found on exchange for {side}: "
                            f"exchange={exchange_qty}, local={local_qty} - RESTORING"
                        )

                        if not self.dry_run:
                            self._restore_position_from_exchange(side, exchange_position)
                        else:
                            # Dry run
                            self.pm.add_position(
                                side=side,
                                entry_price=current_price,
                                quantity=exchange_qty,
                                grid_level=0,
                                order_id=None
                            )

                    # SCENARIO 4: Unexplained mismatch - FAIL-FAST
                    else:
                        reason = (
                            f"Position mismatch for {side} requires manual intervention: "
                            f"exchange={exchange_qty}, local={local_qty}, diff={qty_diff:.6f}. "
                            f"This may indicate: (1) positions opened outside bot, "
                            f"(2) partial close, (3) exchange API issue. "
                            f"Please verify positions on exchange and restart bot."
                        )
                        self.logger.error(f"❌ [{self.symbol}] {reason}")
                        self._create_emergency_stop_flag(reason)
                        self.emergency_stopped = True
                        raise RuntimeError(f"[{self.symbol}] {reason}")

                self.logger.info(f"✅ [{self.symbol}] State restored successfully from exchange")

            finally:
                # Clear syncing flag
                with self._sync_lock:
                    self._is_syncing = False

            # Check if resync is needed (WebSocket updates during restore)
            with self._resync_lock:
                if not self._needs_resync:
                    # Success - no WebSocket updates during restore
                    break  # Exit retry loop

            # Resync needed - increment retry count
            retry_count += 1

        # If we exited loop due to max retries, create emergency stop with diagnostics
        if retry_count >= max_retries:
            # Collect diagnostic data
            exchange_long_qty = 0.0
            exchange_short_qty = 0.0
            exchange_long_avg = 0.0
            exchange_short_avg = 0.0

            try:
                if not self.dry_run:
                    for side in ['Buy', 'Sell']:
                        pos = self.client.get_active_position(self.symbol, side, self.category)
                        if pos:
                            qty = float(pos.get('size', 0))
                            avg = float(pos.get('avgPrice', 0))
                            if side == 'Buy':
                                exchange_long_qty, exchange_long_avg = qty, avg
                            else:
                                exchange_short_qty, exchange_short_avg = qty, avg
            except:
                pass  # Best effort

            local_long_qty = self.pm.get_total_quantity('Buy')
            local_short_qty = self.pm.get_total_quantity('Sell')

            diagnostic_data = {
                'retry_count': retry_count,
                'max_retries': max_retries,
                'resync_triggers': self._resync_triggers,
                'exchange_state': {
                    'long_qty': exchange_long_qty,
                    'long_avg_price': exchange_long_avg,
                    'short_qty': exchange_short_qty,
                    'short_avg_price': exchange_short_avg,
                },
                'local_state': {
                    'long_qty': local_long_qty,
                    'short_qty': local_short_qty,
                },
                'current_price': current_price,
            }

            # Build detailed error message
            trigger_events = [t['event'] for t in self._resync_triggers]
            trigger_summary = ', '.join(trigger_events[-10:]) if trigger_events else 'none'

            reason = (
                f"Failed to restore state after {max_retries} attempts due to continuous position changes.\n"
                f"\n"
                f"🔍 DIAGNOSTIC INFO:\n"
                f"- Retry count: {retry_count}\n"
                f"- WebSocket interruptions: {len(self._resync_triggers)}\n"
                f"- Interruption events: {trigger_summary}\n"
                f"- Exchange state: LONG={exchange_long_qty}@${exchange_long_avg:.4f}, "
                f"SHORT={exchange_short_qty}@${exchange_short_avg:.4f}\n"
                f"- Local state: LONG={local_long_qty}, SHORT={local_short_qty}\n"
                f"\n"
                f"💡 POSSIBLE CAUSES:\n"
                f"1. Manual trading active during bot startup\n"
                f"2. Another bot/system using same account\n"
                f"3. Very active market with frequent TP triggers\n"
                f"4. Exchange API returning inconsistent data\n"
                f"\n"
                f"🔧 RESOLUTION:\n"
                f"1. Stop all manual trading and other bots\n"
                f"2. Wait for market calm (no active orders/positions changing)\n"
                f"3. Restart bot\n"
                f"4. Or close all positions manually on exchange and restart with fresh state\n"
            )

            self.logger.error(f"❌ [{self.symbol}] {reason}")

            # Create emergency stop with full diagnostic data
            self._create_emergency_stop_flag(reason, additional_data=diagnostic_data)
            self.emergency_stopped = True
            raise RuntimeError(f"[{self.symbol}] {reason}")

    def _restore_position_from_exchange(self, side: str, exchange_position: dict):
        """
        Restore position from exchange data after bot restart

        Called when exchange has a position but local tracking is empty.
        Uses order history to reconstruct grid levels.

        Args:
            side: Position side ('Buy' or 'Sell')
            exchange_position: Position data from exchange with 'size' and 'avgPrice'
        """
        try:
            # Extract exchange data (source of truth)
            exchange_qty = float(exchange_position.get('size', 0))
            exchange_avg_price = float(exchange_position.get('avgPrice', 0))

            if exchange_qty <= 0 or exchange_avg_price <= 0:
                self.logger.warning(
                    f"⚠️ [{self.symbol}] Invalid exchange position data for {side}: "
                    f"qty={exchange_qty}, avgPrice={exchange_avg_price}"
                )
                return

            self.logger.info(
                f"📥 [{self.symbol}] RESTORING {side} position from exchange: "
                f"{exchange_qty} @ ${exchange_avg_price:.4f}"
            )

            # Restore grid levels from order history
            positions = self._restore_grid_levels_from_order_history(side, exchange_qty)

            # Check if order history restoration returned empty (fallback needed)
            if not positions:
                # Check if this is resync-triggered failure or fallback scenario
                with self._resync_lock:
                    if self._needs_resync:
                        # Soft failure - validation detected incomplete restoration
                        # Return early WITHOUT exception to allow retry loop to retry
                        self.logger.warning(
                            f"⚠️ [{self.symbol}] Grid level restoration incomplete for {side} "
                            f"(validation failed). Returning for retry..."
                        )
                        return  # Exit early, let retry loop handle it

                # FALLBACK: Order history restoration returned empty
                # This happens when TP closed only part of position
                # Use exchange position data directly
                self.logger.warning(
                    f"⚠️ [{self.symbol}] Order history restoration returned empty for {side}. "
                    f"Using FALLBACK: creating single position from exchange data."
                )
                self.logger.info(
                    f"📊 [{self.symbol}] Exchange position: qty={exchange_qty}, avgPrice=${exchange_avg_price:.4f}"
                )

                # Create single position at grid level 0 from exchange data
                # Grid level tracking will be lost, but position will be tracked correctly
                # TP order will be created based on exchange avgPrice
                positions = [(exchange_qty, exchange_avg_price, 0, None)]

                self.logger.info(
                    f"✅ [{self.symbol}] FALLBACK: Restored {side} as single level 0 with {exchange_qty} @ ${exchange_avg_price:.4f}"
                )

            # Add each position to PositionManager
            for qty, price, grid_level, order_id in positions:
                self.pm.add_position(
                    side=side,
                    entry_price=price if price > 0 else exchange_avg_price,
                    quantity=qty,
                    grid_level=grid_level,
                    order_id=order_id
                )

                # CRITICAL: Save reference qty for perfect symmetry
                # This ensures opposite side will use same qty on this level
                with self._reference_qty_lock:
                    if grid_level not in self._reference_qty_per_level:
                        self._reference_qty_per_level[grid_level] = qty
                        self.logger.debug(
                            f"[{self.symbol}] Restored reference qty for level {grid_level}: {qty:.6f}"
                        )

            # Create TP order for restored position
            if not self.dry_run:
                try:
                    # Force cancel all reduce-only orders since local tracking is stale after restart
                    tp_id = self._update_tp_order(side, force_cancel_all=True)
                    if tp_id:
                        self.logger.info(
                            f"✅ [{self.symbol}] {side} position restored ({len(positions)} levels) and TP order created (ID: {tp_id})"
                        )
                    else:
                        # Fail-fast: TP is critical for risk management
                        raise RuntimeError(
                            f"Failed to create TP order for restored {side} position - place_tp_order returned None"
                        )
                except Exception as e:
                    self.logger.error(
                        f"❌ [{self.symbol}] Failed to create TP order for restored {side} position: {e}"
                    )
                    # Fail-fast - don't continue with broken state
                    raise

            # Log to metrics tracker
            if self.metrics_tracker:
                self.metrics_tracker.log_trade(
                    symbol=self.symbol,
                    side=side,
                    action="OPEN",
                    price=exchange_avg_price,
                    quantity=exchange_qty,
                    reason=f"RESTORE (from exchange, {len(positions)} levels)",
                    pnl=None
                )

        except Exception as e:
            self.logger.error(
                f"❌ [{self.symbol}] Failed to restore {side} position from exchange: {e}",
                exc_info=True
            )
            raise  # Fail-fast - don't continue with broken state

    def _restore_grid_levels_from_order_history(self, side: str, total_qty: float) -> list:
        """
        Restore grid levels from order history by analyzing filled orders

        Algorithm:
        1. Get order history from exchange
        2. Find last reduce-only order (last TP close)
        3. All orders after that = current position orders
        4. First order = grid level 0, subsequent = grid levels 1, 2, 3...

        Args:
            side: Position side ('Buy' or 'Sell')
            total_qty: Total quantity from exchange position

        Returns:
            List of (qty, price, grid_level, order_id) tuples
        """
        try:
            # Get order history from exchange
            # Filter for Filled orders only to avoid cancelled TP orders cluttering history
            # This dramatically increases effective history depth (200 filled vs 200 mixed)
            orders = self.client.get_order_history(
                symbol=self.symbol,
                category=self.category,
                limit=TradingConstants.ORDER_HISTORY_LIMIT,
                order_status="Filled"
            )

            self.logger.info(
                f"[{self.symbol}] Retrieved {len(orders) if orders else 0} filled orders from history for {side} restoration"
            )

            if not orders:
                # FAIL-FAST: Cannot restore without order history
                raise RuntimeError(
                    f"[{self.symbol}] No order history available - cannot restore {side} position. "
                    f"Manual intervention required: close position on exchange and restart bot."
                )

            # Check if history may be truncated (reached API limit)
            if len(orders) == TradingConstants.ORDER_HISTORY_LIMIT:
                self.logger.warning(
                    f"⚠️ [{self.symbol}] Retrieved exactly {TradingConstants.ORDER_HISTORY_LIMIT} orders - "
                    f"history may be truncated. If restoration fails, consider closing positions manually."
                )

            # DEBUG: Log ALL orders to see actual format
            self.logger.info(f"[{self.symbol}] === DEBUG: First 10 orders from history ===")
            for i, o in enumerate(orders[:10]):
                self.logger.info(
                    f"  Order {i}: side={o.get('side')}, positionIdx={o.get('positionIdx')} (type={type(o.get('positionIdx'))}), "
                    f"status={o.get('orderStatus')}, reduceOnly={o.get('reduceOnly')} (type={type(o.get('reduceOnly'))}), "
                    f"qty={o.get('cumExecQty')}, orderType={o.get('orderType')}"
                )

            # Determine positionIdx for this side (try both string and int)
            position_idx_str = '1' if side == 'Buy' else '2'
            position_idx_int = 1 if side == 'Buy' else 2

            # Determine opposite side for TP close detection
            # TP close for Buy position = Sell order with reduceOnly=True
            # TP close for Sell position = Buy order with reduceOnly=True
            opposite_side = 'Sell' if side == 'Buy' else 'Buy'

            # CRITICAL: For finding last TP close, we need to look at ALL orders with this positionIdx
            # Because TP close for Buy position = Sell order with positionIdx=1 and reduceOnly=True!
            position_orders = [
                o for o in orders
                if o.get('positionIdx') in [position_idx_str, position_idx_int]
                and o.get('orderStatus') == 'Filled'
            ]

            self.logger.info(
                f"[{self.symbol}] Found {len(position_orders)} filled orders for positionIdx={position_idx_int}"
            )

            # Sort by creation time (oldest first)
            position_orders.sort(key=lambda x: int(x.get('createdTime', 0)))

            # SIMPLE APPROACH: Find last TP close, everything AFTER it = current position
            # TP closes ENTIRE position at once, so this is straightforward

            # Find last TP close (newest = first match since sorted oldest-first)
            last_tp_idx = -1
            for i, order in enumerate(position_orders):
                if order.get('side') == opposite_side and order.get('reduceOnly'):
                    last_tp_idx = i
                    self.logger.info(
                        f"[{self.symbol}] Found last TP close at index {i}/{len(position_orders)}: "
                        f"{opposite_side} {order.get('cumExecQty')} @ {order.get('avgPrice')}"
                    )
                    # Don't break - we want the LAST (most recent) TP, which is highest index

            if last_tp_idx < 0:
                self.logger.info(
                    f"[{self.symbol}] No TP close found in history, using all {side} orders"
                )
                orders_after_tp = position_orders
            else:
                # Everything AFTER TP close (indices last_tp_idx+1 onwards)
                orders_after_tp = position_orders[last_tp_idx + 1:]
                self.logger.info(
                    f"[{self.symbol}] Taking {len(orders_after_tp)} orders after TP at index {last_tp_idx}"
                )

            # Filter for opening orders of correct side
            current_position_orders = [
                o for o in orders_after_tp
                if o.get('side') == side and not o.get('reduceOnly')
            ]

            self.logger.info(
                f"[{self.symbol}] {side} opening orders after last TP: {len(current_position_orders)}"
            )

            # Debug: log these orders
            for i, o in enumerate(current_position_orders):
                self.logger.debug(
                    f"  Position order {i}: {o.get('side')} {o.get('cumExecQty')} @ {o.get('avgPrice')}, "
                    f"reduceOnly={o.get('reduceOnly')}, type={o.get('orderType')}"
                )

            if not current_position_orders:
                # FALLBACK: No opening orders after last TP
                # This happens when TP closed only PART of position
                # Remaining position = orders opened BEFORE the TP close
                # Use fallback: restore from exchange position data directly
                self.logger.warning(
                    f"⚠️ [{self.symbol}] No {side} opening orders found after last TP in order history. "
                    f"This means TP closed only part of position, or all orders are before TP. "
                    f"Using FALLBACK: will restore from exchange position data."
                )
                return []  # Signal to use fallback

            # Reconstruct grid levels
            positions = []
            for i, order in enumerate(current_position_orders):
                qty = float(order.get('cumExecQty', 0))
                price = float(order.get('avgPrice', 0))
                order_id = order.get('orderId')  # Extract orderId from order history
                grid_level = i  # First order = level 0, second = level 1, etc.

                if qty > 0 and price > 0:
                    positions.append((qty, price, grid_level, order_id))
                    self.logger.info(
                        f"  Level {grid_level}: {qty} @ ${price:.4f} (orderId: {order_id})"
                    )

            self.logger.info(
                f"📊 [{self.symbol}] Restored {len(positions)} grid levels for {side} from order history"
            )

            # Validate restored quantity
            if positions:
                restored_qty = sum(qty for qty, _, _, _ in positions)
                qty_diff = restored_qty - total_qty  # signed difference

                if abs(qty_diff) > 0.001:
                    if qty_diff < 0:
                        # Restored less than exchange - new orders opened after we fetched history
                        # This means exchange state changed during restore - trigger re-sync
                        self.logger.error(
                            f"❌ [{self.symbol}] Restored {abs(qty_diff):.6f} LESS than exchange for {side}: "
                            f"restored={restored_qty}, exchange={total_qty}, missing={abs(qty_diff):.6f}\n"
                            f"This indicates order history is incomplete (fetched {len(orders)} orders).\n"
                            f"Possible causes:\n"
                            f"- More than {TradingConstants.ORDER_HISTORY_LIMIT} orders between last TP and now\n"
                            f"- Position opened during order history fetch\n"
                            f"Will trigger re-sync."
                        )
                        # Set resync flag - retry loop will re-attempt restore
                        with self._resync_lock:
                            self._needs_resync = True
                            self._resync_triggers.append({
                                'timestamp': now_helsinki().isoformat(),
                                'event': 'validation_failed',
                                'reason': f'restored_qty < exchange_qty: {restored_qty} < {total_qty}',
                                'side': side
                            })
                        # Don't raise here - let retry loop handle it
                        return []  # Return empty to signal failure
                    else:
                        # Restored MORE than exchange - this is an error! Included old orders
                        self.logger.error(
                            f"❌ [{self.symbol}] Restored {qty_diff:.6f} MORE than exchange "
                            f"(restored={restored_qty}, exchange={total_qty}). "
                            f"Likely included old orders! Using fallback."
                        )
                        raise RuntimeError("Restored more than exchange - logic error")
                else:
                    self.logger.info(
                        f"✅ [{self.symbol}] Quantity validation passed: restored={restored_qty}, exchange={total_qty}"
                    )

            if not positions:
                # FAIL-FAST: No valid positions reconstructed
                raise RuntimeError(
                    f"[{self.symbol}] Could not reconstruct any valid {side} positions from order history. "
                    f"Manual intervention required: close position on exchange and restart bot."
                )

            return positions

        except Exception as e:
            self.logger.error(
                f"[{self.symbol}] Error restoring grid levels from order history: {e}",
                exc_info=True
            )
            # FAIL-FAST: Cannot restore positions
            raise RuntimeError(
                f"[{self.symbol}] Failed to restore {side} position from order history: {e}"
            ) from e

    def sync_with_exchange(self, current_price: float):
        """
        Periodic sync with exchange to handle edge cases

        This method runs periodically (every 60s) during normal operation to:
        1. Detect untracked closes (position closed on exchange but WebSocket missed it)
        2. Verify TP orders exist for all positions
        3. Handle adaptive reopen after untracked close

        NOTE: This is NOT used for initial state restoration!
        Use restore_state_from_exchange() BEFORE starting WebSockets instead.

        Args:
            current_price: Current market price
        """
        # Block all operations if emergency stop was triggered
        if self.emergency_stopped:
            self.logger.debug(
                f"[{self.symbol}] Sync skipped: bot in emergency stop state."
            )
            return

        # Set syncing flag
        with self._sync_lock:
            self._is_syncing = True

        try:
            # CRITICAL: On first sync after bot restart, cancel ALL orders (TP + pending)
            # Orders may be outdated if bot was offline for a while
            if not self._first_sync_done:
                self.logger.info(
                    f"[{self.symbol}] 🔄 First sync after restart - cancelling all existing orders"
                )
                self._cancel_all_orders()

                # CRITICAL: Clear local TP order IDs so they get recreated below
                # Without this, verification logic thinks TP orders still exist
                with self._tp_orders_lock:
                    self._tp_orders = {'Buy': None, 'Sell': None}
                self.pm.set_tp_order_id('Buy', None)
                self.pm.set_tp_order_id('Sell', None)

                self._first_sync_done = True

            self.logger.debug(f"[{self.symbol}] Periodic sync with exchange...")

            for side in ['Buy', 'Sell']:
                # Get position state from exchange
                exchange_position = None
                exchange_qty = 0.0

                if not self.dry_run:
                    try:
                        exchange_position = self.client.get_active_position(
                            symbol=self.symbol,
                            side=side,
                            category=self.category
                        )
                        exchange_qty = float(exchange_position.get('size', 0)) if exchange_position else 0.0
                    except Exception as e:
                        self.logger.warning(f"[{self.symbol}] Failed to get {side} position: {e}")
                        continue  # Skip this side, try next

                # Get local position state
                local_qty = self.pm.get_total_quantity(side)

                # Calculate difference
                qty_diff = abs(exchange_qty - local_qty)
                tolerance = 0.001

                # SCENARIO 1: Positions synced - verify TP order
                if qty_diff <= tolerance:
                    if local_qty > 0:
                        # Verify TP order exists
                        with self._tp_orders_lock:
                            tp_order_id = self._tp_orders.get(side) or self.pm.get_tp_order_id(side)

                        if not tp_order_id and not self.dry_run:
                            self.logger.warning(
                                f"⚠️  [{self.symbol}] TP order missing for {side} position (qty={local_qty}) - creating"
                            )
                            self._update_tp_order(side)

                # SCENARIO 2: Untracked close detected
                elif exchange_qty == 0 and local_qty > 0:
                    # UNTRACKED CLOSE: Position closed on exchange but not detected via WebSocket
                    self.logger.warning(
                        f"⚠️  [{self.symbol}] Untracked {side} position close detected: "
                        f"exchange=0, local={local_qty}. WebSocket missed close event."
                    )

                    # Check debounce - prevent duplicate reopen if just did it
                    with self._sync_lock:
                        now = time.monotonic()
                        if now < self._just_reopened_until_ts.get(side, 0.0):
                            self.logger.debug(
                                f"[{self.symbol}] Skipping reopen for {side} (debounce until {self._just_reopened_until_ts[side]:.3f})"
                            )
                            continue  # Skip to next side

                    self.logger.info(
                        f"🔄 [{self.symbol}] Handling missed {side} close - clearing local state and reopening"
                    )

                    # Clear local positions (simulate what WebSocket close would do)
                    self.pm.remove_all_positions(side)
                    self.pm.set_tp_order_id(side, None)

                    # Clear TP order from in-memory tracking
                    with self._tp_orders_lock:
                        if side in self._tp_orders:
                            del self._tp_orders[side]

                    # Reopen position if not in emergency stop
                    if not self.emergency_stopped and not self.dry_run:
                        # Calculate adaptive reopen size
                        opposite_side = 'Sell' if side == 'Buy' else 'Buy'
                        reopen_margin = self.calculate_reopen_size(side, opposite_side)

                        self.logger.info(
                            f"🆕 [{self.symbol}] ADAPTIVE REOPEN (missed close): {side} with ${reopen_margin:.2f} margin"
                        )

                        # Use _open_initial_position() which handles reserve checks internally
                        success = self._open_initial_position(
                            side=side,
                            current_price=current_price,
                            custom_margin_usd=reopen_margin
                        )

                        if success:
                            # Set debounce - prevent duplicate reopen for 3 seconds
                            with self._sync_lock:
                                self._just_reopened_until_ts[side] = now + 3.0

                            self.logger.info(
                                f"✅ [{self.symbol}] Reopened {side} after untracked close with ${reopen_margin:.2f} margin"
                            )
                        else:
                            # Failed to reopen - will retry in next sync cycle
                            self.logger.warning(
                                f"⚠️ [{self.symbol}] Failed to reopen {side} after untracked close. "
                                f"Will retry in next sync cycle (60s)."
                            )

                # SCENARIO 3: Other mismatches - log warning (should have been caught at startup)
                else:
                    self.logger.warning(
                        f"⚠️  [{self.symbol}] Position mismatch during periodic sync for {side}: "
                        f"exchange={exchange_qty}, local={local_qty}, diff={qty_diff:.6f}. "
                        f"This might indicate manual intervention or API lag. "
                        f"If persists, restart bot."
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

            # RECOVERY: Detect asymmetric positions and failed reopening
            if not self.dry_run:
                long_count = self.pm.get_position_count('Buy')
                short_count = self.pm.get_position_count('Sell')

                # Check for imbalance (one side has positions, other is empty)
                # ⚠️ FIXED: Changed from >= 2 to >= 1 (any imbalance is a problem!)
                is_severely_unbalanced = (
                    (long_count >= 1 and short_count == 0) or
                    (short_count >= 1 and long_count == 0)
                )

                # Check if we have recorded failed reopening
                failed_reopen_sides = self._failed_reopen_sides

                if is_severely_unbalanced or failed_reopen_sides:
                    missing_side = 'Sell' if long_count > short_count else 'Buy'

                    # Only recover if missing side is in failed set OR severe imbalance
                    should_recover = (
                        missing_side in failed_reopen_sides or
                        is_severely_unbalanced
                    )

                    if should_recover:
                        self.logger.warning(
                            f"🔧 [{self.symbol}] RECOVERY MODE: Detected missing {missing_side} position. "
                            f"LONG={long_count} levels, SHORT={short_count} levels. "
                            f"Attempting to reopen {missing_side}..."
                        )

                        # Calculate reopen size
                        opposite_side = 'Sell' if missing_side == 'Buy' else 'Buy'
                        reopen_margin = self.calculate_reopen_size(missing_side, opposite_side)

                        self.logger.info(
                            f"🆕 [{self.symbol}] RECOVERY REOPEN: {missing_side} "
                            f"with ${reopen_margin:.2f} margin"
                        )

                        # Use _open_initial_position() which handles reserve checks and fallback internally
                        success = self._open_initial_position(
                            side=missing_side,
                            current_price=current_price,
                            custom_margin_usd=reopen_margin
                        )

                        if success:
                            # SUCCESS - clear failed flag
                            if missing_side in failed_reopen_sides:
                                failed_reopen_sides.remove(missing_side)

                            self.logger.info(
                                f"✅ [{self.symbol}] RECOVERY SUCCESS: Reopened {missing_side} "
                                f"with ${reopen_margin:.2f} margin"
                            )
                        else:
                            # Failed - will retry in next sync cycle
                            self.logger.error(
                                f"❌ [{self.symbol}] RECOVERY FAILED: Could not reopen {missing_side}. "
                                f"Will retry in next sync cycle (60s)."
                            )

            # CRITICAL: Restore pending orders for symmetry (if missing)
            # This ensures positions are always balanced with reserved pending
            for side in ['Buy', 'Sell']:
                if self.pm.get_position_count(side) > 0:
                    # Position exists - check if pending needed
                    placed_count = self._place_pending_for_symmetry(
                        opened_side=side,
                        base_price=current_price
                    )
                    if placed_count > 0:
                        self.logger.info(
                            f"[{self.symbol}] 🔄 Restored {placed_count} pending entries for {side} during sync"
                        )

        finally:
            # Clear syncing flag
            with self._sync_lock:
                self._is_syncing = False
