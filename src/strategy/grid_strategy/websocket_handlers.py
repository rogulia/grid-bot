"""WebSocket event handlers for Grid Strategy"""

import time
from ...utils.timestamp_converter import TimestampConverter
from ...utils.timezone import now_helsinki


class WebSocketHandlersMixin:
    """Mixin for WebSocket event handlers"""

    def on_price_update(self, current_price: float):
        """
        Handle price update and execute strategy logic

        Args:
            current_price: Current market price
        """
        # Store current price (from WebSocket)
        self.current_price = current_price

        # Update price history for ATR calculation (Phase 1: Advanced Risk Management)
        self._update_price_history(current_price)

        # Check for pending order recalculation on large price moves (>5%)
        # This prevents pending orders from becoming too far from market
        self._check_pending_recalculation(current_price)

        # Update current price for all tracked limit orders (for retry logic)
        # This ensures retries use fresh market price
        if hasattr(self, 'limit_order_manager'):
            with self.limit_order_manager._lock:
                for order_id in list(self.limit_order_manager._tracked_orders.keys()):
                    self.limit_order_manager.update_current_price(order_id, current_price)

            # Periodically cleanup old orders (once per minute)
            if not hasattr(self, '_last_cleanup_time'):
                self._last_cleanup_time = 0

            if time.time() - self._last_cleanup_time > 60:
                self.limit_order_manager.cleanup_old_orders(max_age_seconds=120)
                self._last_cleanup_time = time.time()

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
                # Position open/add - log
                self.logger.debug(
                    f"📝 [{symbol}] {side} OPEN: qty={exec_qty} price={exec_price}"
                )

                # Detect limit orders and notify LimitOrderManager
                order_id = exec_data.get('orderId', '')
                if order_type == 'Limit' and order_id:
                    # Limit order executed - notify LimitOrderManager
                    synthetic_order_data = {
                        'orderId': order_id,
                        'orderStatus': 'Filled',
                        'orderType': 'Limit',
                        'side': side,
                        'execQty': exec_qty
                    }
                    self.limit_order_manager.on_order_update(synthetic_order_data)

                return

            # POSITION CLOSE - process with real data from exchange
            # Determine which position side was closed
            # Order side 'Sell' closes LONG, 'Buy' closes SHORT
            closed_position_side = 'Buy' if side == 'Sell' else 'Sell'

            self.logger.info(
                f"💰 [{symbol}] {closed_position_side} CLOSED via WebSocket (order: {side}): "
                f"qty={closed_size} price={exec_price} pnl=${closed_pnl:.4f}"
            )

            # Convert timestamp to Helsinki timezone
            close_timestamp = TimestampConverter.exchange_ms_to_helsinki(exec_time_ms)

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
                    side=closed_position_side,  # Position side (Buy for LONG, Sell for SHORT)
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

            # Check if BOTH sides are now empty - clear reference quantities for fresh start
            if not self.pm.has_positions("Buy") and not self.pm.has_positions("Sell"):
                self.logger.info(f"[{self.symbol}] Both sides empty after close - clearing reference quantities")
                self.clear_reference_quantities()

            # CRITICAL: Cancel ALL pending entry orders (BOTH sides) before reopen
            # This prevents orphaned pending orders from opposite side
            self.logger.info(f"[{self.symbol}] ✅ Cancelling ALL pending entries (both sides) before reopen")
            self._cancel_all_pending_entries("Buy")   # LONG
            self._cancel_all_pending_entries("Sell")  # SHORT

            # Reopen initial position if needed
            # Get current price (could be from WebSocket or this execution price)
            current_price = exec_price

            # Check if we should reopen
            if not self.emergency_stopped and not self.dry_run:
                # CRITICAL: Reopening MUST succeed. Retry multiple times.
                max_retries = 3  # 3 attempts total
                retry_delay_base = 2  # Start with 2 seconds

                reopening_succeeded = False

                for attempt in range(max_retries):
                    # Calculate adaptive reopen size (Phase 5: Advanced Risk Management)
                    opposite_side = 'Sell' if closed_position_side == 'Buy' else 'Buy'
                    reopen_margin = self.calculate_reopen_size(closed_position_side, opposite_side)

                    # Log adaptive reopen calculation
                    self.logger.info(
                        f"🆕 [{symbol}] ADAPTIVE REOPEN: {closed_position_side} "
                        f"with ${reopen_margin:.2f} margin after TP "
                        f"(attempt {attempt+1}/{max_retries})"
                    )

                    # Use _open_initial_position() which now returns True/False
                    success = self._open_initial_position(
                        side=closed_position_side,
                        current_price=current_price,
                        custom_margin_usd=reopen_margin
                    )

                    if success:
                        # SUCCESS!
                        reopening_succeeded = True
                        self.logger.info(
                            f"✅ [{symbol}] Reopened {closed_position_side} "
                            f"with ${reopen_margin:.2f} margin on attempt {attempt+1}"
                        )
                        break  # Exit retry loop
                    else:
                        # _open_initial_position() returned False (reserve check failed or exception)
                        is_last_attempt = (attempt == max_retries - 1)

                        if is_last_attempt:
                            # ALL RETRIES FAILED - try fallback with initial_size
                            self.logger.warning(
                                f"⚠️ [{symbol}] All retry attempts failed. "
                                f"Trying FALLBACK with initial size ${self.initial_size_usd:.2f}..."
                            )
                        else:
                            # Calculate exponential backoff delay
                            retry_delay = retry_delay_base * (2 ** attempt)  # 2s, 4s, 8s

                            self.logger.warning(
                                f"⚠️ [{symbol}] Reopening attempt {attempt+1} failed. "
                                f"Retrying in {retry_delay}s..."
                            )
                            time.sleep(retry_delay)

                # FALLBACK: If all retries failed, try with initial_size_usd
                if not reopening_succeeded:
                    self.logger.info(
                        f"🔄 [{symbol}] FALLBACK REOPEN: Attempting with initial size ${self.initial_size_usd:.2f}"
                    )

                    fallback_success = self._open_initial_position(
                        side=closed_position_side,
                        current_price=current_price,
                        custom_margin_usd=self.initial_size_usd
                    )

                    if fallback_success:
                        reopening_succeeded = True
                        self.logger.info(
                            f"✅ [{symbol}] FALLBACK SUCCESS: Reopened {closed_position_side} "
                            f"with initial size ${self.initial_size_usd:.2f}"
                        )
                    else:
                        self.logger.error(
                            f"❌ [{symbol}] FALLBACK FAILED: Could not reopen even with initial size"
                        )

                # Check if reopening failed completely (including fallback)
                if not reopening_succeeded:
                    # Store failed reopen info for sync_with_exchange to detect
                    self._failed_reopen_sides.add(closed_position_side)

                    self.logger.error(
                        f"💥 [{symbol}] Position {closed_position_side} NOT REOPENED after TP close! "
                        f"Bot will attempt recovery in next sync cycle (60s). "
                        f"Manual monitoring recommended."
                    )

        except Exception as e:
            self.logger.error(
                f"❌ [{symbol}] Error processing execution event: {e}",
                exc_info=True
            )

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
                            # CRITICAL: Reopening MUST succeed. Retry multiple times.
                            max_retries = 3
                            retry_delay_base = 2

                            reopening_succeeded = False

                            for attempt in range(max_retries):
                                # Calculate adaptive reopen size
                                opposite_side = 'Sell' if side == 'Buy' else 'Buy'
                                reopen_margin = self.calculate_reopen_size(side, opposite_side)

                                self.logger.info(
                                    f"[{self.symbol}] ADAPTIVE REOPEN via WebSocket: {side} "
                                    f"with ${reopen_margin:.2f} margin (attempt {attempt+1}/{max_retries})"
                                )

                                # Use _open_initial_position() which now returns True/False
                                success = self._open_initial_position(
                                    side=side,
                                    current_price=current_price,
                                    custom_margin_usd=reopen_margin
                                )

                                if success:
                                    reopening_succeeded = True
                                    self.logger.info(
                                        f"✅ [{self.symbol}] Reopened {side} with ${reopen_margin:.2f} "
                                        f"margin on attempt {attempt+1}"
                                    )
                                    break
                                else:
                                    is_last_attempt = (attempt == max_retries - 1)
                                    if not is_last_attempt:
                                        retry_delay = retry_delay_base * (2 ** attempt)
                                        self.logger.warning(
                                            f"⚠️ [{self.symbol}] Reopening attempt {attempt+1} failed. "
                                            f"Retrying in {retry_delay}s..."
                                        )
                                        time.sleep(retry_delay)

                            # FALLBACK: If all retries failed, try with initial_size_usd
                            if not reopening_succeeded:
                                self.logger.info(
                                    f"🔄 [{self.symbol}] FALLBACK: Attempting reopen with "
                                    f"initial size ${self.initial_size_usd:.2f}"
                                )

                                fallback_success = self._open_initial_position(
                                    side=side,
                                    current_price=current_price,
                                    custom_margin_usd=self.initial_size_usd
                                )

                                if fallback_success:
                                    reopening_succeeded = True
                                    self.logger.info(
                                        f"✅ [{self.symbol}] FALLBACK SUCCESS: Reopened {side} "
                                        f"with initial size ${self.initial_size_usd:.2f}"
                                    )

                            # If all failed, add to failed reopens
                            if not reopening_succeeded:
                                self._failed_reopen_sides.add(side)
                                self.logger.error(
                                    f"💥 [{self.symbol}] Position {side} NOT REOPENED after WebSocket close! "
                                    f"Added to recovery queue. Will attempt recovery in next sync cycle (60s)."
                                )

            else:
                # Position still open (size > 0)

                # Check if we have this position tracked locally
                local_qty = self.pm.get_total_quantity(side)

                if local_qty == 0:
                    # Check if we're currently syncing/restoring
                    with self._sync_lock:
                        is_syncing = self._is_syncing

                    if is_syncing:
                        # Position update during sync/restore - DON'T block, allow sync to detect and fix
                        # Removed _needs_resync trigger to prevent infinite loops
                        self.logger.info(
                            f"[{self.symbol}] Position update during sync: {side} size={size_float} "
                            f"@ ${avg_price} - sync_with_exchange() will handle mismatch if needed"
                        )
                        return

                    # Position exists on exchange but not tracked locally (and NOT during sync)
                    # This should NEVER happen - positions should ONLY be restored via REST API in restore/sync
                    # WebSocket should only update existing positions, not create new ones
                    self.logger.error(
                        f"❌ [{self.symbol}] Position mismatch: exchange has {side} position ({size_float} @ ${avg_price}) "
                        f"but local tracking is empty. This indicates restore/sync failed or was skipped."
                    )

                    # FAIL-FAST: Position should have been restored by REST API
                    reason = (
                        f"Position exists on exchange but not tracked locally for {side}: "
                        f"exchange={size_float}, local=0. Position restoration should happen ONLY via REST API "
                        f"in restore_state_from_exchange() or sync_with_exchange(). Restart bot to trigger proper sync."
                    )

                    # Create emergency stop flag
                    self._create_emergency_stop_flag(reason)
                    self.emergency_stopped = True

                    raise RuntimeError(f"[{self.symbol}] {reason}")

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

        Called when wallet stream sends an update. Updates balance, MM Rate, Initial Margin,
        and Maintenance Margin cache.

        Args:
            wallet_data: Wallet data from Bybit WebSocket
        """
        try:
            # Extract ALL balance fields from WebSocket
            total_available = wallet_data.get('totalAvailableBalance')
            account_mm_rate = wallet_data.get('accountMMRate')
            total_initial_margin = wallet_data.get('totalInitialMargin')
            total_maintenance_margin = wallet_data.get('totalMaintenanceMargin')

            if total_available is not None:
                balance = float(total_available)

                # Update BalanceManager cache from WebSocket with ALL fields
                if self.balance_manager:
                    # Convert MM Rate from decimal to percentage (e.g., 0.0017 -> 0.17%)
                    mm_rate_pct = None
                    if account_mm_rate and account_mm_rate != '':
                        mm_rate_pct = float(account_mm_rate) * 100

                    # Convert Initial Margin to float
                    initial_margin = None
                    if total_initial_margin and total_initial_margin != '':
                        initial_margin = float(total_initial_margin)

                    # Convert Maintenance Margin to float
                    maintenance_margin = None
                    if total_maintenance_margin and total_maintenance_margin != '':
                        maintenance_margin = float(total_maintenance_margin)

                    # Update BalanceManager with all fields
                    self.balance_manager.update_from_websocket(
                        balance=balance,
                        mm_rate=mm_rate_pct,
                        initial_margin=initial_margin,
                        maintenance_margin=maintenance_margin
                    )

                    # Build log message
                    log_parts = [f"${balance:.2f}"]
                    if mm_rate_pct is not None:
                        log_parts.append(f"MM Rate: {mm_rate_pct:.4f}%")
                    if initial_margin is not None:
                        log_parts.append(f"IM: ${initial_margin:.2f}")
                    if maintenance_margin is not None:
                        log_parts.append(f"MM: ${maintenance_margin:.2f}")

                    self.logger.debug(f"[{self.symbol}] Wallet update: {', '.join(log_parts)}")

        except Exception as e:
            self.logger.error(
                f"[{self.symbol}] Error processing wallet update: {e}",
                exc_info=True
            )

    def on_order_update(self, order_data: dict):
        """
        Handle order update from WebSocket

        Called when order stream sends an update. Tracks TP order IDs automatically
        and manages limit order retries.

        Args:
            order_data: Order data from Bybit WebSocket
        """
        try:
            order_id = order_data.get('orderId')
            order_status = order_data.get('orderStatus')
            order_type = order_data.get('orderType')
            side = order_data.get('side')
            position_idx = order_data.get('positionIdx')
            reduce_only = order_data.get('reduceOnly', False)

            # DEBUG: Log every order update to verify callback is working
            self.logger.debug(
                f"[{self.symbol}] 📞 Order update received: orderId={order_id}, "
                f"status={order_status}, type={order_type}, side={side}, "
                f"positionIdx={position_idx}, reduceOnly={reduce_only}"
            )

            # Check if we're currently syncing/restoring
            with self._sync_lock:
                is_syncing = self._is_syncing

            if is_syncing:
                # Order update during restore - trigger re-sync
                with self._resync_lock:
                    self._needs_resync = True
                    self._resync_triggers.append({
                        'timestamp': now_helsinki().isoformat(),
                        'event': 'order_update',
                        'orderId': order_id,
                        'orderStatus': order_status,
                        'side': side,
                        'type': order_type
                    })

                self.logger.info(
                    f"[{self.symbol}] Order update during restore: {side} {order_type} "
                    f"status={order_status} - will re-sync"
                )
                return

            # Pass to LimitOrderManager for limit order tracking
            # Manager handles timeout/retry logic for its tracked orders
            self.limit_order_manager.on_order_update(order_data)

            # Track pending ENTRY orders (NOT reduceOnly)
            # These are limit orders placed for position symmetry
            if not reduce_only and order_type == 'Limit' and order_status in ['Filled', 'PartiallyFilled', 'Cancelled']:
                # Check if this is one of our tracked pending orders
                grid_level = None
                track_side = side  # 'Buy' or 'Sell'

                with self._pending_entry_lock:
                    for level, oid in self._pending_entry_orders[track_side].items():
                        if oid == order_id:
                            grid_level = level
                            break

                if grid_level is not None:
                    # This is our pending entry order
                    if order_status == 'Filled':
                        # FULLY filled - add to position manager
                        qty = float(order_data.get('qty', 0))
                        avg_price = float(order_data.get('avgPrice', 0))

                        # CRITICAL: Orphan position check
                        # Verify that position for this side still exists
                        current_positions = (self.pm.long_positions if track_side == 'Buy'
                                            else self.pm.short_positions)

                        if not current_positions:
                            self.logger.warning(
                                f"[{self.symbol}] ⚠️ Pending entry filled AFTER position closed! "
                                f"{track_side} level {grid_level} filled but position already closed. "
                                f"Adding orphan position - will be closed by TP or emergency."
                            )

                        # Add to position manager
                        self.pm.add_position(
                            side=track_side,
                            entry_price=avg_price,
                            quantity=qty,
                            grid_level=grid_level,
                            order_id=order_id
                        )

                        self.logger.info(
                            f"[{self.symbol}] ✅ Pending entry FILLED: {track_side} level {grid_level} "
                            f"@ ${avg_price:.4f} (qty={qty:.4f})"
                        )

                        # Remove from pending tracking
                        with self._pending_entry_lock:
                            del self._pending_entry_orders[track_side][grid_level]

                        # Update TP order with new average entry
                        self._update_tp_order(track_side)

                    elif order_status == 'PartiallyFilled':
                        # Partial fill - log but wait for full fill
                        filled_qty = float(order_data.get('cumExecQty', 0))
                        total_qty = float(order_data.get('qty', 0))
                        fill_percent = (filled_qty / total_qty) * 100 if total_qty > 0 else 0

                        self.logger.info(
                            f"[{self.symbol}] 📊 Pending entry partial fill: {track_side} level {grid_level} "
                            f"{fill_percent:.1f}% filled ({filled_qty:.4f}/{total_qty:.4f})"
                        )

                    elif order_status == 'Cancelled':
                        # Cancelled - AUTO-RETRY (Critical fix #1)
                        self.logger.warning(
                            f"[{self.symbol}] ⚠️ Pending entry cancelled by exchange: "
                            f"{track_side} level {grid_level} (orderId={order_id}). "
                            f"AUTO-RETRYING..."
                        )

                        # Remove from tracking
                        with self._pending_entry_lock:
                            del self._pending_entry_orders[track_side][grid_level]

                        # Automatically re-place with current price
                        retry_order_id = self.place_pending_entry_order(
                            side=track_side,
                            grid_level=grid_level,
                            base_price=self.current_price
                        )

                        if retry_order_id:
                            self.logger.info(
                                f"[{self.symbol}] ✅ Pending entry re-placed: {track_side} level {grid_level} "
                                f"(new orderId={retry_order_id})"
                            )
                        else:
                            self.logger.error(
                                f"[{self.symbol}] ❌ Failed to re-place pending entry: {track_side} level {grid_level}. "
                                f"Will retry in next sync."
                            )

            # Only track Take Profit orders (Limit orders with reduceOnly=True)
            # TP orders are created as Limit orders, NOT Market orders!
            if reduce_only and order_type in ['Market', 'Limit'] and order_status in ['New', 'Filled', 'Cancelled']:
                # Determine which side this TP order belongs to
                # positionIdx: 1=LONG, 2=SHORT
                if position_idx == '1':  # LONG TP (closes Buy position)
                    track_side = 'Buy'
                elif position_idx == '2':  # SHORT TP (closes Sell position)
                    track_side = 'Sell'
                else:
                    return  # Unknown position index

                # Check if TP tracking is in PENDING state (race condition prevention)
                with self._tp_orders_lock:
                    tracked_tp_id = self._tp_orders.get(track_side)

                if tracked_tp_id == "PENDING":
                    # TP order is being created - skip WebSocket update to avoid race
                    self.logger.debug(
                        f"[{self.symbol}] Skipping TP order update - tracking not ready (PENDING state)"
                    )
                    return

                if order_status == 'New':
                    # New TP order created - track it
                    with self._tp_orders_lock:
                        self._tp_orders[track_side] = order_id
                    self.logger.info(
                        f"[{self.symbol}] 🎯 TP order tracked: {track_side} -> {order_id}"
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

    def _check_pending_recalculation(self, current_price: float):
        """
        Check if pending orders need recalculation due to large price moves

        If price moved >5% since pending orders were placed, cancel and recalculate
        them at current market price to ensure they stay close to market.

        Args:
            current_price: Current market price
        """
        # Initialize price tracking dict if not exists
        if not hasattr(self, '_last_pending_check_price'):
            self._last_pending_check_price = {
                "Buy": current_price,
                "Sell": current_price
            }

        # Check each side for recalculation need
        for side in ["Buy", "Sell"]:
            with self._pending_entry_lock:
                pending_orders = dict(self._pending_entry_orders.get(side, {}))

            if not pending_orders:
                # No pending orders for this side, update last check price
                self._last_pending_check_price[side] = current_price
                continue

            # Calculate price move since last check
            last_price = self._last_pending_check_price[side]
            if last_price <= 0:
                # Invalid last price, reset
                self._last_pending_check_price[side] = current_price
                continue

            price_change_pct = abs((current_price - last_price) / last_price * 100)

            # Recalculate if price moved >5%
            if price_change_pct > 5.0:
                self.logger.info(
                    f"[{self.symbol}] Price moved {price_change_pct:.2f}% - "
                    f"recalculating {len(pending_orders)} pending {side} orders"
                )

                # Cancel old pending orders
                self._cancel_all_pending_entries(side)

                # Recalculate and place new pending orders
                # Only if position still exists on this side
                if self.pm.has_positions(side):
                    # Get current position levels
                    positions = self.pm.long_positions if side == 'Buy' else self.pm.short_positions
                    max_level = max(pos.grid_level for pos in positions) if positions else 0

                    # Place pending for each level that opposite side hasn't filled yet
                    opposite_side = 'Sell' if side == 'Buy' else 'Buy'
                    opposite_positions = self.pm.short_positions if opposite_side == 'Sell' else self.pm.long_positions
                    opposite_max_level = max(pos.grid_level for pos in opposite_positions) if opposite_positions else 0

                    # Place pending orders for levels where opposite side is ahead
                    if opposite_max_level > max_level:
                        levels_to_place = list(range(max_level + 1, opposite_max_level + 1))
                        base_price = current_price

                        placed_count = 0
                        for level in levels_to_place:
                            order_id = self.place_pending_entry_order(
                                side=side,
                                grid_level=level,
                                base_price=base_price
                            )
                            if order_id:
                                placed_count += 1

                        if placed_count > 0:
                            self.logger.info(
                                f"[{self.symbol}] ✅ Recalculated and placed {placed_count} pending {side} orders "
                                f"at new price ${current_price:.4f}"
                            )

                # Update last check price
                self._last_pending_check_price[side] = current_price
