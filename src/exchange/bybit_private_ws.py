"""Bybit Private WebSocket client for real-time execution data"""

import hmac
import json
import logging
import time
import threading
from typing import Callable, Optional
from pybit.unified_trading import WebSocket


class BybitPrivateWebSocket:
    """
    Private WebSocket client for authenticated Bybit execution stream

    Provides real-time execution events (trades, fills, closures) for all symbols
    on this account. Requires API credentials for authentication.

    Fail-Fast Design:
    - Connection failed → RuntimeError (don't start account)
    - Disconnected → emergency stop callback
    - Invalid data → ERROR log + skip event
    - Reconnect failed 3x → emergency stop
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        execution_callback: Callable[[dict], None],
        disconnect_callback: Callable[[], None],
        demo: bool = True
    ):
        """
        Initialize Private WebSocket

        Args:
            api_key: Bybit API key
            api_secret: Bybit API secret
            execution_callback: Called when execution event received
            disconnect_callback: Called when connection lost (emergency stop)
            demo: True = testnet, False = mainnet
        """
        self.logger = logging.getLogger("sol-trader.private_ws")
        self.api_key = api_key
        self.api_secret = api_secret
        self.execution_callback = execution_callback
        self.disconnect_callback = disconnect_callback
        self.demo = demo

        self.ws: Optional[WebSocket] = None
        self._connected = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 3
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_heartbeat = threading.Event()

        # Callback pause mechanism (for restore/sync operations)
        self._callbacks_paused = False
        self._pause_lock = threading.Lock()

    def _handle_execution(self, message: dict):
        """
        Handle incoming execution messages

        Args:
            message: WebSocket message from Bybit
        """
        try:
            # Check if callbacks are paused (during restore/sync)
            with self._pause_lock:
                if self._callbacks_paused:
                    return  # Skip callback processing during restore

            # Validate message structure
            if 'topic' not in message:
                self.logger.warning(f"Message without topic: {message}")
                return

            if message['topic'] != 'execution':
                return  # Ignore non-execution topics

            if 'data' not in message:
                self.logger.error(f"Execution message without data: {message}")
                return

            # Process each execution in data array
            executions = message['data']
            if not isinstance(executions, list):
                executions = [executions]

            for exec_data in executions:
                try:
                    # Validate required fields (FAIL-FAST)
                    required_fields = ['symbol', 'side', 'execPrice', 'execQty', 'execTime']
                    missing_fields = [f for f in required_fields if f not in exec_data]

                    if missing_fields:
                        self.logger.error(
                            f"❌ Execution event missing required fields {missing_fields}: {exec_data}"
                        )
                        continue  # Skip this event but continue processing others

                    # Log execution
                    symbol = exec_data['symbol']
                    side = exec_data['side']
                    exec_price = exec_data['execPrice']
                    exec_qty = exec_data['execQty']
                    closed_size = exec_data.get('closedSize', '0')
                    closed_pnl = exec_data.get('closedPnl', '0')

                    is_close = float(closed_size) > 0 or float(closed_pnl) != 0
                    action = "CLOSE" if is_close else "OPEN"

                    self.logger.debug(
                        f"🔔 Execution: {symbol} {side} {action} "
                        f"qty={exec_qty} price={exec_price} pnl={closed_pnl}"
                    )

                    # Call user callback
                    if self.execution_callback:
                        self.execution_callback(exec_data)

                except Exception as e:
                    self.logger.error(
                        f"❌ Error processing execution: {e}",
                        exc_info=True
                    )
                    # Continue with next execution

        except Exception as e:
            self.logger.error(f"❌ Error in execution handler: {e}", exc_info=True)

    def _handle_disconnect(self, message: dict):
        """Handle disconnection"""
        self.logger.error(f"💥 Private WebSocket disconnected: {message}")
        self._connected = False

        # Call disconnect callback (emergency stop)
        if self.disconnect_callback:
            try:
                self.disconnect_callback()
            except Exception as e:
                self.logger.error(f"Error in disconnect callback: {e}")

        # Try to reconnect if not max attempts
        if self._reconnect_attempts < self._max_reconnect_attempts:
            self._reconnect_attempts += 1
            wait_time = min(2 ** self._reconnect_attempts, 60)  # Exponential backoff, max 60s

            self.logger.warning(
                f"⚠️  Attempting reconnect #{self._reconnect_attempts}/{self._max_reconnect_attempts} "
                f"in {wait_time}s..."
            )

            time.sleep(wait_time)

            # Close old WebSocket before creating new one (prevent eventpoll leak)
            if self.ws:
                try:
                    self.ws.close()
                    self.logger.debug("Closed old WebSocket before reconnect")
                except Exception as e:
                    self.logger.debug(f"Error closing old WebSocket (non-critical): {e}")
                self.ws = None

            try:
                self.start()
            except Exception as e:
                self.logger.error(f"❌ Reconnect failed: {e}")
        else:
            self.logger.error(
                f"💥 Max reconnect attempts ({self._max_reconnect_attempts}) reached. "
                f"Manual intervention required!"
            )

    def _heartbeat_loop(self):
        """Send ping every 20 seconds to keep connection alive"""
        while not self._stop_heartbeat.is_set():
            if self._connected and self.ws:
                try:
                    # pybit handles ping automatically, but we log it
                    self.logger.debug("💓 Heartbeat")
                except Exception as e:
                    self.logger.error(f"Heartbeat error: {e}")

            # Wait 20 seconds or until stop signal
            self._stop_heartbeat.wait(20)

    def start(self):
        """Start Private WebSocket connection (FAIL-FAST on error)"""
        try:
            env_name = "DEMO" if self.demo else "MAINNET"
            self.logger.info(f"🔐 Starting Private WebSocket ({env_name})...")

            # Initialize WebSocket with authentication
            # testnet=False (mainnet), demo=True (demo mode) -> wss://stream-demo.bybit.com
            # testnet=False, demo=False -> wss://stream.bybit.com
            self.ws = WebSocket(
                testnet=False,           # Not testnet - use mainnet infrastructure
                demo=self.demo,          # Demo mode flag (stream-demo vs stream)
                channel_type="private",  # Private channel
                api_key=self.api_key,
                api_secret=self.api_secret
            )

            # Subscribe to execution stream (all symbols for this account)
            self.ws.execution_stream(
                callback=self._handle_execution
            )

            self._connected = True
            self._reconnect_attempts = 0  # Reset on successful connect

            # Start heartbeat thread
            if not self._heartbeat_thread or not self._heartbeat_thread.is_alive():
                self._stop_heartbeat.clear()
                self._heartbeat_thread = threading.Thread(
                    target=self._heartbeat_loop,
                    daemon=True
                )
                self._heartbeat_thread.start()

            self.logger.info(f"✅ Private WebSocket connected ({env_name})")

        except Exception as e:
            self.logger.error(f"❌ Failed to start Private WebSocket: {e}", exc_info=True)
            self._connected = False
            # FAIL-FAST: Don't allow account to start with broken WebSocket
            raise RuntimeError(
                f"Cannot start Private WebSocket - account cannot trade safely"
            ) from e

    def stop(self):
        """Stop WebSocket connection"""
        self.logger.info("🛑 Stopping Private WebSocket...")

        # Stop heartbeat
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._stop_heartbeat.set()
            self._heartbeat_thread.join(timeout=5)

        # Close WebSocket
        if self.ws:
            try:
                # Explicitly close WebSocket to prevent event loop leak
                # pybit WebSocket wraps websocket-client which creates eventpoll descriptors
                # Without explicit close, old event loops remain open causing descriptor leak
                try:
                    self.ws.close()  # Close the underlying WebSocket connection
                except Exception as close_err:
                    self.logger.debug(f"WebSocket close error (non-critical): {close_err}")

                self._connected = False
                self.ws = None
                self.logger.info("✅ Private WebSocket stopped")
            except Exception as e:
                self.logger.error(f"Error stopping WebSocket: {e}")

    def is_connected(self) -> bool:
        """Check if WebSocket is connected"""
        return self._connected

    def pause_callbacks(self):
        """
        Pause all callback execution (for restore/sync operations)

        Used during sync_with_exchange() to prevent WebSocket events from
        triggering resync loops during restore.
        """
        with self._pause_lock:
            self._callbacks_paused = True
        self.logger.debug("Private WebSocket callbacks PAUSED")

    def resume_callbacks(self):
        """
        Resume callback execution after restore/sync completes
        """
        with self._pause_lock:
            self._callbacks_paused = False
        self.logger.debug("Private WebSocket callbacks RESUMED")
