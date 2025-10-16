"""Isolated trading account for one user/client in multi-account bot"""

import asyncio
import logging
import time
import threading
import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from ..exchange.bybit_client import BybitClient
from ..exchange.bybit_private_ws import BybitPrivateWebSocket
from ..exchange.bybit_websocket import BybitWebSocket
from ..strategy.position_manager import PositionManager
from ..strategy.grid_strategy import GridStrategy
from ..analytics.metrics_tracker import MetricsTracker
from .state_manager import StateManager
from ..utils.timezone import now_helsinki
from ..utils.logger import HelsinkiFormatter
from ..utils.emergency_stop_manager import EmergencyStopManager


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
        risk_config: Dict,
        log_level: str = "INFO"
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
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        """
        self.account_id = account_id
        self.name = name
        self.demo = demo
        self.dry_run = dry_run
        self.log_level = log_level

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

        # Position WebSocket for real-time position updates (per symbol, initialized in initialize())
        self.position_websockets: Dict[str, BybitWebSocket] = {}

        # Strategy components (per symbol)
        self.strategies: Dict[str, GridStrategy] = {}
        self.position_managers: Dict[str, PositionManager] = {}
        self.state_managers: Dict[str, StateManager] = {}

        # Metrics tracker (per account, shared across symbols)
        self.metrics_tracker: Optional[MetricsTracker] = None

        # Configuration
        self.strategies_config = strategies_config
        self.risk_config = risk_config

        # Load risk management settings
        self.balance_buffer_percent = risk_config.get('balance_buffer_percent', 15.0)

        # Last log times for periodic logging
        self.last_log_times: Dict[str, float] = {}

        # Account-level lock for atomicity (prevents race conditions in multi-symbol execution)
        # ONE lock per account protects all critical operations
        self._account_lock = threading.Lock()

        # Balance operation lock for multi-symbol atomic operations
        # Prevents race conditions when multiple symbols check/reserve balance simultaneously
        self._balance_operation_lock = threading.Lock()

        # Early Freeze state (Phase 2: Advanced Risk Management)
        # Preventive mechanism to block averaging before panic mode
        self.averaging_frozen = False
        self.freeze_reason: Optional[str] = None

        # Panic Mode state (Phase 3: Advanced Risk Management)
        # Critical state when IM is severely constrained
        self.panic_mode = False
        self.panic_reason: Optional[str] = None
        self.panic_entered_at: Optional[float] = None

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

        # Get logging level from config (converted to logging constant)
        log_level_const = getattr(logging, self.log_level.upper())

        # Main logger: {ID}_bot_{date}.log
        self.logger = logging.getLogger(f"account.{self.id_str}")
        self.logger.setLevel(log_level_const)
        self.logger.propagate = False  # Don't propagate to root

        # File handler for main log
        bot_file_handler = logging.FileHandler(
            log_dir / f"{self.id_str}_bot_{today}.log"
        )
        bot_file_handler.setFormatter(formatter)
        self.logger.addHandler(bot_file_handler)

        # Console handler (for systemd/screen output) - with Helsinki timezone
        console_handler = logging.StreamHandler()
        console_formatter = HelsinkiFormatter(
            f'%(asctime)s [{self.id_str}] %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # Trades logger: {ID}_trades_{date}.log
        self.trades_logger = logging.getLogger(f"account.{self.id_str}.trades")
        self.trades_logger.setLevel(log_level_const)
        self.trades_logger.propagate = False

        trades_file_handler = logging.FileHandler(
            log_dir / f"{self.id_str}_trades_{today}.log"
        )
        trades_file_handler.setFormatter(formatter)
        self.trades_logger.addHandler(trades_file_handler)

        # Positions logger: {ID}_positions_{date}.log
        self.positions_logger = logging.getLogger(f"account.{self.id_str}.positions")
        self.positions_logger.setLevel(log_level_const)
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
        EmergencyStopManager.validate_and_raise(
            account_id=self.account_id,
            account_name=self.name
        )

        # Create balance manager (shared across all strategies for this account)
        from ..utils.balance_manager import BalanceManager
        self.balance_manager = BalanceManager(self.client)

        # Get account balance via balance_manager (REQUIRED - fail-fast if not available)
        self.logger.info("Fetching account balance...")
        balance_data = self.balance_manager.get_full_balance_data(force_refresh=True)
        if not balance_data:
            raise RuntimeError(f"[{self.id_str}] Failed to get wallet balance from exchange")

        # Get totalEquity for metrics tracker (fallback to totalAvailableBalance if needed)
        initial_balance = float(balance_data.get('totalEquity', balance_data.get('totalAvailableBalance', 0)))
        if initial_balance == 0:
            raise RuntimeError(f"[{self.id_str}] Invalid balance response from exchange")

        self.logger.info(f"💰 Account Balance: ${initial_balance:.2f}")

        # Initialize metrics tracker with account ID prefix
        self.metrics_tracker = MetricsTracker(
            initial_balance=initial_balance,
            file_prefix=f"{self.id_str}_"  # Files: 001_trades_history.csv, etc.
        )

        # ⭐ Start Private WebSocket BEFORE restore to catch execution events
        # This prevents race condition where restore opens positions but execution events are missed
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

            # Create grid strategy (pass account_id, logger, balance_manager, and account reference!)
            self.strategies[symbol] = GridStrategy(
                client=self.client,
                position_manager=self.position_managers[symbol],
                config=combined_config,
                dry_run=self.dry_run,
                metrics_tracker=self.metrics_tracker,
                account_id=self.account_id,          # ✅ Pass ID
                account_logger=self.logger,          # ✅ Pass logger
                balance_manager=self.balance_manager, # ✅ Pass shared balance manager
                trading_account=self                 # ✅ Pass account reference for reserve checking
            )

            # ⭐ NEW ARCHITECTURE: Restore state from exchange BEFORE starting WebSockets
            self.logger.info(f"🔄 [{symbol}] Restoring state from exchange...")
            
            # Get current market price via REST API
            try:
                if not self.dry_run:
                    current_price = self.client.get_market_price(symbol, category)
                    self.logger.info(f"💵 [{symbol}] Current market price: ${current_price:.4f}")
                else:
                    # Dry run: use placeholder price
                    current_price = 100.0
                    self.logger.info(f"[{symbol}] [DRY RUN] Using placeholder price: ${current_price:.4f}")
                
                # Restore positions, open initial positions if needed, create TP orders
                self.strategies[symbol].restore_state_from_exchange(current_price)
                self.logger.info(f"✅ [{symbol}] State restored successfully")
                
            except Exception as e:
                self.logger.error(
                    f"❌ [{symbol}] Failed to restore state from exchange: {e}",
                    exc_info=True
                )
                raise RuntimeError(
                    f"[{symbol}] Cannot start trading without restored state"
                ) from e

            # ⭐ NOW start WebSockets (after state is restored)
            # Create Position WebSocket for real-time position updates
            if not self.dry_run:
                try:
                    self.logger.info(f"🔐 [{symbol}] Starting Position WebSocket...")

                    # Create position callback that routes to strategy
                    def position_callback(position_data: dict):
                        """Route position update to strategy"""
                        try:
                            self.strategies[symbol].on_position_update(position_data)
                        except Exception as e:
                            self.logger.error(
                                f"[{symbol}] Error in position callback: {e}",
                                exc_info=True
                            )

                    # Create wallet callback that routes to strategy
                    def wallet_callback(wallet_data: dict):
                        """Route wallet update to strategy"""
                        try:
                            self.strategies[symbol].on_wallet_update(wallet_data)
                        except Exception as e:
                            self.logger.error(
                                f"[{symbol}] Error in wallet callback: {e}",
                                exc_info=True
                            )

                    # Create order callback that routes to strategy
                    def order_callback(order_data: dict):
                        """Route order update to strategy"""
                        try:
                            self.strategies[symbol].on_order_update(order_data)
                        except Exception as e:
                            self.logger.error(
                                f"[{symbol}] Error in order callback: {e}",
                                exc_info=True
                            )

                    # Create WebSocket with credentials and all callbacks
                    position_ws = BybitWebSocket(
                        symbol=symbol,
                        price_callback=lambda price: None,  # Not using price from this WS (using shared WS)
                        demo=self.demo,
                        channel_type="linear",
                        api_key=self.api_key,
                        api_secret=self.api_secret,
                        position_callback=position_callback,
                        wallet_callback=wallet_callback,
                        order_callback=order_callback,
                        websocket_logger=self.logger  # Use account logger for visibility
                    )
                    position_ws.start()

                    self.position_websockets[symbol] = position_ws
                    self.logger.info(f"✅ [{symbol}] Position WebSocket started")

                except Exception as e:
                    self.logger.error(
                        f"❌ [{symbol}] Failed to start Position WebSocket: {e}",
                        exc_info=True
                    )
                    raise RuntimeError(
                        f"[{symbol}] Cannot start without Position WebSocket"
                    ) from e
            else:
                self.logger.info(f"🔕 [{symbol}] Dry run mode - Position WebSocket disabled")

            # Initialize last log time
            self.last_log_times[symbol] = 0

            self.logger.info(f"✅ [{symbol}] Initialized successfully")

        self.logger.info("=" * 60)
        self.logger.info(f"✅ Account {self.id_str} fully initialized with {len(self.strategies)} symbol(s)")
        self.logger.info("=" * 60)

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

            # ⭐ State already restored in initialize() - no need for initial sync here!
            # WebSocket just provides price updates for normal operation

            # Check Early Freeze trigger (Phase 2: Advanced Risk Management)
            # This runs BEFORE averaging checks in on_price_update()
            should_freeze, freeze_reason = self.check_early_freeze_trigger()

            if should_freeze and not self.averaging_frozen:
                # Activate Early Freeze
                self.freeze_all_averaging(freeze_reason)

            elif not should_freeze and self.averaging_frozen and not self.panic_mode:
                # Conditions recovered, unfreeze (only if NOT in panic mode)
                self.unfreeze_all_averaging()

            # Check Panic Mode trigger (Phase 3: Advanced Risk Management)
            # Check AFTER Early Freeze but BEFORE emergency close
            if not self.panic_mode:
                # Check LOW_IM trigger - insufficient funds to balance positions
                panic_low_im, panic_reason = self.check_panic_trigger_low_im()
                if panic_low_im:
                    self.enter_panic_mode(panic_reason)

            # Execute strategy logic
            # Note: If averaging_frozen=True, reserve checking will block averaging
            # Emergency close check (MM Rate) happens inside strategy.on_price_update()
            strategy.on_price_update(price)

            # Periodic sync and logging (every 60 seconds)
            current_time = time.time()
            last_log_time = self.last_log_times.get(symbol, 0)

            if current_time - last_log_time >= 60:
                # Periodic sync to verify TP orders and catch untracked closes
                strategy.sync_with_exchange(price)

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

                # Log Initial Margin status (Phase 6: Advanced Risk Management)
                # Log once per 60s for entire account (not per symbol)
                last_im_log = self.last_log_times.get('_im_monitoring', 0)
                if current_time - last_im_log >= 60:
                    self.log_im_status()
                    self.last_log_times['_im_monitoring'] = current_time

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

    @staticmethod
    def calculate_safety_factor(atr_percent: float) -> float:
        """
        Calculate dynamic safety factor based on market volatility (ATR)

        Safety factor consists of three components:
        1. Base buffer (10%): covers fees, rounding errors
        2. Gap buffer (2-10%): covers price movements during execution (dynamic!)
        3. Tier buffer (5%): covers Portfolio Margin tier rate non-linearity

        Args:
            atr_percent: Average True Range as percentage of price (e.g., 1.5 for 1.5%)

        Returns:
            Safety factor multiplier (1.17 to 1.25)
        """
        # 1. Base buffer: комиссии + округления
        base_buffer = 0.10  # +10%

        # 2. Gap buffer: проскальзывание и гэпы (динамический на основе ATR!)
        if atr_percent < 1.0:
            gap_buffer = 0.02  # +2% (низкая волатильность)
        elif atr_percent < 2.0:
            gap_buffer = 0.05  # +5% (средняя волатильность)
        else:
            gap_buffer = 0.10  # +10% (высокая волатильность)

        # 3. Tier buffer: Portfolio Margin non-linearity
        tier_buffer = 0.05  # +5% (постоянный запас на tier rates)

        # Итоговый safety factor
        safety_factor = 1.0 + base_buffer + gap_buffer + tier_buffer

        return safety_factor

    def calculate_account_safety_reserve(self) -> float:
        """
        Calculate dynamic safety reserve for the entire account

        Safety reserve is the minimum margin needed to balance ALL symbols
        when any side gets closed. This prevents catastrophic scenarios where
        a side closes but we don't have margin to rebalance.

        Uses dynamic safety factor based on worst-case ATR across all symbols.

        Returns:
            Safety reserve in USD (sum of all symbol imbalances × safety_factor)
        """
        total_reserve = 0.0
        atr_values = []

        # Calculate reserve for each symbol and collect ATR values
        for symbol, strategy in self.strategies.items():
            try:
                # Get position manager for this symbol
                pm = strategy.pm

                # Get current price from strategy
                current_price = strategy.current_price
                if current_price <= 0:
                    # No price yet, skip this symbol
                    continue

                # Calculate coin imbalance (NOT level imbalance!)
                long_qty = pm.get_total_quantity('Buy')
                short_qty = pm.get_total_quantity('Sell')
                coin_imbalance = abs(long_qty - short_qty)

                # Calculate MARGIN needed to balance (not position value!)
                # Formula: (quantity × price) / leverage = margin required
                position_value = coin_imbalance * current_price
                imbalance_margin = position_value / strategy.leverage

                # Add to total reserve
                total_reserve += imbalance_margin

                # Collect ATR for this symbol
                atr_percent = strategy.calculate_atr_percent()
                atr_values.append(atr_percent)

            except Exception as e:
                self.logger.error(f"[{symbol}] Error calculating reserve: {e}")
                # Continue with other symbols

        # Use worst-case (maximum) ATR for safety factor
        # This ensures we're protected against the most volatile symbol
        max_atr_percent = max(atr_values) if atr_values else 1.5

        # Calculate dynamic safety factor
        safety_factor = self.calculate_safety_factor(max_atr_percent)

        # Apply safety factor
        final_reserve = total_reserve * safety_factor

        # Log detailed info (debug level to avoid spam)
        self.logger.debug(
            f"[Account {self.id_str}] Safety reserve: "
            f"base=${total_reserve:.2f}, ATR={max_atr_percent:.2f}%, "
            f"factor={safety_factor:.2f}, final=${final_reserve:.2f}"
        )

        return final_reserve

    def monitor_initial_margin(self) -> Dict[str, float]:
        """
        Monitor Initial Margin and Available Balance with detailed metrics (Phase 6: Advanced Risk Management)

        Returns comprehensive metrics for IM monitoring and logging.

        Returns:
            Dictionary with keys:
            - total_balance: totalAvailableBalance from exchange
            - total_initial_margin: total IM used across all positions
            - total_maintenance_margin: total MM across all positions
            - account_mm_rate: account-level MM Rate percentage
            - safety_reserve: dynamic safety reserve for rebalancing
            - available_for_trading: balance available after IM and reserve
            - available_percent: percentage of balance available
        """
        metrics = {}

        try:
            # Get balance manager from first strategy (shared across all symbols)
            balance_manager = None
            for strategy in self.strategies.values():
                balance_manager = strategy.balance_manager
                break

            if not balance_manager:
                self.logger.warning(f"[Account {self.id_str}] No balance manager available for IM monitoring")
                return {}

            # Get all balance fields from BalanceManager (uses WebSocket cache)
            metrics['total_balance'] = balance_manager.get_available_balance()
            metrics['total_initial_margin'] = balance_manager.get_initial_margin() or 0.0
            metrics['total_maintenance_margin'] = balance_manager.get_maintenance_margin() or 0.0
            mm_rate = balance_manager.get_mm_rate()
            metrics['account_mm_rate'] = mm_rate if mm_rate is not None else 0.0

            # Calculate safety reserve (dynamic based on ATR)
            metrics['safety_reserve'] = self.calculate_account_safety_reserve()

            # Calculate available for trading
            # Note: totalAvailableBalance already has IM subtracted by Bybit
            # So we only subtract our safety reserve
            metrics['available_for_trading'] = metrics['total_balance'] - metrics['safety_reserve']

            # Calculate percentage
            if metrics['total_balance'] > 0:
                # Calculate true available as percentage of (balance + IM)
                # This gives more intuitive percentage (how much of total equity is available)
                total_equity = metrics['total_balance'] + metrics['total_initial_margin']
                metrics['available_percent'] = (metrics['available_for_trading'] / total_equity) * 100
            else:
                metrics['available_percent'] = 0.0

            return metrics

        except Exception as e:
            self.logger.error(f"[Account {self.id_str}] Error monitoring Initial Margin: {e}")
            return {}

    def log_im_status(self) -> None:
        """
        Log Initial Margin status with appropriate warning levels (Phase 6: Advanced Risk Management)

        Log levels:
        - INFO: periodic logging (every 60s via process_price)
        - WARNING: available_percent < 30%
        - ERROR: available_percent < 15%
        - CRITICAL: available_for_trading < 0 (reserve breached!)
        """
        metrics = self.monitor_initial_margin()

        if not metrics:
            return

        # Extract values
        total_balance = metrics.get('total_balance', 0.0)
        total_im = metrics.get('total_initial_margin', 0.0)
        safety_reserve = metrics.get('safety_reserve', 0.0)
        available = metrics.get('available_for_trading', 0.0)
        available_pct = metrics.get('available_percent', 0.0)
        mm_rate = metrics.get('account_mm_rate', 0.0)

        # Build log message
        log_msg = (
            f"[Account {self.id_str}] IM Status: "
            f"balance=${total_balance:.2f}, "
            f"used_IM=${total_im:.2f}, "
            f"reserve=${safety_reserve:.2f}, "
            f"available=${available:.2f} ({available_pct:.1f}%), "
            f"MM_Rate={mm_rate:.2f}%"
        )

        # Determine log level based on available percentage and absolute value
        if available < 0:
            # CRITICAL: Reserve breached!
            self.logger.critical(f"🚨 {log_msg} - SAFETY RESERVE BREACHED!")
        elif available_pct < 15:
            # ERROR: Very low available margin
            self.logger.error(f"❌ {log_msg} - CRITICALLY LOW AVAILABLE MARGIN")
        elif available_pct < 30:
            # WARNING: Low available margin
            self.logger.warning(f"⚠️  {log_msg} - LOW AVAILABLE MARGIN")
        else:
            # INFO: Normal status (log only periodically, handled by caller)
            self.logger.info(log_msg)

    def check_reserve_before_averaging(
        self,
        symbol: str,
        side: str,
        next_averaging_margin: float
    ) -> bool:
        """
        Check if there's sufficient funds AFTER averaging to balance positions (CRITICAL!)

        CORE LOGIC: Must simulate the averaging and verify that AFTER it we still
        have enough funds to balance positions BY QUANTITY (long_qty = short_qty for each symbol).

        This prevents situations where:
        - Before averaging: can balance ✅
        - After averaging: CANNOT balance ❌ (DISASTER!)

        CRITICAL: Calculates cost PER SYMBOL (each symbol's imbalance_qty × its price / leverage),
        simulating the averaging operation on the target symbol. This is correct for multi-symbol
        accounts where different coins have different prices.

        Args:
            symbol: Symbol that wants to average (e.g., 'DOGEUSDT')
            side: Side of averaging ('Buy' or 'Sell')
            next_averaging_margin: Margin required for next averaging

        Returns:
            True if averaging can proceed safely, False if blocked
        """
        # Use account lock to ensure atomic check-then-act
        with self._account_lock:
            # Check Early Freeze first (Phase 2: Advanced Risk Management)
            if self.averaging_frozen:
                # Throttled logging to avoid spam
                self.logger.debug(
                    f"[{symbol}] Averaging BLOCKED by Early Freeze. "
                    f"Reason: {self.freeze_reason}"
                )
                return False

            try:
                # Get strategy for this symbol to calculate next qty
                strategy = self.strategies.get(symbol)
                if not strategy:
                    self.logger.error(f"[{symbol}] Reserve check FAILED: strategy not found")
                    return False
                if strategy.current_price <= 0:
                    self.logger.error(
                        f"[{symbol}] Reserve check FAILED: current_price not set "
                        f"(price={strategy.current_price}). This is a bug!"
                    )
                    return False

                # Calculate next quantity based on margin
                next_qty = (next_averaging_margin * strategy.leverage) / strategy.current_price

                # 1. Calculate cost to balance EACH symbol separately, AFTER simulating averaging
                total_cost_to_balance_after = 0.0
                buffer_multiplier = 1 + (self.balance_buffer_percent / 100.0)

                for strat in self.strategies.values():
                    try:
                        # Calculate LONG and SHORT quantities for THIS symbol
                        symbol_long_qty = sum(pos.quantity for pos in strat.pm.long_positions)
                        symbol_short_qty = sum(pos.quantity for pos in strat.pm.short_positions)

                        # SIMULATE: If this is the averaging symbol, add next_qty
                        if strat.symbol == symbol:
                            if side == 'Buy':
                                symbol_long_qty += next_qty
                            else:
                                symbol_short_qty += next_qty

                        # Calculate imbalance for THIS symbol AFTER averaging
                        symbol_imbalance_qty = abs(symbol_long_qty - symbol_short_qty)

                        if symbol_imbalance_qty < 0.001:
                            # This symbol will be balanced, skip
                            continue

                        # Cost to balance THIS symbol (using its own price and leverage)
                        if strat.current_price > 0:
                            position_value_to_balance = symbol_imbalance_qty * strat.current_price
                            symbol_cost_to_balance = (position_value_to_balance / strat.leverage) * buffer_multiplier
                            total_cost_to_balance_after += symbol_cost_to_balance

                    except Exception as e:
                        self.logger.debug(f"Error calculating imbalance for {strat.symbol}: {e}")
                        continue

                # 2. Available funds AFTER averaging
                # Note: get_available_balance() already has current margin deducted by Bybit
                # So we only subtract the NEW averaging margin (not total_margin_used again!)
                available_after = self.balance_manager.get_available_balance() - next_averaging_margin

                # 3. THE CRITICAL CHECK: Can we balance AFTER averaging?
                if available_after >= total_cost_to_balance_after:
                    self.logger.info(
                        f"[{symbol}] ✅ Reserve check PASSED: AFTER {side} averaging ${next_averaging_margin:.2f}, "
                        f"available_after=${available_after:.2f} >= cost=${total_cost_to_balance_after:.2f}"
                    )
                    return True
                else:
                    self.logger.warning(
                        f"[{symbol}] ❌ Reserve check FAILED: AFTER {side} averaging ${next_averaging_margin:.2f}, "
                        f"available_after=${available_after:.2f} < cost=${total_cost_to_balance_after:.2f}. "
                        f"BLOCKING averaging - would make positions unbalanceable!"
                    )
                    return False

            except Exception as e:
                self.logger.error(
                    f"[{symbol}] Error in reserve check: {e}. "
                    f"BLOCKING averaging for safety!"
                )
                return False  # Fail-safe: block on error

    def check_early_freeze_trigger(self) -> tuple[bool, str]:
        """
        Check if Early Freeze should be activated (Phase 2: Advanced Risk Management)

        SAME LOGIC as panic mode: Cannot balance positions.
        Early Freeze = preventive action before situation worsens.

        CORE RULE: Must ALWAYS have enough funds to balance positions (hedge protection).

        Returns:
            (should_freeze: bool, reason: str)
        """
        # Early Freeze uses SAME logic as Panic Mode
        # The difference is in actions taken, not in the trigger
        return self.check_panic_trigger_low_im()

    def freeze_all_averaging(self, reason: str):
        """
        Freeze all averaging operations across ALL symbols (Phase 2: Early Freeze)

        Args:
            reason: Reason for freezing
        """
        if not self.averaging_frozen:
            self.averaging_frozen = True
            self.freeze_reason = reason
            self.logger.warning(
                f"⚠️ EARLY FREEZE ACTIVATED: Blocking all averaging operations. "
                f"Reason: {reason}. "
                f"TP orders continue to work normally."
            )

    def unfreeze_all_averaging(self):
        """
        Unfreeze averaging operations when conditions recover (Phase 2: Early Freeze)
        """
        if self.averaging_frozen:
            self.averaging_frozen = False
            old_reason = self.freeze_reason
            self.freeze_reason = None
            self.logger.info(
                f"✅ EARLY FREEZE DEACTIVATED: Available IM recovered. "
                f"Resuming averaging operations. "
                f"Previous reason: {old_reason}"
            )

    def check_panic_trigger_low_im(self) -> tuple[bool, str]:
        """
        Check Panic Mode trigger: Cannot balance positions (Phase 3: Advanced Risk Management)

        This is the PRIMARY panic trigger. Activates when available funds are insufficient
        to balance positions by QUANTITY (make long_qty = short_qty for each symbol) plus safety buffer.

        CORE LOGIC: In hedge mode, balanced positions BY QUANTITY = perfect hedge.
        When long_qty = short_qty, unrealized PnL = 0 regardless of price movement.
        Must ALWAYS have enough funds to balance the quantity imbalance.

        CRITICAL: Calculates cost PER SYMBOL (each symbol's imbalance_qty × its price / leverage),
        then sums total cost. This is correct for multi-symbol accounts where coins have different prices.

        Returns:
            (triggered: bool, reason: str)
        """
        try:
            # Get available balance (REAL funds that can be used)
            total_balance = self.balance_manager.get_available_balance()
            
            if total_balance <= 0:
                return (False, "")

            # Calculate cost to balance EACH symbol separately (per-symbol qty × price)
            total_cost_to_balance = 0.0
            total_margin_used = 0.0
            imbalance_details = []  # For logging

            for strategy in self.strategies.values():
                try:
                    # Calculate LONG and SHORT quantities for THIS symbol only
                    symbol_long_qty = 0.0
                    symbol_short_qty = 0.0

                    for pos in strategy.pm.long_positions:
                        symbol_long_qty += pos.quantity
                        position_value = pos.quantity * pos.entry_price
                        total_margin_used += position_value / strategy.leverage

                    for pos in strategy.pm.short_positions:
                        symbol_short_qty += pos.quantity
                        position_value = pos.quantity * pos.entry_price
                        total_margin_used += position_value / strategy.leverage

                    # Calculate imbalance for THIS symbol only
                    symbol_imbalance_qty = abs(symbol_long_qty - symbol_short_qty)

                    if symbol_imbalance_qty < 0.001:
                        # This symbol is balanced, skip
                        continue

                    # Cost to balance THIS symbol (using its own price and leverage)
                    if strategy.current_price > 0:
                        position_value_to_balance = symbol_imbalance_qty * strategy.current_price
                        buffer_multiplier = 1 + (self.balance_buffer_percent / 100.0)
                        symbol_cost_to_balance = (position_value_to_balance / strategy.leverage) * buffer_multiplier

                        total_cost_to_balance += symbol_cost_to_balance

                        # Store for logging
                        imbalance_details.append({
                            'symbol': strategy.symbol,
                            'imbalance_qty': symbol_imbalance_qty,
                            'long_qty': symbol_long_qty,
                            'short_qty': symbol_short_qty,
                            'cost': symbol_cost_to_balance
                        })

                except Exception as e:
                    self.logger.debug(f"Error calculating imbalance for {strategy.symbol}: {e}")
                    continue

            # If all symbols balanced, no panic needed
            if total_cost_to_balance < 0.01:
                return (False, "")  # Perfect balance - no risk!

            # Available funds (what we have free to use)
            # NOTE: total_balance from get_available_balance() is ALREADY net of margin!
            # Bybit API returns totalAvailableBalance which already deducts used margin.
            # DO NOT subtract total_margin_used again (double subtraction bug!)
            available = total_balance

            # PANIC trigger: cannot balance positions
            if available < total_cost_to_balance:
                # Build detailed reason with per-symbol breakdown
                details_str = ", ".join([
                    f"{d['symbol']}:imb={d['imbalance_qty']:.1f}(L:{d['long_qty']:.1f}/S:{d['short_qty']:.1f})=${d['cost']:.2f}"
                    for d in imbalance_details
                ])
                reason = (
                    f"LOW_IM: available=${available:.2f} < cost_to_balance=${total_cost_to_balance:.2f} "
                    f"[{details_str}]"
                )
                return (True, reason)

            return (False, "")

        except Exception as e:
            self.logger.error(f"Error checking LOW_IM panic trigger: {e}")
            return (False, "")

    def check_and_reserve_balance(self, symbol: str, margin_needed: float) -> bool:
        """
        Thread-safe balance check for multi-symbol operations

        Prevents race condition when multiple symbols check balance simultaneously.
        Uses lock to make check + reserve atomic.

        Args:
            symbol: Symbol requesting balance check
            margin_needed: Margin required (will be multiplied by buffer)

        Returns:
            True if balance is sufficient (with buffer), False otherwise
        """
        with self._balance_operation_lock:
            # Get strategy to access balance_manager
            strategy = self.strategies.get(symbol)
            if not strategy:
                self.logger.warning(f"[{symbol}] Strategy not found for balance check")
                return False

            # Get available balance
            available = strategy.balance_manager.get_available_balance()

            # Apply buffer
            buffer_multiplier = 1 + (self.balance_buffer_percent / 100.0)
            required = margin_needed * buffer_multiplier

            if available >= required:
                return True
            else:
                return False

    def balance_all_positions_adaptive(self) -> bool:
        """
        Balance all positions adaptively across all symbols (Phase 7: Advanced Risk Management)

        In panic mode, attempt to balance LONG and SHORT positions to reduce risk.
        Uses ALL available balance (ignores safety reserve in panic state).

        Strategy:
        - Full balancing: if available >= total_needed, balance all symbols 100%
        - Partial balancing: if 0 < available < total_needed, proportional distribution
        - Critical state: if available ≈ 0, log and skip (insufficient funds)

        Returns:
            True if balancing was attempted (full or partial), False if skipped
        """
        try:
            # Collect imbalance information from ALL symbols
            imbalances = []

            for symbol, strategy in self.strategies.items():
                pm = strategy.pm
                current_price = strategy.current_price

                if current_price <= 0:
                    continue

                long_qty = pm.get_total_quantity('Buy')
                short_qty = pm.get_total_quantity('Sell')
                imbalance_qty = abs(long_qty - short_qty)

                if imbalance_qty < 0.001:
                    # Already balanced
                    continue

                # Determine lagging side
                lagging_side = 'Buy' if long_qty < short_qty else 'Sell'

                # Calculate margin needed to balance
                # We need to buy enough on lagging side to match the other side
                qty_to_buy = imbalance_qty
                position_value_needed = qty_to_buy * current_price
                margin_needed = position_value_needed / strategy.leverage

                imbalances.append({
                    'symbol': symbol,
                    'lagging_side': lagging_side,
                    'qty_to_buy': qty_to_buy,
                    'margin_needed': margin_needed,
                    'current_price': current_price,
                    'strategy': strategy
                })

            if not imbalances:
                self.logger.info(
                    f"[Account {self.id_str}] All symbols already balanced, no action needed"
                )
                return False

            # Calculate total margin needed
            total_margin_needed = sum(item['margin_needed'] for item in imbalances)

            # Get available balance (in PANIC mode, we use ALL available, ignore safety reserve!)
            balance_manager = None
            for strategy in self.strategies.values():
                balance_manager = strategy.balance_manager
                break

            if not balance_manager:
                self.logger.error(f"[Account {self.id_str}] No balance manager for position balancing")
                return False

            available = balance_manager.get_available_balance()

            self.logger.info(
                f"[Account {self.id_str}] Position balancing: "
                f"need ${total_margin_needed:.2f}, available ${available:.2f}"
            )

            # Determine balancing strategy
            if available < 1.0:
                # CRITICAL: Insufficient funds
                self.logger.critical(
                    f"🚨 [Account {self.id_str}] CRITICAL STATE: "
                    f"Cannot balance positions - available ${available:.2f} < $1.00"
                )
                return False

            elif available >= total_margin_needed:
                # FULL BALANCING: Enough funds to balance everything
                scale_factor = 1.0
                self.logger.info(
                    f"💯 [Account {self.id_str}] FULL BALANCING: "
                    f"available ${available:.2f} >= needed ${total_margin_needed:.2f}"
                )

            else:
                # PARTIAL BALANCING: Proportional distribution
                scale_factor = available / total_margin_needed
                self.logger.warning(
                    f"⚠️  [Account {self.id_str}] PARTIAL BALANCING: "
                    f"available ${available:.2f} < needed ${total_margin_needed:.2f}, "
                    f"scale_factor={scale_factor:.2%}"
                )

            # Execute balancing orders
            for item in imbalances:
                symbol = item['symbol']
                lagging_side = item['lagging_side']
                qty_to_buy = item['qty_to_buy'] * scale_factor  # Scale down if partial
                margin_used = item['margin_needed'] * scale_factor
                strategy = item['strategy']

                self.logger.info(
                    f"🔧 [{symbol}] PANIC BALANCE: {lagging_side} {qty_to_buy:.6f} @ ${item['current_price']:.4f} "
                    f"(margin ${margin_used:.2f})"
                )

                if not self.dry_run:
                    try:
                        # Place market order on lagging side
                        response = strategy.client.place_order(
                            symbol=symbol,
                            side=lagging_side,
                            qty=qty_to_buy,
                            order_type="Market",
                            category=strategy.category
                        )

                        # Update position manager
                        pm = strategy.pm
                        pm.add_position(
                            side=lagging_side,
                            entry_price=item['current_price'],
                            quantity=qty_to_buy,
                            grid_level=pm.get_position_count(lagging_side)  # Current level
                        )

                        # Update TP order
                        strategy._update_tp_order(lagging_side)

                        # Log to metrics
                        if strategy.metrics_tracker:
                            strategy.metrics_tracker.log_trade(
                                symbol=symbol,
                                side=lagging_side,
                                action="BALANCE",
                                price=item['current_price'],
                                quantity=qty_to_buy,
                                reason=f"Panic balance (scale={scale_factor:.2%})",
                                pnl=None
                            )

                        self.logger.info(f"✅ [{symbol}] Balance order executed successfully")

                    except Exception as e:
                        self.logger.error(f"❌ [{symbol}] Failed to execute balance order: {e}")
                        # Continue with other symbols

            self.logger.info(
                f"🎯 [Account {self.id_str}] Position balancing completed "
                f"({len(imbalances)} symbols, scale={scale_factor:.2%})"
            )
            return True

        except Exception as e:
            self.logger.error(
                f"[Account {self.id_str}] Error in balance_all_positions_adaptive: {e}",
                exc_info=True
            )
            return False

    def enter_panic_mode(self, reason: str):
        """
        Enter Panic Mode (Phase 3: Advanced Risk Management)

        Critical state: Cannot balance positions with available funds.

        Actions:
        1. Freeze all averaging operations
        2. Attempt to balance positions (use available funds)

        NOTE: TP orders are NOT cancelled anymore. With balance-based logic,
        if we can't balance now, deleting TPs won't help. We need positions
        to close naturally via TP to free up margin.

        Args:
            reason: Reason for entering panic mode
        """
        if not self.panic_mode:
            self.panic_mode = True
            self.panic_reason = reason
            self.panic_entered_at = time.time()

            self.logger.error(
                f"🔴 PANIC MODE ACTIVATED! "
                f"Reason: {reason}. "
                f"Freezing averaging, attempting position balancing. "
                f"TP orders remain active for natural exits."
            )

            # 1. Freeze all averaging (if not already frozen)
            if not self.averaging_frozen:
                self.freeze_all_averaging(f"PANIC: {reason}")

            # 2. Attempt to balance positions (Phase 7: Advanced Risk Management)
            balancing_attempted = self.balance_all_positions_adaptive()
            if balancing_attempted:
                self.logger.info(
                    f"[Account {self.id_str}] Position balancing executed during panic mode entry"
                )
            else:
                self.logger.warning(
                    f"[Account {self.id_str}] Position balancing skipped (already balanced or insufficient funds)"
                )

    def exit_panic_mode(self, reason: str):
        """
        Exit Panic Mode (Phase 3: Advanced Risk Management)

        Conditions recovered, resume normal operations.

        NOTE: TP orders are no longer removed during panic, so no need to restore them.

        Args:
            reason: Reason for exiting panic mode
        """
        if self.panic_mode:
            self.panic_mode = False
            old_reason = self.panic_reason
            self.panic_reason = None
            duration = time.time() - self.panic_entered_at if self.panic_entered_at else 0
            self.panic_entered_at = None

            self.logger.info(
                f"🟢 PANIC MODE DEACTIVATED! "
                f"Reason: {reason}. "
                f"Duration: {duration:.1f}s. "
                f"Previous reason: {old_reason}. "
                f"Resuming normal operations."
            )

            # Unfreeze averaging only if Early Freeze is also cleared
            # (Early Freeze check in process_price will handle automatic unfreeze)
            self.logger.info("Averaging will resume when Early Freeze clears")

    def cancel_tp_intelligently(self):
        """
        Cancel TP orders intelligently during Panic Mode (Phase 4: Advanced Risk Management)

        Strategy: Remove TP from TREND side (not losing side!), keep TP on COUNTER-TREND.

        Why: If market reverses, counter-trend side will hit TP = natural exit from panic!
        """
        for symbol, strategy in self.strategies.items():
            try:
                # Determine trend direction based on grid levels
                trend_side, counter_side, trend_direction = strategy.determine_trend_side()

                self.logger.info(
                    f"[{symbol}] Detected trend: {trend_direction} "
                    f"(trend_side={trend_side} level={strategy.pm.get_position_count(trend_side)}, "
                    f"counter={counter_side} level={strategy.pm.get_position_count(counter_side)})"
                )

                # Get TP order IDs for both sides
                trend_tp_id = strategy.pm.get_tp_order_id(trend_side)
                counter_tp_id = strategy.pm.get_tp_order_id(counter_side)

                # Cancel TP on TREND side (let it continue growing if trend continues)
                if trend_tp_id and not strategy.dry_run:
                    try:
                        strategy.client.cancel_order(
                            symbol=symbol,
                            order_id=trend_tp_id,
                            category=strategy.category
                        )
                        # Clear from position manager tracking
                        strategy.pm.set_tp_order_id(trend_side, None)
                        self.logger.info(
                            f"✅ [{symbol}] Removed TP on TREND side ({trend_side}): "
                            f"allow further growth"
                        )
                    except Exception as e:
                        self.logger.warning(
                            f"[{symbol}] Failed to cancel TP on {trend_side}: {e}"
                        )

                # KEEP TP on COUNTER-TREND side (natural exit if reversal!)
                if counter_tp_id:
                    self.logger.info(
                        f"✅ [{symbol}] KEEPING TP on COUNTER-TREND side ({counter_side}): "
                        f"waiting for reversal (natural exit)"
                    )

                # Log strategy
                self.logger.info(
                    f"[{symbol}] TP Strategy: "
                    f"If trend reverses → {counter_side} hits TP → natural exit ✅ | "
                    f"If trend continues → wait for stabilization"
                )

            except Exception as e:
                self.logger.error(f"[{symbol}] Error in cancel_tp_intelligently: {e}")
                continue

    def _restore_tp_orders_after_panic(self):
        """
        Restore TP orders for all positions after exiting Panic Mode (Phase 4: Advanced Risk Management)

        This method is called when panic mode is deactivated. It ensures that all open
        positions have their TP orders restored, particularly the TREND side TP that
        was cancelled during panic entry.

        Strategy:
        - Check all symbols and all sides
        - If positions exist but no TP order → create TP order
        - Skip if no positions or TP already exists
        """
        self.logger.info(f"[Account {self.id_str}] Restoring TP orders after panic mode exit...")

        restored_count = 0
        for symbol, strategy in self.strategies.items():
            try:
                for side in ['Buy', 'Sell']:
                    # Check if this side has positions
                    positions = strategy.pm.long_positions if side == 'Buy' else strategy.pm.short_positions

                    if not positions:
                        # No positions on this side, skip
                        continue

                    # Check if TP order already exists
                    existing_tp_id = strategy.pm.get_tp_order_id(side)

                    if existing_tp_id:
                        # TP order already exists (e.g., counter-trend side that wasn't cancelled)
                        self.logger.debug(
                            f"[{symbol}] {side} already has TP order (ID: {existing_tp_id}), skipping"
                        )
                        continue

                    # Restore TP order
                    if not self.dry_run:
                        try:
                            strategy._update_tp_order(side)
                            restored_count += 1
                            self.logger.info(
                                f"✅ [{symbol}] Restored TP order for {side} side "
                                f"({len(positions)} positions)"
                            )
                        except Exception as e:
                            self.logger.error(
                                f"❌ [{symbol}] Failed to restore TP order for {side}: {e}"
                            )
                    else:
                        # Dry run mode
                        self.logger.info(
                            f"✅ [{symbol}] [DRY RUN] Would restore TP order for {side} side"
                        )
                        restored_count += 1

            except Exception as e:
                self.logger.error(f"[{symbol}] Error restoring TP orders: {e}")
                continue

        if restored_count > 0:
            self.logger.info(
                f"🎯 [Account {self.id_str}] TP restoration completed: "
                f"{restored_count} TP orders restored"
            )
        else:
            self.logger.info(
                f"[Account {self.id_str}] No TP orders needed restoration "
                f"(all positions already have TP orders)"
            )

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
        """
        Graceful shutdown of account

        NOTE: No final state logging here - snapshots are logged every 60 seconds automatically.
        Daily reports are generated separately via generate_daily_report().
        Shutdown only stops WebSocket connections.
        """
        self.logger.info("=" * 60)
        self.logger.info(f"🛑 Shutting down account {self.id_str}: {self.name}")
        self.logger.info("=" * 60)

        # Stop Private WebSocket
        if self.private_ws:
            try:
                self.logger.info("🛑 Stopping Private WebSocket...")
                self.private_ws.stop()
                self.logger.info("✅ Private WebSocket stopped")
            except Exception as e:
                self.logger.error(f"Error stopping Private WebSocket: {e}")

        # Stop all Position WebSockets
        for symbol, position_ws in self.position_websockets.items():
            try:
                self.logger.info(f"🛑 Stopping Position WebSocket for {symbol}...")
                position_ws.stop()
                self.logger.info(f"✅ Position WebSocket for {symbol} stopped")
            except Exception as e:
                self.logger.error(f"Error stopping Position WebSocket for {symbol}: {e}")

        # Session reports removed - only daily reports are generated (at 00:01)
        self.logger.info("=" * 60)
        self.logger.info(f"✅ Account {self.id_str} shutdown complete")
        self.logger.info("=" * 60)
