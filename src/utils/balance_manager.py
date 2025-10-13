"""Balance manager for centralized balance retrieval and caching"""

import time
import logging
import threading
from typing import Optional, Dict
from ..exchange.bybit_client import BybitClient
from config.constants import TradingConstants


class BalanceManager:
    """
    Centralized balance management with WebSocket-first approach

    Balance is updated in real-time via Wallet WebSocket stream.
    REST API is only used for initial balance fetch at startup.

    WebSocket updates are pushed to this manager via update_from_websocket().
    """

    def __init__(self, client: BybitClient, cache_ttl_seconds: float = 5.0):
        """
        Initialize balance manager

        Args:
            client: Bybit API client
            cache_ttl_seconds: DEPRECATED - kept for compatibility but not used
        """
        self.logger = logging.getLogger("sol-trader.balance_manager")
        self.client = client

        # Thread safety lock (WebSocket callbacks run in separate threads)
        self._lock = threading.Lock()

        # Cache (updated by WebSocket, not by polling)
        self._cached_balance: Optional[float] = None
        self._cached_mm_rate: Optional[float] = None
        self._cached_initial_margin: Optional[float] = None
        self._cached_maintenance_margin: Optional[float] = None
        self._cached_full_data: Optional[Dict] = None
        self._last_update_time: float = 0  # Timestamp of last WebSocket update

    def get_available_balance(self, force_refresh: bool = False) -> float:
        """
        Get total available balance (from WebSocket cache)

        Args:
            force_refresh: Force refresh from REST API (DEPRECATED - only for startup)

        Returns:
            Available balance in USDT

        Raises:
            RuntimeError: If balance not available
        """
        if force_refresh:
            # Force refresh only at startup before WebSocket is connected
            self._refresh_balance_from_api()

        with self._lock:
            if self._cached_balance is None:
                raise RuntimeError("Balance not available - WebSocket not yet connected")

            return self._cached_balance

    def get_mm_rate(self, force_refresh: bool = False) -> Optional[float]:
        """
        Get Account Maintenance Margin Rate (from WebSocket cache)

        Args:
            force_refresh: Force refresh from REST API (DEPRECATED - only for startup)

        Returns:
            MM Rate as percentage (e.g., 0.17 for 0.17%) or None if not available
        """
        if force_refresh:
            # Force refresh only at startup before WebSocket is connected
            self._refresh_balance_from_api()

        with self._lock:
            return self._cached_mm_rate

    def get_initial_margin(self, force_refresh: bool = False) -> Optional[float]:
        """
        Get Total Initial Margin used by positions (from WebSocket cache)

        Args:
            force_refresh: Force refresh from REST API (DEPRECATED - only for startup)

        Returns:
            Initial Margin in USDT or None if not available
        """
        if force_refresh:
            # Force refresh only at startup before WebSocket is connected
            self._refresh_balance_from_api()

        with self._lock:
            return self._cached_initial_margin

    def get_maintenance_margin(self, force_refresh: bool = False) -> Optional[float]:
        """
        Get Total Maintenance Margin required (from WebSocket cache)

        Args:
            force_refresh: Force refresh from REST API (DEPRECATED - only for startup)

        Returns:
            Maintenance Margin in USDT or None if not available
        """
        if force_refresh:
            # Force refresh only at startup before WebSocket is connected
            self._refresh_balance_from_api()

        with self._lock:
            return self._cached_maintenance_margin

    def get_full_balance_data(self, force_refresh: bool = False) -> Dict:
        """
        Get full balance data (from WebSocket cache)

        Args:
            force_refresh: Force refresh from REST API (DEPRECATED - only for startup)

        Returns:
            Full balance data dict
        """
        if force_refresh:
            # Force refresh only at startup before WebSocket is connected
            self._refresh_balance_from_api()

        with self._lock:
            return self._cached_full_data or {}

    def update_from_websocket(
        self,
        balance: float,
        mm_rate: Optional[float] = None,
        initial_margin: Optional[float] = None,
        maintenance_margin: Optional[float] = None
    ):
        """
        Update balance cache from Wallet WebSocket

        This is the PRIMARY way to update balance (not REST API polling).
        Called by GridStrategy.on_wallet_update() when WebSocket sends update.

        Args:
            balance: Total available balance in USDT
            mm_rate: Account Maintenance Margin Rate as percentage (e.g., 0.17 for 0.17%)
            initial_margin: Total Initial Margin used by positions in USDT
            maintenance_margin: Total Maintenance Margin required in USDT
        """
        with self._lock:
            self._cached_balance = balance
            self._cached_mm_rate = mm_rate
            self._cached_initial_margin = initial_margin
            self._cached_maintenance_margin = maintenance_margin
            self._last_update_time = time.time()

        # Build debug message
        msg_parts = [f"${balance:.2f}"]
        if mm_rate is not None:
            msg_parts.append(f"MM Rate: {mm_rate:.4f}%")
        if initial_margin is not None:
            msg_parts.append(f"IM: ${initial_margin:.2f}")
        if maintenance_margin is not None:
            msg_parts.append(f"MM: ${maintenance_margin:.2f}")

        self.logger.debug(f"Balance updated from WebSocket: {', '.join(msg_parts)}")

    def _refresh_balance_from_api(self):
        """
        Refresh balance data from REST API

        ONLY used at startup before WebSocket is connected.
        After startup, all updates come via update_from_websocket().
        """
        try:
            self.logger.debug("Fetching initial balance from REST API...")
            balance_info = self.client.get_wallet_balance(account_type="UNIFIED")

            if not balance_info or 'list' not in balance_info:
                raise RuntimeError("Invalid balance response from exchange")

            # Find UNIFIED account
            for account in balance_info.get('list', []):
                if account.get('accountType') == 'UNIFIED':
                    with self._lock:
                        # Extract available balance
                        self._cached_balance = float(account.get('totalAvailableBalance', 0))

                        # Extract MM Rate (accountMMRate is decimal, convert to percentage)
                        account_mm_rate_str = account.get('accountMMRate', '')
                        if account_mm_rate_str and account_mm_rate_str != '':
                            self._cached_mm_rate = float(account_mm_rate_str) * 100
                        else:
                            self._cached_mm_rate = None

                        # Extract Initial Margin
                        total_im_str = account.get('totalInitialMargin', '')
                        if total_im_str and total_im_str != '':
                            self._cached_initial_margin = float(total_im_str)
                        else:
                            self._cached_initial_margin = None

                        # Extract Maintenance Margin
                        total_mm_str = account.get('totalMaintenanceMargin', '')
                        if total_mm_str and total_mm_str != '':
                            self._cached_maintenance_margin = float(total_mm_str)
                        else:
                            self._cached_maintenance_margin = None

                        # Store full data for advanced usage
                        self._cached_full_data = account

                        # Update timestamp
                        self._last_update_time = time.time()

                    # Build log message
                    msg_parts = [f"${self._cached_balance:.2f}"]
                    if self._cached_mm_rate is not None:
                        msg_parts.append(f"MM Rate: {self._cached_mm_rate:.4f}%")
                    if self._cached_initial_margin is not None:
                        msg_parts.append(f"IM: ${self._cached_initial_margin:.2f}")
                    if self._cached_maintenance_margin is not None:
                        msg_parts.append(f"MM: ${self._cached_maintenance_margin:.2f}")

                    self.logger.info(f"Initial balance from API: {', '.join(msg_parts)}")
                    return

            raise RuntimeError("UNIFIED account not found in balance response")

        except Exception as e:
            self.logger.error(f"Failed to get balance from API: {e}")
            raise RuntimeError(f"Cannot get balance from exchange: {e}") from e
