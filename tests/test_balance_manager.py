"""Tests for BalanceManager utility (WebSocket-based)"""

import pytest
from unittest.mock import Mock, MagicMock
from src.utils.balance_manager import BalanceManager


class TestBalanceManager:
    """Test BalanceManager utility class (WebSocket-based)"""

    @pytest.fixture
    def mock_client(self):
        """Create mock Bybit client"""
        client = Mock()
        client.get_wallet_balance = MagicMock(return_value={
            'list': [{
                'accountType': 'UNIFIED',
                'totalAvailableBalance': '1000.50',
                'accountMMRate': '0.255'  # Decimal format (will be * 100 = 25.5%)
            }]
        })
        return client

    @pytest.fixture
    def balance_manager(self, mock_client):
        """Create BalanceManager instance"""
        return BalanceManager(client=mock_client)

    def test_initialization(self, mock_client):
        """Test BalanceManager initialization"""
        manager = BalanceManager(client=mock_client, cache_ttl_seconds=5.0)

        assert manager.client == mock_client
        assert manager._cached_balance is None
        assert manager._cached_mm_rate is None
        assert manager._last_update_time == 0

    def test_get_balance_without_websocket_raises_error(self, balance_manager):
        """Test that getting balance without WebSocket update raises error"""
        with pytest.raises(RuntimeError, match="Balance not available - WebSocket not yet connected"):
            balance_manager.get_available_balance()

    def test_force_refresh_at_startup(self, balance_manager, mock_client):
        """Test force_refresh=True fetches from REST API (startup only)"""
        balance = balance_manager.get_available_balance(force_refresh=True)

        assert balance == 1000.50
        assert balance_manager._cached_balance == 1000.50
        assert mock_client.get_wallet_balance.call_count == 1

    def test_update_from_websocket(self, balance_manager):
        """Test updating balance from WebSocket"""
        # Simulate WebSocket update
        balance_manager.update_from_websocket(balance=1234.56, mm_rate=15.5)

        # Should update cache
        assert balance_manager.get_available_balance() == 1234.56
        assert balance_manager.get_mm_rate() == 15.5

    def test_update_from_websocket_without_mm_rate(self, balance_manager):
        """Test WebSocket update without MM Rate"""
        # Simulate WebSocket update without MM Rate
        balance_manager.update_from_websocket(balance=500.00, mm_rate=None)

        assert balance_manager.get_available_balance() == 500.00
        assert balance_manager.get_mm_rate() is None

    def test_multiple_websocket_updates(self, balance_manager):
        """Test multiple WebSocket updates in sequence"""
        # First update
        balance_manager.update_from_websocket(balance=100.0, mm_rate=10.0)
        assert balance_manager.get_available_balance() == 100.0
        assert balance_manager.get_mm_rate() == 10.0

        # Second update
        balance_manager.update_from_websocket(balance=200.0, mm_rate=20.0)
        assert balance_manager.get_available_balance() == 200.0
        assert balance_manager.get_mm_rate() == 20.0

        # Third update
        balance_manager.update_from_websocket(balance=300.0, mm_rate=30.0)
        assert balance_manager.get_available_balance() == 300.0
        assert balance_manager.get_mm_rate() == 30.0

    def test_get_mm_rate_after_websocket_update(self, balance_manager):
        """Test getting MM rate after WebSocket update"""
        balance_manager.update_from_websocket(balance=1000.0, mm_rate=25.5)

        mm_rate = balance_manager.get_mm_rate()
        assert mm_rate == 25.5

    def test_get_mm_rate_without_websocket_returns_none(self, balance_manager):
        """Test that MM rate returns None when WebSocket hasn't updated yet"""
        # No force_refresh, just check cached value
        mm_rate = balance_manager.get_mm_rate()
        assert mm_rate is None

    def test_force_refresh_mm_rate(self, balance_manager, mock_client):
        """Test force refresh for MM rate"""
        mm_rate = balance_manager.get_mm_rate(force_refresh=True)

        assert mm_rate == 25.5
        assert mock_client.get_wallet_balance.call_count == 1

    def test_get_full_balance_data_without_websocket(self, balance_manager):
        """Test full balance data before WebSocket connection"""
        data = balance_manager.get_full_balance_data()
        assert data == {}

    def test_get_full_balance_data_force_refresh(self, balance_manager, mock_client):
        """Test full balance data with force refresh"""
        data = balance_manager.get_full_balance_data(force_refresh=True)

        assert 'accountType' in data
        assert data['accountType'] == 'UNIFIED'
        assert mock_client.get_wallet_balance.call_count == 1

    def test_api_error_handling_on_force_refresh(self, balance_manager, mock_client):
        """Test handling of API errors on force refresh"""
        mock_client.get_wallet_balance.side_effect = RuntimeError("API Error")

        with pytest.raises(RuntimeError, match="Cannot get balance from exchange"):
            balance_manager.get_available_balance(force_refresh=True)

    def test_invalid_balance_format_on_force_refresh(self, balance_manager, mock_client):
        """Test handling of invalid balance format on force refresh"""
        mock_client.get_wallet_balance.return_value = {
            'list': [{
                'accountType': 'UNIFIED',
                'totalAvailableBalance': 'invalid',
                'accountMMRate': '0.255'
            }]
        }

        with pytest.raises(RuntimeError, match="Cannot get balance from exchange"):
            balance_manager.get_available_balance(force_refresh=True)

    def test_missing_balance_field_on_force_refresh(self, balance_manager, mock_client):
        """Test handling of missing balance field on force refresh"""
        mock_client.get_wallet_balance.return_value = {
            'list': [{
                'accountType': 'UNIFIED',
                'accountMMRate': '0.255'
                # Missing totalAvailableBalance
            }]
        }

        # Should use 0 as default
        balance = balance_manager.get_available_balance(force_refresh=True)
        assert balance == 0.0

    def test_missing_mm_rate_on_force_refresh(self, balance_manager, mock_client):
        """Test handling of missing MM rate on force refresh"""
        mock_client.get_wallet_balance.return_value = {
            'list': [{
                'accountType': 'UNIFIED',
                'totalAvailableBalance': '1000.00'
                # No accountMMRate field
            }]
        }

        mm_rate = balance_manager.get_mm_rate(force_refresh=True)
        assert mm_rate is None

    def test_empty_string_mm_rate_on_force_refresh(self, balance_manager, mock_client):
        """Test handling of empty string MM rate on force refresh"""
        mock_client.get_wallet_balance.return_value = {
            'list': [{
                'accountType': 'UNIFIED',
                'totalAvailableBalance': '1000.00',
                'accountMMRate': ''  # Empty string
            }]
        }

        mm_rate = balance_manager.get_mm_rate(force_refresh=True)
        assert mm_rate is None

    def test_websocket_first_approach(self, balance_manager, mock_client):
        """Test WebSocket-first approach: REST API only for startup"""
        # Startup: force refresh
        balance1 = balance_manager.get_available_balance(force_refresh=True)
        assert balance1 == 1000.50
        assert mock_client.get_wallet_balance.call_count == 1

        # After startup: all updates via WebSocket
        balance_manager.update_from_websocket(balance=2000.0, mm_rate=50.0)
        balance2 = balance_manager.get_available_balance()
        assert balance2 == 2000.0

        # No additional REST API calls
        assert mock_client.get_wallet_balance.call_count == 1

    def test_websocket_update_timestamp(self, balance_manager):
        """Test that WebSocket updates record timestamp"""
        import time

        initial_time = balance_manager._last_update_time
        assert initial_time == 0

        # First update
        balance_manager.update_from_websocket(balance=1000.0, mm_rate=10.0)
        time1 = balance_manager._last_update_time
        assert time1 > initial_time

        # Wait a bit
        time.sleep(0.1)

        # Second update
        balance_manager.update_from_websocket(balance=2000.0, mm_rate=20.0)
        time2 = balance_manager._last_update_time
        assert time2 > time1
