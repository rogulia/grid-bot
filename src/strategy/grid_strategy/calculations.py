"""Price calculations and conversions for Grid Strategy"""

import time
from config.constants import TradingConstants


class CalculationsMixin:
    """Mixin for price calculations and conversions"""

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

    def _get_qty_for_level(self, grid_level: int, side: str, price: float) -> float:
        """
        Get quantity for specific grid level WITH reference qty checking

        This ensures PERFECT qty symmetry: when level N opens on first side,
        we save qty as reference. Second side ALWAYS uses the same qty.

        Args:
            grid_level: Grid level (0, 1, 2, ...)
            side: 'Buy' or 'Sell' (for logging)
            price: Current price (used if no reference exists)

        Returns:
            Quantity of coins (either reference or calculated)
        """
        # Check if reference exists for this level
        with self._reference_qty_lock:
            if grid_level in self._reference_qty_per_level:
                # Reference exists - use it for perfect symmetry
                ref_qty = self._reference_qty_per_level[grid_level]
                self.logger.debug(
                    f"[{self.symbol}] Using reference qty for {side} level {grid_level}: {ref_qty:.6f}"
                )
                return ref_qty

        # No reference - calculate and save as reference
        level_margin = self.initial_size_usd * (self.multiplier ** grid_level)
        qty = self._usd_to_qty(level_margin, price)

        # Save as reference for future symmetry
        with self._reference_qty_lock:
            self._reference_qty_per_level[grid_level] = qty

        self.logger.debug(
            f"[{self.symbol}] Created reference qty for {side} level {grid_level}: {qty:.6f} "
            f"(margin=${level_margin:.2f} @ ${price:.4f})"
        )

        return qty

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

        IMPORTANT: Checks available balance and caps reopen margin if insufficient funds.

        Args:
            closed_side: Side that closed ('Buy' or 'Sell')
            opposite_side: Side still open ('Sell' or 'Buy')

        Returns:
            Reopen margin in USD (capped by available balance)
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

        # ⚠️ CRITICAL: Check available balance and cap margin if insufficient
        # This prevents calculate_reopen_size from returning unrealistic values
        try:
            if hasattr(self, 'balance_manager') and self.balance_manager:
                available_balance = self.balance_manager.get_available_balance()

                if reopen_margin > available_balance:
                    original_margin = reopen_margin
                    # Cap at available, but not below initial_size
                    reopen_margin = max(self.initial_size_usd, available_balance)

                    self.logger.warning(
                        f"[{self.symbol}] ⚠️ Calculated reopen margin ${original_margin:.2f} "
                        f"exceeds available balance ${available_balance:.2f}. "
                        f"Capping at ${reopen_margin:.2f}"
                    )
        except Exception as e:
            # Don't fail if balance check fails - just log and continue with calculated value
            self.logger.debug(f"[{self.symbol}] Could not check balance in calculate_reopen_size: {e}")

        return reopen_margin
