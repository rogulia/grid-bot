# Changelog

All notable changes to the SOL-Trader project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.5.0] - 2025-01-15

### Changed - REST API Optimization (WebSocket-First Architecture)

**Goal:** Minimize REST API usage by leveraging WebSocket streams for all monitoring data.

#### Eliminated Duplicate Balance Calls
- **Problem:** Multiple REST API calls to `get_wallet_balance()` across different strategy components
- **Solution:** Created shared `BalanceManager` instance per account
  - All strategies share one balance_manager
  - Wallet WebSocket updates cache in real-time
  - Force refresh only on initialization
- **Files Modified:**
  - `src/core/trading_account.py`: Create shared balance_manager (lines 185-206, 247-257)
  - `src/strategy/grid_strategy.py`: Accept optional balance_manager parameter (lines 22-67)
- **Impact:** Eliminated 1+ REST API call per strategy at startup

#### Position Restoration via WebSocket Snapshot
- **Problem:** Using `get_active_position()` REST API in `sync_with_exchange()` to check for positions (~120 calls/hour)
- **Solution:** Position WebSocket restoration
  - Position WebSocket sends snapshot on connect (contains all open positions)
  - Added restoration logic in `on_position_update()`:
    - Detects when position exists on exchange but not tracked locally (`size > 0` and `local_qty == 0`)
    - Restores position from WebSocket data (avgPrice, quantity)
    - Creates TP order automatically
    - Logs as "RESTORE" action to metrics
  - State file auto-updates via `pm.add_position()`
- **Files Modified:**
  - `src/strategy/grid_strategy.py`:
    - Removed `get_active_position()` call from `sync_with_exchange()` (line ~496)
    - Added position restoration in `on_position_update()` (lines 1148-1198)
    - Updated `sync_with_exchange()` docstring to document new flow (lines 310-328)
- **Impact:**
  - Eliminated ~120 REST API calls per hour
  - Position WebSocket is now source of truth (not state file)
  - Simpler, more reliable restoration logic

#### Removed Order History Restoration Method
- **Problem:** Complex 132-line method using `get_order_history()` REST API to reconstruct positions
- **Solution:**
  - Deleted entire `_restore_positions_from_order_history()` method
  - Position WebSocket snapshot provides accurate position data
  - No need to reconstruct from order history
- **Files Modified:**
  - `src/strategy/grid_strategy.py`: Deleted lines 310-441 (132 lines removed)
- **Impact:**
  - Eliminated `get_order_history()` REST API calls
  - Reduced code complexity by 132 lines
  - More reliable restoration (exchange avgPrice > reconstructed price)

### Architecture Changes

**REST API Usage After Optimization:**

**Monitoring Calls (eliminated completely):**
- ❌ ~~get_wallet_balance()~~ → Replaced by Wallet WebSocket updates
- ❌ ~~get_active_position()~~ → Replaced by Position WebSocket snapshot
- ❌ ~~get_order_history()~~ → Replaced by Position WebSocket snapshot

**Command Calls (still needed):**
- ✅ `place_order()` - Place grid orders
- ✅ `cancel_order()` - Cancel TP orders
- ✅ `close_position()` - Close positions
- ✅ `set_leverage()` - Set leverage at startup
- ✅ `set_position_mode()` - Set hedge mode at startup
- ✅ `get_closed_pnl()` - Fetch real PnL after position close
- ✅ `get_instrument_info()` - Fetch trading rules at startup
- ✅ `get_wallet_balance()` - Initial balance fetch (once at startup with force_refresh=True)

**Total REST API Calls Eliminated:** ~125+ calls/hour (balance + position polling)

**New Architecture:** WebSocket-First - ALL monitoring data via WebSocket streams, REST API only for commands

### Documentation Updates

#### CLAUDE.md
- Updated Exchange Layer section to describe WebSocket-First Architecture (lines 23-29)
- Updated Utilities Layer to describe WebSocket-first balance_manager (lines 35-39)
- Updated Strategy Logic Flow with detailed WebSocket stream breakdown (lines 48-79):
  - Price Updates (Public WebSocket)
  - Position Updates (Private WebSocket)
  - Wallet Updates (Private WebSocket)
  - Order Updates (Private WebSocket)
  - Periodic Sync details
- Updated State Persistence and Sync section (lines 461-490):
  - Documented Position WebSocket restoration flow
  - Clarified that Position WebSocket > State File
  - Removed references to old REST API polling approach

#### CODE_QUALITY_IMPROVEMENTS.md
- Added Phase 1.5: REST API Optimization section (lines 550-637)
- Documented all three optimization improvements with details
- Listed REST API usage before/after optimization
- Clarified WebSocket-First architecture approach

### Testing
- All 172 tests passing ✅
- No breaking changes to existing functionality
- Position restoration tested and verified

### Key Design Decisions

1. **Position WebSocket as Source of Truth:**
   - State file can be outdated after crashes/kills
   - Always trust WebSocket snapshot for position restoration
   - State file serves as backup only, auto-updates via `pm.add_position()`

2. **Grid Level Loss Tradeoff:**
   - Restored positions lose grid level detail (all marked as grid_level=0)
   - However, exchange avgPrice is accurate and that's what matters for TP calculation
   - This is acceptable tradeoff for simpler, more reliable restoration

3. **Shared Balance Manager:**
   - One balance_manager instance per account
   - All strategies share same instance to avoid duplicate calls
   - Wallet WebSocket keeps it updated in real-time

## [1.0.0] - 2025-01-12

### Added - Phase 1: Critical Thread Safety Fixes

#### WebSocket Thread Safety
- Added `threading.Lock()` to all shared resources accessed by WebSocket callbacks:
  - `BalanceManager._cached_balance` and `._cached_mm_rate`
  - `GridStrategy._tp_orders` dict
  - `GridStrategy._last_cum_realised_pnl` dict
  - `PositionManager.long_positions` and `.short_positions` lists

#### WebSocket Reconnect Mechanism
- Implemented automatic reconnection with exponential backoff
- Added connection state tracking (`is_connected()` method)
- Heartbeat monitoring for silent disconnect detection
- Max 10 reconnect attempts with 1s to 60s delay

#### WebSocket Connection Management
- Proper connection closing in `stop()` method using `ws.exit()`
- Clean shutdown of heartbeat monitor thread
- Graceful handling of connection errors

### Files Modified (Phase 1)
- `src/utils/balance_manager.py`: Thread safety with locks
- `src/strategy/grid_strategy.py`: Thread safety for dicts
- `src/strategy/position_manager.py`: Thread safety for lists
- `src/exchange/bybit_websocket.py`: Reconnect + heartbeat monitoring

### Testing
- All 172 tests passing
- Unit tests with concurrent access
- Integration tests with real WebSocket callbacks

---

## Release Notes

### v1.5.0 Summary
This release significantly reduces REST API usage by implementing a WebSocket-First architecture. All monitoring data now comes from WebSocket streams in real-time, with REST API used only for commands (order execution, leverage setting, etc.). This improves reliability, reduces latency, and eliminates ~125+ API calls per hour.

**Key Improvements:**
- 🚀 ~125+ REST API calls eliminated per hour
- 📡 Position WebSocket is now source of truth for position restoration
- 🔄 Simpler, more reliable position restoration on bot restart
- 📊 Shared balance manager eliminates duplicate calls
- 🧹 132 lines of complex restoration code removed

**Breaking Changes:** None - all changes are backward compatible

**Migration Notes:** No action required - bot automatically uses new WebSocket-first architecture
