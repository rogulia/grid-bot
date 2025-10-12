"""Unit tests for PositionManager"""

import pytest
from datetime import datetime
from src.strategy.position_manager import PositionManager, Position
from src.utils.timezone import now_helsinki


class TestPosition:
    """Tests for Position dataclass"""

    def test_position_creation(self):
        """Test creating a position"""
        pos = Position(
            side='Buy',
            entry_price=100.0,
            quantity=0.5,
            timestamp=now_helsinki(),
            grid_level=0
        )

        assert pos.side == 'Buy'
        assert pos.entry_price == 100.0
        assert pos.quantity == 0.5
        assert pos.grid_level == 0
        assert isinstance(pos.timestamp, datetime)


class TestPositionManager:
    """Tests for PositionManager"""

    def test_initialization(self, position_manager):
        """Test PositionManager initialization"""
        assert position_manager.leverage == 100
        assert len(position_manager.long_positions) == 0
        assert len(position_manager.short_positions) == 0
        assert position_manager.last_long_entry is None
        assert position_manager.last_short_entry is None

    def test_add_long_position(self, position_manager):
        """Test adding a LONG position"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)

        assert len(position_manager.long_positions) == 1
        assert position_manager.long_positions[0].entry_price == 100.0
        assert position_manager.long_positions[0].quantity == 0.1
        assert position_manager.last_long_entry == 100.0

    def test_add_short_position(self, position_manager):
        """Test adding a SHORT position"""
        position_manager.add_position('Sell', 100.0, 0.1, 0)

        assert len(position_manager.short_positions) == 1
        assert position_manager.short_positions[0].entry_price == 100.0
        assert position_manager.short_positions[0].quantity == 0.1
        assert position_manager.last_short_entry == 100.0

    def test_add_multiple_positions(self, position_manager):
        """Test adding multiple positions"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        position_manager.add_position('Buy', 99.0, 0.2, 1)
        position_manager.add_position('Buy', 98.0, 0.4, 2)

        assert len(position_manager.long_positions) == 3
        assert position_manager.last_long_entry == 98.0

    def test_get_total_quantity_long(self, position_manager):
        """Test getting total quantity for LONG positions"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        position_manager.add_position('Buy', 99.0, 0.2, 1)
        position_manager.add_position('Buy', 98.0, 0.4, 2)

        total = position_manager.get_total_quantity('Buy')
        assert total == pytest.approx(0.7)

    def test_get_total_quantity_short(self, position_manager):
        """Test getting total quantity for SHORT positions"""
        position_manager.add_position('Sell', 100.0, 0.1, 0)
        position_manager.add_position('Sell', 101.0, 0.2, 1)

        total = position_manager.get_total_quantity('Sell')
        assert total == pytest.approx(0.3)

    def test_get_average_entry_price_long(self, position_manager):
        """Test calculating average entry price for LONG"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)  # 10 USD
        position_manager.add_position('Buy', 99.0, 0.2, 1)   # 19.8 USD
        # Total: 29.8 USD for 0.3 qty = 99.333 avg

        avg = position_manager.get_average_entry_price('Buy')
        assert avg == pytest.approx(99.333, rel=0.01)

    def test_get_average_entry_price_short(self, position_manager):
        """Test calculating average entry price for SHORT"""
        position_manager.add_position('Sell', 100.0, 0.1, 0)  # 10 USD
        position_manager.add_position('Sell', 102.0, 0.2, 1)  # 20.4 USD
        # Total: 30.4 USD for 0.3 qty = 101.333 avg

        avg = position_manager.get_average_entry_price('Sell')
        assert avg == pytest.approx(101.333, rel=0.01)

    def test_get_average_entry_price_no_positions(self, position_manager):
        """Test average entry price with no positions"""
        avg = position_manager.get_average_entry_price('Buy')
        assert avg is None

    def test_calculate_pnl_long_profit(self, position_manager):
        """Test PnL calculation for LONG in profit"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        current_price = 110.0

        pnl = position_manager.calculate_pnl(current_price, 'Buy')
        # Entry: 0.1 @ 100
        # Current: 0.1 @ 110
        # Price change: +10
        # PnL: 0.1 * 10 = +1.0 USD (leverage does NOT affect PnL!)
        assert pnl == pytest.approx(1.0)

    def test_calculate_pnl_long_loss(self, position_manager):
        """Test PnL calculation for LONG in loss"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        current_price = 90.0

        pnl = position_manager.calculate_pnl(current_price, 'Buy')
        # Entry: 0.1 @ 100
        # Current: 0.1 @ 90
        # Price change: -10
        # PnL: 0.1 * (-10) = -1.0 USD (leverage does NOT affect PnL!)
        assert pnl == pytest.approx(-1.0)

    def test_calculate_pnl_short_profit(self, position_manager):
        """Test PnL calculation for SHORT in profit"""
        position_manager.add_position('Sell', 100.0, 0.1, 0)
        current_price = 90.0

        pnl = position_manager.calculate_pnl(current_price, 'Sell')
        # Entry: 0.1 @ 100
        # Current: 0.1 @ 90
        # Price change: +10 (for short)
        # PnL: 0.1 * 10 = +1.0 USD (leverage does NOT affect PnL!)
        assert pnl == pytest.approx(1.0)

    def test_calculate_pnl_short_loss(self, position_manager):
        """Test PnL calculation for SHORT in loss"""
        position_manager.add_position('Sell', 100.0, 0.1, 0)
        current_price = 110.0

        pnl = position_manager.calculate_pnl(current_price, 'Sell')
        # Entry: 0.1 @ 100
        # Current: 0.1 @ 110
        # Price change: -10 (for short)
        # PnL: 0.1 * (-10) = -1.0 USD (leverage does NOT affect PnL!)
        assert pnl == pytest.approx(-1.0)

    def test_calculate_pnl_multiple_positions(self, position_manager):
        """Test PnL with multiple positions"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        position_manager.add_position('Buy', 99.0, 0.2, 1)
        current_price = 105.0

        pnl = position_manager.calculate_pnl(current_price, 'Buy')
        # Position 1: 0.1 * (105-100) = 0.5 USD
        # Position 2: 0.2 * (105-99) = 1.2 USD
        # Total PnL: 1.7 USD (leverage does NOT affect PnL!)
        assert pnl == pytest.approx(1.7)

    def test_calculate_pnl_no_positions(self, position_manager):
        """Test PnL calculation with no positions"""
        pnl = position_manager.calculate_pnl(100.0, 'Buy')
        assert pnl == 0.0

    # NOTE: Liquidation tests removed - liqPrice is meaningless for hedged positions.
    # Use Account Maintenance Margin Rate (accountMMRate) instead.
    # See grid_strategy.py::_check_risk_limits() for correct implementation.

    def test_remove_all_positions_long(self, position_manager):
        """Test removing all LONG positions"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        position_manager.add_position('Buy', 99.0, 0.2, 1)

        position_manager.remove_all_positions('Buy')

        assert len(position_manager.long_positions) == 0
        assert position_manager.last_long_entry is None

    def test_remove_all_positions_short(self, position_manager):
        """Test removing all SHORT positions"""
        position_manager.add_position('Sell', 100.0, 0.1, 0)
        position_manager.add_position('Sell', 101.0, 0.2, 1)

        position_manager.remove_all_positions('Sell')

        assert len(position_manager.short_positions) == 0
        assert position_manager.last_short_entry is None

    def test_get_position_count(self, position_manager):
        """Test getting position count"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        position_manager.add_position('Buy', 99.0, 0.2, 1)
        position_manager.add_position('Sell', 100.0, 0.1, 0)

        assert position_manager.get_position_count('Buy') == 2
        assert position_manager.get_position_count('Sell') == 1

    def test_tp_order_id_tracking(self, position_manager):
        """Test TP order ID tracking"""
        position_manager.set_tp_order_id('Buy', 'tp_123')
        position_manager.set_tp_order_id('Sell', 'tp_456')

        assert position_manager.get_tp_order_id('Buy') == 'tp_123'
        assert position_manager.get_tp_order_id('Sell') == 'tp_456'

        # Clear TP order
        position_manager.set_tp_order_id('Buy', None)
        assert position_manager.get_tp_order_id('Buy') is None


