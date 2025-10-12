"""Multi-account bot orchestrator with WebSocket sharing"""

import logging
from typing import Dict, List, Tuple

from ..exchange.bybit_websocket import BybitWebSocket
from .trading_account import TradingAccount


class MultiAccountBot:
    """
    Orchestrates multiple trading accounts with efficient WebSocket sharing

    WebSocket Sharing Strategy:
    - Key: (symbol, demo: bool)
    - One WebSocket per unique (symbol, environment) pair
    - Demo and production environments use separate WebSockets
    - Multiple accounts share same WebSocket if same (symbol, demo)

    Example:
    - 3 accounts trading SOLUSDT in demo → 1 WebSocket
    - 1 account SOLUSDT demo + 1 account SOLUSDT prod → 2 WebSockets
    - Account1: SOL+DOGE (demo), Account2: SOL (prod) → 3 WebSockets
    """

    def __init__(self):
        """Initialize multi-account bot"""
        self.logger = logging.getLogger("multi_account_bot")

        # WebSocket feeds: (symbol, demo) -> BybitWebSocket
        # Each unique (symbol, environment) pair gets one WebSocket
        self.price_feeds: Dict[Tuple[str, bool], BybitWebSocket] = {}

        # Subscribers: (symbol, demo) -> List[TradingAccount]
        # Accounts subscribed to each WebSocket feed
        self.subscribers: Dict[Tuple[str, bool], List[TradingAccount]] = {}

        # All accounts
        self.accounts: List[TradingAccount] = []

    def register_account(self, account: TradingAccount):
        """
        Register account and subscribe to its symbols

        Creates WebSocket feeds as needed and subscribes account to them.

        Args:
            account: TradingAccount instance to register
        """
        self.accounts.append(account)

        # Subscribe account to each symbol it trades
        for symbol in account.get_symbols():
            key = (symbol, account.demo)

            # Create WebSocket if doesn't exist
            if key not in self.price_feeds:
                self._create_websocket(symbol, account.demo)

            # Add account to subscribers
            if key not in self.subscribers:
                self.subscribers[key] = []

            self.subscribers[key].append(account)

            env_str = 'demo' if account.demo else 'prod'
            self.logger.info(
                f"📡 Account {account.id_str} ({account.name}) subscribed to "
                f"{symbol} ({env_str})"
            )

    def _create_websocket(self, symbol: str, demo: bool):
        """
        Create WebSocket for (symbol, demo) pair

        Args:
            symbol: Trading symbol (e.g., SOLUSDT)
            demo: True = demo environment, False = production
        """
        key = (symbol, demo)

        def price_callback(price: float):
            """Callback when price update received"""
            self._on_price_update(symbol, demo, price)

        # Create WebSocket
        ws = BybitWebSocket(
            symbol=symbol,
            price_callback=price_callback,
            demo=demo,
            channel_type="linear"
        )
        ws.start()

        self.price_feeds[key] = ws

        env_str = 'demo' if demo else 'PROD'
        self.logger.info(f"✅ Created WebSocket: {symbol} ({env_str})")

    def _on_price_update(self, symbol: str, demo: bool, price: float):
        """
        Broadcast price update to all subscribers

        Called by WebSocket when new price arrives.
        Broadcasts to all accounts subscribed to this (symbol, demo) feed.

        Args:
            symbol: Trading symbol
            demo: Environment (demo/prod)
            price: Current market price
        """
        key = (symbol, demo)
        subscribers = self.subscribers.get(key, [])

        # Broadcast to all subscribed accounts
        for account in subscribers:
            try:
                account.process_price(symbol, price)
            except Exception as e:
                # Log error but continue with other accounts
                # One account's error should not affect others
                self.logger.error(
                    f"❌ Error processing price for account {account.id_str} "
                    f"({account.name}) on {symbol}: {e}",
                    exc_info=True
                )

    def get_stats(self) -> Dict:
        """
        Get bot statistics

        Returns:
            Dictionary with:
            - total_accounts: Number of registered accounts
            - total_websockets: Number of WebSocket connections
            - websocket_breakdown: Dict of {websocket_key: subscriber_count}
        """
        websocket_breakdown = {}
        for (symbol, demo), subscribers in self.subscribers.items():
            env_str = 'demo' if demo else 'prod'
            key = f"{symbol} ({env_str})"
            websocket_breakdown[key] = len(subscribers)

        return {
            'total_accounts': len(self.accounts),
            'total_websockets': len(self.price_feeds),
            'websocket_breakdown': websocket_breakdown
        }

    async def shutdown(self):
        """Shutdown all accounts and WebSockets"""
        self.logger.info("=" * 80)
        self.logger.info("🛑 Shutting down multi-account bot...")
        self.logger.info("=" * 80)

        # Shutdown all accounts
        for account in self.accounts:
            try:
                await account.shutdown()
            except Exception as e:
                self.logger.error(
                    f"❌ Error shutting down account {account.id_str}: {e}",
                    exc_info=True
                )

        # Stop all WebSockets
        for (symbol, demo), ws in self.price_feeds.items():
            try:
                env_str = 'demo' if demo else 'prod'
                self.logger.info(f"Stopping WebSocket: {symbol} ({env_str})")
                ws.stop()
            except Exception as e:
                self.logger.error(f"❌ Error stopping WebSocket {symbol}: {e}")

        self.logger.info("=" * 80)
        self.logger.info("✅ All accounts and WebSockets stopped")
        self.logger.info("=" * 80)
