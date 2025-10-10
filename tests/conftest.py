"""Pytest fixtures and configuration for sol-trader tests"""

import pytest
from unittest.mock import Mock, MagicMock
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategy.position_manager import PositionManager, Position
from src.exchange.bybit_client import BybitClient
from src.strategy.grid_strategy import GridStrategy
from src.analytics.metrics_tracker import MetricsTracker


@pytest.fixture
def sample_config():
    """Sample strategy configuration"""
    return {
        'symbol': 'SOLUSDT',
        'category': 'linear',
        'leverage': 100,
        'initial_position_size_usd': 1.0,
        'grid_step_percent': 1.0,
        'averaging_multiplier': 2.0,
        'take_profit_percent': 1.0,
        'max_grid_levels_per_side': 10,
        'liquidation_buffer': 0.5,
        'max_total_exposure': 1000.0
    }


@pytest.fixture
def position_manager():
    """Create a fresh PositionManager instance"""
    return PositionManager(leverage=100, symbol='SOLUSDT', enable_state_persistence=False)


@pytest.fixture
def mock_bybit_client():
    """Mock Bybit client for testing"""
    client = Mock(spec=BybitClient)

    # Mock session for instrument info
    mock_session = Mock()
    mock_session.get_instruments_info = Mock(return_value={
        'retCode': 0,
        'result': {
            'list': [{
                'symbol': 'SOLUSDT',
                'lotSizeFilter': {
                    'minOrderQty': '0.1',
                    'maxOrderQty': '10000',
                    'qtyStep': '0.1'
                }
            }]
        }
    })
    client.session = mock_session

    # Mock common methods
    client.set_leverage = Mock(return_value=True)
    client.get_ticker = Mock(return_value={'lastPrice': '100.0'})
    client.get_wallet_balance = Mock(return_value={
        'list': [{'totalEquity': '1000.0'}]
    })
    client.get_active_position = Mock(return_value=None)
    client.place_order = Mock(return_value={
        'orderId': 'test_order_123',
        'orderStatus': 'Filled'
    })
    client.place_tp_order = Mock(return_value='tp_order_123')
    client.cancel_order = Mock(return_value=True)
    client.close_position = Mock(return_value=True)

    return client


@pytest.fixture
def mock_metrics_tracker():
    """Mock MetricsTracker for testing"""
    tracker = Mock(spec=MetricsTracker)
    tracker.log_trade = Mock()
    tracker.log_snapshot = Mock()
    tracker.save_summary_report = Mock(return_value={
        'performance': {
            'total_pnl': 0.0,
            'roi_percent': 0.0,
            'win_rate': 0.0
        }
    })
    return tracker


@pytest.fixture
def grid_strategy(mock_bybit_client, position_manager, sample_config, mock_metrics_tracker):
    """Create GridStrategy instance with mocks"""
    return GridStrategy(
        client=mock_bybit_client,
        position_manager=position_manager,
        config=sample_config,
        dry_run=True,
        metrics_tracker=mock_metrics_tracker
    )


@pytest.fixture
def sample_long_position():
    """Sample LONG position"""
    return Position(
        side='Buy',
        entry_price=100.0,
        quantity=0.1,
        grid_level=0
    )


@pytest.fixture
def sample_short_position():
    """Sample SHORT position"""
    return Position(
        side='Sell',
        entry_price=100.0,
        quantity=0.1,
        grid_level=0
    )


@pytest.fixture
def position_manager_with_positions():
    """PositionManager with some existing positions"""
    pm = PositionManager(leverage=100, symbol='SOLUSDT', enable_state_persistence=False)

    # Add LONG positions
    pm.add_position('Buy', 100.0, 0.1, 0)
    pm.add_position('Buy', 99.0, 0.2, 1)

    # Add SHORT positions
    pm.add_position('Sell', 100.0, 0.1, 0)
    pm.add_position('Sell', 101.0, 0.2, 1)

    return pm
