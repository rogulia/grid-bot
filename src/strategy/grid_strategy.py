"""Grid trading strategy with dual-sided hedging"""

import logging
import time
import threading
from datetime import datetime
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
    from ..core.trading_account import TradingAccount


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
        balance_manager: Optional['BalanceManager'] = None,
        trading_account: Optional['TradingAccount'] = None
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
            trading_account: Optional reference to parent TradingAccount (for reserve checking)
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

        # Trading account reference (for reserve checking)
        self.trading_account = trading_account

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
        # Balance buffer for reserve checks (configurable per account)
        self.balance_buffer_percent = config.get('balance_buffer_percent', 15.0)

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

        # ATR calculation for dynamic safety factor (Phase 1: Advanced Risk Management)
        # Store last N prices for ATR calculation
        self._price_history = []
        self._atr_period = 20  # Last 20 price updates
        self._cached_atr_percent = None
        self._atr_last_update = 0

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

    def _calculate_grid_levels_for_margin(self, target_margin: float) -> list:
        """
        Calculate which grid levels are needed to reach target margin
        
        Args:
            target_margin: Target margin in USD
            
        Returns:
            List of grid levels needed (e.g., [0, 1, 2] for levels 0+1+2)
        """
        levels = []
        current_margin = 0.0
        level = 0
        
        while current_margin < target_margin and level < self.max_grid_levels:
            # Margin for this level: initial × multiplier^level
            level_margin = self.initial_size_usd * (self.multiplier ** level)
            
            if current_margin + level_margin <= target_margin * 1.01:  # 1% tolerance
                levels.append(level)
                current_margin += level_margin
                level += 1
            else:
                break
        
        return levels if levels else [0]

    def _open_initial_position(self, side: str, current_price: float, custom_margin_usd: Optional[float] = None):
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
                    response = self.client.place_order(
                        symbol=self.symbol,
                        side=side,
                        qty=level_qty,
                        order_type="Market",
                        category=self.category
                    )
                    self.logger.debug(f"[{self.symbol}] Level {grid_level} order response: {response}")

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

    def is_stopped(self) -> bool:
        """
        Check if strategy is in emergency stop state

        Returns:
            True if emergency stop was triggered, False otherwise
        """
        return self.emergency_stopped

    def determine_trend_side(self) -> tuple[str, str, str]:
        """
        Determine trend direction based on grid levels (Phase 4: Advanced Risk Management)

        The side that has averaged MORE is the TREND side (price moved against it).

        Returns:
            (trend_side, counter_trend_side, trend_direction)
            Example: ('Sell', 'Buy', 'DOWN') means downtrend
        """
        long_level = self.pm.get_position_count('Buy')
        short_level = self.pm.get_position_count('Sell')

        if short_level > long_level:
            # SHORT усреднялся больше → downtrend (цена падала)
            return ('Sell', 'Buy', 'DOWN')
        else:
            # LONG усреднялся больше → uptrend (цена росла)
            return ('Buy', 'Sell', 'UP')

    def get_total_margin(self, side: str) -> float:
        """
        Calculate total margin used by a side (Phase 5: Advanced Risk Management)

        Args:
            side: 'Buy' or 'Sell'

        Returns:
            Total margin in USD
        """
        positions = self.pm.long_positions if side == 'Buy' else self.pm.short_positions
        total_margin = 0.0

        for pos in positions:
            # Position value = quantity × current_price
            position_value = pos.quantity * self.current_price
            # Margin = position_value / leverage
            margin = position_value / self.leverage
            total_margin += margin

        return total_margin

    def calculate_reopen_size(self, closed_side: str, opposite_side: str) -> float:
        """
        Calculate adaptive reopen size: MINUS TWO STEPS from opposite side

        Logic:
        - If opposite has levels 0+1+2+3+4 (total 5 levels)
        - Reopen on: 0+1+2 (3 levels, minus last two steps)
        - Reserve for balance: levels 3+4 (for panic mode balancing)

        This keeps positions close but leaves margin for emergency balancing.

        Args:
            closed_side: Side that closed ('Buy' or 'Sell')
            opposite_side: Side still open ('Sell' or 'Buy')

        Returns:
            Reopen margin in USD
        """
        # Get opposite side positions to find max grid level
        opposite_positions = (self.pm.long_positions if opposite_side == 'Buy' 
                            else self.pm.short_positions)
        
        if not opposite_positions:
            # No opposite positions - just reopen initial
            return self.initial_size_usd
        
        # Find max grid level on opposite side
        max_opposite_level = max(pos.grid_level for pos in opposite_positions)
        
        # Calculate reopen levels: minus TWO steps
        reopen_max_level = max(0, max_opposite_level - 2)
        
        # Calculate total margin for these levels
        reopen_margin = 0.0
        for level in range(reopen_max_level + 1):
            level_margin = self.initial_size_usd * (self.multiplier ** level)
            reopen_margin += level_margin
        
        # Ensure at least initial size
        if reopen_margin < self.initial_size_usd:
            reopen_margin = self.initial_size_usd
            reopen_max_level = 0
        
        # Calculate reserved margin (last two levels)
        reserved_levels = []
        reserved_margin = 0.0
        for level in range(reopen_max_level + 1, max_opposite_level + 1):
            level_margin = self.initial_size_usd * (self.multiplier ** level)
            reserved_margin += level_margin
            reserved_levels.append(level)

        self.logger.info(
            f"[{self.symbol}] Adaptive reopen: opposite has {max_opposite_level + 1} levels, "
            f"reopening to level {reopen_max_level} (${reopen_margin:.2f}), "
            f"reserving levels {reserved_levels} (${reserved_margin:.2f}) for balance"
        )

        return reopen_margin

    def calculate_atr_percent(self) -> float:
        """
        Calculate Average True Range (ATR) as percentage of current price

        ATR measures market volatility over the last N price updates.
        Used for dynamic safety factor calculation.

        Returns:
            ATR as percentage (e.g., 1.5 for 1.5%)
            Returns 1.5 if insufficient data (default medium volatility)
        """
        # Return cached value if updated recently (within 60 seconds)
        if self._cached_atr_percent is not None:
            time_since_update = time.time() - self._atr_last_update
            if time_since_update < 60:
                return self._cached_atr_percent

        # Need at least 2 prices to calculate ranges
        if len(self._price_history) < 2:
            self._cached_atr_percent = 1.5  # Default: medium volatility
            return self._cached_atr_percent

        # Calculate true ranges for all consecutive prices
        true_ranges = []
        for i in range(1, len(self._price_history)):
            prev_price = self._price_history[i-1]
            curr_price = self._price_history[i]

            # True range = abs(high - low) for single price = abs(curr - prev)
            true_range = abs(curr_price - prev_price)
            true_ranges.append(true_range)

        # Average true range
        atr = sum(true_ranges) / len(true_ranges)

        # Convert to percentage of current price
        if self.current_price > 0:
            atr_percent = (atr / self.current_price) * 100
        else:
            atr_percent = 1.5  # Fallback

        # Cache result
        self._cached_atr_percent = atr_percent
        self._atr_last_update = time.time()

        return atr_percent

    def _update_price_history(self, price: float):
        """
        Update price history for ATR calculation

        Args:
            price: New price to add to history
        """
        self._price_history.append(price)

        # Keep only last N prices
        if len(self._price_history) > self._atr_period:
            self._price_history.pop(0)

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
            List of (qty, price, grid_level) tuples
        """
        try:
            # Get order history from exchange
            # Use increased limit to capture more history (was 50, now 100)
            from config.constants import TradingConstants
            orders = self.client.get_order_history(
                symbol=self.symbol,
                category=self.category,
                limit=TradingConstants.ORDER_HISTORY_LIMIT
            )
            
            self.logger.info(
                f"[{self.symbol}] Retrieved {len(orders) if orders else 0} orders from history for {side} restoration"
            )
            
            if not orders:
                # FAIL-FAST: Cannot restore without order history
                raise RuntimeError(
                    f"[{self.symbol}] No order history available - cannot restore {side} position. "
                    f"Manual intervention required: close position on exchange and restart bot."
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
                # FAIL-FAST: Cannot restore without position orders
                raise RuntimeError(
                    f"[{self.symbol}] No {side} position orders found after last TP - cannot restore. "
                    f"Manual intervention required: close position on exchange and restart bot."
                )
            
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
                        self.logger.warning(
                            f"⚠️ [{self.symbol}] Restored {abs(qty_diff):.6f} less than exchange "
                            f"(restored={restored_qty}, exchange={total_qty}). "
                            f"New orders likely opened after history fetch. Will sync on next update."
                        )
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

            # Add each position to PositionManager
            for qty, price, grid_level, order_id in positions:
                self.pm.add_position(
                    side=side,
                    entry_price=price if price > 0 else exchange_avg_price,
                    quantity=qty,
                    grid_level=grid_level,
                    order_id=order_id
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

    def sync_with_exchange(self, current_price: float):
        """
        Sync local position state with exchange reality

        **REST API Position Check (at startup only):**
        - Uses get_active_position() REST API to fetch real position state from exchange
        - Compares exchange quantity vs local quantity with strict tolerance (0.001)
        - RESTORES positions if exchange has them but local tracking is empty
        - OPENS initial positions if both exchange and local have none
        - FAIL-FAST if unexplained mismatch detected (manual intervention required)

        **Position Restoration Flow (bot restart):**
        1. Bot starts, sync_with_exchange() called on first price update
        2. Fetch position from exchange via REST API (source of truth)
        3. If exchange_qty > 0 and local_qty == 0 → RESTORE position
        4. Create TP order for restored position
        5. Continue normal trading via WebSocket

        **During Normal Operation:**
        - Execution WebSocket handles all position updates in real-time
        - This method only runs periodically to verify TP orders exist

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

            # Get local position state
            local_qty = self.pm.get_total_quantity(side)

            # Calculate difference (strict tolerance for rounding only)
            qty_diff = abs(exchange_qty - local_qty)
            tolerance = 0.001  # Only rounding errors allowed

            self.logger.debug(
                f"[{self.symbol}] {side} position check: "
                f"exchange={exchange_qty}, local={local_qty}, diff={qty_diff:.6f}"
            )

            # Check scenario: no position on either side (open initial)
            if exchange_qty == 0 and local_qty == 0:
                # OPEN: No position on exchange or locally - open initial position
                self.logger.info(
                    f"🆕 [{self.symbol}] No {side} position exists - opening initial position"
                )

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

                order_id = None
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

                        # Extract orderId from response
                        if response and 'result' in response:
                            order_id = response['result'].get('orderId')

                        # Track position locally
                        self.pm.add_position(
                            side=side,
                            entry_price=current_price,
                            quantity=initial_qty,
                            grid_level=0,
                            order_id=order_id
                        )

                        # Create TP order for new position
                        # Force cancel all reduce-only orders to ensure clean state
                        tp_id = self._update_tp_order(side, force_cancel_all=True)
                        if not tp_id:
                            # Fail-fast: TP is critical for risk management
                            raise RuntimeError(
                                f"Failed to create TP order for initial {side} position - place_tp_order returned None"
                            )

                        # Log trade to metrics
                        self.metrics_tracker.log_trade(
                            timestamp=datetime.now(),
                            symbol=self.symbol,
                            side=side,
                            action='OPEN',
                            price=current_price,
                            quantity=initial_qty,
                            reason='Initial position (sync)'
                        )

                    except Exception as e:
                        reason = f"Failed to open initial {side} position: {e}"
                        self.logger.error(f"❌ [{self.symbol}] {reason}")
                        raise RuntimeError(f"[{self.symbol}] {reason}") from e
                else:
                    # Dry run: just track the position
                    self.pm.add_position(
                        side=side,
                        entry_price=current_price,
                        quantity=initial_qty,
                        grid_level=0,
                        order_id=None
                    )

            # Check if positions are synced (within tolerance)
            elif qty_diff <= tolerance:
                # SYNCED: Exchange and local match
                self.logger.info(
                    f"✅ [{self.symbol}] {side} position SYNCED: "
                    f"exchange={exchange_qty}, local={local_qty}"
                )

                # Verify TP order exists (if position exists)
                if local_qty > 0:
                    with self._tp_orders_lock:
                        tp_order_id = self._tp_orders.get(side) or self.pm.get_tp_order_id(side)

                    if not tp_order_id and not self.dry_run:
                        # TP order missing - create one
                        self.logger.warning(
                            f"⚠️  [{self.symbol}] TP order missing for {side} position (qty={local_qty}) - creating new one"
                        )
                        self._update_tp_order(side)

            elif exchange_qty > 0 and local_qty == 0:
                # RESTORE: Exchange has position but local tracking is empty
                self.logger.warning(
                    f"📥 [{self.symbol}] Position mismatch detected for {side}: "
                    f"exchange={exchange_qty}, local={local_qty} - RESTORING from exchange"
                )

                if not self.dry_run:
                    self._restore_position_from_exchange(side, exchange_position)
                else:
                    # Dry run: just track the position
                    self.pm.add_position(
                        side=side,
                        entry_price=current_price,
                        quantity=exchange_qty,
                        grid_level=0,
                        order_id=None
                    )

            else:
                # FAIL-FAST: Unexplained mismatch (manual intervention required)
                reason = (
                    f"Position mismatch for {side} requires manual intervention: "
                    f"exchange={exchange_qty}, local={local_qty}, diff={qty_diff:.6f}. "
                    f"This may indicate: (1) positions opened outside bot, "
                    f"(2) partial close not tracked, (3) exchange API issue. "
                    f"Please verify positions on exchange and restart bot."
                )
                self.logger.error(f"❌ [{self.symbol}] {reason}")

                # Create emergency stop flag
                self._create_emergency_stop_flag(reason)
                self.emergency_stopped = True

                # Stop bot - raise RuntimeError to prevent further operations
                raise RuntimeError(f"[{self.symbol}] {reason}")

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

            # Reopen initial position if needed
            # Get current price (could be from WebSocket or this execution price)
            current_price = exec_price

            # Check if we should reopen
            if not self.emergency_stopped and not self.dry_run:
                try:
                    # Calculate adaptive reopen size (Phase 5: Advanced Risk Management)
                    opposite_side = 'Sell' if closed_position_side == 'Buy' else 'Buy'
                    reopen_margin = self.calculate_reopen_size(closed_position_side, opposite_side)

                    # Log adaptive reopen calculation
                    self.logger.info(
                        f"🆕 [{symbol}] ADAPTIVE REOPEN: {closed_position_side} with ${reopen_margin:.2f} margin after TP"
                    )

                    # Use _open_initial_position() to properly split into grid levels
                    # This ensures consistent behavior with on_position_update() and correct grid_level tracking
                    self._open_initial_position(
                        side=closed_position_side,
                        current_price=current_price,
                        custom_margin_usd=reopen_margin
                    )

                    self.logger.info(
                        f"✅ [{symbol}] Reopened {closed_position_side} with ${reopen_margin:.2f} margin (split into grid levels)"
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
                response = self.client.place_order(
                    symbol=self.symbol,
                    side=side,
                    qty=new_size,
                    order_type="Market",
                    category=self.category
                )
                self.logger.info(f"[{self.symbol}] Order response: {response}")

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
                                # Calculate adaptive reopen size (Phase 5: Advanced Risk Management)
                                opposite_side = 'Sell' if side == 'Buy' else 'Buy'
                                reopen_margin = self.calculate_reopen_size(side, opposite_side)

                                self.logger.info(
                                    f"[{self.symbol}] ADAPTIVE REOPEN via WebSocket: {side} with ${reopen_margin:.2f} margin"
                                )

                                # Open with custom margin
                                self._open_initial_position(side, current_price, custom_margin_usd=reopen_margin)
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
                    # This should NEVER happen - positions should ONLY be restored via REST API in sync_with_exchange()
                    # WebSocket should only update existing positions, not create new ones
                    self.logger.error(
                        f"❌ [{self.symbol}] Position mismatch: exchange has {side} position ({size_float} @ ${avg_price}) "
                        f"but local tracking is empty. This indicates sync_with_exchange() failed or was skipped."
                    )
                    
                    # FAIL-FAST: Position should have been restored by REST API
                    reason = (
                        f"Position exists on exchange but not tracked locally for {side}: "
                        f"exchange={size_float}, local=0. Position restoration should happen ONLY via REST API "
                        f"in sync_with_exchange(), not from WebSocket. Restart bot to trigger proper sync."
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
            reduce_only = order_data.get('reduceOnly', False)

            # DEBUG: Log every order update to verify callback is working
            self.logger.debug(
                f"[{self.symbol}] 📞 Order update received: orderId={order_id}, "
                f"status={order_status}, type={order_type}, side={side}, "
                f"positionIdx={position_idx}, reduceOnly={reduce_only}"
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
