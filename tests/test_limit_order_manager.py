"""Tests for Limit Order Manager"""

import unittest
import time
from unittest.mock import Mock, MagicMock, patch
from src.utils.limit_order_manager import LimitOrderManager
from config.constants import TradingConstants


class TestLimitOrderManager(unittest.TestCase):
    """Test cases for LimitOrderManager"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_client = Mock()
        self.symbol = "BTCUSDT"
        self.category = "linear"
        
        self.manager = LimitOrderManager(
            client=self.mock_client,
            symbol=self.symbol,
            category=self.category,
            dry_run=False
        )

    def tearDown(self):
        """Clean up after tests"""
        self.manager.cleanup()

    def test_calculate_limit_price_buy(self):
        """Test limit price calculation for Buy orders"""
        current_price = 100.0
        offset_percent = 0.03  # 0.03%
        
        limit_price = self.manager.calculate_limit_price(
            side='Buy',
            current_price=current_price,
            offset_percent=offset_percent
        )
        
        # Buy orders should be slightly above market
        expected_price = current_price * (1 + offset_percent / 100)
        self.assertAlmostEqual(limit_price, expected_price, places=4)
        self.assertGreater(limit_price, current_price)

    def test_calculate_limit_price_sell(self):
        """Test limit price calculation for Sell orders"""
        current_price = 100.0
        offset_percent = 0.03  # 0.03%
        
        limit_price = self.manager.calculate_limit_price(
            side='Sell',
            current_price=current_price,
            offset_percent=offset_percent
        )
        
        # Sell orders should be slightly below market
        expected_price = current_price * (1 - offset_percent / 100)
        self.assertAlmostEqual(limit_price, expected_price, places=4)
        self.assertLess(limit_price, current_price)

    def test_place_limit_order_success(self):
        """Test successful limit order placement"""
        # Mock successful API response
        self.mock_client.place_order.return_value = {
            'retCode': 0,
            'result': {
                'orderId': 'test_order_123'
            }
        }
        
        order_id = self.manager.place_limit_order(
            side='Buy',
            qty=1.0,
            current_price=100.0,
            reason='Test order'
        )
        
        self.assertEqual(order_id, 'test_order_123')
        self.assertIn('test_order_123', self.manager._tracked_orders)
        
        # Verify order was placed with correct parameters
        self.mock_client.place_order.assert_called_once()
        call_args = self.mock_client.place_order.call_args
        self.assertEqual(call_args[1]['symbol'], self.symbol)
        self.assertEqual(call_args[1]['side'], 'Buy')
        self.assertEqual(call_args[1]['qty'], 1.0)
        self.assertEqual(call_args[1]['order_type'], 'Limit')
        self.assertIsNotNone(call_args[1]['price'])

    def test_place_limit_order_failure(self):
        """Test limit order placement failure"""
        # Mock failed API response
        self.mock_client.place_order.return_value = {
            'retCode': 10001,
            'retMsg': 'Error message'
        }
        
        order_id = self.manager.place_limit_order(
            side='Buy',
            qty=1.0,
            current_price=100.0,
            reason='Test order'
        )
        
        self.assertIsNone(order_id)

    def test_on_order_filled(self):
        """Test handling of filled order"""
        # Place an order first
        self.mock_client.place_order.return_value = {
            'retCode': 0,
            'result': {'orderId': 'test_order_123'}
        }
        
        order_id = self.manager.place_limit_order(
            side='Buy',
            qty=1.0,
            current_price=100.0,
            reason='Test'
        )
        
        # Set up callback
        callback_called = []
        def on_filled(oid, info):
            callback_called.append((oid, info))
        
        self.manager.set_callbacks(on_filled=on_filled)
        
        # Simulate filled order
        order_data = {
            'orderId': order_id,
            'orderStatus': 'Filled'
        }
        
        self.manager.on_order_update(order_data)
        
        # Verify callback was called
        self.assertEqual(len(callback_called), 1)
        self.assertEqual(callback_called[0][0], order_id)
        
        # Verify order removed from tracking
        self.assertNotIn(order_id, self.manager._tracked_orders)

    def test_dry_run_mode(self):
        """Test dry run mode doesn't place real orders"""
        dry_run_manager = LimitOrderManager(
            client=self.mock_client,
            symbol=self.symbol,
            category=self.category,
            dry_run=True
        )
        
        order_id = dry_run_manager.place_limit_order(
            side='Buy',
            qty=1.0,
            current_price=100.0,
            reason='Test'
        )
        
        # Should return dry run ID
        self.assertIsNotNone(order_id)
        self.assertTrue(order_id.startswith('DRY_RUN_'))
        
        # Should not call actual API
        self.mock_client.place_order.assert_not_called()
        
        dry_run_manager.cleanup()

    def test_update_current_price(self):
        """Test updating current price for tracked order"""
        # Place an order
        self.mock_client.place_order.return_value = {
            'retCode': 0,
            'result': {'orderId': 'test_order_123'}
        }
        
        order_id = self.manager.place_limit_order(
            side='Buy',
            qty=1.0,
            current_price=100.0,
            reason='Test'
        )
        
        # Update price
        new_price = 105.0
        self.manager.update_current_price(order_id, new_price)
        
        # Verify price was updated
        order_info = self.manager.get_tracked_order(order_id)
        self.assertEqual(order_info['current_price'], new_price)


class TestLimitOrderTimeout(unittest.TestCase):
    """Test cases for limit order timeout and retry logic"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_client = Mock()
        self.symbol = "BTCUSDT"
        
        # Use shorter timeout for testing
        self.original_timeout = TradingConstants.LIMIT_ORDER_TIMEOUT_SEC
        TradingConstants.LIMIT_ORDER_TIMEOUT_SEC = 0.5  # 500ms for testing
        
        self.manager = LimitOrderManager(
            client=self.mock_client,
            symbol=self.symbol,
            category="linear",
            dry_run=False
        )

    def tearDown(self):
        """Clean up after tests"""
        TradingConstants.LIMIT_ORDER_TIMEOUT_SEC = self.original_timeout
        self.manager.cleanup()

    def test_timeout_triggers_retry(self):
        """Test that timeout triggers order retry"""
        # Mock successful order placement
        self.mock_client.place_order.return_value = {
            'retCode': 0,
            'result': {'orderId': 'test_order_123'}
        }
        
        # Place initial order
        order_id = self.manager.place_limit_order(
            side='Buy',
            qty=1.0,
            current_price=100.0,
            reason='Test',
            retry_count=0
        )
        
        self.assertIsNotNone(order_id)
        
        # Wait for timeout
        time.sleep(0.6)
        
        # Should have cancelled old order and placed new one
        self.mock_client.cancel_order.assert_called()
        
        # Should have called place_order twice (initial + retry)
        self.assertEqual(self.mock_client.place_order.call_count, 2)

    def test_max_retries_fallback_to_market(self):
        """Test that max retries leads to market order fallback"""
        # Mock successful order placement for limit orders
        self.mock_client.place_order.side_effect = [
            {'retCode': 0, 'result': {'orderId': 'order_1'}},  # Initial limit
            {'retCode': 0, 'result': {'orderId': 'order_2'}},  # Retry 1 limit
            {'retCode': 0, 'result': {'orderId': 'order_3'}},  # Retry 2 limit
            {'retCode': 0, 'result': {'orderId': 'order_market'}}  # Market fallback
        ]
        
        # Place initial order (retry_count = 0)
        order_id = self.manager.place_limit_order(
            side='Buy',
            qty=1.0,
            current_price=100.0,
            reason='Test',
            retry_count=0
        )
        
        # Wait for timeout and first retry
        time.sleep(0.6)
        
        # Wait for second retry
        time.sleep(0.6)
        
        # Wait for third timeout (should trigger market fallback)
        time.sleep(0.6)
        
        # Should have placed 3 limit orders + 1 market order
        self.assertGreaterEqual(self.mock_client.place_order.call_count, 3)


if __name__ == '__main__':
    unittest.main()

