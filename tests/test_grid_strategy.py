"""Unit tests for GridStrategy"""

import pytest
from unittest.mock import Mock, MagicMock, patch, call
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

        # Mock BalanceManager to return critical Account MM Rate
        with patch.object(grid_strategy.balance_manager, 'get_mm_rate', return_value=92.0):
            with patch.object(grid_strategy, '_emergency_close') as mock_close:
                # Should raise RuntimeError and close ALL positions
                with pytest.raises(RuntimeError, match="Maintenance Margin Rate"):
                    grid_strategy._check_risk_limits(100.0)

                # Should close both LONG and SHORT
                assert mock_close.call_count == 2

    def test_check_risk_limits_max_exposure(self, grid_strategy, position_manager, mock_bybit_client):
        """Test that insufficient balance is checked in _execute_grid_order, not in _check_risk_limits"""
        # This test was updated because max_exposure check was moved from _check_risk_limits
        # to _execute_grid_order where it checks actual available balance from exchange

        # Mock wallet balance with safe accountMMRate
        mock_bybit_client.get_wallet_balance.return_value = {
            'list': [{
                'accountType': 'UNIFIED',
                'accountMMRate': '0.01',  # 1% - safe
                'totalAvailableBalance': '1000.0'  # Plenty of balance
            }]
        }

        # Risk check should pass now (balance check happens in _execute_grid_order)
        is_safe = grid_strategy._check_risk_limits(100.0)
        assert is_safe is True


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
        # Mock: Buy position exists on exchange, no Sell position
        def get_position_side_effect(symbol, side, category):
            if side == 'Buy':
                return {'size': '0.5', 'avgPrice': '100.0'}
            return None  # No Sell position

        mock_bybit_client.get_active_position.side_effect = get_position_side_effect

        # Mock order history (required for position restoration)
        mock_bybit_client.get_order_history.return_value = [
            {
                'orderId': 'order1',
                'side': 'Buy',
                'positionIdx': 1,  # 1=LONG
                'cumExecQty': '0.5',  # Executed quantity (not 'qty')
                'avgPrice': '100.0',
                'orderStatus': 'Filled',
                'createdTime': '1609459200000'
            }
        ]

        grid_strategy.pm = position_manager

        grid_strategy.sync_with_exchange(100.0)

        # Should restore Buy position from exchange
        assert position_manager.get_total_quantity('Buy') > 0
        # Should also open initial Sell position
        assert position_manager.get_total_quantity('Sell') > 0


class TestUpdateTPOrder:
    """Tests for TP order management"""

    def test_update_tp_order_calculates_correct_price_long(self, grid_strategy, position_manager):
        """Test TP order price calculation for LONG (with fees)"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        with patch.object(grid_strategy.client, 'place_tp_order') as mock_place_tp:
            mock_place_tp.return_value = 'tp_123'
            grid_strategy.dry_run = False

            grid_strategy._update_tp_order('Buy')

            # Should place TP at 101.075 (1% + 0.075% fees above 100)
            # Fees: 1 position × 0.055% (taker) + 0.020% (maker) = 0.075%
            # Honest TP: 1.0% + 0.075% = 1.075%
            call_args = mock_place_tp.call_args
            tp_price = call_args[1]['tp_price']
            assert tp_price == pytest.approx(101.075)

    def test_update_tp_order_calculates_correct_price_short(self, grid_strategy, position_manager):
        """Test TP order price calculation for SHORT (with fees)"""
        position_manager.add_position('Sell', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        with patch.object(grid_strategy.client, 'place_tp_order') as mock_place_tp:
            mock_place_tp.return_value = 'tp_123'
            grid_strategy.dry_run = False

            grid_strategy._update_tp_order('Sell')

            # Should place TP at 98.925 (1% + 0.075% fees below 100)
            # Fees: 1 position × 0.055% (taker) + 0.020% (maker) = 0.075%
            # Honest TP: 1.0% + 0.075% = 1.075%
            call_args = mock_place_tp.call_args
            tp_price = call_args[1]['tp_price']
            assert tp_price == pytest.approx(98.925)

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
        """Test that on_price_update checks risk limits and grid entries"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        with patch.object(grid_strategy, '_check_risk_limits', return_value=True) as mock_risk:
            with patch.object(grid_strategy, '_check_grid_entries') as mock_grid:
                grid_strategy.on_price_update(100.0)

                # Checks should be called (TP now handled by WebSocket)
                mock_risk.assert_called_once_with(100.0)
                mock_grid.assert_called_once_with(100.0)

    def test_on_price_update_stops_on_risk_failure(self, grid_strategy, position_manager):
        """Test that on_price_update stops if risk check fails"""
        position_manager.add_position('Buy', 100.0, 0.1, 0)
        grid_strategy.pm = position_manager

        with patch.object(grid_strategy, '_check_risk_limits', return_value=False) as mock_risk:
            with patch.object(grid_strategy, '_check_grid_entries') as mock_grid:
                grid_strategy.on_price_update(100.0)

                # Risk check called
                mock_risk.assert_called_once()
                # But grid should not be called
                mock_grid.assert_not_called()


class TestOnPositionUpdate:
    """Tests for on_position_update (Position WebSocket callback)"""

    def test_position_opening_tracked(self, grid_strategy, position_manager):
        """Test that position opening updates cumRealisedPnl tracking"""
        grid_strategy.pm = position_manager

        # Position update: new position opened
        position_data = {
            'symbol': 'SOLUSDT',
            'side': 'Buy',
            'size': '0.5',  # Position opened
            'cumRealisedPnl': '0',  # No PnL yet
            'avgPrice': '100.0'
        }

        grid_strategy.on_position_update(position_data)

        # Should track cumRealisedPnl
        assert grid_strategy._last_cum_realised_pnl['Buy'] == 0.0

    def test_position_closing_detected(self, grid_strategy, position_manager):
        """Test that position closure is detected when size=0"""
        # Setup: Add positions
        position_manager.add_position('Buy', 100.0, 0.5, 0)
        grid_strategy.pm = position_manager

        # Track initial cumRealisedPnl
        grid_strategy._last_cum_realised_pnl['Buy'] = 10.0

        # Position update: position closed
        position_data = {
            'symbol': 'SOLUSDT',
            'side': 'Buy',
            'size': '0',  # Closed
            'cumRealisedPnl': '15.5',  # Made profit
            'avgPrice': '101.0'
        }

        with patch.object(grid_strategy, '_open_initial_position') as mock_open:
            grid_strategy.dry_run = False
            grid_strategy.on_position_update(position_data)

            # Should clear positions
            assert position_manager.get_position_count('Buy') == 0

            # Should update cumRealisedPnl tracking
            assert grid_strategy._last_cum_realised_pnl['Buy'] == 15.5

            # Should log to metrics (check via metrics_tracker mock)
            assert grid_strategy.metrics_tracker.log_trade.called

    def test_cumrealised_pnl_delta_calculation(self, grid_strategy, position_manager):
        """Test that realized PnL is calculated as delta of cumRealisedPnl"""
        # Setup: Add positions
        position_manager.add_position('Sell', 100.0, 0.5, 0)
        grid_strategy.pm = position_manager

        # Track initial cumRealisedPnl
        grid_strategy._last_cum_realised_pnl['Sell'] = 20.0

        # Position update: position closed with increased cumRealisedPnl
        position_data = {
            'symbol': 'SOLUSDT',
            'side': 'Sell',
            'size': '0',  # Closed
            'cumRealisedPnl': '28.75',  # Increased by 8.75
            'avgPrice': '98.0'
        }

        grid_strategy.dry_run = False

        with patch.object(grid_strategy, '_open_initial_position'):
            grid_strategy.on_position_update(position_data)

            # Check metrics_tracker was called with correct delta PnL
            call_args = grid_strategy.metrics_tracker.log_trade.call_args
            assert call_args[1]['pnl'] == pytest.approx(8.75)  # Delta: 28.75 - 20.0

    def test_multiple_position_updates(self, grid_strategy, position_manager):
        """Test handling multiple position updates in sequence"""
        grid_strategy.pm = position_manager

        # First update: position opened
        position_data_1 = {
            'symbol': 'SOLUSDT',
            'side': 'Buy',
            'size': '0.5',
            'cumRealisedPnl': '0',
            'avgPrice': '100.0'
        }
        grid_strategy.on_position_update(position_data_1)
        assert grid_strategy._last_cum_realised_pnl['Buy'] == 0.0

        # Second update: position increased
        position_data_2 = {
            'symbol': 'SOLUSDT',
            'side': 'Buy',
            'size': '1.0',
            'cumRealisedPnl': '0',
            'avgPrice': '99.5'
        }
        grid_strategy.on_position_update(position_data_2)
        assert grid_strategy._last_cum_realised_pnl['Buy'] == 0.0

        # Third update: position closed with profit
        position_manager.add_position('Buy', 99.5, 1.0, 0)  # Add position for closure test
        position_data_3 = {
            'symbol': 'SOLUSDT',
            'side': 'Buy',
            'size': '0',
            'cumRealisedPnl': '12.5',
            'avgPrice': '101.0'
        }
        grid_strategy.dry_run = False

        with patch.object(grid_strategy, '_open_initial_position'):
            grid_strategy.on_position_update(position_data_3)
            assert grid_strategy._last_cum_realised_pnl['Buy'] == 12.5

    def test_position_closure_calls_metrics(self, grid_strategy, position_manager):
        """Test that position closure logs to metrics tracker"""
        position_manager.add_position('Buy', 100.0, 0.5, 0)
        grid_strategy.pm = position_manager
        grid_strategy._last_cum_realised_pnl['Buy'] = 5.0

        position_data = {
            'symbol': 'SOLUSDT',
            'side': 'Buy',
            'size': '0',
            'cumRealisedPnl': '10.25',
            'avgPrice': '101.0'
        }

        grid_strategy.dry_run = False

        with patch.object(grid_strategy, '_open_initial_position'):
            grid_strategy.on_position_update(position_data)

            # Check metrics_tracker.log_trade was called
            grid_strategy.metrics_tracker.log_trade.assert_called_once()
            call_kwargs = grid_strategy.metrics_tracker.log_trade.call_args[1]

            # Verify key fields
            assert call_kwargs['symbol'] == 'SOLUSDT'
            assert call_kwargs['action'] == 'CLOSE'
            assert call_kwargs['pnl'] == pytest.approx(5.25)  # 10.25 - 5.0

    def test_position_closure_with_loss(self, grid_strategy, position_manager):
        """Test that position closure with loss is logged correctly"""
        position_manager.add_position('Sell', 100.0, 0.5, 0)
        grid_strategy.pm = position_manager
        grid_strategy._last_cum_realised_pnl['Sell'] = 10.0

        position_data = {
            'symbol': 'SOLUSDT',
            'side': 'Sell',
            'size': '0',
            'cumRealisedPnl': '7.5',  # Loss: -2.5
            'avgPrice': '102.0'
        }

        grid_strategy.dry_run = False

        with patch.object(grid_strategy, '_open_initial_position'):
            grid_strategy.on_position_update(position_data)

            # Check metrics logged with negative PnL
            call_kwargs = grid_strategy.metrics_tracker.log_trade.call_args[1]
            assert call_kwargs['pnl'] == pytest.approx(-2.5)
            assert call_kwargs['reason'] == 'Loss/Liquidation'

    def test_dry_run_mode_skips_reopen(self, grid_strategy, position_manager):
        """Test that dry_run mode doesn't reopen positions"""
        position_manager.add_position('Buy', 100.0, 0.5, 0)
        grid_strategy.pm = position_manager
        grid_strategy.dry_run = True  # DRY RUN

        position_data = {
            'symbol': 'SOLUSDT',
            'side': 'Buy',
            'size': '0',
            'cumRealisedPnl': '10.0',
            'avgPrice': '101.0'
        }

        with patch.object(grid_strategy, '_open_initial_position') as mock_open:
            grid_strategy.on_position_update(position_data)

            # Should NOT call _open_initial_position in dry_run mode
            mock_open.assert_not_called()


class TestOnWalletUpdate:
    """Tests for on_wallet_update (Wallet WebSocket callback)"""

    def test_balance_update_from_websocket(self, grid_strategy):
        """Test that balance update is pushed to BalanceManager"""
        # Setup balance manager mock
        balance_manager = MagicMock()
        grid_strategy.balance_manager = balance_manager

        # Wallet update: balance changed
        wallet_data = {
            'accountType': 'UNIFIED',
            'totalAvailableBalance': '1234.56',
            'accountMMRate': '0.0015'  # 0.15% as decimal
        }

        grid_strategy.on_wallet_update(wallet_data)

        # Should call balance_manager.update_from_websocket with converted values
        balance_manager.update_from_websocket.assert_called_once()
        call_kwargs = balance_manager.update_from_websocket.call_args[1]

        assert call_kwargs['balance'] == pytest.approx(1234.56)  # Balance
        assert call_kwargs['mm_rate'] == pytest.approx(0.15)     # MM Rate in percentage
        assert call_kwargs['initial_margin'] is None             # Not in test data
        assert call_kwargs['maintenance_margin'] is None         # Not in test data

    def test_mm_rate_update(self, grid_strategy):
        """Test that MM Rate is correctly converted from decimal to percentage"""
        balance_manager = MagicMock()
        grid_strategy.balance_manager = balance_manager

        # Wallet update with MM Rate
        wallet_data = {
            'accountType': 'UNIFIED',
            'totalAvailableBalance': '500.00',
            'accountMMRate': '0.8950'  # 89.50% as decimal
        }

        grid_strategy.on_wallet_update(wallet_data)

        # Check MM Rate conversion (decimal * 100 = percentage)
        call_kwargs = balance_manager.update_from_websocket.call_args[1]
        assert call_kwargs['balance'] == pytest.approx(500.00)
        assert call_kwargs['mm_rate'] == pytest.approx(89.50)

    def test_missing_mm_rate(self, grid_strategy):
        """Test handling of missing MM Rate in wallet data"""
        balance_manager = MagicMock()
        grid_strategy.balance_manager = balance_manager

        # Wallet update without MM Rate
        wallet_data = {
            'accountType': 'UNIFIED',
            'totalAvailableBalance': '500.00',
            'accountMMRate': ''  # Empty string
        }

        grid_strategy.on_wallet_update(wallet_data)

        # Should call with None for MM Rate
        call_kwargs = balance_manager.update_from_websocket.call_args[1]
        assert call_kwargs['balance'] == pytest.approx(500.00)
        assert call_kwargs['mm_rate'] is None

    def test_invalid_wallet_data_handling(self, grid_strategy):
        """Test that invalid wallet data doesn't crash"""
        balance_manager = MagicMock()
        grid_strategy.balance_manager = balance_manager

        # Invalid wallet data: missing totalAvailableBalance
        wallet_data = {
            'accountType': 'UNIFIED',
            'accountMMRate': '0.0015'
        }

        # Should not raise exception
        grid_strategy.on_wallet_update(wallet_data)

        # Should not call update if balance is missing
        balance_manager.update_from_websocket.assert_not_called()

    def test_no_balance_manager(self, grid_strategy):
        """Test that missing balance_manager doesn't crash"""
        grid_strategy.balance_manager = None

        wallet_data = {
            'accountType': 'UNIFIED',
            'totalAvailableBalance': '1000.00',
            'accountMMRate': '0.0015'
        }

        # Should not raise exception
        grid_strategy.on_wallet_update(wallet_data)


class TestOnOrderUpdate:
    """Tests for on_order_update (Order WebSocket callback)"""

    def test_tp_order_new_tracking(self, grid_strategy):
        """Test that new TP order is tracked in _tp_orders"""
        # Order update: new TP order created
        order_data = {
            'orderId': 'tp_order_123',
            'orderStatus': 'New',
            'orderType': 'Market',
            'positionIdx': '1',  # LONG position
            'symbol': 'SOLUSDT',
            'side': 'Sell'
        }

        grid_strategy.on_order_update(order_data)

        # Should track order ID for Buy side (positionIdx 1 = LONG)
        assert grid_strategy._tp_orders['Buy'] == 'tp_order_123'

    def test_tp_order_filled_removes_tracking(self, grid_strategy):
        """Test that filled TP order is removed from tracking"""
        # Setup: track an order
        grid_strategy._tp_orders['Sell'] = 'tp_order_456'

        # Order update: TP order filled
        order_data = {
            'orderId': 'tp_order_456',
            'orderStatus': 'Filled',
            'orderType': 'Market',
            'positionIdx': '2',  # SHORT position
            'symbol': 'SOLUSDT',
            'side': 'Buy'
        }

        grid_strategy.on_order_update(order_data)

        # Should remove from tracking
        assert 'Sell' not in grid_strategy._tp_orders or grid_strategy._tp_orders['Sell'] is None

    def test_tp_order_cancelled_removes_tracking(self, grid_strategy):
        """Test that cancelled TP order is removed from tracking"""
        # Setup: track an order
        grid_strategy._tp_orders['Buy'] = 'tp_order_789'

        # Order update: TP order cancelled
        order_data = {
            'orderId': 'tp_order_789',
            'orderStatus': 'Cancelled',
            'orderType': 'Market',
            'positionIdx': '1',  # LONG position
            'symbol': 'SOLUSDT',
            'side': 'Sell'
        }

        grid_strategy.on_order_update(order_data)

        # Should remove from tracking
        assert 'Buy' not in grid_strategy._tp_orders or grid_strategy._tp_orders['Buy'] is None

    def test_multiple_order_updates_sequence(self, grid_strategy):
        """Test handling multiple order updates in sequence"""
        # First: new order for Buy side
        order_data_1 = {
            'orderId': 'order_1',
            'orderStatus': 'New',
            'orderType': 'Market',
            'positionIdx': '1',
            'symbol': 'SOLUSDT',
            'side': 'Sell'
        }
        grid_strategy.on_order_update(order_data_1)
        assert grid_strategy._tp_orders['Buy'] == 'order_1'

        # Second: new order for Sell side
        order_data_2 = {
            'orderId': 'order_2',
            'orderStatus': 'New',
            'orderType': 'Market',
            'positionIdx': '2',
            'symbol': 'SOLUSDT',
            'side': 'Buy'
        }
        grid_strategy.on_order_update(order_data_2)
        assert grid_strategy._tp_orders['Sell'] == 'order_2'

        # Third: filled order for Buy side
        order_data_3 = {
            'orderId': 'order_1',
            'orderStatus': 'Filled',
            'orderType': 'Market',
            'positionIdx': '1',
            'symbol': 'SOLUSDT',
            'side': 'Sell'
        }
        grid_strategy.on_order_update(order_data_3)
        assert 'Buy' not in grid_strategy._tp_orders or grid_strategy._tp_orders['Buy'] is None
        assert grid_strategy._tp_orders['Sell'] == 'order_2'  # Sell order still tracked

    def test_position_idx_to_side_mapping(self, grid_strategy):
        """Test correct mapping of positionIdx to side tracking"""
        # positionIdx 1 = LONG = Buy side
        order_data_long = {
            'orderId': 'long_order',
            'orderStatus': 'New',
            'orderType': 'Market',
            'positionIdx': '1',
            'symbol': 'SOLUSDT',
            'side': 'Sell'
        }
        grid_strategy.on_order_update(order_data_long)
        assert grid_strategy._tp_orders['Buy'] == 'long_order'

        # positionIdx 2 = SHORT = Sell side
        order_data_short = {
            'orderId': 'short_order',
            'orderStatus': 'New',
            'orderType': 'Market',
            'positionIdx': '2',
            'symbol': 'SOLUSDT',
            'side': 'Buy'
        }
        grid_strategy.on_order_update(order_data_short)
        assert grid_strategy._tp_orders['Sell'] == 'short_order'

    def test_invalid_position_idx_ignored(self, grid_strategy):
        """Test that orders with invalid positionIdx are ignored"""
        # Order with positionIdx 0 (invalid for hedge mode)
        order_data = {
            'orderId': 'invalid_order',
            'orderStatus': 'New',
            'orderType': 'Market',
            'positionIdx': '0',
            'symbol': 'SOLUSDT',
            'side': 'Sell'
        }

        grid_strategy.on_order_update(order_data)

        # Should not track anything
        assert grid_strategy._tp_orders.get('Buy') is None
        assert grid_strategy._tp_orders.get('Sell') is None

    def test_non_market_orders_ignored(self, grid_strategy):
        """Test that non-Market orders are ignored"""
        # Limit order (not TP order)
        order_data = {
            'orderId': 'limit_order',
            'orderStatus': 'New',
            'orderType': 'Limit',
            'positionIdx': '1',
            'symbol': 'SOLUSDT',
            'side': 'Sell'
        }

        grid_strategy.on_order_update(order_data)

        # Should not track Limit orders
        assert grid_strategy._tp_orders.get('Buy') is None


