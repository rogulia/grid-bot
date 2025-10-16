"""Grid trading strategy with dual-sided hedging"""

import logging
import time
import threading
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from .calculations import CalculationsMixin
from .risk_management import RiskManagementMixin
from .order_management import OrderManagementMixin
from .restoration import RestorationMixin
from .websocket_handlers import WebSocketHandlersMixin

from ..position_manager import PositionManager
from ...exchange.bybit_client import BybitClient
from ...utils.logger import log_trade
from ...utils.balance_manager import BalanceManager
from ...utils.timestamp_converter import TimestampConverter
from ...utils.emergency_stop_manager import EmergencyStopManager
from ...utils.limit_order_manager import LimitOrderManager
from ...utils.timezone import now_helsinki
from config.constants import TradingConstants, LogMessages, ValidationLimits

if TYPE_CHECKING:
    from ...analytics.metrics_tracker import MetricsTracker
    from ...core.trading_account import TradingAccount


class GridStrategy(
    CalculationsMixin,
    RiskManagementMixin,
    OrderManagementMixin,
    RestorationMixin,
    WebSocketHandlersMixin
):
    """Grid strategy for dual-sided LONG/SHORT trading"""

    def __init__(
        self,
        client: BybitClient,
        position_manager: PositionManager,
        config: dict,
        dry_run: bool = True,
        metrics_tracker: Optional['MetricsTracker'] = None,
        account_id: int = 0,
        account_logger: Optional[logging.Logger] = None,
        balance_manager: Optional['BalanceManager'] = None,
        trading_account: Optional['TradingAccount'] = None
    ):
        """
        Initialize grid strategy

        Args:
            client: Bybit API client
            position_manager: Position manager instance
            config: Strategy configuration
            dry_run: Dry run mode (no real orders)
            metrics_tracker: Optional metrics tracker for analytics
            account_id: Account ID (for multi-account support)
            account_logger: Logger from TradingAccount (logs to per-account files)
            balance_manager: Optional shared balance manager (for multi-strategy accounts)
            trading_account: Optional reference to parent TradingAccount (for reserve checking)
        """
        # Account identification
        self.account_id = account_id
        self.id_str = f"{account_id:03d}"  # Zero-padded ID for files

        # Use account's logger (writes to per-account log files)
        # If not provided, fall back to default logger
        self.logger = account_logger or logging.getLogger("sol-trader.grid_strategy")

        self.client = client
        self.pm = position_manager
        self.dry_run = dry_run
        self.metrics_tracker = metrics_tracker

        # Balance manager (use shared if provided, otherwise create new)
        if balance_manager:
            self.balance_manager = balance_manager
        else:
            # Create new balance manager (for tests and standalone use)
            self.balance_manager = BalanceManager(
                client,
                cache_ttl_seconds=TradingConstants.BALANCE_CACHE_TTL_SEC
            )

        # Trading account reference (for reserve checking)
        self.trading_account = trading_account

        # Load config - symbol is REQUIRED (no default!)
        if 'symbol' not in config:
            raise ValueError("Trading symbol must be specified in config - cannot use hardcoded default")
        self.symbol = config['symbol']

        # Initialize Limit Order Manager for handling limit orders with retries
        self.limit_order_manager = LimitOrderManager(
            client=client,
            symbol=self.symbol,
            category=config.get('category', 'linear'),
            logger=self.logger,
            dry_run=dry_run
        )

        self.category = config.get('category', 'linear')
        self.leverage = config.get('leverage', 100)
        self.initial_size_usd = config.get('initial_position_size_usd', 1.0)  # in USD
        self.grid_step_pct = config.get('grid_step_percent', 1.0)
        self.multiplier = config.get('averaging_multiplier', 2.0)
        self.tp_pct = config.get('take_profit_percent', 1.0)
        self.max_grid_levels = config.get('max_grid_levels_per_side', 10)

        # Risk management
        # MM rate threshold for emergency close (configurable per account)
        self.mm_rate_threshold = config.get('mm_rate_threshold', 90.0)
        # Balance buffer for reserve checks (configurable per account)
        self.balance_buffer_percent = config.get('balance_buffer_percent', 15.0)

        # Validate configuration
        self._validate_config()

        # Get instrument info from Bybit API
        self._load_instrument_info()

        # Throttling for log messages (to avoid spam)
        # Track last time each warning type was logged
        self._last_warning_time = {
            'mm_rate': 0,
            'insufficient_balance': 0
        }
        self._warning_interval = TradingConstants.WARNING_LOG_INTERVAL_SEC

        # Emergency stop flag - prevents any operations after critical failure
        self.emergency_stopped = False

        # Thread safety locks (WebSocket callbacks run in separate threads)
        self._tp_orders_lock = threading.Lock()
        self._pnl_lock = threading.Lock()

        # Syncing flag - prevents WebSocket position updates during initial sync
        self._is_syncing = False
        self._sync_lock = threading.Lock()

        # Retry mechanism for restore - WebSocket events trigger re-sync instead of emergency stop
        self._needs_resync = False  # Flag: resync needed due to WebSocket updates during restore
        self._resync_lock = threading.Lock()  # Protect resync flag
        self._resync_triggers = []  # Track WebSocket events that triggered resync (for diagnostics)

        # Debounce for missed close reopen - prevents duplicate reopen
        self._just_reopened_until_ts = {'Buy': 0.0, 'Sell': 0.0}

        # Track cumulative realized PnL for each side (to calculate delta on position close)
        self._last_cum_realised_pnl = {'Buy': 0.0, 'Sell': 0.0}

        # Track TP order IDs for each side (updated via Order WebSocket)
        self._tp_orders = {'Buy': None, 'Sell': None}

        # Track current price from WebSocket (eliminates need for REST get_ticker calls)
        self.current_price: float = 0.0

        # ATR calculation for dynamic safety factor (Phase 1: Advanced Risk Management)
        # Store last N prices for ATR calculation
        self._price_history = []
        self._atr_period = 20  # Last 20 price updates
        self._cached_atr_percent = None
        self._atr_last_update = 0

        # Failed reopening tracking (for recovery in sync_with_exchange)
        self._failed_reopen_sides = set()  # Track sides that failed to reopen after TP

        # First sync flag - used to cancel all orders on bot restart
        self._first_sync_done = False

        # Pending entry orders tracking (for position symmetry)
        # Maps grid_level → order_id for each side
        self._pending_entry_orders = {
            'Buy': {},   # {grid_level: order_id}
            'Sell': {}
        }
        self._pending_entry_lock = threading.Lock()

        # Base price for pending calculation (updated when pending are placed)
        # Used to detect when price has moved enough to recalculate pending
        self._base_price_for_pending = {
            'Buy': None,
            'Sell': None
        }

        # Reference qty per grid level (for perfect qty symmetry)
        # When first side opens level N, we save qty as reference
        # Second side (or pending) ALWAYS uses the same qty
        # This ensures perfect hedging: same qty on both sides regardless of price difference
        self._reference_qty_per_level = {}  # {grid_level: qty}
        self._reference_qty_lock = threading.Lock()

        self.logger.info(
            f"[{self.symbol}] Grid strategy initialized - Symbol: {self.symbol}, "
            f"Leverage: {self.leverage}x, Grid: {self.grid_step_pct}%, "
            f"Multiplier: {self.multiplier}x, Dry Run: {self.dry_run}"
        )
        self.logger.info(
            f"[{self.symbol}] Instrument limits - Min: {self.min_qty}, Step: {self.qty_step}, Max: {self.max_qty}"
        )

    def _validate_config(self):
        """Validate all configuration parameters"""
        errors = []

        # Validate leverage
        if not (ValidationLimits.MIN_LEVERAGE <= self.leverage <= ValidationLimits.MAX_LEVERAGE):
            errors.append(
                f"Leverage {self.leverage} out of range "
                f"[{ValidationLimits.MIN_LEVERAGE}, {ValidationLimits.MAX_LEVERAGE}]"
            )

        # Validate position size
        if not (ValidationLimits.MIN_POSITION_SIZE_USD <= self.initial_size_usd <= ValidationLimits.MAX_POSITION_SIZE_USD):
            errors.append(
                f"Position size ${self.initial_size_usd} out of range "
                f"[${ValidationLimits.MIN_POSITION_SIZE_USD}, ${ValidationLimits.MAX_POSITION_SIZE_USD}]"
            )

        # Validate grid step
        if not (ValidationLimits.MIN_GRID_STEP_PCT <= self.grid_step_pct <= ValidationLimits.MAX_GRID_STEP_PCT):
            errors.append(
                f"Grid step {self.grid_step_pct}% out of range "
                f"[{ValidationLimits.MIN_GRID_STEP_PCT}%, {ValidationLimits.MAX_GRID_STEP_PCT}%]"
            )

        # Validate multiplier
        if not (ValidationLimits.MIN_MULTIPLIER < self.multiplier <= ValidationLimits.MAX_MULTIPLIER):
            errors.append(
                f"Multiplier {self.multiplier} out of range "
                f"({ValidationLimits.MIN_MULTIPLIER}, {ValidationLimits.MAX_MULTIPLIER}]"
            )

        # Validate take profit
        if not (ValidationLimits.MIN_TP_PCT <= self.tp_pct <= ValidationLimits.MAX_TP_PCT):
            errors.append(
                f"Take profit {self.tp_pct}% out of range "
                f"[{ValidationLimits.MIN_TP_PCT}%, {ValidationLimits.MAX_TP_PCT}%]"
            )

        # Validate max grid levels
        if not (ValidationLimits.MIN_GRID_LEVELS <= self.max_grid_levels <= ValidationLimits.MAX_GRID_LEVELS):
            errors.append(
                f"Max grid levels {self.max_grid_levels} out of range "
                f"[{ValidationLimits.MIN_GRID_LEVELS}, {ValidationLimits.MAX_GRID_LEVELS}]"
            )

        # Validate MM rate threshold
        if not (0 <= self.mm_rate_threshold <= 100):
            errors.append(f"MM rate threshold {self.mm_rate_threshold}% must be between 0 and 100")

        # If there are errors, raise exception with all errors
        if errors:
            error_msg = "Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            self.logger.error(f"[{self.symbol}] {error_msg}")
            raise ValueError(error_msg)

        self.logger.info(f"[{self.symbol}] Configuration validation passed")

    def _load_instrument_info(self):
        """Load instrument trading parameters from Bybit API"""
        try:
            response = self.client.session.get_instruments_info(
                category=self.category,
                symbol=self.symbol
            )

            if response.get('retCode') != 0:
                raise RuntimeError(
                    f"[{self.symbol}] Bybit API returned error: {response.get('retMsg', 'Unknown error')}"
                )

            result = response.get('result', {})
            if not result.get('list'):
                raise RuntimeError(
                    f"[{self.symbol}] Bybit API returned empty instrument list"
                )

            instrument = result['list'][0]
            lot_filter = instrument.get('lotSizeFilter')
            if not lot_filter:
                raise RuntimeError(
                    f"[{self.symbol}] Instrument does not have lotSizeFilter"
                )

            self.min_qty = float(lot_filter['minOrderQty'])
            self.qty_step = float(lot_filter['qtyStep'])
            self.max_qty = float(lot_filter['maxOrderQty'])

            self.logger.info(
                f"[{self.symbol}] Loaded instrument info for {self.symbol}: "
                f"min={self.min_qty}, step={self.qty_step}, max={self.max_qty}"
            )

        except Exception as e:
            self.logger.error(f"[{self.symbol}] Failed to load instrument info: {e}")
            raise RuntimeError(
                f"[{self.symbol}] Cannot start bot without instrument info"
            ) from e
