"""Unit tests for BybitClient with mocked API calls"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from src.exchange.bybit_client import BybitClient


class TestBybitClientInitialization:
    """Tests for BybitClient initialization"""

    @patch('src.exchange.bybit_client.HTTP')
    def test_initialization_demo(self, mock_http):
        """Test initialization with demo credentials"""
        client = BybitClient(
            api_key='test_key',
            api_secret='test_secret',
            demo=True
        )

        assert client.demo is True
        mock_http.assert_called_once()
        # Verify demo parameter was passed
        call_kwargs = mock_http.call_args[1]
        assert call_kwargs.get('demo') is True

    @patch('src.exchange.bybit_client.HTTP')
    def test_initialization_production(self, mock_http):
        """Test initialization with production credentials"""
        client = BybitClient(
            api_key='test_key',
            api_secret='test_secret',
            demo=False
        )

        assert client.demo is False
        mock_http.assert_called_once()


class TestSetLeverage:
    """Tests for set_leverage method"""

    @patch('src.exchange.bybit_client.HTTP')
    def test_set_leverage_success(self, mock_http):
        """Test successful leverage setting"""
        mock_session = MagicMock()
        mock_session.set_leverage.return_value = {
            'retCode': 0,
            'retMsg': 'OK'
        }
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        result = client.set_leverage('SOLUSDT', 100, 'linear')

        assert result is not None
        mock_session.set_leverage.assert_called_once_with(
            category='linear',
            symbol='SOLUSDT',
            buyLeverage='100',
            sellLeverage='100'
        )

    @patch('src.exchange.bybit_client.HTTP')
    def test_set_leverage_error_handling(self, mock_http):
        """Test leverage setting error handling"""
        mock_session = MagicMock()
        mock_session.set_leverage.side_effect = Exception('API Error')
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)

        with pytest.raises(Exception):
            client.set_leverage('SOLUSDT', 100, 'linear')


class TestPlaceOrder:
    """Tests for place_order method"""

    @patch('src.exchange.bybit_client.HTTP')
    def test_place_market_order_buy(self, mock_http):
        """Test placing market buy order"""
        mock_session = MagicMock()
        mock_session.place_order.return_value = {
            'retCode': 0,
            'result': {
                'orderId': '12345',
                'orderStatus': 'Filled'
            }
        }
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        result = client.place_order(
            symbol='SOLUSDT',
            side='Buy',
            qty=0.1,
            order_type='Market',
            category='linear'
        )

        assert result is not None
        mock_session.place_order.assert_called_once()
        call_kwargs = mock_session.place_order.call_args[1]
        assert call_kwargs['side'] == 'Buy'
        assert call_kwargs['orderType'] == 'Market'
        assert call_kwargs['qty'] == '0.1'

    @patch('src.exchange.bybit_client.HTTP')
    def test_place_limit_order(self, mock_http):
        """Test placing limit order"""
        mock_session = MagicMock()
        mock_session.place_order.return_value = {
            'retCode': 0,
            'result': {'orderId': '12345'}
        }
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        result = client.place_order(
            symbol='SOLUSDT',
            side='Sell',
            qty=0.1,
            order_type='Limit',
            price=100.5,
            category='linear'
        )

        assert result is not None
        call_kwargs = mock_session.place_order.call_args[1]
        assert call_kwargs['orderType'] == 'Limit'
        assert call_kwargs['price'] == '100.5'


class TestPlaceTPOrder:
    """Tests for place_tp_order method"""

    @patch('src.exchange.bybit_client.HTTP')
    def test_place_tp_order_long(self, mock_http):
        """Test placing TP order for LONG position"""
        mock_session = MagicMock()
        mock_session.place_order.return_value = {
            'retCode': 0,
            'result': {'orderId': 'tp_123'}
        }
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        order_id = client.place_tp_order(
            symbol='SOLUSDT',
            side='Sell',  # Close LONG with Sell
            qty=0.5,
            tp_price=101.0,
            category='linear'
        )

        assert order_id == 'tp_123'
        call_kwargs = mock_session.place_order.call_args[1]
        assert call_kwargs['orderType'] == 'Limit'
        assert call_kwargs['price'] == '101.0'
        assert call_kwargs['side'] == 'Sell'


class TestGetActivePosition:
    """Tests for get_active_position method"""

    @patch('src.exchange.bybit_client.HTTP')
    def test_get_active_position_exists(self, mock_http):
        """Test getting active position when it exists"""
        mock_session = MagicMock()
        mock_session.get_positions.return_value = {
            'retCode': 0,
            'result': {
                'list': [
                    {
                        'symbol': 'SOLUSDT',
                        'side': 'Buy',
                        'size': '0.5',
                        'avgPrice': '100.0'
                    }
                ]
            }
        }
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        position = client.get_active_position('SOLUSDT', 'Buy', 'linear')

        assert position is not None
        assert position['size'] == '0.5'
        assert position['avgPrice'] == '100.0'

    @patch('src.exchange.bybit_client.HTTP')
    def test_get_active_position_none(self, mock_http):
        """Test getting active position when none exists"""
        mock_session = MagicMock()
        mock_session.get_positions.return_value = {
            'retCode': 0,
            'result': {'list': []}
        }
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        position = client.get_active_position('SOLUSDT', 'Buy', 'linear')

        assert position is None


class TestGetTicker:
    """Tests for get_ticker method"""

    @patch('src.exchange.bybit_client.HTTP')
    def test_get_ticker_success(self, mock_http):
        """Test getting ticker data"""
        mock_session = MagicMock()
        mock_session.get_tickers.return_value = {
            'retCode': 0,
            'result': {
                'list': [
                    {
                        'symbol': 'SOLUSDT',
                        'lastPrice': '123.45',
                        'bid1Price': '123.40',
                        'ask1Price': '123.50'
                    }
                ]
            }
        }
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        ticker = client.get_ticker('SOLUSDT', 'linear')

        assert ticker is not None
        assert ticker['lastPrice'] == '123.45'


class TestGetWalletBalance:
    """Tests for get_wallet_balance method"""

    @patch('src.exchange.bybit_client.HTTP')
    def test_get_wallet_balance_success(self, mock_http):
        """Test getting wallet balance"""
        mock_session = MagicMock()
        mock_session.get_wallet_balance.return_value = {
            'retCode': 0,
            'result': {
                'list': [
                    {
                        'accountType': 'UNIFIED',
                        'totalEquity': '1000.50',
                        'totalAvailableBalance': '950.00'
                    }
                ]
            }
        }
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        balance = client.get_wallet_balance()

        assert balance is not None
        assert balance['list'][0]['totalEquity'] == '1000.50'


class TestCancelOrder:
    """Tests for cancel_order method"""

    @patch('src.exchange.bybit_client.HTTP')
    def test_cancel_order_success(self, mock_http):
        """Test canceling an order"""
        mock_session = MagicMock()
        mock_session.cancel_order.return_value = {
            'retCode': 0,
            'result': {'orderId': '12345'}
        }
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        result = client.cancel_order('SOLUSDT', '12345', 'linear')

        assert result is not None
        mock_session.cancel_order.assert_called_once_with(
            category='linear',
            symbol='SOLUSDT',
            orderId='12345'
        )


class TestClosePosition:
    """Tests for close_position method"""

    @patch('src.exchange.bybit_client.HTTP')
    def test_close_position_long(self, mock_http):
        """Test closing LONG position"""
        mock_session = MagicMock()
        mock_session.place_order.return_value = {
            'retCode': 0,
            'result': {'orderId': 'close_123'}
        }
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        result = client.close_position('SOLUSDT', 'Buy', 0.5, 'linear')

        assert result is not None
        # Should place Sell order to close Buy position
        call_kwargs = mock_session.place_order.call_args[1]
        assert call_kwargs['side'] == 'Sell'
        assert call_kwargs['qty'] == '0.5'

    @patch('src.exchange.bybit_client.HTTP')
    def test_close_position_short(self, mock_http):
        """Test closing SHORT position"""
        mock_session = MagicMock()
        mock_session.place_order.return_value = {
            'retCode': 0,
            'result': {'orderId': 'close_456'}
        }
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        result = client.close_position('SOLUSDT', 'Sell', 0.5, 'linear')

        assert result is not None
        # Should place Buy order to close Sell position
        call_kwargs = mock_session.place_order.call_args[1]
        assert call_kwargs['side'] == 'Buy'
        assert call_kwargs['qty'] == '0.5'


class TestErrorHandling:
    """Tests for error handling"""

    @patch('src.exchange.bybit_client.HTTP')
    def test_api_error_with_retry(self, mock_http):
        """Test API error handling"""
        mock_session = MagicMock()
        mock_session.place_order.side_effect = Exception('Network error')
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)

        with pytest.raises(Exception) as exc_info:
            client.place_order('SOLUSDT', 'Buy', 0.1, 'Market', 'linear')

        assert 'Network error' in str(exc_info.value)

    @patch('src.exchange.bybit_client.HTTP')
    def test_invalid_response_handling(self, mock_http):
        """Test handling of invalid API responses"""
        mock_session = MagicMock()
        mock_session.get_ticker.return_value = None
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        ticker = client.get_ticker('SOLUSDT', 'linear')

        # Should handle gracefully
        assert ticker is None


class TestClosedPnL:
    """Tests for get_closed_pnl method"""

    @patch('src.exchange.bybit_client.HTTP')
    def test_get_closed_pnl_success(self, mock_http):
        """Test successful closed PnL retrieval"""
        mock_session = MagicMock()
        mock_response = {
            'retCode': 0,
            'result': {
                'list': [
                    {
                        'closedPnl': '1.5',
                        'openFee': '0.06',
                        'closeFee': '0.06',
                        'avgEntryPrice': '100',
                        'avgExitPrice': '101.5',
                        'qty': '1.0',
                        'symbol': 'SOLUSDT',
                        'side': 'Buy'
                    }
                ]
            }
        }
        mock_session.get_closed_pnl.return_value = mock_response
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        records = client.get_closed_pnl('SOLUSDT', limit=1)

        assert len(records) == 1
        assert records[0]['closedPnl'] == '1.5'
        assert records[0]['openFee'] == '0.06'
        assert records[0]['closeFee'] == '0.06'
        mock_session.get_closed_pnl.assert_called_once_with(
            category='linear',
            symbol='SOLUSDT',
            limit=1
        )

    @patch('src.exchange.bybit_client.HTTP')
    def test_get_closed_pnl_empty(self, mock_http):
        """Test closed PnL with no records"""
        mock_session = MagicMock()
        mock_response = {
            'retCode': 0,
            'result': {'list': []}
        }
        mock_session.get_closed_pnl.return_value = mock_response
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        records = client.get_closed_pnl('SOLUSDT')

        assert records == []

    @patch('src.exchange.bybit_client.HTTP')
    def test_get_closed_pnl_failure(self, mock_http):
        """Test closed PnL API failure"""
        mock_session = MagicMock()
        mock_response = {
            'retCode': 10001,
            'retMsg': 'API error'
        }
        mock_session.get_closed_pnl.return_value = mock_response
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        records = client.get_closed_pnl('SOLUSDT')

        assert records == []

    @patch('src.exchange.bybit_client.HTTP')
    def test_get_closed_pnl_exception(self, mock_http):
        """Test closed PnL with exception"""
        mock_session = MagicMock()
        mock_session.get_closed_pnl.side_effect = Exception('Network error')
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        records = client.get_closed_pnl('SOLUSDT')

        assert records == []


class TestTransactionLog:
    """Tests for get_transaction_log method"""

    @patch('src.exchange.bybit_client.HTTP')
    def test_get_transaction_log_success(self, mock_http):
        """Test successful transaction log retrieval"""
        mock_session = MagicMock()
        mock_response = {
            'retCode': 0,
            'result': {
                'list': [
                    {
                        'type': 'SETTLEMENT',
                        'symbol': 'SOLUSDT',
                        'category': 'linear',
                        'funding': '-0.003',
                        'transactionTime': '1696521600000'
                    }
                ]
            }
        }
        mock_session.get_transaction_log.return_value = mock_response
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        records = client.get_transaction_log(
            symbol='SOLUSDT',
            type='SETTLEMENT',
            limit=10
        )

        assert len(records) == 1
        assert records[0]['type'] == 'SETTLEMENT'
        assert records[0]['funding'] == '-0.003'
        mock_session.get_transaction_log.assert_called_once()

    @patch('src.exchange.bybit_client.HTTP')
    def test_get_transaction_log_all_symbols(self, mock_http):
        """Test transaction log for all symbols"""
        mock_session = MagicMock()
        mock_response = {
            'retCode': 0,
            'result': {
                'list': [
                    {'type': 'SETTLEMENT', 'symbol': 'SOLUSDT'},
                    {'type': 'SETTLEMENT', 'symbol': 'BTCUSDT'}
                ]
            }
        }
        mock_session.get_transaction_log.return_value = mock_response
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        records = client.get_transaction_log()

        assert len(records) == 2

    @patch('src.exchange.bybit_client.HTTP')
    def test_get_transaction_log_failure(self, mock_http):
        """Test transaction log API failure"""
        mock_session = MagicMock()
        mock_response = {
            'retCode': 10001,
            'retMsg': 'API error'
        }
        mock_session.get_transaction_log.return_value = mock_response
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        records = client.get_transaction_log()

        assert records == []

    @patch('src.exchange.bybit_client.HTTP')
    def test_get_transaction_log_exception(self, mock_http):
        """Test transaction log with exception"""
        mock_session = MagicMock()
        mock_session.get_transaction_log.side_effect = Exception('Network error')
        mock_http.return_value = mock_session

        client = BybitClient('key', 'secret', demo=True)
        records = client.get_transaction_log(symbol='SOLUSDT')

        assert records == []
