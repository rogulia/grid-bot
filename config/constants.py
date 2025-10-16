"""Trading constants and configuration values"""


class TradingConstants:
    """Constants used throughout the trading system"""

    # === Timing and Intervals ===

    # Logging and warning intervals (seconds)
    WARNING_LOG_INTERVAL_SEC = 60  # Log warnings max once per 60 seconds
    BALANCE_CACHE_TTL_SEC = 5.0    # Cache balance/MM rate for 5 seconds
    SYNC_INTERVAL_SEC = 60         # Sync with exchange every 60 seconds

    # Delays removed - Position WebSocket provides real-time updates
    # EXCHANGE_PROCESS_DELAY_SEC removed - no longer needed with WebSocket

    # === API Limits and Pagination ===

    ORDER_HISTORY_LIMIT = 200      # Max orders to fetch from history (Bybit API max limit)
    CLOSED_PNL_LIMIT = 20          # Max closed PnL records to fetch
    TRANSACTION_LOG_LIMIT = 50     # Max transaction log entries
    MAX_PAGINATION_PAGES = 10      # Max pages to fetch when paginating (safety limit)

    # === State Restoration ===

    RESTORATION_TIMEOUT_SEC = 30   # Max time for state restoration (prevents hanging)

    # === Trading Tolerances ===

    QTY_MATCH_TOLERANCE_PERCENT = 0.1  # Quantity matching tolerance as percentage (0.1%)

    # === Fees ===

    BYBIT_TAKER_FEE_RATE = 0.00055  # 0.055% taker fee on Bybit (market orders)
    BYBIT_MAKER_FEE_RATE = 0.00020  # 0.020% maker fee on Bybit (limit orders)

    # === Limit Orders ===

    LIMIT_ORDER_PRICE_OFFSET_PERCENT = 0.03  # Price offset from market (0.03% for balance)
    LIMIT_ORDER_TIMEOUT_SEC = 10             # Timeout before retry (10 seconds)
    LIMIT_ORDER_MAX_RETRIES = 3              # Max retries before fallback to market

    # === WebSocket ===

    WEBSOCKET_LOG_EVERY_N_UPDATES = 10  # Log every Nth price update
    WEBSOCKET_RECONNECT_DELAY_SEC = 5   # Delay before reconnecting

    # === Risk Management ===

    MM_RATE_WARNING_THRESHOLD = 50.0    # Warning threshold for MM Rate (log if > 50%)

    # === Position Index (Bybit) ===

    POSITION_IDX_LONG = 1   # Position index for LONG positions
    POSITION_IDX_SHORT = 2  # Position index for SHORT positions

    # === File Naming ===

    ACCOUNT_ID_PADDING = 3  # Zero-pad account IDs to 3 digits (001, 002, ...)

    # === Date/Time Formats ===

    DEFAULT_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"  # Standard datetime format
    DATE_FORMAT = "%Y-%m-%d"                       # Date-only format

    # === Emergency Stop ===

    EMERGENCY_STOP_FILE_PREFIX = "."  # Hidden file prefix for emergency flags


class ValidationLimits:
    """Validation limits for strategy configuration"""

    # Leverage limits
    MIN_LEVERAGE = 1
    MAX_LEVERAGE = 200

    # Position size limits (USD)
    MIN_POSITION_SIZE_USD = 0.1
    MAX_POSITION_SIZE_USD = 100000.0

    # Grid step limits (percent)
    MIN_GRID_STEP_PCT = 0.01
    MAX_GRID_STEP_PCT = 100.0

    # Multiplier limits
    MIN_MULTIPLIER = 1.0  # Exclusive lower bound
    MAX_MULTIPLIER = 10.0

    # Take profit limits (percent)
    MIN_TP_PCT = 0.01
    MAX_TP_PCT = 100.0

    # Grid levels limits
    MIN_GRID_LEVELS = 1
    MAX_GRID_LEVELS = 50


class LogMessages:
    """Standard log message templates"""

    # Balance and MM Rate
    BALANCE_UPDATE = "[{symbol}] 💎 Balance: ${balance:.2f}, Account MM Rate: {mm_rate:.4f}%"
    BALANCE_ONLY = "[{symbol}] 💎 Balance: ${balance:.2f}"

    # Position operations
    POSITION_OPENED = "[{symbol}] 📈 {side} position opened: {qty} @ ${price:.4f}"
    POSITION_CLOSED = "[{symbol}] 💰 {side} position closed: PnL=${pnl:.4f}"
    POSITION_AVERAGED = "[{symbol}] 🔄 {side} averaged: level {level}, qty {qty} @ ${price:.4f}"

    # Emergency
    EMERGENCY_CLOSE = "💥 CRITICAL: {reason}! EMERGENCY CLOSE ALL POSITIONS!"
    EMERGENCY_STOP_CREATED = "🛑 Emergency stop flag created: {reason}"

    # Sync
    SYNC_START = "🔄 [{symbol}] Syncing positions with exchange..."
    SYNC_COMPLETE = "✅ [{symbol}] Sync completed"

    # Warnings
    INSUFFICIENT_BALANCE = "⚠️  [{symbol}] Insufficient balance for {side} averaging: need ${needed:.2f} MARGIN, available ${available:.2f}"
    HIGH_MM_RATE = "⚠️  [{symbol}] Account Maintenance Margin Rate: {mm_rate:.2f}% (caution!)"


# Export for easy importing
__all__ = ['TradingConstants', 'ValidationLimits', 'LogMessages']
