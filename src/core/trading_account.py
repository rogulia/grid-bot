"""Isolated trading account for one user/client in multi-account bot"""

import asyncio
import logging
import time
import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from ..exchange.bybit_client import BybitClient
from ..exchange.bybit_private_ws import BybitPrivateWebSocket
from ..strategy.position_manager import PositionManager
from ..strategy.grid_strategy import GridStrategy
from ..analytics.metrics_tracker import MetricsTracker
from .state_manager import StateManager
from ..utils.timezone import now_helsinki
from ..utils.logger import HelsinkiFormatter


class TradingAccount:
    """
    Represents one user's trading account with complete isolation

    Each account has:
    - Own API credentials and environment (demo/prod)
    - Own strategies and positions
    - Own risk limits
    - Own data files (state, metrics, logs)
    - Own emergency stop flag

    Designed for SaaS model where each account = one paying user
    """

    def __init__(
        self,
        account_id: int,
        name: str,
        api_key: str,
        api_secret: str,
        demo: bool,
        dry_run: bool,
        strategies_config: List[Dict],
        risk_config: Dict
    ):
        """
        Initialize trading account

        Args:
            account_id: Unique numeric ID (1-999)
            name: Display name for UI
            api_key: Bybit API key
            api_secret: Bybit API secret
            demo: True = demo environment, False = production
            dry_run: True = simulation mode, False = real API calls
            strategies_config: List of strategy configurations
            risk_config: Risk management settings (per-account)
        """
        self.account_id = account_id
        self.name = name
        self.demo = demo
        self.dry_run = dry_run

        # Zero-padded ID for files and logs (001, 002, ..., 999)
        self.id_str = f"{account_id:03d}"

        # Setup per-account logging (3 log files)
        self._setup_logging()

        # Store credentials for private WebSocket
        self.api_key = api_key
        self.api_secret = api_secret

        # Bybit client with unique credentials
        self.client = BybitClient(api_key, api_secret, demo)

        # Private WebSocket for execution stream (initialized in initialize())
        self.private_ws: Optional[BybitPrivateWebSocket] = None

        # Strategy components (per symbol)
        self.strategies: Dict[str, GridStrategy] = {}
        self.position_managers: Dict[str, PositionManager] = {}
        self.state_managers: Dict[str, StateManager] = {}

        # Metrics tracker (per account, shared across symbols)
        self.metrics_tracker: Optional[MetricsTracker] = None

        # Configuration
        self.strategies_config = strategies_config
        self.risk_config = risk_config

        # Last log times for periodic logging
        self.last_log_times: Dict[str, float] = {}

    def _setup_logging(self):
        """
        Setup per-account log files

        Creates 3 log files for each account:
        - {ID}_bot_{date}.log - Main bot operations
        - {ID}_trades_{date}.log - All trades
        - {ID}_positions_{date}.log - Position state changes
        """
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)

        today = now_helsinki().strftime("%Y-%m-%d")

        # Formatter for all log files (Helsinki timezone)
        formatter = HelsinkiFormatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # Main logger: {ID}_bot_{date}.log
        self.logger = logging.getLogger(f"account.{self.id_str}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False  # Don't propagate to root

        # File handler for main log
        bot_file_handler = logging.FileHandler(
            log_dir / f"{self.id_str}_bot_{today}.log"
        )
        bot_file_handler.setFormatter(formatter)
        self.logger.addHandler(bot_file_handler)

        # Console handler (for systemd/screen output)
        console_handler = logging.StreamHandler()
        console_formatter = logging.Formatter(
            f'[{self.id_str}] %(levelname)s - %(message)s'
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # Trades logger: {ID}_trades_{date}.log
        self.trades_logger = logging.getLogger(f"account.{self.id_str}.trades")
        self.trades_logger.setLevel(logging.INFO)
        self.trades_logger.propagate = False

        trades_file_handler = logging.FileHandler(
            log_dir / f"{self.id_str}_trades_{today}.log"
        )
        trades_file_handler.setFormatter(formatter)
        self.trades_logger.addHandler(trades_file_handler)

        # Positions logger: {ID}_positions_{date}.log
        self.positions_logger = logging.getLogger(f"account.{self.id_str}.positions")
        self.positions_logger.setLevel(logging.INFO)
        self.positions_logger.propagate = False

        positions_file_handler = logging.FileHandler(
            log_dir / f"{self.id_str}_positions_{today}.log"
        )
        positions_file_handler.setFormatter(formatter)
        self.positions_logger.addHandler(positions_file_handler)

    async def initialize(self):
        """
        Initialize account: check emergency stop, get balance, setup strategies

        Raises:
            RuntimeError: If emergency stop file exists or initialization fails
        """
        self.logger.info("=" * 60)
        self.logger.info(f"👤 Initializing Account {self.id_str}: {self.name}")
        self.logger.info(f"   Environment: {'DEMO' if self.demo else 'PRODUCTION ⚠️'}")
        self.logger.info(f"   Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
        self.logger.info("=" * 60)

        # ⚠️ CRITICAL: Check for emergency stop file
        emergency_file = Path(f"data/.{self.id_str}_emergency_stop")
        if emergency_file.exists():
            try:
                with open(emergency_file) as f:
                    data = json.load(f)

                raise RuntimeError(
                    f"❌ Account {self.id_str} ({self.name}) has emergency stop flag!\n"
                    f"   File: {emergency_file}\n"
                    f"   Timestamp: {data.get('timestamp', 'unknown')}\n"
                    f"   Reason: {data.get('reason', 'unknown')}\n"
                    f"   Symbol: {data.get('symbol', 'N/A')}\n"
                    f"\n"
                    f"   Fix issues and remove file:\n"
                    f"   rm {emergency_file}"
                )
            except json.JSONDecodeError:
                raise RuntimeError(
                    f"❌ Account {self.id_str} has corrupted emergency stop file: {emergency_file}\n"
                    f"   Remove it manually: rm {emergency_file}"
                )

        # Get account balance (REQUIRED - fail-fast if not available)
        self.logger.info("Fetching account balance...")
        balance_data = self.client.get_wallet_balance()
        if not balance_data:
            raise RuntimeError(f"[{self.id_str}] Failed to get wallet balance from exchange")

        accounts = balance_data.get('list', [])
        if not accounts or 'totalEquity' not in accounts[0]:
            raise RuntimeError(f"[{self.id_str}] Invalid balance response from exchange")

        initial_balance = float(accounts[0]['totalEquity'])
        self.logger.info(f"💰 Account Balance: ${initial_balance:.2f}")

        # Initialize metrics tracker with account ID prefix
        self.metrics_tracker = MetricsTracker(
            initial_balance=initial_balance,
            file_prefix=f"{self.id_str}_"  # Files: 001_trades_history.csv, etc.
        )

        # Initialize each trading symbol
        for strategy_config in self.strategies_config:
            symbol = strategy_config['symbol']
            leverage = strategy_config['leverage']
            category = strategy_config.get('category', 'linear')

            self.logger.info(f"📊 Setting up {symbol} (leverage: {leverage}x)")

            # Set position mode to Hedge Mode (required for dual-sided trading)
            if not self.dry_run:
                self.client.set_position_mode(symbol, mode=3, category=category)
                self.logger.debug(f"[{symbol}] Set position mode: Hedge")
            else:
                self.logger.info(f"[{symbol}] [DRY RUN] Would set Hedge Mode")

            # Set leverage
            if not self.dry_run:
                self.client.set_leverage(symbol, leverage, category)
                self.logger.debug(f"[{symbol}] Set leverage: {leverage}x")
            else:
                self.logger.info(f"[{symbol}] [DRY RUN] Would set leverage: {leverage}x")

            # Create state manager with ID prefix (before position_manager!)
            self.state_managers[symbol] = StateManager(
                state_file=f"data/{self.id_str}_bot_state.json",  # File: 001_bot_state.json
                symbol=symbol,
                account_id=self.account_id
            )

            # Create position manager (pass state_manager for correct file naming)
            self.position_managers[symbol] = PositionManager(
                leverage=leverage,
                symbol=symbol,
                state_manager=self.state_managers[symbol]  # ✅ Use account-specific state manager
            )

            # Merge strategy config with risk config
            combined_config = {**strategy_config, **self.risk_config}

            # Create grid strategy (pass account_id and logger!)
            self.strategies[symbol] = GridStrategy(
                client=self.client,
                position_manager=self.position_managers[symbol],
                config=combined_config,
                dry_run=self.dry_run,
                metrics_tracker=self.metrics_tracker,
                account_id=self.account_id,      # ✅ Pass ID
                account_logger=self.logger        # ✅ Pass logger
            )

            # Get initial price
            ticker = self.client.get_ticker(symbol, category)
            if not ticker:
                raise RuntimeError(f"[{self.id_str}] Failed to get ticker for {symbol}")

            initial_price = float(ticker['lastPrice'])
            self.logger.info(f"💵 [{symbol}] Initial price: ${initial_price:.4f}")

            # Sync with exchange (opens initial positions if needed)
            self.strategies[symbol].sync_with_exchange(initial_price)

            # Initialize last log time
            self.last_log_times[symbol] = 0

            self.logger.info(f"✅ [{symbol}] Initialized successfully")

        self.logger.info("=" * 60)
        self.logger.info(f"✅ Account {self.id_str} fully initialized with {len(self.strategies)} symbol(s)")
        self.logger.info("=" * 60)

        # Start Private WebSocket for execution stream (FAIL-FAST if error)
        if not self.dry_run:
            try:
                self.logger.info("🔐 Starting Private WebSocket for execution stream...")
                self.private_ws = BybitPrivateWebSocket(
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    execution_callback=self._on_execution,
                    disconnect_callback=self._on_disconnect,
                    demo=self.demo
                )
                self.private_ws.start()
                self.logger.info("✅ Private WebSocket started successfully")
            except Exception as e:
                self.logger.error(f"❌ Failed to start Private WebSocket: {e}", exc_info=True)
                raise RuntimeError(
                    f"Account {self.id_str} cannot start without Private WebSocket"
                ) from e
        else:
            self.logger.info("🔕 Dry run mode - Private WebSocket disabled")

    def _on_execution(self, exec_data: dict):
        """
        Handle execution event from Private WebSocket

        Called when trade execution received from Bybit.
        Routes to appropriate strategy for processing.

        Args:
            exec_data: Execution data from Bybit WebSocket
        """
        try:
            symbol = exec_data.get('symbol')
            if not symbol:
                self.logger.error(f"❌ Execution event without symbol: {exec_data}")
                return

            strategy = self.strategies.get(symbol)
            if not strategy:
                self.logger.warning(f"⚠️  Execution for unknown symbol {symbol}: {exec_data}")
                return

            # Route to strategy
            strategy.on_execution(exec_data)

        except Exception as e:
            self.logger.error(
                f"❌ Error processing execution event: {e}",
                exc_info=True
            )

    def _on_disconnect(self):
        """
        Handle Private WebSocket disconnection

        Emergency stop this account - cannot trade safely without execution stream.
        """
        self.logger.error(
            f"💥 Private WebSocket disconnected for account {self.id_str}!\n"
            f"   Cannot trade safely without execution stream.\n"
            f"   Emergency stopping account..."
        )

        # Emergency stop all strategies
        for symbol, strategy in self.strategies.items():
            try:
                strategy.emergency_stop(f"Private WebSocket disconnected")
            except Exception as e:
                self.logger.error(f"Error emergency stopping {symbol}: {e}")

    def process_price(self, symbol: str, price: float):
        """
        Process price update for a symbol

        Called by MultiAccountBot when WebSocket receives price update.
        Broadcasts price to strategy and handles periodic logging/sync.

        Args:
            symbol: Trading symbol (e.g., SOLUSDT)
            price: Current market price
        """
        try:
            strategy = self.strategies.get(symbol)
            position_manager = self.position_managers.get(symbol)

            if not strategy or not position_manager:
                self.logger.error(f"[{symbol}] No strategy/position_manager found")
                return

            # Check if strategy is in emergency stop state
            if strategy.is_stopped():
                # Don't spam logs - emergency already logged
                return

            # Execute strategy logic
            strategy.on_price_update(price)

            # Periodic sync and logging (every 60 seconds)
            current_time = time.time()
            last_log_time = self.last_log_times.get(symbol, 0)

            if current_time - last_log_time >= 60:
                # NOTE: sync_with_exchange() removed - Private WebSocket now provides
                # real-time execution data via on_execution() callback. No need for polling.

                # Calculate PnL
                long_pnl = position_manager.calculate_pnl(price, 'Buy')
                short_pnl = position_manager.calculate_pnl(price, 'Sell')

                # Log current state
                self.logger.info(
                    f"[{symbol}] Price: ${price:.4f} | "
                    f"LONG PnL: ${long_pnl:.2f} | SHORT PnL: ${short_pnl:.2f}"
                )

                # Log snapshot to CSV
                if self.metrics_tracker:
                    self.metrics_tracker.log_snapshot(
                        symbol=symbol,
                        price=price,
                        long_positions=len(position_manager.long_positions),
                        short_positions=len(position_manager.short_positions),
                        long_qty=position_manager.get_total_quantity('Buy'),
                        short_qty=position_manager.get_total_quantity('Sell'),
                        long_pnl=long_pnl,
                        short_pnl=short_pnl
                    )

                self.last_log_times[symbol] = current_time

        except Exception as e:
            self.logger.error(f"[{symbol}] Error in price update handler: {e}", exc_info=True)

    def get_symbols(self) -> List[str]:
        """
        Get list of symbols this account trades

        Returns:
            List of symbol names
        """
        return [s['symbol'] for s in self.strategies_config]

    def is_stopped(self) -> bool:
        """
        Check if any strategy is in emergency stop state

        Returns:
            True if any strategy has emergency_stopped flag set
        """
        return any(strategy.is_stopped() for strategy in self.strategies.values())

    def generate_daily_report(self, date: str):
        """
        Generate daily report for specific date

        Args:
            date: Date in format YYYY-MM-DD (e.g., '2025-10-11')

        Returns:
            Summary dict if report was generated, None if no data for this date
        """
        if self.metrics_tracker:
            return self.metrics_tracker.generate_daily_report(date)
        return None

    async def shutdown(self):
        """Graceful shutdown of account"""
        self.logger.info("=" * 60)
        self.logger.info(f"🛑 Shutting down account {self.id_str}: {self.name}")
        self.logger.info("=" * 60)

        # Log final state for each symbol
        for symbol, strategy in self.strategies.items():
            position_manager = self.position_managers[symbol]

            # Get current price from WebSocket or client
            try:
                ticker = self.client.get_ticker(symbol, strategy.category)
                current_price = float(ticker['lastPrice']) if ticker else 0

                if current_price > 0:
                    long_pnl = position_manager.calculate_pnl(current_price, 'Buy')
                    short_pnl = position_manager.calculate_pnl(current_price, 'Sell')
                    total_pnl = long_pnl + short_pnl

                    self.logger.info(f"📊 FINAL STATE - {symbol}")
                    self.logger.info(f"   Price: ${current_price:.4f}")
                    self.logger.info(f"   LONG Positions: {len(position_manager.long_positions)}")
                    self.logger.info(f"   SHORT Positions: {len(position_manager.short_positions)}")
                    self.logger.info(f"   LONG PnL: ${long_pnl:.2f}")
                    self.logger.info(f"   SHORT PnL: ${short_pnl:.2f}")
                    self.logger.info(f"   TOTAL PnL: ${total_pnl:.2f}")

                    # Log final snapshot
                    if self.metrics_tracker:
                        self.metrics_tracker.log_snapshot(
                            symbol=symbol,
                            price=current_price,
                            long_positions=len(position_manager.long_positions),
                            short_positions=len(position_manager.short_positions),
                            long_qty=position_manager.get_total_quantity('Buy'),
                            short_qty=position_manager.get_total_quantity('Sell'),
                            long_pnl=long_pnl,
                            short_pnl=short_pnl
                        )
            except Exception as e:
                self.logger.error(f"[{symbol}] Error getting final state: {e}")

        # Stop Private WebSocket
        if self.private_ws:
            try:
                self.logger.info("🛑 Stopping Private WebSocket...")
                self.private_ws.stop()
                self.logger.info("✅ Private WebSocket stopped")
            except Exception as e:
                self.logger.error(f"Error stopping Private WebSocket: {e}")

        # Session reports removed - only daily reports are generated (at 00:01)
        self.logger.info("=" * 60)
        self.logger.info(f"✅ Account {self.id_str} shutdown complete")
        self.logger.info("=" * 60)
