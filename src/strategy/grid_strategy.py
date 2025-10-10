"""Grid trading strategy with dual-sided hedging"""

import logging
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
        metrics_tracker: Optional['MetricsTracker'] = None
    ):
        """
        Initialize grid strategy

        Args:
            client: Bybit API client
            position_manager: Position manager instance
            config: Strategy configuration
            dry_run: Dry run mode (no real orders)
            metrics_tracker: Optional metrics tracker for analytics
        """
        self.logger = logging.getLogger("sol-trader.grid_strategy")
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

        # Get instrument info from Bybit API
        self._load_instrument_info()

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
        rounded_qty = round(raw_qty / self.qty_step) * self.qty_step

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

    def sync_with_exchange(self, current_price: float):
        """
        Sync local position state with exchange reality
        - Check if positions exist on exchange
        - If not, open initial position
        - If yes, update TP order if needed

        Args:
            current_price: Current market price
        """
        self.logger.info(f"🔄 [{self.symbol}] Syncing positions with exchange...")

        for side in ['Buy', 'Sell']:
            # Check if we have local position tracked
            local_qty = self.pm.get_total_quantity(side)

            # Check what's on exchange
            exchange_pos = self.client.get_active_position(self.symbol, side, self.category)

            if exchange_pos:
                exchange_qty = float(exchange_pos.get('size', 0))
                exchange_avg_price = float(exchange_pos.get('avgPrice', current_price))

                if local_qty == 0:
                    # Exchange has position but we don't track it -> restore tracking
                    self.logger.info(
                        f"📥 [{self.symbol}] Restoring {side} position from exchange: "
                        f"{exchange_qty} @ ${exchange_avg_price:.4f}"
                    )
                    self.pm.add_position(
                        side=side,
                        entry_price=exchange_avg_price,
                        quantity=exchange_qty,
                        grid_level=0  # We don't know exact grid level, assume 0
                    )
                    # Set TP order
                    self._update_tp_order(side)
                else:
                    # Both have position -> verify TP order exists and recover ID if lost
                    self.logger.info(f"✅ [{self.symbol}] {side} position already synced")

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

                    if not self.dry_run:
                        try:
                            # Wait for exchange to process
                            import time
                            time.sleep(1)

                            # Get last closed PnL record
                            closed_records = self.client.get_closed_pnl(self.symbol, limit=1)
                            if closed_records:
                                record = closed_records[0]
                                actual_pnl = float(record.get('closedPnl', 0))
                                open_fee = float(record.get('openFee', 0))
                                close_fee = float(record.get('closeFee', 0))

                                # Determine close reason by comparing exit vs entry prices
                                # For SHORT: exitPrice < entryPrice = profit (sold high, bought back low)
                                # For LONG: exitPrice > entryPrice = profit (bought low, sold high)
                                avg_entry = float(record.get('avgEntryPrice', 0))
                                avg_exit = float(record.get('avgExitPrice', 0))

                                if side == 'Buy':  # LONG position
                                    is_profitable = avg_exit > avg_entry
                                else:  # SHORT position
                                    is_profitable = avg_exit < avg_entry

                                if is_profitable:
                                    price_diff_pct = abs((avg_exit - avg_entry) / avg_entry * 100)
                                    close_reason = f"Take Profit ({price_diff_pct:.2f}%)"
                                else:
                                    close_reason = "Stop-Loss or Liquidation"

                                self.logger.error(
                                    f"💥 [{self.symbol}] {side} CLOSED ON EXCHANGE: "
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
                            price=current_price,
                            quantity=local_qty,
                            reason=close_reason,
                            pnl=actual_pnl,
                            open_fee=open_fee,
                            close_fee=close_fee,
                            funding_fee=funding_fee
                        )

                    # NOW remove local tracking
                    self.pm.remove_all_positions(side)
                    self.pm.set_tp_order_id(side, None)

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
        try:
            # Check risk limits first
            if not self._check_risk_limits(current_price):
                return

            # Check for grid entries (averaging)
            self._check_grid_entries(current_price)

            # Check for take profit
            self._check_take_profit(current_price)

        except Exception as e:
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
            self.logger.info(
                f"[{self.symbol}] {side} grid level hit: {price_change_pct:.2f}% from last entry"
            )
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
                dry_run=self.dry_run
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
                dry_run=self.dry_run
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
                dry_run=self.dry_run
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
        Check risk limits and close positions if necessary

        For hedged positions (LONG+SHORT), checks Account Maintenance Margin Rate
        instead of individual position liqPrice (which is meaningless for hedges).

        Args:
            current_price: Current market price

        Returns:
            True if safe to continue, False if risk limits hit
        """
        # Get wallet balance with Account Maintenance Margin Rate (REQUIRED - no fallback!)
        available_balance = None
        account_mm_rate = None

        if not self.dry_run:
            balance_info = self.client.get_wallet_balance(account_type="UNIFIED")
            if balance_info and 'list' in balance_info:
                for account in balance_info['list']:
                    if account.get('accountType') == 'UNIFIED':
                        # totalAvailableBalance = funds available for new positions
                        available_balance = float(
                            account.get('totalAvailableBalance', 0)
                        )

                        # accountMMRate = Account Maintenance Margin Rate (%)
                        # This is the ONLY metric that matters for hedged positions!
                        account_mm_rate_str = account.get('accountMMRate', '')
                        if account_mm_rate_str and account_mm_rate_str != '':
                            # Convert to percentage (Bybit returns as decimal, e.g. "0.0017" = 0.17%)
                            account_mm_rate = float(account_mm_rate_str) * 100

                        # Note: MM Rate is logged in sync_with_exchange() every 60s
                        # to avoid spamming logs (this method is called every price update)
                        break

            if available_balance is None:
                raise RuntimeError(
                    f"[{self.symbol}] Failed to get wallet balance from exchange - cannot check risk limits"
                )
        else:
            # In dry run, use mock values
            available_balance = 1000.0
            account_mm_rate = 0.17  # Mock: safe value

        # Check Account Maintenance Margin Rate (for hedged positions)
        # This is the CORRECT way to check liquidation risk for LONG+SHORT strategy
        if not self.dry_run and account_mm_rate is not None:
            # Log current MM rate for monitoring
            if account_mm_rate > 50.0:  # Warning if > 50%
                self.logger.warning(
                    f"⚠️ Account Maintenance Margin Rate: {account_mm_rate:.2f}% (caution!)"
                )

            # Emergency close if >= 90%
            if account_mm_rate >= 90.0:
                self.logger.error(
                    f"💥 CRITICAL: Account MM Rate {account_mm_rate:.2f}% >= 90%! "
                    f"EMERGENCY CLOSE ALL POSITIONS!"
                )

                # Close ALL positions (both LONG and SHORT)
                for side in ['Buy', 'Sell']:
                    total_qty = self.pm.get_total_quantity(side)
                    if total_qty > 0:
                        self._emergency_close(
                            side, current_price,
                            f"Account MM Rate {account_mm_rate:.2f}% >= 90%"
                        )

                # STOP BOT - this is critical!
                raise RuntimeError(
                    f"[{self.symbol}] Bot stopped: Account Maintenance Margin Rate "
                    f"{account_mm_rate:.2f}% reached critical level (>= 90%). "
                    f"All positions closed. Review account and fix issues before restarting."
                )

        # Check total exposure
        total_qty_long = self.pm.get_total_quantity('Buy')
        total_qty_short = self.pm.get_total_quantity('Sell')
        total_exposure = (total_qty_long + total_qty_short) * current_price

        if total_exposure > self.max_exposure:
            self.logger.warning(
                f"⚠️ [{self.symbol}] Max exposure reached: ${total_exposure:.2f} > ${self.max_exposure:.2f}"
            )
            return False

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
                dry_run=self.dry_run
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
