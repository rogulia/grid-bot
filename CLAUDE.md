# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SOL-Trader is an automated Bybit futures trading bot implementing a dual-sided grid strategy (simultaneous LONG + SHORT positions) for SOLUSDT perpetual futures. The bot uses position averaging with a multiplier on each grid level and takes profit when prices reverse by a configured percentage.

**Language:** Python 3.9+
**Exchange:** Bybit (Demo and Production)
**Package Manager:** UV (Universal Virtualenv)

## Core Architecture

### Three-Layer Design

1. **Exchange Layer** (`src/exchange/`)
   - `bybit_client.py`: HTTP API wrapper using pybit for order execution, position management, leverage setting, and balance queries
   - `bybit_websocket.py`: WebSocket client for real-time price updates with auto-reconnect

2. **Strategy Layer** (`src/strategy/`)
   - `position_manager.py`: Tracks separate LONG and SHORT position lists, calculates weighted average entry prices, unrealized PnL, and liquidation distances
   - `grid_strategy.py`: Core trading logic - determines when to add positions (averaging), when to take profit, and enforces risk limits

3. **Analytics Layer** (`src/analytics/`)
   - `metrics_tracker.py`: Real-time performance tracking, CSV logging (trades and snapshots), and summary report generation

4. **State Management** (`src/core/`)
   - `state_manager.py`: Persists bot state to JSON (`data/bot_state.json`) including all positions and TP order IDs for state recovery after restarts

### Strategy Logic Flow

The bot operates via a price update callback system:

1. WebSocket receives price update
2. `main.py::on_price_update()` calls `grid_strategy.on_price_update()`
3. Strategy checks in order:
   - Risk limits (liquidation distance, max exposure)
   - Grid entry triggers (price moved grid_step_percent against position)
   - Take profit triggers (price moved tp_percent in favor of position)
4. Position manager tracks all positions with grid levels and calculates PnL
5. Every 60 seconds: `sync_with_exchange()` is called to check if TP orders executed and reopen positions if needed
6. Metrics tracker logs snapshots every 60 seconds

**Key Insight:** Each side (LONG/SHORT) is managed independently. Positions are added when price moves against the side (averaging down/up), and closed when price reverses favorably.

### Critical Architecture Decisions

**1. Fail-Fast Principle (NO FALLBACKS):**
The bot NEVER uses fallback or default values for critical data. If data cannot be fetched from exchange, bot stops with error.
- No fake liquidation calculations - always uses real `liqPrice` from Bybit
- No default balance values - fetches real balance or raises RuntimeError
- No placeholder instrument info - fetches from exchange or raises error
- No estimated PnL after close - fetches real closed PnL from exchange

**Why:** Fallbacks mask problems and lead to incorrect trading decisions. Better to stop than trade with wrong data.

**2. Real Liquidation Price from Exchange:**
- Uses `liqPrice` field from Bybit `get_active_position()` response
- `liqPrice` correctly accounts for entire account balance in Cross Margin mode
- Emergency close triggers when distance to `liqPrice` < `liquidation_buffer` %
- NEVER calculates liquidation locally - exchange knows best

**3. Classic Martingale Formula:**
Each new position size = last position size × multiplier
- Sequence: 1 → 2 → 4 → 8 → 16 → 32...
- NOT: 1 → 1 → 2 → 4 → 8... (old incorrect formula)
- Formula: `new_margin = last_position_margin * multiplier`

**4. Symbol Prefixes in All Logs:**
Every log message includes `[{self.symbol}]` prefix for multi-symbol trading clarity.

### Position Lifecycle

- **Entry**: Initial positions opened in `main.py::initialize()` at startup
- **Averaging**: New positions added at grid levels when price moves grid_step_percent against side
- **Sizing**: Each new position is sized using `current_total_qty * (multiplier - 1)`
- **Exit**: All positions on a side close together when weighted average entry price shows take_profit_percent gain
- **Emergency Close**: Positions close if Account MM Rate >= 90%
- **Balance Check**: Before each averaging, bot checks `totalAvailableBalance` from exchange to ensure sufficient margin

### Critical Risk Management

**High leverage warning (100x default):** At 100x leverage, liquidation occurs at ~1% price movement. The bot tracks liquidation distance separately for LONG and SHORT sides in `position_manager.get_liquidation_distance()`. Emergency close triggers when distance <= liquidation_buffer (default 0.5%).

### Multi-Symbol Architecture

The bot supports trading multiple symbols simultaneously in a single instance:

**Shared Components (1 per bot):**
- `BybitClient`: Single API client for all symbols
- `MetricsTracker`: Aggregated analytics across all symbols

**Per-Symbol Components:**
- `GridStrategy`: Independent strategy instance
- `PositionManager`: Separate position tracking
- `BybitWebSocket`: Dedicated WebSocket connection
- State in `bot_state.json`: Keyed by symbol name

**Key Design Points:**
- Each symbol operates independently - one symbol's failure doesn't affect others
- All log messages prefixed with `[SYMBOL]` for filtering
- CSV data includes `symbol` column for multi-symbol analysis
- State file uses dict structure: `{"DOGEUSDT": {...}, "SOLUSDT": {...}}`

## Development Commands

### Environment Setup
```bash
# Create virtual environment
~/.local/bin/uv venv

# Activate
source .venv/bin/activate

# Install dependencies
~/.local/bin/uv pip install -r requirements.txt
```

### Running the Bot
```bash
# Main bot execution (foreground)
python src/main.py

# Analytics (requires bot to have run first)
python scripts/analyze.py
python scripts/analyze.py --plot --period 24h

# Utility scripts
python scripts/check_balance.py        # Check demo account balance
python scripts/pre_launch_check.py     # Pre-launch validation
python scripts/check_hedge_mode.py     # Verify hedge mode configuration
```

### Background/Service Mode
```bash
# Setup bot as systemd service (one-time setup, requires sudo)
sudo bash scripts/setup_service.sh

# Control service using bot_control.sh
./scripts/bot_control.sh start         # Start bot service
./scripts/bot_control.sh stop          # Stop bot service
./scripts/bot_control.sh restart       # Restart bot service
./scripts/bot_control.sh status        # Check service status
./scripts/bot_control.sh logs          # View systemd logs (real-time)
./scripts/bot_control.sh logs-bot      # View bot file logs

# Alternative: Run in screen session (simpler, no auto-restart)
./scripts/run_background.sh
```

### Testing
```bash
# Run all tests (113 total)
pytest tests/ -v

# Run specific test file
pytest tests/test_position_manager.py -v
pytest tests/test_grid_strategy.py -v
pytest tests/test_integration.py -v

# Run specific test
pytest tests/test_position_manager.py::TestPositionManager::test_get_liquidation_distance_long -v

# Run tests matching pattern
pytest tests/ -k "liquidation" -v

# Dry run mode (logs only, no API calls)
# Set in config/config.yaml: bot.dry_run = true
python src/main.py
```

## Configuration

All configuration in `config/config.yaml`:

**Multi-symbol trading:**
Bot supports trading multiple symbols simultaneously. Each symbol has independent strategy configuration.

**Strategy parameters (per symbol):**
- `symbol`: Trading pair (REQUIRED - no default!)
- `category`: Contract type (linear for USDT perpetuals)
- `leverage`: Trading leverage (75-100x typical - HIGH RISK)
- `initial_position_size_usd`: Starting margin in USD (NOT position size - leverage applied automatically!)
- `grid_step_percent`: Price movement % to trigger averaging
- `averaging_multiplier`: Classic martingale multiplier (2.0 = 1→2→4→8→16)
- `take_profit_percent`: Profit % to close positions
- `max_grid_levels_per_side`: Maximum averaging levels

**Risk parameters (global for all symbols):**
- `max_total_exposure`: **DEPRECATED** - No longer used. Balance checks use `totalAvailableBalance` from exchange directly before each order
- `liquidation_buffer`: **DEPRECATED** - Liquidation risk now managed via Account MM Rate (emergency close at 90%)
- `emergency_stop_loss`: Total PnL loss trigger (not currently implemented)

**Bot modes:**
- `bot.dry_run`: true = simulation (no API calls), false = real execution
- `exchange.demo_trading`: true = demo server, false = production

### API Credentials

Stored in `.env` (not committed):
```bash
BYBIT_API_KEY=your_key
BYBIT_API_SECRET=your_secret
BYBIT_ENV=demo  # or production
```

Get demo API keys from https://testnet.bybit.com

## Data and Logs

**Generated files:**
- `logs/bot_YYYY-MM-DD.log`: Main bot events and state changes
- `logs/trades_YYYY-MM-DD.log`: Every trade execution
- `logs/positions_YYYY-MM-DD.log`: Position state snapshots
- `data/performance_metrics.csv`: Time-series snapshots (every 60s)
- `data/trades_history.csv`: All trades with PnL
- `data/summary_report.json` / `.txt`: Final performance summary
- `data/bot_state.json`: Persisted position state (for recovery after restarts)

## Important Patterns

### Adding New Exchange Methods
When adding new Bybit API functionality, wrap it in `bybit_client.py` with proper error handling and logging. Follow the pattern:
```python
def new_method(self, ...):
    try:
        response = self.session.api_method(...)
        self.logger.info(f"Success message")
        return response
    except Exception as e:
        self.logger.error(f"Failed: {e}")
        raise
```

### Modifying Strategy Logic
Grid logic lives in `grid_strategy.py`. The three check methods (`_check_grid_entries`, `_check_take_profit`, `_check_risk_limits`) run on every price update. Keep them efficient - they execute multiple times per second.

### Position Tracking
Never modify `position_manager.py` position lists directly. Always use:
- `add_position()` to add
- `remove_all_positions()` to close
These methods maintain consistency with `last_long_entry` and `last_short_entry` tracking.

### State Persistence and Sync
The bot uses a dual-sync mechanism for reliability:
1. **State file persistence**: `state_manager.py` saves all positions to `data/bot_state.json` after changes (multi-symbol format)
2. **Exchange sync**: `sync_with_exchange()` is called every 60 seconds in `main.py:196` to verify positions match exchange reality
3. **On startup**: `sync_with_exchange()` checks exchange first, then opens initial positions if none exist
4. **Position close detection**: When positions disappear from exchange, bot:
   - Fetches real PnL from `get_closed_pnl()` (REQUIRED - no fallback!)
   - Determines close reason (positive PnL = Take Profit, negative = Liquidation)
   - Logs ERROR level message with full details
   - Records to CSV via metrics_tracker
   - Reopens positions if appropriate

This design ensures the bot recovers correctly after restarts and detects all position closures even if WebSocket misses the event.

## Testing Strategy

1. **Dry Run Testing** (bot.dry_run = true): Test logic without API calls
2. **Demo Trading** (demo_trading = true, dry_run = false): Real API calls with virtual money
3. **Production** (demo_trading = false, dry_run = false): Real money - USE WITH CAUTION

Always test new features in dry run, then demo, before considering production use.

## Dependencies

Core libraries:
- `pybit`: Official Bybit API client (HTTP and WebSocket)
- `python-dotenv`: Environment variable management
- `pyyaml`: YAML configuration parsing
- `pandas`: Analytics data processing
- `matplotlib`: Chart generation for analytics
- `pytest`: Testing framework

## Known Limitations

1. **Leverage risk**: High leverage (75-100x) means small price movements cause liquidation. Each side liquidates independently despite hedging. Cross Margin mode means entire account balance backs all positions.
2. **Funding rate**: Holding both LONG and SHORT incurs funding rate on both sides (small net cost).
3. **Commission accumulation**: Frequent averaging can accumulate taker fees (~0.055% on Bybit). Use limit orders where possible (not currently implemented).
4. **No partial closes**: Positions close all-or-nothing per side. No gradual exit strategy.
5. **Balance checks use shared account balance**: All symbols share the same `totalAvailableBalance` from exchange. Each averaging checks if sufficient balance exists before placing order.
6. **Emergency close on data issues**: Bot stops immediately if exchange data cannot be fetched (fail-fast principle).

## Quick Reference

**Start bot in demo mode (foreground):**
1. Set API keys in `.env`
2. Ensure `config/config.yaml` has:
   - `strategies:` section with at least one symbol (REQUIRED - no default!)
   - `demo_trading: true`
   - `dry_run: false`
3. Run `python src/main.py`
4. Monitor logs in `logs/` directory or systemd journal
5. Stop with Ctrl+C (graceful shutdown)

**Start bot as background service:**
1. One-time setup: `sudo bash scripts/setup_service.sh`
2. Start: `sudo systemctl start sol-trader`
3. Check status: `sudo systemctl status sol-trader`
4. View logs: `sudo journalctl -u sol-trader -f`
5. Filter by symbol: `sudo journalctl -u sol-trader -f | grep DOGEUSDT`
6. Stop: `sudo systemctl stop sol-trader`

See `BACKGROUND_SERVICE_GUIDE.md` for detailed instructions on running the bot 24/7.

**Analyze performance:**
```bash
python scripts/analyze.py --plot
```

**Check demo account balance:**
```bash
python scripts/check_balance.py
```

**Run tests:**
```bash
pytest tests/ -v  # All 113 tests
```

## Common Development Workflows

### Adding a New Symbol to Trade

1. Stop the bot: `sudo systemctl stop sol-trader`
2. Edit `config/config.yaml` - add new strategy entry under `strategies:`
3. Ensure symbol is valid on Bybit (check https://testnet.bybit.com for demo)
4. Start bot: `sudo systemctl start sol-trader`
5. Verify in logs: `sudo journalctl -u sol-trader -f | grep "[NEWSYMBOL]"`

### Debugging Position Issues

1. Check current state: `cat data/bot_state.json | python3 -m json.tool`
2. View recent trades: `cat data/trades_history.csv | tail -20`
3. Check liquidation warnings: `sudo journalctl -u sol-trader | grep "liquidation"`
4. Verify positions on exchange manually at https://testnet.bybit.com (demo) or https://bybit.com (prod)

### Modifying Grid Strategy Logic

Files to modify:
- `src/strategy/grid_strategy.py`: Entry/exit logic
- `src/strategy/position_manager.py`: Position calculations
- Tests: `tests/test_grid_strategy.py` and `tests/test_integration.py`

Critical: Always update tests when changing strategy logic. Run `pytest tests/ -v` before committing.

### Adding New Risk Checks

1. Add check method in `grid_strategy.py` (e.g., `_check_new_risk()`)
2. Call from `on_price_update()` or `_check_risk_limits()`
3. Ensure check uses real exchange data - NO fallbacks or estimates
4. Add test in `tests/test_grid_strategy.py::TestRiskLimits`
5. Test with dry_run=true first, then demo_trading

### Troubleshooting Bot Crashes

1. Check systemd logs: `sudo journalctl -u sol-trader -n 100`
2. Check bot file logs: `tail -100 logs/bot_$(date +%Y-%m-%d).log`
3. Check for errors: `sudo journalctl -u sol-trader | grep ERROR`
4. Common causes:
   - Missing symbol in config (bot requires at least one strategy)
   - Exchange API timeout (network issue)
   - Invalid API keys (check .env file)
   - Insufficient balance for min order size
