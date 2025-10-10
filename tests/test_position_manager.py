"""Unit tests for PositionManager"""

import pytest
from datetime import datetime
from src.strategy.position_manager import PositionManager, Position


class TestPosition:
    """Tests for Position dataclass"""

    def test_position_creation(self):
        """Test creating a position"""
        pos = Position(
            side='Buy',
            entry_price=100.0,
            quantity=0.5,
            timestamp=datetime.now(),
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


class TestUnrealizedPnLWithFees:
    """Tests for calculate_unrealized_pnl_with_fees method"""

    def test_calculate_fees_winning_long_position(self, position_manager):
        """Test fee calculation for winning LONG position"""
        # Add position: 1.0 SOL @ $100
        position_manager.add_position('Buy', 100.0, 1.0, 0)

        # Current price: $105 → $5 profit
        current_price = 105.0
        exchange_unrealized = 5.0  # From exchange API

        result = position_manager.calculate_unrealized_pnl_with_fees(
            current_price, 'Buy', exchange_unrealized
        )

        # Check base PnL
        assert result['base_pnl'] == 5.0

        # Check estimated fees (0.06% = 0.0006)
        # Open fee: 1.0 * 100 * 0.0006 = 0.06
        assert result['estimated_open_fee'] == pytest.approx(0.06, abs=0.01)

        # Close fee: 1.0 * 105 * 0.0006 = 0.063
        assert result['estimated_close_fee'] == pytest.approx(0.063, abs=0.01)

        # Net PnL: 5.0 - 0.06 - 0.063 = 4.877
        assert result['net_pnl'] == pytest.approx(4.877, abs=0.01)
        assert result['net_pnl'] < result['base_pnl']  # Less than base due to fees

    def test_calculate_fees_losing_short_position(self, position_manager):
        """Test fee calculation for losing SHORT position"""
        # Add position: 0.5 SOL @ $100
        position_manager.add_position('Sell', 100.0, 0.5, 0)

        # Current price: $105 → -$2.5 loss
        current_price = 105.0
        exchange_unrealized = -2.5  # From exchange API

        result = position_manager.calculate_unrealized_pnl_with_fees(
            current_price, 'Sell', exchange_unrealized
        )

        # Check base PnL
        assert result['base_pnl'] == -2.5

        # Check estimated fees
        # Open fee: 0.5 * 100 * 0.0006 = 0.03
        assert result['estimated_open_fee'] == pytest.approx(0.03, abs=0.01)

        # Close fee: 0.5 * 105 * 0.0006 = 0.0315
        assert result['estimated_close_fee'] == pytest.approx(0.0315, abs=0.01)

        # Net PnL: -2.5 - 0.03 - 0.0315 = -2.5615 (more loss due to fees)
        assert result['net_pnl'] == pytest.approx(-2.5615, abs=0.01)
        assert result['net_pnl'] < result['base_pnl']  # More negative

    def test_calculate_fees_no_position(self, position_manager):
        """Test fee calculation with no position"""
        result = position_manager.calculate_unrealized_pnl_with_fees(
            100.0, 'Buy', 0.0
        )

        assert result['base_pnl'] == 0.0
        assert result['estimated_open_fee'] == 0.0
        assert result['estimated_close_fee'] == 0.0
        assert result['net_pnl'] == 0.0

    def test_calculate_fees_multiple_positions(self, position_manager):
        """Test fee calculation with multiple averaged positions"""
        # Add multiple positions (averaging scenario)
        position_manager.add_position('Buy', 100.0, 0.5, 0)  # $50 value
        position_manager.add_position('Buy', 95.0, 0.5, 1)   # $47.5 value
        # Total: 1.0 SOL, avg entry ~$97.5

        # Current price: $100
        current_price = 100.0
        # Unrealized: 1.0 * (100 - 97.5) = 2.5
        exchange_unrealized = 2.5

        result = position_manager.calculate_unrealized_pnl_with_fees(
            current_price, 'Buy', exchange_unrealized
        )

        # Average entry price
        avg_entry = position_manager.get_average_entry_price('Buy')
        assert avg_entry == pytest.approx(97.5)

        # Open fee based on average: 1.0 * 97.5 * 0.0006 = 0.0585
        assert result['estimated_open_fee'] == pytest.approx(0.0585, abs=0.01)

        # Close fee: 1.0 * 100 * 0.0006 = 0.06
        assert result['estimated_close_fee'] == pytest.approx(0.06, abs=0.01)

        # Net PnL: 2.5 - 0.0585 - 0.06 = 2.3815
        assert result['net_pnl'] == pytest.approx(2.3815, abs=0.01)

    def test_calculate_fees_custom_fee_rate(self, position_manager):
        """Test fee calculation with custom fee rate"""
        position_manager.add_position('Buy', 100.0, 1.0, 0)

        # Use maker fee rate (0.02% = 0.0002)
        result = position_manager.calculate_unrealized_pnl_with_fees(
            105.0, 'Buy', 5.0, taker_fee_rate=0.0002
        )

        # Open fee: 100 * 0.0002 = 0.02
        assert result['estimated_open_fee'] == pytest.approx(0.02, abs=0.01)

        # Close fee: 105 * 0.0002 = 0.021
        assert result['estimated_close_fee'] == pytest.approx(0.021, abs=0.01)

        # Net PnL: 5.0 - 0.02 - 0.021 = 4.959
        assert result['net_pnl'] == pytest.approx(4.959, abs=0.01)

    def test_calculate_fees_breakeven_position(self, position_manager):
        """Test fee calculation at breakeven price"""
        position_manager.add_position('Buy', 100.0, 1.0, 0)

        # Current price = entry price
        current_price = 100.0
        exchange_unrealized = 0.0

        result = position_manager.calculate_unrealized_pnl_with_fees(
            current_price, 'Buy', exchange_unrealized
        )

        assert result['base_pnl'] == 0.0

        # Even at breakeven, fees still apply
        assert result['estimated_open_fee'] > 0
        assert result['estimated_close_fee'] > 0

        # Net PnL is negative due to fees
        assert result['net_pnl'] < 0  # Loss from fees even at breakeven!
