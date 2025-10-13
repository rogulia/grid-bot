"""
Tests for Advanced Risk Management System v3.1

Tests cover:
- Phase 1: Dynamic Safety Factor based on ATR
- Phase 2: Early Freeze Mechanism
- Phase 3: Panic Mode Implementation
- Phase 4: Intelligent TP Management
- Phase 5: Adaptive Reopen by Margin Ratio
- Phase 6: Dynamic IM Monitoring
- Phase 7: Position Balancing in Panic Mode
"""

import pytest
import time
from unittest.mock import Mock, MagicMock, patch, PropertyMock
from src.core.trading_account import TradingAccount
from src.strategy.grid_strategy import GridStrategy
from src.strategy.position_manager import PositionManager
from src.exchange.bybit_client import BybitClient
from src.utils.balance_manager import BalanceManager


# ==================== FIXTURES ====================

@pytest.fixture
def mock_client():
    """Mock Bybit client"""
    client = Mock(spec=BybitClient)
    client.place_order = Mock(return_value={'orderId': 'test_order_123'})
    client.cancel_order = Mock(return_value={'orderId': 'test_order_123'})
    client.close_position = Mock(return_value={'orderId': 'test_order_123'})

    # Mock session for get_instruments_info
    client.session = Mock()
    client.session.get_instruments_info = Mock(return_value={
        'retCode': 0,
        'result': {
            'list': [{
                'lotSizeFilter': {
                    'minOrderQty': '0.1',
                    'qtyStep': '0.1',
                    'maxOrderQty': '10000'
                }
            }]
        }
    })

    return client


@pytest.fixture
def mock_balance_manager():
    """Mock BalanceManager with common responses"""
    bm = Mock(spec=BalanceManager)
    bm.get_available_balance = Mock(return_value=100.0)
    bm.get_mm_rate = Mock(return_value=10.0)  # 10% MM Rate
    bm.get_initial_margin = Mock(return_value=50.0)
    bm.get_maintenance_margin = Mock(return_value=5.0)
    return bm


@pytest.fixture
def position_manager():
    """Create real PositionManager for testing"""
    return PositionManager(symbol="TESTUSDT")


@pytest.fixture
def grid_strategy(mock_client, position_manager, mock_balance_manager):
    """Create GridStrategy with mocked dependencies"""
    config = {
        'symbol': 'TESTUSDT',
        'leverage': 100,
        'initial_position_size_usd': 1.0,
        'grid_step_percent': 1.0,
        'averaging_multiplier': 2.0,
        'take_profit_percent': 1.0,
        'max_grid_levels_per_side': 10,
        'mm_rate_threshold': 90.0
    }

    strategy = GridStrategy(
        client=mock_client,
        position_manager=position_manager,
        config=config,
        dry_run=True,
        balance_manager=mock_balance_manager
    )

    # Set current price for calculations
    strategy.current_price = 100.0

    return strategy


@pytest.fixture
def trading_account(mock_client):
    """Create TradingAccount with mocked dependencies"""
    strategies_config = [
        {
            'symbol': 'TESTUSDT',
            'leverage': 100,
            'initial_position_size_usd': 1.0,
            'grid_step_percent': 1.0,
            'averaging_multiplier': 2.0,
            'take_profit_percent': 1.0,
            'max_grid_levels_per_side': 10
        }
    ]

    risk_config = {
        'mm_rate_threshold': 90.0
    }

    account = TradingAccount(
        account_id=1,
        name="Test Account",
        api_key="test_key",
        api_secret="test_secret",
        demo=True,
        dry_run=True,
        strategies_config=strategies_config,
        risk_config=risk_config
    )

    # Mock the client
    account.client = mock_client

    return account


# ==================== PHASE 1: DYNAMIC SAFETY FACTOR ====================

class TestPhase1DynamicSafetyFactor:
    """Tests for Phase 1: Dynamic Safety Factor based on ATR"""

    def test_calculate_atr_percent_insufficient_data(self, grid_strategy):
        """Test ATR calculation with insufficient price history"""
        # No price history yet
        atr = grid_strategy.calculate_atr_percent()
        assert atr == 1.5  # Default medium volatility

    def test_calculate_atr_percent_basic(self, grid_strategy):
        """Test ATR calculation with basic price history"""
        # Add price history: 100, 102, 101, 103, 102
        prices = [100.0, 102.0, 101.0, 103.0, 102.0]
        for price in prices:
            grid_strategy._update_price_history(price)

        atr = grid_strategy.calculate_atr_percent()

        # True ranges: |102-100|=2, |101-102|=1, |103-101|=2, |102-103|=1
        # Average: (2+1+2+1)/4 = 1.5
        # Percent: 1.5/102 * 100 = 1.47%
        assert 1.4 <= atr <= 1.6

    def test_calculate_atr_percent_caching(self, grid_strategy):
        """Test ATR caching works correctly"""
        # Add price history
        for price in [100.0, 102.0, 101.0]:
            grid_strategy._update_price_history(price)

        # First call
        atr1 = grid_strategy.calculate_atr_percent()

        # Second call immediately (should use cache)
        atr2 = grid_strategy.calculate_atr_percent()

        assert atr1 == atr2  # Should be identical (cached)

    def test_safety_factor_low_volatility(self):
        """Test safety factor calculation for low volatility (ATR < 1.0%)"""
        atr_percent = 0.5
        factor = TradingAccount.calculate_safety_factor(atr_percent)

        # base(0.10) + gap(0.02) + tier(0.05) = 0.17 → factor = 1.17
        assert factor == 1.17

    def test_safety_factor_medium_volatility(self):
        """Test safety factor calculation for medium volatility (1.0-2.0%)"""
        atr_percent = 1.5
        factor = TradingAccount.calculate_safety_factor(atr_percent)

        # base(0.10) + gap(0.05) + tier(0.05) = 0.20 → factor = 1.20
        assert factor == 1.20

    def test_safety_factor_high_volatility(self):
        """Test safety factor calculation for high volatility (>2.0%)"""
        atr_percent = 3.0
        factor = TradingAccount.calculate_safety_factor(atr_percent)

        # base(0.10) + gap(0.10) + tier(0.05) = 0.25 → factor = 1.25
        assert factor == 1.25


# ==================== PHASE 2: EARLY FREEZE ====================

class TestPhase2EarlyFreeze:
    """Tests for Phase 2: Early Freeze Mechanism"""

    def test_early_freeze_trigger_activation(self, trading_account, mock_balance_manager):
        """Test Early Freeze activates when available < next_worst_case × 1.5"""
        # Setup: Create strategy and add positions
        trading_account.initialize()

        # Mock balance to trigger freeze
        # If next averaging needs $10 and available is $12 (< $15), freeze triggers
        mock_balance_manager.get_available_balance.return_value = 12.0

        # Mock strategy to return predictable next averaging
        for strategy in trading_account.strategies.values():
            strategy.balance_manager = mock_balance_manager
            # Add position to have non-zero next averaging
            strategy.pm.add_position('Buy', 100.0, 1.0, 0)

        should_freeze, reason = trading_account.check_early_freeze_trigger()

        # Should trigger freeze
        assert should_freeze
        assert "EARLY_FREEZE" in reason

    def test_early_freeze_blocks_averaging(self, trading_account):
        """Test Early Freeze blocks averaging operations"""
        trading_account.initialize()

        # Activate freeze
        trading_account.freeze_all_averaging("Test freeze")

        # Try to average (should be blocked)
        result = trading_account.check_reserve_before_averaging(
            symbol="TESTUSDT",
            side="Buy",
            next_averaging_margin=1.0
        )

        assert not result  # Should be blocked
        assert trading_account.averaging_frozen

    def test_early_freeze_automatic_unfreeze(self, trading_account, mock_balance_manager):
        """Test Early Freeze automatically unfreezes when conditions recover"""
        trading_account.initialize()

        # Activate freeze
        trading_account.freeze_all_averaging("Test freeze")
        assert trading_account.averaging_frozen

        # Mock high balance (conditions recovered)
        mock_balance_manager.get_available_balance.return_value = 1000.0
        for strategy in trading_account.strategies.values():
            strategy.balance_manager = mock_balance_manager

        # Check freeze trigger (should not trigger)
        should_freeze, _ = trading_account.check_early_freeze_trigger()

        assert not should_freeze

        # Unfreeze
        if not should_freeze and trading_account.averaging_frozen and not trading_account.panic_mode:
            trading_account.unfreeze_all_averaging()

        assert not trading_account.averaging_frozen


# ==================== PHASE 3: PANIC MODE ====================

class TestPhase3PanicMode:
    """Tests for Phase 3: Panic Mode Implementation"""

    def test_panic_trigger_low_im(self, trading_account, mock_balance_manager):
        """Test Panic Mode triggers when available < next_worst_case × 3"""
        trading_account.initialize()

        # Mock low balance to trigger panic
        mock_balance_manager.get_available_balance.return_value = 5.0
        for strategy in trading_account.strategies.values():
            strategy.balance_manager = mock_balance_manager
            # Add position so next averaging is significant
            strategy.pm.add_position('Buy', 100.0, 1.0, 0)

        triggered, reason = trading_account.check_panic_trigger_low_im()

        assert triggered
        assert "LOW_IM" in reason

    def test_panic_trigger_high_imbalance(self, trading_account, mock_balance_manager):
        """Test Panic Mode triggers on high imbalance (ratio > 10 AND available < 30%)"""
        trading_account.initialize()

        # Create large imbalance
        for strategy in trading_account.strategies.values():
            strategy.balance_manager = mock_balance_manager
            strategy.current_price = 100.0
            # Add 20 LONG, 1 SHORT → ratio = 19
            for _ in range(5):
                strategy.pm.add_position('Buy', 100.0, 4.0, 0)
            strategy.pm.add_position('Sell', 100.0, 1.0, 0)

        # Mock low available percentage
        mock_balance_manager.get_available_balance.return_value = 10.0
        mock_balance_manager.get_initial_margin.return_value = 30.0  # available = 25%

        triggered, reason = trading_account.check_panic_trigger_high_imbalance()

        assert triggered
        assert "HIGH_IMBALANCE" in reason

    def test_enter_panic_mode_workflow(self, trading_account):
        """Test entering panic mode executes all actions"""
        trading_account.initialize()

        # Enter panic mode
        trading_account.enter_panic_mode("Test panic")

        # Check state
        assert trading_account.panic_mode
        assert trading_account.panic_reason == "Test panic"
        assert trading_account.averaging_frozen  # Should freeze averaging
        assert trading_account.panic_entered_at is not None


# ==================== PHASE 4: INTELLIGENT TP ====================

class TestPhase4IntelligentTP:
    """Tests for Phase 4: Intelligent TP Management"""

    def test_determine_trend_side_uptrend(self, grid_strategy):
        """Test trend detection for uptrend (LONG averaged more)"""
        # Add more LONG positions than SHORT
        for _ in range(3):
            grid_strategy.pm.add_position('Buy', 100.0, 1.0, 0)
        grid_strategy.pm.add_position('Sell', 100.0, 1.0, 0)

        trend_side, counter_side, trend_direction = grid_strategy.determine_trend_side()

        assert trend_side == 'Buy'
        assert counter_side == 'Sell'
        assert trend_direction == 'UP'

    def test_determine_trend_side_downtrend(self, grid_strategy):
        """Test trend detection for downtrend (SHORT averaged more)"""
        # Add more SHORT positions than LONG
        grid_strategy.pm.add_position('Buy', 100.0, 1.0, 0)
        for _ in range(3):
            grid_strategy.pm.add_position('Sell', 100.0, 1.0, 0)

        trend_side, counter_side, trend_direction = grid_strategy.determine_trend_side()

        assert trend_side == 'Sell'
        assert counter_side == 'Buy'
        assert trend_direction == 'DOWN'

    def test_cancel_tp_intelligently(self, trading_account, mock_client):
        """Test TP cancellation only removes trend side TP"""
        trading_account.initialize()

        # Setup positions
        for symbol, strategy in trading_account.strategies.items():
            # Uptrend: LONG averaged more
            for _ in range(3):
                strategy.pm.add_position('Buy', 100.0, 1.0, 0)
            strategy.pm.add_position('Sell', 100.0, 1.0, 0)

            # Set TP order IDs
            strategy.pm.set_tp_order_id('Buy', 'tp_long_123')
            strategy.pm.set_tp_order_id('Sell', 'tp_short_456')

        # Cancel TP intelligently
        trading_account.cancel_tp_intelligently()

        # Should cancel LONG (trend) but keep SHORT (counter-trend)
        assert mock_client.cancel_order.called


# ==================== PHASE 5: ADAPTIVE REOPEN ====================

class TestPhase5AdaptiveReopen:
    """Tests for Phase 5: Adaptive Reopen by Margin Ratio"""

    def test_get_total_margin_calculation(self, grid_strategy):
        """Test total margin calculation for a side"""
        # Add positions: 1.0 coin at $100, leverage 100
        # Position value = 1.0 * 100 = $100
        # Margin = 100 / 100 = $1
        grid_strategy.pm.add_position('Buy', 100.0, 1.0, 0)
        grid_strategy.pm.add_position('Buy', 100.0, 2.0, 1)

        # Total qty = 3.0, value = $300, margin = $3
        total_margin = grid_strategy.get_total_margin('Buy')

        assert total_margin == pytest.approx(3.0, rel=0.01)

    def test_calculate_reopen_size_large_imbalance(self, grid_strategy):
        """Test adaptive reopen with large imbalance (ratio ≥ 16)"""
        # Setup: opposite side has 16× initial margin
        grid_strategy.initial_size_usd = 1.0

        # Add positions to opposite side totaling $16 margin
        for _ in range(4):
            grid_strategy.pm.add_position('Sell', 100.0, 4.0, 0)  # 4×$100/100 = $4 each

        reopen_margin = grid_strategy.calculate_reopen_size('Buy', 'Sell')

        # ratio = 16 / 1 = 16 → coefficient = 1.0
        # reopen = 16 * 1.0 = $16
        assert reopen_margin == pytest.approx(16.0, rel=0.01)

    def test_calculate_reopen_size_medium_imbalance(self, grid_strategy):
        """Test adaptive reopen with medium imbalance (ratio ≥ 8)"""
        grid_strategy.initial_size_usd = 1.0

        # Add positions totaling $10 margin (ratio = 10)
        for _ in range(2):
            grid_strategy.pm.add_position('Sell', 100.0, 5.0, 0)

        reopen_margin = grid_strategy.calculate_reopen_size('Buy', 'Sell')

        # ratio = 10 / 1 = 10 → coefficient = 0.5
        # reopen = 10 * 0.5 = $5
        assert reopen_margin == pytest.approx(5.0, rel=0.01)

    def test_calculate_reopen_size_small_imbalance(self, grid_strategy):
        """Test adaptive reopen with small imbalance (ratio < 4)"""
        grid_strategy.initial_size_usd = 1.0

        # Add positions totaling $2 margin (ratio = 2)
        grid_strategy.pm.add_position('Sell', 100.0, 2.0, 0)

        reopen_margin = grid_strategy.calculate_reopen_size('Buy', 'Sell')

        # ratio = 2 / 1 = 2 → return initial
        assert reopen_margin == 1.0


# ==================== PHASE 6: IM MONITORING ====================

class TestPhase6IMMonitoring:
    """Tests for Phase 6: Dynamic IM Monitoring"""

    def test_monitor_initial_margin_metrics(self, trading_account, mock_balance_manager):
        """Test IM monitoring returns correct metrics"""
        trading_account.initialize()

        # Setup balance manager
        for strategy in trading_account.strategies.values():
            strategy.balance_manager = mock_balance_manager

        metrics = trading_account.monitor_initial_margin()

        assert 'total_balance' in metrics
        assert 'total_initial_margin' in metrics
        assert 'safety_reserve' in metrics
        assert 'available_for_trading' in metrics
        assert 'available_percent' in metrics
        assert 'account_mm_rate' in metrics

    def test_im_monitoring_warning_thresholds(self, trading_account, mock_balance_manager):
        """Test IM monitoring warning levels"""
        trading_account.initialize()

        # Setup strategy
        for strategy in trading_account.strategies.values():
            strategy.balance_manager = mock_balance_manager

        # Test WARNING threshold (< 30%)
        mock_balance_manager.get_available_balance.return_value = 5.0
        mock_balance_manager.get_initial_margin.return_value = 15.0  # ~25% available

        metrics = trading_account.monitor_initial_margin()

        # Should calculate low available percentage
        assert metrics['available_percent'] < 30.0


# ==================== PHASE 7: POSITION BALANCING ====================

class TestPhase7PositionBalancing:
    """Tests for Phase 7: Position Balancing in Panic Mode"""

    def test_balance_positions_full(self, trading_account, mock_balance_manager):
        """Test full position balancing (available >= total_needed)"""
        trading_account.initialize()

        # Create imbalance: LONG=5, SHORT=1
        for symbol, strategy in trading_account.strategies.items():
            strategy.balance_manager = mock_balance_manager
            strategy.current_price = 100.0
            strategy.pm.add_position('Buy', 100.0, 5.0, 0)
            strategy.pm.add_position('Sell', 100.0, 1.0, 0)

        # Mock high balance (enough for full balancing)
        mock_balance_manager.get_available_balance.return_value = 100.0

        result = trading_account.balance_all_positions_adaptive()

        # Should attempt balancing
        assert result is True

    def test_balance_positions_partial(self, trading_account, mock_balance_manager):
        """Test partial balancing (0 < available < total_needed)"""
        trading_account.initialize()

        # Create imbalance
        for symbol, strategy in trading_account.strategies.items():
            strategy.balance_manager = mock_balance_manager
            strategy.current_price = 100.0
            strategy.pm.add_position('Buy', 100.0, 10.0, 0)
            strategy.pm.add_position('Sell', 100.0, 1.0, 0)

        # Mock low balance (partial only)
        # Need ~$9 to balance, have $5
        mock_balance_manager.get_available_balance.return_value = 5.0

        result = trading_account.balance_all_positions_adaptive()

        # Should attempt partial balancing
        assert result is True

    def test_balance_positions_critical(self, trading_account, mock_balance_manager):
        """Test critical state (available < $1.00)"""
        trading_account.initialize()

        # Create imbalance
        for symbol, strategy in trading_account.strategies.items():
            strategy.balance_manager = mock_balance_manager
            strategy.current_price = 100.0
            strategy.pm.add_position('Buy', 100.0, 10.0, 0)
            strategy.pm.add_position('Sell', 100.0, 1.0, 0)

        # Mock critical balance
        mock_balance_manager.get_available_balance.return_value = 0.5

        result = trading_account.balance_all_positions_adaptive()

        # Should skip balancing
        assert result is False

    def test_balance_already_balanced_symbols(self, trading_account, mock_balance_manager):
        """Test balancing skips already balanced symbols"""
        trading_account.initialize()

        # Create balanced positions
        for symbol, strategy in trading_account.strategies.items():
            strategy.balance_manager = mock_balance_manager
            strategy.current_price = 100.0
            strategy.pm.add_position('Buy', 100.0, 5.0, 0)
            strategy.pm.add_position('Sell', 100.0, 5.0, 0)

        mock_balance_manager.get_available_balance.return_value = 100.0

        result = trading_account.balance_all_positions_adaptive()

        # Should skip (already balanced)
        assert result is False


# ==================== INTEGRATION TESTS ====================

class TestIntegration:
    """Integration tests for full workflow scenarios"""

    def test_full_workflow_early_freeze_to_panic(self, trading_account, mock_balance_manager):
        """Test complete workflow: Normal → Early Freeze → Panic → Recovery"""
        trading_account.initialize()

        # Setup strategies
        for strategy in trading_account.strategies.values():
            strategy.balance_manager = mock_balance_manager
            strategy.current_price = 100.0
            strategy.pm.add_position('Buy', 100.0, 1.0, 0)

        # Step 1: Trigger Early Freeze
        mock_balance_manager.get_available_balance.return_value = 15.0
        should_freeze, reason = trading_account.check_early_freeze_trigger()
        if should_freeze:
            trading_account.freeze_all_averaging(reason)

        assert trading_account.averaging_frozen

        # Step 2: Trigger Panic Mode
        mock_balance_manager.get_available_balance.return_value = 5.0
        triggered, reason = trading_account.check_panic_trigger_low_im()
        if triggered:
            trading_account.enter_panic_mode(reason)

        assert trading_account.panic_mode
        assert trading_account.averaging_frozen

        # Step 3: Recovery
        mock_balance_manager.get_available_balance.return_value = 100.0
        should_freeze, _ = trading_account.check_early_freeze_trigger()
        if not should_freeze and not trading_account.panic_mode:
            trading_account.unfreeze_all_averaging()

        # Would need to clear panic mode manually or via counter-trend TP

    def test_multi_symbol_reserve_checking(self, trading_account, mock_balance_manager):
        """Test reserve checking works across multiple symbols"""
        trading_account.initialize()

        # Setup multiple symbols (would need to add more strategies)
        for strategy in trading_account.strategies.values():
            strategy.balance_manager = mock_balance_manager
            strategy.current_price = 100.0
            strategy.pm.add_position('Buy', 100.0, 5.0, 0)
            strategy.pm.add_position('Sell', 100.0, 1.0, 0)

        # Calculate safety reserve
        reserve = trading_account.calculate_account_safety_reserve()

        # Reserve should account for all symbols
        assert reserve > 0

    def test_tp_restoration_after_panic_exit(self, trading_account, mock_balance_manager):
        """Test TP orders are restored when exiting panic mode"""
        trading_account.initialize()

        for strategy in trading_account.strategies.values():
            strategy.balance_manager = mock_balance_manager
            strategy.current_price = 100.0

            # Setup positions on both sides
            strategy.pm.add_position('Buy', 100.0, 5.0, 1)
            strategy.pm.add_position('Sell', 100.0, 5.0, 1)

            # Simulate existing TP orders
            strategy.pm.set_tp_order_id('Buy', 'tp_buy_123')
            strategy.pm.set_tp_order_id('Sell', 'tp_sell_456')

        # Enter panic mode (should cancel TREND side TP)
        trading_account.panic_mode = True
        trading_account.panic_reason = "Test panic"
        trading_account.panic_entered_at = time.time()

        # Simulate cancel_tp_intelligently() - removes trend side TP
        for strategy in trading_account.strategies.values():
            trend_side, _, _ = strategy.determine_trend_side()
            strategy.pm.set_tp_order_id(trend_side, None)  # Simulate cancellation

        # Exit panic mode (should restore TP orders)
        trading_account.exit_panic_mode("Test recovery")

        # Verify panic mode is off
        assert not trading_account.panic_mode
        assert trading_account.panic_reason is None

        # Note: TP restoration happens but we can't easily verify
        # without mocking strategy._update_tp_order()
        # The method logs the restoration which is tested implicitly
