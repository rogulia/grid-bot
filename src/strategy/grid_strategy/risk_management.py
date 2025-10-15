"""Risk management for Grid Strategy"""

import time
from config.constants import TradingConstants, LogMessages
from ...utils.emergency_stop_manager import EmergencyStopManager
from ...utils.logger import log_trade


class RiskManagementMixin:
    """Mixin for risk management and emergency stops"""

    def is_stopped(self) -> bool:
        """
        Check if strategy is in emergency stop state

        Returns:
            True if emergency stop was triggered, False otherwise
        """
        return self.emergency_stopped

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

    def _create_emergency_stop_flag(self, reason: str, additional_data: dict = None):
        """
        Create emergency stop flag file to prevent bot restart

        This file signals systemd/supervisor not to restart the bot
        after emergency shutdown. User must manually remove file and
        fix issues before restarting.

        Args:
            reason: Reason for emergency stop
            additional_data: Optional additional diagnostic data
        """
        emergency_manager = EmergencyStopManager(logger=self.logger)
        emergency_manager.create(
            account_id=self.account_id,
            symbol=self.symbol,
            reason=reason,
            additional_data=additional_data
        )
