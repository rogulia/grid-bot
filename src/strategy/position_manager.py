"""Position manager for tracking LONG and SHORT positions"""

import logging
from dataclasses import dataclass
from typing import List, Optional, Dict
from datetime import datetime
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))
from core.state_manager import StateManager
from utils.timezone import now_helsinki


@dataclass
class Position:
    """Represents a single position"""
    side: str  # 'Buy' or 'Sell'
    entry_price: float
    quantity: float
    timestamp: datetime
    grid_level: int  # Which grid level (0 = initial, 1 = first averaging, etc.)

    def get_pnl(self, current_price: float, leverage: int = 1) -> float:
        """
        Calculate unrealized PnL for this position

        Args:
            current_price: Current market price
            leverage: Leverage multiplier (NOT used in PnL calculation - kept for compatibility)

        Returns:
            Unrealized PnL in USDT
        """
        if self.side == 'Buy':  # LONG
            price_change = current_price - self.entry_price
        else:  # SHORT
            price_change = self.entry_price - current_price

        # PnL = quantity * price_change
        # Note: Leverage does NOT affect absolute PnL in USDT!
        # Leverage only affects: margin requirement, liquidation price, and ROI%
        pnl = self.quantity * price_change
        return pnl


class PositionManager:
    """Manage LONG and SHORT positions separately"""

    def __init__(self, leverage: int = 100, symbol: Optional[str] = None, enable_state_persistence: bool = True):
        """
        Initialize position manager

        Args:
            leverage: Trading leverage
            symbol: Trading symbol (e.g., SOLUSDT) - required for multi-symbol support
            enable_state_persistence: Enable saving state to JSON
        """
        self.logger = logging.getLogger("sol-trader.position_manager")
        self.leverage = leverage
        self.symbol = symbol

        self.long_positions: List[Position] = []
        self.short_positions: List[Position] = []

        # Track last entry prices for grid calculation
        self.last_long_entry: Optional[float] = None
        self.last_short_entry: Optional[float] = None

        # Track Take Profit order IDs
        self.long_tp_order_id: Optional[str] = None
        self.short_tp_order_id: Optional[str] = None

        # State persistence
        self.state_manager = StateManager(symbol=symbol) if enable_state_persistence else None
        if self.state_manager:
            self.logger.info(f"State persistence enabled" +
                           (f" for {symbol}" if symbol else ""))

    def add_position(
        self,
        side: str,
        entry_price: float,
        quantity: float,
        grid_level: int = 0
    ):
        """
        Add a new position

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)
            entry_price: Entry price
            quantity: Position quantity
            grid_level: Grid level number
        """
        position = Position(
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            timestamp=now_helsinki(),
            grid_level=grid_level
        )

        if side == 'Buy':
            self.long_positions.append(position)
            self.last_long_entry = entry_price
            self.logger.info(
                f"Added LONG position: {quantity} @ ${entry_price:.4f} "
                f"(grid level {grid_level})"
            )
        else:
            self.short_positions.append(position)
            self.last_short_entry = entry_price
            self.logger.info(
                f"Added SHORT position: {quantity} @ ${entry_price:.4f} "
                f"(grid level {grid_level})"
            )

        # Save state after adding position
        self._save_state()

    def remove_all_positions(self, side: str):
        """
        Remove all positions for a given side

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)
        """
        if side == 'Buy':
            count = len(self.long_positions)
            self.long_positions = []
            self.last_long_entry = None
            self.logger.info(f"Closed all {count} LONG positions")
        else:
            count = len(self.short_positions)
            self.short_positions = []
            self.last_short_entry = None
            self.logger.info(f"Closed all {count} SHORT positions")

        # Save state after removing positions
        self._save_state()

    def get_average_entry_price(self, side: str) -> Optional[float]:
        """
        Calculate weighted average entry price for a side

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)

        Returns:
            Average entry price or None if no positions
        """
        positions = self.long_positions if side == 'Buy' else self.short_positions

        if not positions:
            return None

        total_quantity = sum(p.quantity for p in positions)
        weighted_sum = sum(p.entry_price * p.quantity for p in positions)

        return weighted_sum / total_quantity if total_quantity > 0 else None

    def get_total_quantity(self, side: str) -> float:
        """
        Get total quantity for a side

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)

        Returns:
            Total quantity (rounded to avoid floating point errors)
        """
        positions = self.long_positions if side == 'Buy' else self.short_positions
        total = sum(p.quantity for p in positions)

        # Round to 8 decimal places to avoid floating point errors like 2.4000000000000004
        # For crypto, 8 decimals is standard precision
        return round(total, 8)

    def calculate_pnl(self, current_price: float, side: Optional[str] = None) -> float:
        """
        Calculate total unrealized PnL

        Args:
            current_price: Current market price
            side: Specific side or None for both

        Returns:
            Total unrealized PnL
        """
        total_pnl = 0.0

        if side is None or side == 'Buy':
            for pos in self.long_positions:
                total_pnl += pos.get_pnl(current_price, self.leverage)

        if side is None or side == 'Sell':
            for pos in self.short_positions:
                total_pnl += pos.get_pnl(current_price, self.leverage)

        return total_pnl

    def get_position_count(self, side: str) -> int:
        """
        Get number of positions for a side

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)

        Returns:
            Number of positions
        """
        return len(self.long_positions) if side == 'Buy' else len(self.short_positions)

    # NOTE: get_liquidation_distance() and is_near_liquidation() methods removed.
    # For hedged positions (LONG+SHORT), individual position liqPrice is meaningless.
    # Use Account Maintenance Margin Rate (accountMMRate) from wallet balance instead.
    # See grid_strategy.py::_check_risk_limits() for correct implementation.

    def set_tp_order_id(self, side: str, order_id: Optional[str]):
        """
        Set Take Profit order ID for a side

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)
            order_id: Order ID or None to clear
        """
        if side == 'Buy':
            self.long_tp_order_id = order_id
        else:
            self.short_tp_order_id = order_id

        # Save state after updating TP order ID
        self._save_state()

    def get_tp_order_id(self, side: str) -> Optional[str]:
        """
        Get Take Profit order ID for a side

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)

        Returns:
            Order ID or None
        """
        return self.long_tp_order_id if side == 'Buy' else self.short_tp_order_id

    def calculate_unrealized_pnl_with_fees(
        self,
        current_price: float,
        side: str,
        exchange_unrealized_pnl: float,
        taker_fee_rate: float = 0.0006  # 0.06% default Bybit taker fee
    ) -> Dict[str, float]:
        """
        Calculate unrealized PnL with estimated fees

        This provides a more realistic view of potential profit by accounting for:
        - Opening fees (already paid)
        - Closing fees (estimated based on current position value)

        Args:
            current_price: Current market price
            side: 'Buy' (LONG) or 'Sell' (SHORT)
            exchange_unrealized_pnl: Unrealized PnL from exchange API (without fees)
            taker_fee_rate: Trading fee rate (default 0.06% for Bybit taker)

        Returns:
            Dictionary with:
                - base_pnl: Unrealized PnL without fees
                - estimated_open_fee: Estimated fee paid to open all positions
                - estimated_close_fee: Estimated fee to close at current price
                - net_pnl: Net PnL after deducting all fees

        Example:
            >>> pm = PositionManager(leverage=100)
            >>> pm.add_position('Buy', 100, 1.0)  # 1 SOL @ $100
            >>> # Price moved to $105, unrealized = +$5
            >>> fees = pm.calculate_unrealized_pnl_with_fees(105, 'Buy', 5.0)
            >>> fees['net_pnl']  # $5 - opening fee - closing fee
            4.874  # Approximately after ~0.06% fees on both sides
        """
        avg_entry = self.get_average_entry_price(side)
        total_qty = self.get_total_quantity(side)

        if not avg_entry or total_qty == 0:
            return {
                'base_pnl': 0.0,
                'estimated_open_fee': 0.0,
                'estimated_close_fee': 0.0,
                'net_pnl': 0.0
            }

        # Calculate position values
        entry_position_value = total_qty * avg_entry
        current_position_value = total_qty * current_price

        # Estimate fees (taker fee on both open and close)
        estimated_open_fee = entry_position_value * taker_fee_rate
        estimated_close_fee = current_position_value * taker_fee_rate

        # Net PnL = unrealized PnL - fees
        net_pnl = exchange_unrealized_pnl - estimated_open_fee - estimated_close_fee

        return {
            'base_pnl': exchange_unrealized_pnl,
            'estimated_open_fee': estimated_open_fee,
            'estimated_close_fee': estimated_close_fee,
            'net_pnl': net_pnl
        }

    def _save_state(self):
        """Save current state to JSON"""
        if self.state_manager:
            self.state_manager.save_state(
                long_positions=self.long_positions,
                short_positions=self.short_positions,
                long_tp_id=self.long_tp_order_id,
                short_tp_id=self.short_tp_order_id
            )
