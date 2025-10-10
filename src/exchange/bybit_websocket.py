"""Bybit WebSocket client for real-time price updates"""

import logging
from typing import Callable, Optional
from pybit.unified_trading import WebSocket


class BybitWebSocket:
    """WebSocket client for real-time Bybit data"""

    def __init__(
        self,
        symbol: str,
        price_callback: Callable[[float], None],
        demo: bool = True,
        channel_type: str = "linear"
    ):
        """
        Initialize Bybit WebSocket client

        Args:
            symbol: Trading symbol (e.g., 'SOLUSDT')
            price_callback: Callback function to handle price updates
            demo: Use demo trading (default: True)
            channel_type: Channel type (linear, inverse, spot, option)
        """
        self.logger = logging.getLogger("sol-trader.bybit_ws")
        self.symbol = symbol
        self.price_callback = price_callback
        self.demo = demo
        self.channel_type = channel_type
        self.ws: Optional[WebSocket] = None
        self.current_price: float = 0.0
        self._connected = False

    def _handle_ticker(self, message: dict):
        """
        Handle incoming ticker messages

        Args:
            message: WebSocket message data
        """
        try:
            # Check message structure
            if 'topic' in message and 'data' in message:
                data = message['data']

                # lastPrice is the current price
                if 'lastPrice' in data:
                    price = float(data['lastPrice'])
                    self.current_price = price

                    # Log less frequently to avoid spam
                    if not hasattr(self, '_price_count'):
                        self._price_count = 0

                    self._price_count += 1
                    if self._price_count % 10 == 0:  # Log every 10th update
                        self.logger.debug(f"Price update: ${price:.4f}")

                    # Call the price callback
                    if self.price_callback:
                        self.price_callback(price)

        except Exception as e:
            self.logger.error(f"Error handling ticker message: {e}")
            self.logger.debug(f"Message: {message}")

    def start(self):
        """Start WebSocket connection and subscribe to ticker"""
        try:
            self.logger.info(
                f"Starting WebSocket connection for {self.symbol} "
                f"(demo={self.demo}, channel={self.channel_type})"
            )

            # Initialize WebSocket
            # Note: For demo trading, public data comes from mainnet
            # Private streams use demo endpoint
            # Don't pass demo parameter - always use mainnet for public data
            self.ws = WebSocket(
                testnet=False,  # Public data from mainnet
                channel_type=self.channel_type
            )

            # Subscribe to ticker stream
            self.ws.ticker_stream(
                symbol=self.symbol,
                callback=self._handle_ticker
            )

            self._connected = True
            self.logger.info(f"WebSocket connected and subscribed to {self.symbol} ticker")

        except Exception as e:
            self.logger.error(f"Failed to start WebSocket: {e}")
            self._connected = False
            raise

    def stop(self):
        """Stop WebSocket connection"""
        if self.ws:
            try:
                self.logger.info("Stopping WebSocket connection...")
                # pybit WebSocket doesn't have explicit stop method
                # Connection will be closed when object is destroyed
                self._connected = False
                self.ws = None
                self.logger.info("WebSocket stopped")
            except Exception as e:
                self.logger.error(f"Error stopping WebSocket: {e}")

    def is_connected(self) -> bool:
        """Check if WebSocket is connected"""
        return self._connected

    def get_current_price(self) -> float:
        """Get the most recent price"""
        return self.current_price
