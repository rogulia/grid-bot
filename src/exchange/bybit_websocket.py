"""Bybit WebSocket client for real-time price updates"""

import logging
import time
import threading
from typing import Callable, Optional
from pybit.unified_trading import WebSocket
from config.constants import TradingConstants


class BybitWebSocket:
    """WebSocket client for real-time Bybit data"""

    def __init__(
        self,
        symbol: str,
        price_callback: Callable[[float], None],
        demo: bool = True,
        channel_type: str = "linear",
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        position_callback: Optional[Callable[[dict], None]] = None,
        wallet_callback: Optional[Callable[[dict], None]] = None,
        order_callback: Optional[Callable[[dict], None]] = None,
        websocket_logger: Optional[logging.Logger] = None
    ):
        """
        Initialize Bybit WebSocket client

        Args:
            symbol: Trading symbol (e.g., 'SOLUSDT')
            price_callback: Callback function to handle price updates
            demo: Use demo trading (default: True)
            channel_type: Channel type (linear, inverse, spot, option)
            api_key: API key for private stream (optional)
            api_secret: API secret for private stream (optional)
            position_callback: Callback function to handle position updates (optional)
            wallet_callback: Callback function to handle wallet updates (optional)
            order_callback: Callback function to handle order updates (optional)
            websocket_logger: Logger instance to use (if None, creates default logger)
        """
        # Use provided logger or create default
        self.logger = websocket_logger if websocket_logger else logging.getLogger("sol-trader.bybit_ws")
        self.symbol = symbol
        self.price_callback = price_callback
        self.position_callback = position_callback
        self.wallet_callback = wallet_callback
        self.order_callback = order_callback
        self.demo = demo
        self.channel_type = channel_type
        self.api_key = api_key
        self.api_secret = api_secret
        self.ws: Optional[WebSocket] = None  # Public ticker stream
        self.ws_private: Optional[WebSocket] = None  # Private position stream
        self.current_price: float = 0.0
        self._connected = False

        # Reconnect mechanism
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._reconnect_delay = 1.0  # Initial delay in seconds
        self._max_reconnect_delay = 60.0  # Maximum delay
        self._should_reconnect = True  # Flag to enable/disable reconnect
        self._last_message_time = time.time()
        self._heartbeat_timeout = 30.0  # Seconds without messages before reconnect
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_heartbeat = threading.Event()

        # Callback pause mechanism (for restore/sync operations)
        self._callbacks_paused = False
        self._pause_lock = threading.Lock()

    def _handle_ticker(self, message: dict):
        """
        Handle incoming ticker messages

        Args:
            message: WebSocket message data
        """
        try:
            # Update heartbeat timestamp
            self._last_message_time = time.time()

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
                    if self._price_count % TradingConstants.WEBSOCKET_LOG_EVERY_N_UPDATES == 0:
                        self.logger.debug(f"Price update: ${price:.4f}")

                    # Call the price callback
                    if self.price_callback:
                        self.price_callback(price)

        except Exception as e:
            self.logger.error(f"Error handling ticker message: {e}")
            self.logger.debug(f"Message: {message}")

    def _handle_position(self, message: dict):
        """
        Handle incoming position messages from private WebSocket

        Args:
            message: WebSocket message data
        """
        try:
            # Check if callbacks are paused (during restore/sync)
            with self._pause_lock:
                if self._callbacks_paused:
                    return  # Skip callback processing during restore

            # Check message structure
            if 'topic' in message and 'data' in message:
                data_array = message['data']

                # Position data comes as array
                for position in data_array:
                    symbol = position.get('symbol')

                    # Only process position for our symbol
                    if symbol == self.symbol:
                        side = position.get('side')  # 'Buy' or 'Sell'
                        size = position.get('size')  # Position size
                        cum_realised_pnl = position.get('cumRealisedPnl')  # Cumulative realized PnL
                        avg_price = position.get('avgPrice')  # Average entry price

                        # Log position update
                        self.logger.debug(
                            f"Position update: {symbol} {side} size={size} "
                            f"avgPrice={avg_price} cumPnL={cum_realised_pnl}"
                        )

                        # Call position callback if set
                        if self.position_callback:
                            self.position_callback(position)

        except Exception as e:
            self.logger.error(f"Error handling position message: {e}")
            self.logger.debug(f"Message: {message}")

    def _handle_wallet(self, message: dict):
        """
        Handle incoming wallet messages from private WebSocket

        Args:
            message: WebSocket message data
        """
        try:
            # Check if callbacks are paused (during restore/sync)
            with self._pause_lock:
                if self._callbacks_paused:
                    return  # Skip callback processing during restore

            # Check message structure
            if 'topic' in message and 'data' in message:
                data_array = message['data']

                # Wallet data comes as array
                for wallet in data_array:
                    account_type = wallet.get('accountType')

                    # Only process UNIFIED account
                    if account_type == 'UNIFIED':
                        # Extract ALL balance fields from WebSocket
                        total_available = wallet.get('totalAvailableBalance')
                        account_mm_rate = wallet.get('accountMMRate')
                        total_initial_margin = wallet.get('totalInitialMargin')
                        total_maintenance_margin = wallet.get('totalMaintenanceMargin')

                        # Build log message with all fields
                        log_parts = []
                        if total_available is not None:
                            log_parts.append(f"balance=${total_available}")
                        if account_mm_rate is not None:
                            log_parts.append(f"MM Rate={account_mm_rate}")
                        if total_initial_margin is not None:
                            log_parts.append(f"IM=${total_initial_margin}")
                        if total_maintenance_margin is not None:
                            log_parts.append(f"MM=${total_maintenance_margin}")

                        self.logger.debug(f"Wallet update: {', '.join(log_parts)}")

                        # Call wallet callback with full wallet data
                        # Callback will extract needed fields
                        if self.wallet_callback:
                            self.wallet_callback(wallet)

        except Exception as e:
            self.logger.error(f"Error handling wallet message: {e}")
            self.logger.debug(f"Message: {message}")

    def _handle_order(self, message: dict):
        """
        Handle incoming order messages from private WebSocket

        Args:
            message: WebSocket message data
        """
        try:
            # Check if callbacks are paused (during restore/sync)
            with self._pause_lock:
                if self._callbacks_paused:
                    return  # Skip callback processing during restore

            # Check message structure
            if 'topic' in message and 'data' in message:
                data_array = message['data']

                # Order data comes as array
                for order in data_array:
                    symbol = order.get('symbol')

                    # Only process orders for our symbol
                    if symbol == self.symbol:
                        order_id = order.get('orderId')
                        order_status = order.get('orderStatus')
                        order_type = order.get('orderType')
                        side = order.get('side')

                        # Log order update
                        self.logger.debug(
                            f"Order update: {symbol} {side} {order_type} "
                            f"orderId={order_id} status={order_status}"
                        )

                        # Call order callback if set
                        if self.order_callback:
                            self.order_callback(order)

        except Exception as e:
            self.logger.error(f"Error handling order message: {e}")
            self.logger.debug(f"Message: {message}")

    def _heartbeat_monitor(self):
        """
        Monitor WebSocket connection health via message timestamps
        Reconnects if no messages received within timeout period
        """
        while not self._stop_heartbeat.is_set():
            try:
                time.sleep(5.0)  # Check every 5 seconds

                if not self._connected:
                    continue

                time_since_last_message = time.time() - self._last_message_time

                if time_since_last_message > self._heartbeat_timeout:
                    self.logger.warning(
                        f"⚠️ No messages received for {time_since_last_message:.1f}s "
                        f"(timeout: {self._heartbeat_timeout}s) - reconnecting..."
                    )
                    self._attempt_reconnect()

            except Exception as e:
                self.logger.error(f"Error in heartbeat monitor: {e}")

    def _attempt_reconnect(self):
        """
        Attempt to reconnect WebSocket with exponential backoff
        """
        if not self._should_reconnect:
            self.logger.info("Reconnect disabled, skipping...")
            return

        if self._reconnect_attempts >= self._max_reconnect_attempts:
            self.logger.error(
                f"❌ Max reconnect attempts ({self._max_reconnect_attempts}) reached - giving up"
            )
            self._connected = False
            return

        # Calculate exponential backoff delay
        delay = min(
            self._reconnect_delay * (2 ** self._reconnect_attempts),
            self._max_reconnect_delay
        )

        self._reconnect_attempts += 1
        self.logger.info(
            f"🔄 Reconnecting in {delay:.1f}s (attempt {self._reconnect_attempts}/{self._max_reconnect_attempts})..."
        )

        time.sleep(delay)

        try:
            # Close existing connections
            self.logger.info("Closing old connections...")
            if self.ws:
                try:
                    # Close WebSocket to prevent event loop leak
                    # Call both close() and exit() for thorough cleanup
                    self.ws.close()
                    self.ws.exit()
                except Exception as e:
                    self.logger.debug(f"Error closing public WebSocket (non-critical): {e}")
                self.ws = None

            if self.ws_private:
                try:
                    # Close private WebSocket to prevent event loop leak
                    self.ws_private.close()
                    self.ws_private.exit()
                except Exception as e:
                    self.logger.debug(f"Error closing private WebSocket (non-critical): {e}")
                self.ws_private = None

            # Reconnect
            self.logger.info("Establishing new connections...")
            self.start()

            # Reset reconnect counter on success
            self._reconnect_attempts = 0
            self.logger.info("✅ Reconnection successful")

        except Exception as e:
            self.logger.error(f"Reconnection failed: {e}")
            # Will retry on next heartbeat check

    def start(self):
        """Start WebSocket connection and subscribe to ticker and position streams"""
        try:
            self.logger.info(
                f"Starting WebSocket connection for {self.symbol} "
                f"(demo={self.demo}, channel={self.channel_type})"
            )

            # Initialize public WebSocket for ticker (price updates)
            # Note: For demo trading, public data comes from mainnet
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

            self.logger.info(f"WebSocket connected and subscribed to {self.symbol} ticker")

            # Initialize private WebSocket for position updates (if API credentials provided)
            if self.api_key and self.api_secret:
                self.logger.info(
                    f"Starting private WebSocket for position updates "
                    f"(testnet={self.demo})"
                )

                self.ws_private = WebSocket(
                    testnet=self.demo,  # Use demo endpoint for private stream if demo=True
                    channel_type="private",
                    api_key=self.api_key,
                    api_secret=self.api_secret
                )

                # Subscribe to position stream
                self.ws_private.position_stream(
                    callback=self._handle_position
                )
                self.logger.info("✅ Subscribed to position updates")

                # Subscribe to wallet stream (if callback provided)
                if self.wallet_callback:
                    self.ws_private.wallet_stream(
                        callback=self._handle_wallet
                    )
                    self.logger.info("✅ Subscribed to wallet updates")

                # Subscribe to order stream (if callback provided)
                if self.order_callback:
                    self.ws_private.order_stream(
                        callback=self._handle_order
                    )
                    self.logger.info("✅ Subscribed to order updates")

                self.logger.info("Private WebSocket connected and subscribed to all streams")
            else:
                self.logger.info("No API credentials - position WebSocket not started")

            self._connected = True
            self._last_message_time = time.time()  # Reset heartbeat timer

            # Start heartbeat monitor thread (if not already running)
            if not self._heartbeat_thread or not self._heartbeat_thread.is_alive():
                self._stop_heartbeat.clear()
                self._heartbeat_thread = threading.Thread(
                    target=self._heartbeat_monitor,
                    daemon=True,
                    name=f"WebSocket-Heartbeat-{self.symbol}"
                )
                self._heartbeat_thread.start()
                self.logger.info("❤️ Heartbeat monitor started")

        except Exception as e:
            self.logger.error(f"Failed to start WebSocket: {e}")
            self._connected = False
            raise

    def stop(self):
        """Stop WebSocket connections (public ticker + private position)"""
        try:
            self.logger.info("Stopping WebSocket connections...")

            # Disable reconnection during shutdown
            self._should_reconnect = False
            self._connected = False

            # Stop heartbeat monitor thread
            if self._heartbeat_thread and self._heartbeat_thread.is_alive():
                self.logger.info("Stopping heartbeat monitor...")
                self._stop_heartbeat.set()
                self._heartbeat_thread.join(timeout=2.0)
                self.logger.info("Heartbeat monitor stopped")

            # Stop public ticker WebSocket
            if self.ws:
                try:
                    # Close WebSocket to prevent event loop leak
                    # Call both close() and exit() for thorough cleanup
                    self.ws.close()
                    self.ws.exit()  # Properly close WebSocket connection
                    self.logger.info("Public WebSocket stopped")
                except Exception as e:
                    self.logger.error(f"Error closing public WebSocket: {e}")
                finally:
                    self.ws = None

            # Stop private position WebSocket
            if self.ws_private:
                try:
                    # Close private WebSocket to prevent event loop leak
                    self.ws_private.close()
                    self.ws_private.exit()  # Properly close WebSocket connection
                    self.logger.info("Private WebSocket stopped")
                except Exception as e:
                    self.logger.error(f"Error closing private WebSocket: {e}")
                finally:
                    self.ws_private = None

            self.logger.info("All WebSocket connections stopped")

        except Exception as e:
            self.logger.error(f"Error stopping WebSocket: {e}")

    def is_connected(self) -> bool:
        """Check if WebSocket is connected"""
        return self._connected

    def get_current_price(self) -> float:
        """Get the most recent price"""
        return self.current_price

    def pause_callbacks(self):
        """
        Pause all callback execution (for restore/sync operations)

        Used during sync_with_exchange() to prevent WebSocket events from
        triggering resync loops during restore.
        """
        with self._pause_lock:
            self._callbacks_paused = True
        self.logger.debug(f"[{self.symbol}] WebSocket callbacks PAUSED")

    def resume_callbacks(self):
        """
        Resume callback execution after restore/sync completes
        """
        with self._pause_lock:
            self._callbacks_paused = False
        self.logger.debug(f"[{self.symbol}] WebSocket callbacks RESUMED")
