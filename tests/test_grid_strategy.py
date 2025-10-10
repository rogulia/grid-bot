"""Unit tests for GridStrategy"""

import pytest
from unittest.mock import Mock, patch, call
from src.strategy.grid_strategy import GridStrategy


class TestGridStrategyInitialization:
    """Tests for GridStrategy initialization"""

    def test_initialization(self, grid_strategy, sample_config):
        """Test GridStrategy initialization"""
        assert grid_strategy.symbol == 'SOLUSDT'
        assert grid_strategy.category == 'linear'
        assert grid_strategy.leverage == 100
        assert grid_strategy.initial_size_usd == 1.0
        assert grid_strategy.grid_step_pct == 1.0
        assert grid_strategy.multiplier == 2.0
        assert grid_strategy.tp_pct == 1.0
        assert grid_strategy.max_grid_levels == 10
        assert grid_strategy.dry_run is True


class TestUSDConversions:
    """Tests for USD to quantity conversions"""

    def test_usd_to_qty(self, grid_strategy):
        """Test USD to quantity conversion with leverage"""
        qty = grid_strategy._usd_to_qty(100.0, 200.0)
        # With 100x leverage: (100 USD × 100) / 200 per coin = 50.0 coins
        assert qty == pytest.approx(50.0)

    def test_usd_to_qty_rounding(self, grid_strategy):
        """Test USD to quantity conversion with rounding and leverage"""
        qty = grid_strategy._usd_to_qty(22.0, 220.0)
        # With 100x leverage: (22 USD × 100) / 220 per coin = 10.0 coins
        assert qty == pytest.approx(10.0)

    def test_usd_to_qty_minimum(self, grid_strategy):
        """Test USD to quantity respects minimum"""
        qty = grid_strategy._usd_to_qty(0.01, 1000.0)
        # Very small amount, should return minimum 0.1
        assert qty == pytest.approx(0.1)

    def test_qty_to_usd(self, grid_strategy):
        """Test quantity to USD conversion"""
        usd = grid_strategy._qty_to_usd(0.5, 200.0)
        # 0.5 coins * 200 per coin = 100 USD
        assert usd == pytest.approx(100.0)


class TestShouldAddPosition:
    """Tests for should_add_position logic"""

    def test_should_add_long_when_price_drops(self, grid_strategy, position_manager):
        """Test LONG position should be added when price drops"""
        # Setup: Add initial LONG position at 100
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        # Price drops to 99 (1% drop)
        should_add = grid_strategy._should_add_position('Buy', 99.0)
        assert should_add is True

    def test_should_not_add_long_when_price_rises(self, grid_strategy, position_manager):
        """Test LONG position should NOT be added when price rises"""
        # Setup: Add initial LONG position at 100
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        # Price rises to 101
        should_add = grid_strategy._should_add_position('Buy', 101.0)
        assert should_add is False

    def test_should_add_short_when_price_rises(self, grid_strategy, position_manager):
        """Test SHORT position should be added when price rises"""
        # Setup: Add initial SHORT position at 100
        position_manager.add_position('Sell', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        # Price rises to 101 (1% rise)
        should_add = grid_strategy._should_add_position('Sell', 101.0)
        assert should_add is True

    def test_should_not_add_short_when_price_drops(self, grid_strategy, position_manager):
        """Test SHORT position should NOT be added when price drops"""
        # Setup: Add initial SHORT position at 100
        position_manager.add_position('Sell', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        # Price drops to 99
        should_add = grid_strategy._should_add_position('Sell', 99.0)
        assert should_add is False

    def test_should_not_add_when_no_last_entry(self, grid_strategy):
        """Test should not add when no last entry exists"""
        # No positions yet
        should_add = grid_strategy._should_add_position('Buy', 100.0)
        assert should_add is False


class TestExecuteGridOrder:
    """Tests for execute_grid_order"""

    def test_execute_grid_order_long(self, grid_strategy, position_manager, mock_bybit_client):
        """Test executing a grid order for LONG"""
        # Setup: Add initial position
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        # Execute grid order at 99
        grid_strategy._execute_grid_order('Buy', 99.0)

        # Should have 2 positions now
        assert position_manager.get_position_count('Buy') == 2

        # Check that order was placed (in dry_run, no actual API call)
        assert mock_bybit_client.place_order.call_count == 0  # dry_run = True

    def test_execute_grid_order_sizing(self, grid_strategy, position_manager):
        """Test grid order sizing with multiplier (applies to MARGIN, not position)"""
        # Setup: Add initial position
        # 0.1 qty @ 100 = $10 position value, $0.1 MARGIN (leverage=100)
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        # Execute grid order at $99
        # Current MARGIN: $10 / 100 = $0.1
        # New MARGIN: $0.1 × (2.0 - 1) = $0.1
        # New position value: $0.1 × 100 = $10
        # New qty: $10 / 99 ≈ 0.101 → rounds to 0.1
        grid_strategy._execute_grid_order('Buy', 99.0)

        # Check total quantity increased
        total = position_manager.get_total_quantity('Buy')
        assert total > 0.1  # Should be ~0.2 now

    def test_execute_grid_order_calls_update_tp(self, grid_strategy, position_manager):
        """Test that executing grid order updates TP"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        with patch.object(grid_strategy, '_update_tp_order') as mock_update_tp:
            grid_strategy._execute_grid_order('Buy', 99.0)
            mock_update_tp.assert_called_once_with('Buy')

    def test_execute_grid_order_margin_vs_position_value(self, grid_strategy, position_manager):
        """Test that averaging applies classic martingale: each position = previous × multiplier"""
        # This is the CRITICAL test for classic martingale progression!

        # Initial position: 1.0 SOL @ $100
        # Position value: $100
        # MARGIN (leverage=100): $1
        position_manager.add_position('Buy', 100.0, 1.0, 0)
        grid_strategy.pm = position_manager

        # Execute grid at $99
        grid_strategy._execute_grid_order('Buy', 99.0)

        # OLD BUGGY BEHAVIOR (if using position value):
        # Would add: $100 × (2.0 - 1) = $100 position → 100 SOL! 💥

        # CLASSIC MARTINGALE BEHAVIOR (multiply last position by multiplier):
        # Last position margin: $1
        # Add: $1 × 2.0 = $2 margin
        # New position: $2 × 100 leverage = $200
        # New qty: $200 / 99 ≈ 2.02 SOL ✅
        # Total: 1.0 + 2.02 ≈ 3.02 SOL

        total_qty = position_manager.get_total_quantity('Buy')

        # Should be approximately 3.0 SOL (1.0 + 2.02), NOT 101 SOL!
        # Classic martingale: 1, 2, 4, 8... (each = previous × 2)
        assert total_qty < 3.5  # Sanity check
        assert total_qty > 2.5  # Should be roughly tripled (1 + 2)
        assert total_qty == pytest.approx(3.0, abs=0.3)  # Approximately 3.0


class TestCheckTakeProfit:
    """Tests for take profit logic"""

    def test_take_profit_long_triggered(self, grid_strategy, position_manager):
        """Test LONG take profit triggers when price rises enough"""
        # Setup: LONG position at 100
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        with patch.object(grid_strategy, '_execute_take_profit') as mock_tp:
            # Price rises to 101 (1% profit)
            grid_strategy._check_take_profit(101.0)
            mock_tp.assert_called_once()

    def test_take_profit_long_not_triggered(self, grid_strategy, position_manager):
        """Test LONG take profit not triggered when price hasn't moved enough"""
        # Setup: LONG position at 100
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        with patch.object(grid_strategy, '_execute_take_profit') as mock_tp:
            # Price rises to 100.5 (only 0.5% profit)
            grid_strategy._check_take_profit(100.5)
            mock_tp.assert_not_called()

    def test_take_profit_short_triggered(self, grid_strategy, position_manager):
        """Test SHORT take profit triggers when price drops enough"""
        # Setup: SHORT position at 100
        position_manager.add_position('Sell', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        with patch.object(grid_strategy, '_execute_take_profit') as mock_tp:
            # Price drops to 99 (1% profit)
            grid_strategy._check_take_profit(99.0)
            mock_tp.assert_called_once()

    def test_take_profit_short_not_triggered(self, grid_strategy, position_manager):
        """Test SHORT take profit not triggered when price hasn't moved enough"""
        # Setup: SHORT position at 100
        position_manager.add_position('Sell', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        with patch.object(grid_strategy, '_execute_take_profit') as mock_tp:
            # Price drops to 99.5 (only 0.5% profit)
            grid_strategy._check_take_profit(99.5)
            mock_tp.assert_not_called()


class TestExecuteTakeProfit:
    """Tests for execute_take_profit"""

    def test_execute_take_profit_closes_position(self, grid_strategy, position_manager):
        """Test that take profit closes position and reopens"""
        # Setup: LONG position at 100
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        # Execute TP at 101
        grid_strategy._execute_take_profit('Buy', 101.0, 1.0)

        # Position should be reopened (strategy reopens after TP)
        assert position_manager.get_position_count('Buy') == 1

    def test_execute_take_profit_reopens_position(self, grid_strategy, position_manager):
        """Test that take profit reopens initial position"""
        # Setup: LONG position at 100
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        # Execute TP at 101
        grid_strategy._execute_take_profit('Buy', 101.0, 1.0)

        # Should have reopened with 1 position
        assert position_manager.get_position_count('Buy') == 1

    def test_execute_take_profit_logs_metrics(self, grid_strategy, position_manager, mock_metrics_tracker):
        """Test that take profit logs to metrics tracker"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager
        grid_strategy.metrics_tracker = mock_metrics_tracker

        grid_strategy._execute_take_profit('Buy', 101.0, 1.0)

        # Should have logged 2 trades: CLOSE and OPEN
        assert mock_metrics_tracker.log_trade.call_count >= 2


class TestRiskLimits:
    """Tests for risk management"""

    def test_check_risk_limits_safe(self, grid_strategy, position_manager, mock_bybit_client):
        """Test risk limits check when safe (low accountMMRate)"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        # Mock wallet balance with safe Account Maintenance Margin Rate
        mock_bybit_client.get_wallet_balance.return_value = {
            'list': [{
                'accountType': 'UNIFIED',
                'accountMMRate': '0.0017'  # 0.17% - safe
            }]
        }

        # Should be safe with low MM rate
        is_safe = grid_strategy._check_risk_limits(100.0)
        assert is_safe is True

    def test_check_risk_limits_near_liquidation(self, grid_strategy, position_manager, mock_bybit_client):
        """Test risk limits when Account MM Rate >= 90% (emergency close all positions)"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        position_manager.add_position('Sell', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager
        grid_strategy.dry_run = False  # Must be False to check real accountMMRate

        # Mock wallet balance with critical Account Maintenance Margin Rate
        mock_bybit_client.get_wallet_balance.return_value = {
            'list': [{
                'accountType': 'UNIFIED',
                'accountMMRate': '0.92'  # 92% - critical! >= 90%
            }]
        }

        with patch.object(grid_strategy, '_emergency_close') as mock_close:
            # Should raise RuntimeError and close ALL positions
            with pytest.raises(RuntimeError, match="Bot stopped.*Maintenance Margin Rate"):
                grid_strategy._check_risk_limits(100.0)

            # Should close both LONG and SHORT
            assert mock_close.call_count == 2

    def test_check_risk_limits_max_exposure(self, grid_strategy, position_manager, mock_bybit_client):
        """Test risk limits when max exposure exceeded"""
        # Add large positions
        for i in range(5):
            position_manager.add_position('Buy', 100.0, 1.0, i)
            position_manager.add_position('Sell', 100.0, 1.0, i)

        grid_strategy.pm = position_manager
        grid_strategy.max_exposure = 100.0  # Low limit

        # Mock wallet balance with safe accountMMRate (so we test exposure limit, not MM rate)
        mock_bybit_client.get_wallet_balance.return_value = {
            'list': [{
                'accountType': 'UNIFIED',
                'accountMMRate': '0.01'  # 1% - safe
            }]
        }

        # Should fail risk check due to max exposure
        is_safe = grid_strategy._check_risk_limits(100.0)
        assert is_safe is False


class TestSyncWithExchange:
    """Tests for sync_with_exchange"""

    def test_sync_opens_initial_positions(self, grid_strategy, mock_bybit_client, position_manager):
        """Test that sync opens initial positions when none exist"""
        # Mock: no positions on exchange
        mock_bybit_client.get_active_position.return_value = None
        grid_strategy.pm = position_manager

        # In dry_run mode
        grid_strategy.sync_with_exchange(100.0)

        # Should track initial positions
        assert position_manager.get_position_count('Buy') == 1
        assert position_manager.get_position_count('Sell') == 1

    def test_sync_restores_positions_from_exchange(self, grid_strategy, mock_bybit_client, position_manager):
        """Test that sync restores positions from exchange"""
        # Mock: position exists on exchange
        mock_bybit_client.get_active_position.return_value = {
            'size': '0.5',
            'avgPrice': '100.0'
        }
        grid_strategy.pm = position_manager

        grid_strategy.sync_with_exchange(100.0)

        # Should restore position from exchange
        assert position_manager.get_total_quantity('Buy') > 0


class TestUpdateTPOrder:
    """Tests for TP order management"""

    def test_update_tp_order_calculates_correct_price_long(self, grid_strategy, position_manager):
        """Test TP order price calculation for LONG"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        with patch.object(grid_strategy.client, 'place_tp_order') as mock_place_tp:
            mock_place_tp.return_value = 'tp_123'
            grid_strategy.dry_run = False

            grid_strategy._update_tp_order('Buy')

            # Should place TP at 101.0 (1% above 100)
            call_args = mock_place_tp.call_args
            tp_price = call_args[1]['tp_price']
            assert tp_price == pytest.approx(101.0)

    def test_update_tp_order_calculates_correct_price_short(self, grid_strategy, position_manager):
        """Test TP order price calculation for SHORT"""
        position_manager.add_position('Sell', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        with patch.object(grid_strategy.client, 'place_tp_order') as mock_place_tp:
            mock_place_tp.return_value = 'tp_123'
            grid_strategy.dry_run = False

            grid_strategy._update_tp_order('Sell')

            # Should place TP at 99.0 (1% below 100)
            call_args = mock_place_tp.call_args
            tp_price = call_args[1]['tp_price']
            assert tp_price == pytest.approx(99.0)

    def test_update_tp_order_cancels_old_order(self, grid_strategy, position_manager):
        """Test that update TP cancels old order first"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        position_manager.set_tp_order_id('Buy', 'old_tp_123')
        grid_strategy.pm = position_manager
        grid_strategy.dry_run = False

        with patch.object(grid_strategy.client, 'cancel_order') as mock_cancel:
            with patch.object(grid_strategy.client, 'place_tp_order') as mock_place:
                mock_place.return_value = 'new_tp_456'

                grid_strategy._update_tp_order('Buy')

                # Should cancel old order
                mock_cancel.assert_called_once_with('SOLUSDT', 'old_tp_123', 'linear')


class TestOnPriceUpdate:
    """Tests for on_price_update orchestration"""

    def test_on_price_update_checks_all_conditions(self, grid_strategy, position_manager):
        """Test that on_price_update checks all conditions"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        with patch.object(grid_strategy, '_check_risk_limits', return_value=True) as mock_risk:
            with patch.object(grid_strategy, '_check_grid_entries') as mock_grid:
                with patch.object(grid_strategy, '_check_take_profit') as mock_tp:
                    grid_strategy.on_price_update(100.0)

                    # All checks should be called
                    mock_risk.assert_called_once_with(100.0)
                    mock_grid.assert_called_once_with(100.0)
                    mock_tp.assert_called_once_with(100.0)

    def test_on_price_update_stops_on_risk_failure(self, grid_strategy, position_manager):
        """Test that on_price_update stops if risk check fails"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        with patch.object(grid_strategy, '_check_risk_limits', return_value=False) as mock_risk:
            with patch.object(grid_strategy, '_check_grid_entries') as mock_grid:
                with patch.object(grid_strategy, '_check_take_profit') as mock_tp:
                    grid_strategy.on_price_update(100.0)

                    # Risk check called
                    mock_risk.assert_called_once()
                    # But grid and TP should not be called
                    mock_grid.assert_not_called()
                    mock_tp.assert_not_called()
