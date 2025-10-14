"""Position manager for tracking LONG and SHORT positions"""

import logging
import threading
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
    order_id: Optional[str] = None  # Order ID from exchange (for tracking)

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

    def __init__(self, leverage: int = 100, symbol: Optional[str] = None, enable_state_persistence: bool = True, state_manager: Optional[StateManager] = None):
        """
        Initialize position manager

        Args:
            leverage: Trading leverage
            symbol: Trading symbol (e.g., SOLUSDT) - required for multi-symbol support
            enable_state_persistence: Enable saving state to JSON (only if state_manager not provided)
            state_manager: Optional pre-configured StateManager (for multi-account support)
        """
        self.logger = logging.getLogger("sol-trader.position_manager")
        self.leverage = leverage
        self.symbol = symbol

        # Thread safety lock (WebSocket callbacks run in separate threads)
        self._lock = threading.Lock()

        self.long_positions: List[Position] = []
        self.short_positions: List[Position] = []

        # Track last entry prices for grid calculation
        self.last_long_entry: Optional[float] = None
        self.last_short_entry: Optional[float] = None

        # Track Take Profit order IDs
        self.long_tp_order_id: Optional[str] = None
        self.short_tp_order_id: Optional[str] = None

        # State persistence (use provided state_manager or create new one)
        if state_manager:
            self.state_manager = state_manager
            self.logger.info(f"Using provided state manager" +
                           (f" for {symbol}" if symbol else ""))
        elif enable_state_persistence:
            self.state_manager = StateManager(symbol=symbol)
            self.logger.info(f"State persistence enabled" +
                           (f" for {symbol}" if symbol else ""))
        else:
            self.state_manager = None

    def add_position(
        self,
        side: str,
        entry_price: float,
        quantity: float,
        grid_level: int = 0,
        order_id: Optional[str] = None
    ):
        """
        Add a new position

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)
            entry_price: Entry price
            quantity: Position quantity
            grid_level: Grid level number
            order_id: Order ID from exchange (optional)
        """
        position = Position(
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            timestamp=now_helsinki(),
            grid_level=grid_level,
            order_id=order_id
        )

        with self._lock:
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

        # Save state after adding position (outside lock to avoid deadlock)
        self._save_state()

    def remove_all_positions(self, side: str):
        """
        Remove all positions for a given side

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)
        """
        with self._lock:
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

        # Save state after removing positions (outside lock to avoid deadlock)
        self._save_state()

    def get_average_entry_price(self, side: str) -> Optional[float]:
        """
        Calculate weighted average entry price for a side

        Args:
            side: 'Buy' (LONG) or 'Sell' (SHORT)

        Returns:
            Average entry price or None if no positions
        """
        with self._lock:
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
        with self._lock:
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
        with self._lock:
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
        with self._lock:
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

    def _save_state(self):
        """Save current state to JSON"""
        if self.state_manager:
            with self._lock:
                # Make copies to avoid holding lock during file I/O
                long_copy = list(self.long_positions)
                short_copy = list(self.short_positions)

            self.state_manager.save_state(
                long_positions=long_copy,
                short_positions=short_copy,
                long_tp_id=self.long_tp_order_id,
                short_tp_id=self.short_tp_order_id
            )
