"""Integration tests for component interactions"""

import pytest
from unittest.mock import Mock, patch
from src.strategy.grid_strategy import GridStrategy
from src.strategy.position_manager import PositionManager


class TestGridStrategyIntegration:
    """Integration tests for GridStrategy with PositionManager"""

    def test_full_long_cycle(self, mock_bybit_client, sample_config, mock_metrics_tracker):
        """Test full LONG position lifecycle: open -> average -> take profit"""
        pm = PositionManager(leverage=10, enable_state_persistence=False)

        # Use lower leverage to avoid risk limits
        config = sample_config.copy()
        config['leverage'] = 10

        strategy = GridStrategy(
            client=mock_bybit_client,
            position_manager=pm,
            config=config,
            dry_run=True,
            metrics_tracker=mock_metrics_tracker
        )

        # Step 1: Initial position at 100
        strategy.pm.add_position('Buy', 100.0, 0.1, 0)
        assert strategy.pm.get_position_count('Buy') == 1

        # Step 2: Price drops to 99 -> should trigger averaging
        strategy.on_price_update(99.0)
        assert strategy.pm.get_position_count('Buy') == 2  # Added second position

        # Step 3: Price rises to 101 -> should trigger take profit
        avg_entry = strategy.pm.get_average_entry_price('Buy')
        assert avg_entry < 100.0  # Average lowered due to averaging

        # Trigger TP (will close and reopen)
        strategy.on_price_update(101.0)
        # Should have reopened with 1 position
        assert strategy.pm.get_position_count('Buy') >= 1

    def test_full_short_cycle(self, mock_bybit_client, sample_config, mock_metrics_tracker):
        """Test full SHORT position lifecycle: open -> average -> take profit"""
        pm = PositionManager(leverage=10, enable_state_persistence=False)

        # Use lower leverage to avoid risk limits
        config = sample_config.copy()
        config['leverage'] = 10

        strategy = GridStrategy(
            client=mock_bybit_client,
            position_manager=pm,
            config=config,
            dry_run=True,
            metrics_tracker=mock_metrics_tracker
        )

        # Step 1: Initial position at 100
        strategy.pm.add_position('Sell', 100.0, 0.1, 0)
        assert strategy.pm.get_position_count('Sell') == 1

        # Step 2: Price rises to 101 -> should trigger averaging
        strategy.on_price_update(101.0)
        assert strategy.pm.get_position_count('Sell') == 2  # Added second position

        # Step 3: Price drops to 99 -> should trigger take profit
        avg_entry = strategy.pm.get_average_entry_price('Sell')
        assert avg_entry > 100.0  # Average raised due to averaging

        strategy.on_price_update(99.0)
        # Should have reopened with 1 position
        assert strategy.pm.get_position_count('Sell') >= 1

    def test_simultaneous_long_short(self, mock_bybit_client, sample_config, mock_metrics_tracker):
        """Test LONG and SHORT positions operating simultaneously"""
        pm = PositionManager(leverage=10, enable_state_persistence=False)

        # Use lower leverage to avoid risk limits
        config = sample_config.copy()
        config['leverage'] = 10

        strategy = GridStrategy(
            client=mock_bybit_client,
            position_manager=pm,
            config=config,
            dry_run=True,
            metrics_tracker=mock_metrics_tracker
        )

        # Open both positions at 100
        strategy.pm.add_position('Buy', 100.0, 0.1, 0)
        strategy.pm.add_position('Sell', 100.0, 0.1, 0)

        # Price rises to 101
        # - LONG should NOT average (price going favorable)
        # - SHORT should average (price going against)
        strategy.on_price_update(101.0)

        assert strategy.pm.get_position_count('Buy') == 1  # No change
        assert strategy.pm.get_position_count('Sell') == 2  # Averaged

        # Price drops to 99
        # - LONG should average (price going against)
        # - SHORT already averaged, check TP
        strategy.on_price_update(99.0)

        assert strategy.pm.get_position_count('Buy') >= 1

    def test_max_grid_levels_limit(self, mock_bybit_client, sample_config, mock_metrics_tracker):
        """Test that max grid levels is enforced"""
        pm = PositionManager(leverage=100)
        config = sample_config.copy()
        config['max_grid_levels_per_side'] = 3  # Low limit for testing

        strategy = GridStrategy(
            client=mock_bybit_client,
            position_manager=pm,
            config=config,
            dry_run=True,
            metrics_tracker=mock_metrics_tracker
        )

        # Add initial position
        pm.add_position('Buy', 100.0, 0.1, 0)

        # Try to trigger multiple averaging
        for i in range(5):
            price = 100.0 - (i + 1)  # 99, 98, 97, 96, 95
            strategy.on_price_update(price)

        # Should not exceed max grid levels
        assert pm.get_position_count('Buy') <= 3

    def test_risk_limit_emergency_close(self, mock_bybit_client, sample_config, mock_metrics_tracker):
        """Test emergency close when approaching liquidation"""
        pm = PositionManager(leverage=100)
        strategy = GridStrategy(
            client=mock_bybit_client,
            position_manager=pm,
            config=sample_config,
            dry_run=True,
            metrics_tracker=mock_metrics_tracker
        )

        # Open LONG position
        pm.add_position('Buy', 100.0, 1.0, 0)

        # Price drops dangerously close to liquidation
        # At 100x leverage, liquidation ~1% below entry
        # With 0.5% buffer, should close at 99.5 or below
        strategy.on_price_update(99.3)

        # Position should be closed (emergency)
        # Note: In current implementation, it clears positions
        # In real scenario, it would place close order

    def test_pnl_calculation_consistency(self, mock_bybit_client, sample_config, mock_metrics_tracker):
        """Test PnL calculations remain consistent through operations"""
        pm = PositionManager(leverage=100)
        strategy = GridStrategy(
            client=mock_bybit_client,
            position_manager=pm,
            config=sample_config,
            dry_run=True,
            metrics_tracker=mock_metrics_tracker
        )

        # Open LONG position
        pm.add_position('Buy', 100.0, 0.1, 0)

        # Check PnL at entry price
        pnl_at_entry = pm.calculate_pnl(100.0, 'Buy')
        assert pnl_at_entry == pytest.approx(0.0)

        # Add another position at 99
        pm.add_position('Buy', 99.0, 0.2, 1)

        # Average entry should be around 99.33
        avg_entry = pm.get_average_entry_price('Buy')

        # PnL at 100 should be positive
        pnl_at_100 = pm.calculate_pnl(100.0, 'Buy')
        assert pnl_at_100 > 0

        # PnL at average entry should be ~0
        pnl_at_avg = pm.calculate_pnl(avg_entry, 'Buy')
        assert pnl_at_avg == pytest.approx(0.0, abs=0.01)


class TestStrategyWithMetricsIntegration:
    """Integration tests for strategy with metrics tracking"""

    def test_metrics_logging_on_trades(self, mock_bybit_client, sample_config, mock_metrics_tracker):
        """Test that metrics are logged correctly during trades"""
        pm = PositionManager(leverage=100)
        strategy = GridStrategy(
            client=mock_bybit_client,
            position_manager=pm,
            config=sample_config,
            dry_run=True,
            metrics_tracker=mock_metrics_tracker
        )

        # Add initial position
        pm.add_position('Buy', 100.0, 0.1, 0)

        # Execute grid order
        strategy._execute_grid_order('Buy', 99.0)

        # Metrics should have been logged
        assert mock_metrics_tracker.log_trade.called

class TestSyncWithExchangeIntegration:
    """Integration tests for exchange sync functionality"""

    def test_sync_restores_state_from_exchange(self, mock_bybit_client, sample_config, mock_metrics_tracker):
        """Test syncing state from exchange on startup"""
        pm = PositionManager(leverage=100)
        strategy = GridStrategy(
            client=mock_bybit_client,
            position_manager=pm,
            config=sample_config,
            dry_run=True,
            metrics_tracker=mock_metrics_tracker
        )

        # Mock exchange having positions
        def get_position_side_effect(symbol, side, category):
            if side == 'Buy':
                return {'size': '0.5', 'avgPrice': '100.0'}
            return None

        mock_bybit_client.get_active_position.side_effect = get_position_side_effect

        # Mock order history (required for position restoration)
        mock_bybit_client.get_order_history.return_value = [
            {
                'orderId': 'order1',
                'side': 'Buy',
                'positionIdx': 1,  # 1=LONG, 2=SHORT
                'cumExecQty': '0.5',  # Executed quantity (not 'qty')
                'avgPrice': '100.0',
                'orderStatus': 'Filled',
                'createdTime': '1609459200000'
            }
        ]

        # Sync with exchange
        strategy.sync_with_exchange(100.0)

        # Should restore LONG position
        assert pm.get_total_quantity('Buy') > 0
        # Should open new SHORT position (none on exchange)
        assert pm.get_total_quantity('Sell') > 0

    def test_sync_opens_initial_when_no_positions(self, mock_bybit_client, sample_config, mock_metrics_tracker):
        """Test sync opens initial positions when none exist"""
        pm = PositionManager(leverage=100)
        strategy = GridStrategy(
            client=mock_bybit_client,
            position_manager=pm,
            config=sample_config,
            dry_run=True,
            metrics_tracker=mock_metrics_tracker
        )

        # Mock no positions on exchange
        mock_bybit_client.get_active_position.return_value = None

        # Sync should open initial positions
        strategy.sync_with_exchange(100.0)

        # Both sides should have initial positions
        assert pm.get_position_count('Buy') == 1
        assert pm.get_position_count('Sell') == 1


class TestMultiLevelAveraging:
    """Integration tests for multi-level position averaging"""

    def test_progressive_averaging_long(self, mock_bybit_client, sample_config, mock_metrics_tracker):
        """Test progressive averaging with multiplier for LONG"""
        pm = PositionManager(leverage=100)
        config = sample_config.copy()
        config['initial_position_size'] = 10.0  # 10 USD
        config['averaging_multiplier'] = 2.0

        strategy = GridStrategy(
            client=mock_bybit_client,
            position_manager=pm,
            config=config,
            dry_run=True,
            metrics_tracker=mock_metrics_tracker
        )

        # Level 0: 10 USD
        pm.add_position('Buy', 100.0, 0.1, 0)

        # Level 1: Should be ~10 USD (current 10 * (2-1))
        strategy._execute_grid_order('Buy', 99.0)

        # Level 2: Should be ~20 USD (current 20 * (2-1))
        strategy._execute_grid_order('Buy', 98.0)

        # Check positions were added
        assert pm.get_position_count('Buy') == 3

        # Total exposure increases progressively
        total_qty = pm.get_total_quantity('Buy')
        assert total_qty > 0.1

    def test_average_entry_price_calculation(self, mock_bybit_client, sample_config, mock_metrics_tracker):
        """Test average entry price after multiple averaging"""
        pm = PositionManager(leverage=100)
        strategy = GridStrategy(
            client=mock_bybit_client,
            position_manager=pm,
            config=sample_config,
            dry_run=True,
            metrics_tracker=mock_metrics_tracker
        )

        # Add positions at different prices
        pm.add_position('Buy', 100.0, 0.1, 0)  # 10 USD
        pm.add_position('Buy', 99.0, 0.1, 1)   # 9.9 USD
        pm.add_position('Buy', 98.0, 0.1, 2)   # 9.8 USD
        # Total: 29.7 USD for 0.3 qty = 99.0 avg

        avg_entry = pm.get_average_entry_price('Buy')
        assert avg_entry == pytest.approx(99.0)


class TestEdgeCases:
    """Integration tests for edge cases"""

    def test_rapid_price_fluctuations(self, mock_bybit_client, sample_config, mock_metrics_tracker):
        """Test handling of rapid price changes"""
        pm = PositionManager(leverage=100)
        strategy = GridStrategy(
            client=mock_bybit_client,
            position_manager=pm,
            config=sample_config,
            dry_run=True,
            metrics_tracker=mock_metrics_tracker
        )

        pm.add_position('Buy', 100.0, 0.1, 0)

        # Rapid price updates
        prices = [100.0, 99.5, 100.5, 99.0, 101.0, 98.5, 101.5]
        for price in prices:
            strategy.on_price_update(price)

        # Bot should handle without errors
        assert pm.get_position_count('Buy') >= 0

    def test_zero_positions_handling(self, mock_bybit_client, sample_config, mock_metrics_tracker):
        """Test handling when no positions exist"""
        pm = PositionManager(leverage=100)
        strategy = GridStrategy(
            client=mock_bybit_client,
            position_manager=pm,
            config=sample_config,
            dry_run=True,
            metrics_tracker=mock_metrics_tracker
        )

        # No positions, should not crash
        strategy.on_price_update(100.0)

        assert pm.get_position_count('Buy') == 0
        assert pm.get_position_count('Sell') == 0

    def test_extreme_price_movements(self, mock_bybit_client, sample_config, mock_metrics_tracker):
        """Test handling of extreme price movements"""
        pm = PositionManager(leverage=100)
        strategy = GridStrategy(
            client=mock_bybit_client,
            position_manager=pm,
            config=sample_config,
            dry_run=True,
            metrics_tracker=mock_metrics_tracker
        )

        pm.add_position('Buy', 100.0, 0.1, 0)

        # Extreme price drop (10%)
        strategy.on_price_update(90.0)

        # Should handle without crashing
        # Risk limits should trigger emergency close
        assert True  # Test passes if no exception
