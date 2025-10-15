# Position Restoration from Exchange

## Table of Contents
1. [Overview](#overview)
2. [When Restoration Happens](#when-restoration-happens)
3. [Restoration Flow](#restoration-flow)
4. [Four Restoration Scenarios](#four-restoration-scenarios)
5. [Grid Level Restoration Algorithm](#grid-level-restoration-algorithm)
6. [Special Cases & Edge Cases](#special-cases--edge-cases)
7. [Retry Mechanism](#retry-mechanism)
8. [Force Cancel Mode](#force-cancel-mode)
9. [Emergency Stop Creation](#emergency-stop-creation)
10. [Periodic Sync vs Initial Restore](#periodic-sync-vs-initial-restore)
11. [Fail-Fast Principles](#fail-fast-principles)
12. [Diagnostics & Troubleshooting](#diagnostics--troubleshooting)
13. [Best Practices](#best-practices)

---

## Overview

Position restoration is a **critical safety mechanism** that ensures the bot's internal state matches the exchange's reality after a restart. Without it, the bot could make incorrect trading decisions based on stale data.

### Key Concepts

**REST API + WebSocket Hybrid Architecture:**
- **Startup (Restoration):** Uses REST API to fetch position state (source of truth)
- **Runtime (Updates):** Uses WebSocket for real-time position updates
- **Periodic Check (Safety):** REST API sync every 60 seconds to catch missed events

**Fail-Fast Philosophy:**
The bot NEVER uses fallback or estimated values. If data cannot be verified from exchange, bot stops with emergency flag. Better to stop than trade with wrong data.

**Tolerance:**
Strict tolerance of `0.001` (allows only rounding errors). Larger differences trigger fail-fast behavior.

---

## When Restoration Happens

### 1. Initial Restore (Startup)
Called **BEFORE WebSocket start** via `restore_state_from_exchange()`:
```python
# In main.py initialization
current_price = client.get_market_price(symbol)
grid_strategy.restore_state_from_exchange(current_price)
# ONLY AFTER successful restore:
grid_strategy.start_websockets()
```

### 2. Periodic Sync (Runtime)
Called **every 60 seconds** during operation via `sync_with_exchange()`:
```python
# Triggered by timer
grid_strategy.sync_with_exchange(current_price)
```

**Purpose:** Detect untracked closes (WebSocket missed event).

---

## Restoration Flow

### High-Level Flow
```
┌─────────────────────────────────────────┐
│  Bot Starts / Restarts                  │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  Get current price via REST API         │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  restore_state_from_exchange()          │
│                                         │
│  Loop: Max 3 attempts                   │
│  For each side (Buy, Sell):             │
│    1. Fetch exchange position (REST)    │
│    2. Compare with local state          │
│    3. Handle scenario (see below)       │
│    4. Create TP orders                  │
│                                         │
│  If WebSocket update during restore:    │
│    → Set resync flag, retry             │
└──────────────┬──────────────────────────┘
               │
               ├─ Success → Start WebSockets
               │
               └─ 3 failures → Emergency Stop
```

### Step-by-Step Process

**Step 1: Block if Emergency Stop Exists**
```python
if self.emergency_stopped:
    raise RuntimeError("Cannot restore: emergency stop active")
```

**Step 2: Get Available Balance**
```python
available_balance = balance_manager.get_available_balance()
# Used to check if sufficient margin for initial positions
```

**Step 3: For Each Side (Buy, Sell)**
```python
for side in ['Buy', 'Sell']:
    # Get exchange state (SOURCE OF TRUTH)
    exchange_position = client.get_active_position(symbol, side)
    exchange_qty = float(exchange_position.get('size', 0))

    # Get local state (should be empty at startup!)
    local_qty = pm.get_total_quantity(side)

    # Calculate difference
    qty_diff = abs(exchange_qty - local_qty)
    tolerance = 0.001  # Only rounding errors allowed

    # Handle based on scenario (see next section)
```

**Step 4: Check Resync Flag**
```python
with self._resync_lock:
    if self._needs_resync:
        retry_count += 1
        continue  # Retry
    else:
        break  # Success
```

---

## Four Restoration Scenarios

### SCENARIO 1: No Positions Anywhere → Open Initial

**Condition:** `exchange_qty == 0 AND local_qty == 0`

**Action:** Open initial position for this side

**Log Example:**
```
2025-10-15 23:49:39 - INFO - 🆕 [DOGEUSDT] No Buy position exists - opening initial position
2025-10-15 23:49:39 - INFO - 🆕 [DOGEUSDT] Opening initial Buy position: $1.00 (364.0 DOGEUSDT) @ $0.2061
```

**Process:**
1. Check balance: `available_balance >= initial_size_usd`
2. If insufficient → Emergency stop
3. Calculate quantity: `qty = initial_size_usd / current_price / leverage`
4. Place order (limit with fallback to market)
5. Track position locally: `pm.add_position()`
6. Create TP order: `_update_tp_order(side, force_cancel_all=True)`

**Code Flow:**
```python
# Check balance
if available_balance < self.initial_size_usd:
    reason = f"Insufficient balance: need ${self.initial_size_usd}, available ${available_balance}"
    self._create_emergency_stop_flag(reason)
    raise RuntimeError(reason)

# Open position
initial_qty = self._usd_to_qty(self.initial_size_usd, current_price)
order_id = self.limit_order_manager.place_limit_order(
    side=side,
    qty=initial_qty,
    current_price=current_price,
    reason="Initial position (restore)",
    position_idx=position_idx
)

# Track locally
pm.add_position(side, current_price, initial_qty, grid_level=0, order_id=order_id)

# Create TP
tp_id = self._update_tp_order(side, force_cancel_all=True)
```

---

### SCENARIO 2: Positions Synced → Verify TP

**Condition:** `qty_diff <= 0.001`

**Action:** Verify TP order exists, create if missing

**Log Example:**
```
2025-10-15 23:49:40 - INFO - ✅ [DOGEUSDT] Buy position SYNCED: exchange=22932.0, local=22932.0
2025-10-15 23:49:40 - INFO - 🎯 [DOGEUSDT] Creating TP order for Buy position (qty=22932.0)
```

**Process:**
1. Positions match → Good!
2. If position exists (`local_qty > 0`):
   - Check if TP order ID exists
   - If missing → Create TP order

**Code Flow:**
```python
if qty_diff <= tolerance:
    self.logger.info(f"✅ [{self.symbol}] {side} position SYNCED")

    # Create TP if missing
    if local_qty > 0:
        tp_order_id = pm.get_tp_order_id(side)
        if not tp_order_id:
            self._update_tp_order(side)
```

---

### SCENARIO 3: Exchange Has Position → RESTORE

**Condition:** `exchange_qty > 0 AND local_qty == 0`

**Action:** Restore position from exchange data

**Log Example:**
```
2025-10-15 23:49:39 - WARNING - 📥 [DOGEUSDT] Position found on exchange for Buy: exchange=22932.0, local=0 - RESTORING
2025-10-15 23:49:39 - INFO - 📥 [DOGEUSDT] RESTORING Buy position from exchange: 22932.0 @ $0.1994
2025-10-15 23:49:39 - INFO - 📊 [DOGEUSDT] Restored 6 grid levels for Buy from order history
2025-10-15 23:49:40 - INFO - ✅ [DOGEUSDT] Buy position restored (6 levels) and TP order created (ID: 18e1e5e5-c183-4e48-ad8d-63291ad92595)
```

**Process:**
1. Exchange has position, local tracking empty (typical after restart)
2. Call `_restore_position_from_exchange(side, exchange_position)`
3. Restore grid levels from order history (see algorithm below)
4. Add each position to PositionManager
5. Create TP order with `force_cancel_all=True`

**Key Point:** Uses **order history** to reconstruct grid levels!

---

### SCENARIO 4: Unexplained Mismatch → FAIL-FAST

**Condition:** Any other case (e.g., `exchange_qty != local_qty` with diff > tolerance)

**Action:** Emergency stop with manual intervention required

**Log Example:**
```
2025-10-15 23:17:40 - ERROR - ❌ [DOGEUSDT] Position mismatch for Buy requires manual intervention:
    exchange=22932.0, local=22568.0, diff=364.000000.
    This may indicate: (1) positions opened outside bot,
    (2) partial close, (3) exchange API issue.
    Please verify positions on exchange and restart bot.
```

**Possible Causes:**
1. Manual trading during bot runtime
2. Another bot/system using same account
3. Partial position close (not supported)
4. Exchange API returning inconsistent data

**Resolution:**
1. Check exchange positions manually
2. Close all positions OR ensure bot state matches
3. Remove emergency stop flag: `rm data/.00X_emergency_stop`
4. Restart bot

---

## Grid Level Restoration Algorithm

When exchange has a position but local tracking is empty, bot reconstructs grid levels from **order history**.

### Algorithm Steps

**1. Fetch Order History**
```python
orders = client.get_order_history(
    symbol=symbol,
    category="linear",
    limit=200,  # TradingConstants.ORDER_HISTORY_LIMIT
    order_status="Filled"  # Only filled orders!
)
```

**Why only "Filled"?** Dramatically increases effective history depth (200 filled vs 200 mixed with cancelled).

**2. Filter Orders by positionIdx**
```python
position_idx = 1 if side == 'Buy' else 2  # Hedge mode
position_orders = [
    o for o in orders
    if o.get('positionIdx') in [position_idx, str(position_idx)]
    and o.get('orderStatus') == 'Filled'
]
```

**3. Find Last TP Close**

TP close = `opposite_side` + `reduceOnly=True`
- For Buy position: TP close = Sell order with reduceOnly=True
- For Sell position: TP close = Buy order with reduceOnly=True

```python
opposite_side = 'Sell' if side == 'Buy' else 'Buy'
last_tp_idx = -1
for i, order in enumerate(position_orders):
    if order.get('side') == opposite_side and order.get('reduceOnly'):
        last_tp_idx = i  # Keep updating to get LAST (most recent) TP
```

**4. Get Orders After Last TP**
```python
if last_tp_idx < 0:
    # No TP found → use all orders
    orders_after_tp = position_orders
else:
    # Everything AFTER TP close
    orders_after_tp = position_orders[last_tp_idx + 1:]
```

**5. Filter for Opening Orders**
```python
current_position_orders = [
    o for o in orders_after_tp
    if o.get('side') == side and not o.get('reduceOnly')
]
```

**6. Reconstruct Grid Levels**
```python
positions = []
for i, order in enumerate(current_position_orders):
    qty = float(order.get('cumExecQty', 0))
    price = float(order.get('avgPrice', 0))
    order_id = order.get('orderId')
    grid_level = i  # First = level 0, second = level 1, etc.

    if qty > 0 and price > 0:
        positions.append((qty, price, grid_level, order_id))
```

**7. Validate Restored Quantity**
```python
restored_qty = sum(qty for qty, _, _, _ in positions)
qty_diff = restored_qty - total_qty  # signed difference

if abs(qty_diff) > 0.001:
    if qty_diff < 0:
        # Restored LESS → trigger resync (position opened during fetch)
        self._needs_resync = True
        return []  # Signal failure
    else:
        # Restored MORE → logic error!
        raise RuntimeError("Restored more than exchange")
```

### Example Restoration

**Exchange State:**
```
Buy position: 22932 @ $0.1994 (6 levels)
```

**Order History Analysis:**
```
Order 1: Buy 364   @ $0.2061  → Level 0
Order 2: Buy 728   @ $0.2061  → Level 1
Order 3: Buy 1456  @ $0.2040  → Level 2
Order 4: Buy 2912  @ $0.2018  → Level 3
Order 5: Buy 5824  @ $0.1996  → Level 4
Order 6: Buy 11648 @ $0.1975  → Level 5
----------------------------------------
Total:   22932
```

**Restored State:**
```python
positions = [
    (364, 0.2061, 0, "37ebd976-4574-44ef-8928-18c3a1634785"),
    (728, 0.2061, 1, "c4a24988-31ef-434d-8cab-fd82c21f043b"),
    (1456, 0.2040, 2, "8fa65e70-4777-4fc7-b3eb-d3a6c8518124"),
    (2912, 0.2018, 3, "c7ab321b-7bd2-4084-a5c2-b69c0f8100a9"),
    (5824, 0.1996, 4, "bd819d49-0fac-4918-b35e-ad165d58981b"),
    (11648, 0.1975, 5, "570f7583-9a50-442e-828e-d93b17dcb4c9")
]
```

**Validation:**
```
Restored:  22932
Exchange:  22932
Diff:      0.000000 ✅
```

---

## Special Cases & Edge Cases

### 1. WebSocket Updates During Restore

**Problem:** Position changes while bot is restoring (manual trading, TP trigger, etc.)

**Detection:** WebSocket callbacks set `_needs_resync = True` if `_is_syncing = True`

**Solution:** Retry loop detects resync flag and retries entire restore process

**Log Example:**
```
2025-10-15 23:49:40 - WARNING - ⚠️ [DOGEUSDT] Position changed during restore (attempt 2/3), retrying...
    Last triggers: execution_update, position_update
```

**Resync Trigger Tracking:**
```python
self._resync_triggers.append({
    'timestamp': '2025-10-15T23:49:40',
    'event': 'execution_update',
    'reason': 'Position qty changed',
    'side': 'Buy'
})
```

---

### 2. Insufficient Balance

**Problem:** Not enough balance to open initial positions

**Detection:** `available_balance < initial_size_usd`

**Solution:** Emergency stop with clear error message

**Log Example:**
```
2025-10-15 23:49:39 - ERROR - ❌ [DOGEUSDT] Insufficient balance to start trading:
    need $1.00 MARGIN for initial position, available $0.50
```

**Emergency Stop Flag Created:** `data/.001_emergency_stop`

---

### 3. Truncated Order History

**Problem:** Position has more grid levels than history depth (> 200 orders between last TP and now)

**Detection:** `len(orders) == ORDER_HISTORY_LIMIT` (200)

**Solution:** Warning logged, but restore continues. If validation fails → retry

**Log Example:**
```
2025-10-15 23:49:39 - WARNING - ⚠️ [DOGEUSDT] Retrieved exactly 200 orders -
    history may be truncated. If restoration fails, consider closing positions manually.
```

---

### 4. Quantity Mismatch: Restored < Exchange

**Problem:** Restored less quantity than exchange reports

**Cause:** Position opened during order history fetch (race condition)

**Solution:** Set resync flag, return empty list → retry loop handles it

**Log Example:**
```
2025-10-15 23:17:40 - ERROR - ❌ [DOGEUSDT] Restored 364.000000 LESS than exchange for Buy:
    restored=22568.0, exchange=22932.0, missing=364.000000
    This indicates order history is incomplete (fetched 200 orders).
    Possible causes:
    - More than 200 orders between last TP and now
    - Position opened during order history fetch
    Will trigger re-sync.
```

---

### 5. Quantity Mismatch: Restored > Exchange

**Problem:** Restored MORE quantity than exchange reports

**Cause:** Logic error - included old orders from previous position

**Solution:** FAIL-FAST with RuntimeError

**Log Example:**
```
2025-10-15 23:49:40 - ERROR - ❌ [DOGEUSDT] Restored 1000.000000 MORE than exchange
    (restored=23932.0, exchange=22932.0). Likely included old orders! Using fallback.
```

---

### 6. No Order History Available

**Problem:** Exchange returns empty order history

**Cause:** API issue, new account, or all orders expired from history

**Solution:** FAIL-FAST - cannot restore without order history

**Log Example:**
```
2025-10-15 23:49:40 - ERROR - ❌ [DOGEUSDT] No order history available - cannot restore Buy position.
    Manual intervention required: close position on exchange and restart bot.
```

---

### 7. No Position Orders After Last TP

**Problem:** Found TP close, but no opening orders after it

**Cause:** Position opened VERY recently (orders not yet in history)

**Solution:** FAIL-FAST with manual intervention instruction

**Log Example:**
```
2025-10-15 23:49:40 - ERROR - ❌ [DOGEUSDT] No Buy position orders found after last TP - cannot restore.
    Manual intervention required: close position on exchange and restart bot.
```

---

## Retry Mechanism

### Overview

Bot makes **up to 3 attempts** to restore state. If all fail → Emergency stop with full diagnostics.

### Retry Loop Logic

```python
max_retries = 3
retry_count = 0

while retry_count < max_retries:
    # Reset resync flag
    with self._resync_lock:
        self._needs_resync = False
        if retry_count == 0:
            self._resync_triggers = []  # Clear on first attempt only

    # Attempt restore
    try:
        # ... restore logic ...
        pass
    finally:
        # Clear syncing flag
        with self._sync_lock:
            self._is_syncing = False

    # Check if resync needed
    with self._resync_lock:
        if not self._needs_resync:
            break  # Success!

    retry_count += 1

# If exited due to max retries → emergency stop
if retry_count >= max_retries:
    # Create emergency stop with diagnostics
    ...
```

### Resync Triggers Tracking

Each WebSocket event during restore is tracked:

```python
self._resync_triggers.append({
    'timestamp': now_helsinki().isoformat(),
    'event': 'execution_update',  # or 'position_update', 'validation_failed'
    'reason': 'Position qty changed',
    'side': 'Buy'
})
```

### Max Retries Emergency Stop

After 3 failed attempts, bot creates detailed emergency stop:

**Log Example:**
```
2025-10-15 23:49:45 - ERROR - ❌ [DOGEUSDT] Failed to restore state after 3 attempts due to continuous position changes.

🔍 DIAGNOSTIC INFO:
- Retry count: 3
- WebSocket interruptions: 7
- Interruption events: execution_update, position_update, execution_update, validation_failed, ...
- Exchange state: LONG=22932@$0.1994, SHORT=1149@$0.1971
- Local state: LONG=22568, SHORT=766

💡 POSSIBLE CAUSES:
1. Manual trading active during bot startup
2. Another bot/system using same account
3. Very active market with frequent TP triggers
4. Exchange API returning inconsistent data

🔧 RESOLUTION:
1. Stop all manual trading and other bots
2. Wait for market calm (no active orders/positions changing)
3. Restart bot
4. Or close all positions manually on exchange and restart with fresh state
```

**Emergency Stop Flag:** `data/.001_emergency_stop` with full diagnostic data

---

## Force Cancel Mode

### Overview

When restoring positions after restart, local TP order tracking is **stale/empty**. Force cancel mode ensures ALL old reduce-only orders are cancelled before creating new TP.

### When Used

```python
# In _restore_position_from_exchange()
tp_id = self._update_tp_order(side, force_cancel_all=True)
```

### Normal Mode vs Force Mode

**Normal Mode** (`force_cancel_all=False`):
- Cancel only tracked TP order ID
- Used during runtime when tracking is up-to-date

**Force Mode** (`force_cancel_all=True`):
- Cancel ALL reduce-only orders from exchange
- Used after restart when tracking is empty/stale

### Implementation

```python
def _update_tp_order(self, side: str, force_cancel_all: bool = False):
    if force_cancel_all:
        # Force mode: Cancel ALL reduce-only orders
        self.logger.info(f"[{self.symbol}] 🔄 Force cancel mode: checking exchange for reduce-only orders...")
        self._cancel_all_reduce_only_orders(side)
    else:
        # Normal mode: Cancel only tracked TP
        old_tp_id = self.pm.get_tp_order_id(side)
        if old_tp_id:
            self.client.cancel_order(self.symbol, old_tp_id, self.category)
```

### _cancel_all_reduce_only_orders()

```python
def _cancel_all_reduce_only_orders(self, side: str):
    """Cancel ALL reduce-only orders for a position side from exchange"""

    # Determine opposite side (TP for Buy = Sell reduce-only)
    opposite_side = 'Sell' if side == 'Buy' else 'Buy'

    # Get ALL open orders from exchange
    open_orders = self.client.get_open_orders(self.symbol, self.category)

    # Filter for reduce-only orders of opposite side
    reduce_only_orders = [
        o for o in open_orders
        if o.get('side') == opposite_side
        and o.get('reduceOnly') == True
    ]

    if reduce_only_orders:
        self.logger.info(f"[{self.symbol}] Found {len(reduce_only_orders)} reduce-only order(s) to cancel")
        for order in reduce_only_orders:
            order_id = order.get('orderId')
            qty = order.get('qty')
            price = order.get('price')

            self.logger.info(f"[{self.symbol}] 🗑️  Cancelling reduce-only order: {order_id} ({opposite_side} {qty} @ ${price})")

            try:
                self.client.cancel_order(self.symbol, order_id, self.category)
            except Exception as e:
                self.logger.warning(f"[{self.symbol}] Failed to cancel {order_id}: {e}")

        self.logger.info(f"[{self.symbol}] ✅ Cancelled {len(reduce_only_orders)} reduce-only order(s) for {side} position")
```

### Log Example

```
2025-10-15 23:50:24 - INFO - [DOGEUSDT] 🔄 Force cancel mode: checking exchange for reduce-only orders...
2025-10-15 23:50:25 - INFO - [DOGEUSDT] 🗑️  Cancelling reduce-only order: 1245f8fd-66ec-4641-927e-47e926fc189a (Buy 380 @ $0.19499)
2025-10-15 23:50:25 - INFO - [DOGEUSDT] ✅ Cancelled 1 reduce-only order(s) for Sell position
2025-10-15 23:50:25 - INFO - [DOGEUSDT] ✅ TP order created: Buy 380.0 @ $0.1950 (avg entry: $0.1971, ID: bd1b1aba-ff47-4eea-99aa-cf317c221af7)
```

---

## Emergency Stop Creation

### When Created

1. **Insufficient balance** to open initial positions
2. **Unexplained position mismatch** (SCENARIO 4)
3. **Max retries exceeded** (3 failed restore attempts)
4. **Critical API failures** (cannot fetch positions)

### Emergency Stop File

**Location:** `data/.00X_emergency_stop` (hidden file)

**Format:**
```json
{
  "timestamp": "2025-10-15T23:49:45+03:00",
  "account_id": 1,
  "symbol": "DOGEUSDT",
  "reason": "Failed to restore state after 3 attempts...",
  "additional_data": {
    "retry_count": 3,
    "max_retries": 3,
    "resync_triggers": [...],
    "exchange_state": {
      "long_qty": 22932.0,
      "long_avg_price": 0.1994,
      "short_qty": 1149.0,
      "short_avg_price": 0.1971
    },
    "local_state": {
      "long_qty": 22568.0,
      "short_qty": 766.0
    },
    "current_price": 0.1976
  }
}
```

### Validation at Next Startup

```python
# In main.py before bot start
EmergencyStopManager.validate_and_raise(
    account_id=account_id,
    account_name=account_name
)
```

If flag exists → bot refuses to start:
```
RuntimeError: [Account 001] Emergency stop flag detected (created: 2025-10-15 23:49:45):
Failed to restore state after 3 attempts due to continuous position changes.
...
Remove flag file after resolving issue: rm data/.001_emergency_stop
```

### Resolution

1. Read emergency stop file: `cat data/.001_emergency_stop | python3 -m json.tool`
2. Diagnose issue using diagnostic data
3. Fix issue (close positions, stop manual trading, etc.)
4. Remove flag: `rm data/.001_emergency_stop`
5. Restart bot

---

## Periodic Sync vs Initial Restore

### Differences

| Aspect | Initial Restore | Periodic Sync |
|--------|----------------|---------------|
| **When** | Startup (before WebSocket) | Every 60s (during operation) |
| **Method** | `restore_state_from_exchange()` | `sync_with_exchange()` |
| **Purpose** | Restore state after restart | Detect untracked closes |
| **Retry** | Yes (3 attempts) | No (single check) |
| **Fail Behavior** | Emergency stop | Warning + recovery attempt |
| **WebSocket** | Not started yet | Already running |

### Periodic Sync Features

**1. Untracked Close Detection**

Detects when position closed on exchange but WebSocket missed event:

```python
# SCENARIO 2: Untracked close detected
if exchange_qty == 0 and local_qty > 0:
    self.logger.warning(f"⚠️ Untracked {side} position close detected")

    # Clear local state
    pm.remove_all_positions(side)
    pm.set_tp_order_id(side, None)

    # Reopen with adaptive sizing
    reopen_margin = self.calculate_reopen_size(side, opposite_side)
    self._open_initial_position(side, current_price, custom_margin_usd=reopen_margin)
```

**2. Missing TP Detection**

Verifies TP orders exist for all positions:

```python
# SCENARIO 1: Positions synced - verify TP order
if qty_diff <= tolerance and local_qty > 0:
    tp_order_id = pm.get_tp_order_id(side)
    if not tp_order_id:
        self.logger.warning(f"⚠️ TP order missing for {side} - creating")
        self._update_tp_order(side)
```

**3. Recovery Mode**

Detects severely unbalanced positions and attempts recovery:

```python
# Check for severe imbalance
is_severely_unbalanced = (
    (long_count >= 2 and short_count == 0) or
    (short_count >= 2 and long_count == 0)
)

if is_severely_unbalanced:
    missing_side = 'Sell' if long_count > short_count else 'Buy'
    self.logger.warning(f"🔧 RECOVERY MODE: Attempting to reopen {missing_side}...")
    # Reopen missing side
```

**4. Debounce for Reopen**

Prevents duplicate reopening within 3 seconds:

```python
with self._sync_lock:
    now = time.monotonic()
    if now < self._just_reopened_until_ts.get(side, 0.0):
        self.logger.debug(f"Skipping reopen for {side} (debounce)")
        continue
```

### Log Examples

**Untracked Close + Adaptive Reopen:**
```
2025-10-15 12:30:45 - WARNING - ⚠️ [DOGEUSDT] Untracked Buy position close detected: exchange=0, local=22932. WebSocket missed close event.
2025-10-15 12:30:45 - INFO - 🔄 [DOGEUSDT] Handling missed Buy close - clearing local state and reopening
2025-10-15 12:30:45 - INFO - 🆕 [DOGEUSDT] ADAPTIVE REOPEN (missed close): Buy with $2.50 margin
2025-10-15 12:30:46 - INFO - ✅ [DOGEUSDT] Reopened Buy after untracked close with $2.50 margin
```

**Recovery Mode:**
```
2025-10-15 12:31:00 - WARNING - 🔧 [DOGEUSDT] RECOVERY MODE: Detected missing Sell position. LONG=5 levels, SHORT=0 levels. Attempting to reopen Sell...
2025-10-15 12:31:00 - INFO - 🆕 [DOGEUSDT] RECOVERY REOPEN: Sell with $4.00 margin
2025-10-15 12:31:01 - INFO - ✅ [DOGEUSDT] RECOVERY SUCCESS: Reopened Sell with $4.00 margin
```

---

## Fail-Fast Principles

### Philosophy

**Better to stop than trade with wrong data.**

The bot NEVER:
- Uses fallback/default values for critical data
- Estimates positions based on incomplete information
- Continues trading after unexplained mismatches
- Assumes data that cannot be verified from exchange

### Examples

**1. No Fallback Balance**
```python
# ❌ WRONG (fallback)
try:
    balance = get_balance()
except:
    balance = 1000.0  # DANGEROUS!

# ✅ CORRECT (fail-fast)
try:
    balance = get_balance()
except Exception as e:
    raise RuntimeError(f"Cannot get balance: {e}")
```

**2. Strict Tolerance**
```python
tolerance = 0.001  # Only rounding errors allowed

if qty_diff > tolerance:
    # FAIL-FAST instead of approximating
    raise RuntimeError(f"Quantity mismatch: {qty_diff}")
```

**3. No Position Estimation**
```python
# ❌ WRONG (estimate)
if not exchange_position:
    # Use local state as fallback
    quantity = local_qty

# ✅ CORRECT (fail-fast)
if not exchange_position:
    raise RuntimeError("Cannot get exchange position")
```

**4. No TP Fallback**
```python
# In _restore_position_from_exchange()
tp_id = self._update_tp_order(side, force_cancel_all=True)
if not tp_id:
    # FAIL-FAST: TP is critical for risk management
    raise RuntimeError("Failed to create TP order")
```

### Benefits

1. **Prevents Silent Failures:** Issues caught immediately
2. **Clear Error Messages:** User knows exactly what's wrong
3. **Data Integrity:** Bot never operates with corrupted state
4. **Easier Debugging:** Errors are explicit, not hidden

---

## Diagnostics & Troubleshooting

### Reading Restoration Logs

**Successful Restoration:**
```
2025-10-15 23:49:39 - INFO - 🔄 [DOGEUSDT] Restoring state from exchange @ $0.1976...
2025-10-15 23:49:39 - DEBUG - [DOGEUSDT] Available balance: $582.88
2025-10-15 23:49:39 - DEBUG - [DOGEUSDT] Buy position check: exchange=11253.0, local=0, diff=11253.000000
2025-10-15 23:49:39 - WARNING - 📥 [DOGEUSDT] Position found on exchange for Buy: exchange=11253.0, local=0 - RESTORING
2025-10-15 23:49:39 - INFO - 📥 [DOGEUSDT] RESTORING Buy position from exchange: 11253.0 @ $0.1986
2025-10-15 23:49:39 - INFO - [DOGEUSDT] Retrieved 200 filled orders from history for Buy restoration
2025-10-15 23:49:39 - INFO - [DOGEUSDT] Found 45 filled orders for positionIdx=1
2025-10-15 23:49:39 - INFO - [DOGEUSDT] Found last TP close at index 40/45: Sell 5445.0 @ 0.2030
2025-10-15 23:49:39 - INFO - [DOGEUSDT] Taking 4 orders after TP at index 40
2025-10-15 23:49:39 - INFO - [DOGEUSDT] Buy opening orders after last TP: 5
2025-10-15 23:49:39 - INFO -   Level 0: 363.0 @ $0.2069 (orderId: a682d1a6-b1f2-4dce-bd73-8e92ff034cb5)
2025-10-15 23:49:39 - INFO -   Level 1: 726.0 @ $0.2030 (orderId: 055500e1-38fc-499a-a87a-291c24646503)
2025-10-15 23:49:39 - INFO -   Level 2: 1452.0 @ $0.2009 (orderId: 7c799956-fdcf-4cac-b0a4-c1f40a0842dd)
2025-10-15 23:49:39 - INFO -   Level 3: 2904.0 @ $0.1988 (orderId: ea12b9f6-68fd-48a2-8e8d-30a7257ae26d)
2025-10-15 23:49:39 - INFO -   Level 4: 5808.0 @ $0.1968 (orderId: 1302e0b0-1cb1-4a02-923b-0459adfb9b16)
2025-10-15 23:49:39 - INFO - 📊 [DOGEUSDT] Restored 5 grid levels for Buy from order history
2025-10-15 23:49:39 - INFO - ✅ [DOGEUSDT] Quantity validation passed: restored=11253.0, exchange=11253.0
2025-10-15 23:49:40 - INFO - [DOGEUSDT] 🔄 Force cancel mode: checking exchange for reduce-only orders...
2025-10-15 23:49:40 - INFO - [DOGEUSDT] ✅ TP order created: Sell 11253.0 @ $0.2012 (avg entry: $0.1986, ID: 3a18a499-88ea-4c50-9682-19cad9e9cad2)
2025-10-15 23:49:40 - INFO - ✅ [DOGEUSDT] Buy position restored (5 levels) and TP order created (ID: 3a18a499-88ea-4c50-9682-19cad9e9cad2)
2025-10-15 23:49:42 - DEBUG - [DOGEUSDT] Sell position check: exchange=380.0, local=0, diff=380.000000
2025-10-15 23:49:42 - WARNING - 📥 [DOGEUSDT] Position found on exchange for Sell: exchange=380.0, local=0 - RESTORING
2025-10-15 23:49:45 - INFO - ✅ [DOGEUSDT] Sell position restored (1 levels) and TP order created (ID: bd1b1aba-ff47-4eea-99aa-cf317c221af7)
2025-10-15 23:49:45 - INFO - ✅ [DOGEUSDT] State restored successfully from exchange
```

**Key Indicators:**
- ✅ Position synced / restored
- ✅ TP order created
- ✅ Quantity validation passed
- ✅ State restored successfully

---

### Common Issues

#### Issue 1: Emergency Stop After 3 Retries

**Symptoms:**
```
❌ Failed to restore state after 3 attempts due to continuous position changes
```

**Causes:**
- Manual trading during bot startup
- Another bot using same account
- Very active market with frequent TP triggers

**Resolution:**
1. Stop all manual trading
2. Stop other bots on same account
3. Wait for calm market
4. Remove emergency flag: `rm data/.00X_emergency_stop`
5. Restart bot

---

#### Issue 2: Insufficient Balance

**Symptoms:**
```
❌ Insufficient balance to start trading: need $1.00 MARGIN, available $0.50
```

**Causes:**
- Depleted balance from losses
- Open positions on other symbols using margin
- Pending orders locking funds

**Resolution:**
1. Check total balance: `python scripts/check_balance.py`
2. Close other positions OR deposit funds
3. Remove emergency flag: `rm data/.00X_emergency_stop`
4. Restart bot

---

#### Issue 3: No Order History

**Symptoms:**
```
❌ No order history available - cannot restore Buy position
```

**Causes:**
- New account with no trading history
- Order history expired (Bybit keeps ~7 days)
- API issue

**Resolution:**
1. Check order history manually on exchange
2. If no history: Close position manually on exchange
3. Remove emergency flag and state file: `rm data/.00X_emergency_stop data/00X_bot_state.json`
4. Restart bot (will open fresh positions)

---

#### Issue 4: Quantity Mismatch

**Symptoms:**
```
❌ Position mismatch for Buy requires manual intervention: exchange=22932.0, local=22568.0, diff=364.000000
```

**Causes:**
- Partial position close (not supported)
- Manual order placement outside bot
- Exchange API lag

**Resolution:**
1. Check positions on exchange manually
2. **Option A:** Close all positions, remove flag, restart
3. **Option B:** If you know issue, manually fix local state:
   - Edit `data/00X_bot_state.json`
   - Ensure quantities match exchange
   - Remove emergency flag
   - Restart bot

---

#### Issue 5: Restored Less Than Exchange

**Symptoms:**
```
❌ Restored 364.000000 LESS than exchange: restored=22568.0, exchange=22932.0, missing=364.000000
```

**Causes:**
- Position opened during order history fetch (race condition)
- Order history truncated (> 200 orders between last TP and now)

**Resolution:**
- Bot automatically retries (up to 3 attempts)
- If all retries fail → see "Emergency Stop After 3 Retries" above

---

### Diagnostic Commands

**1. Check Emergency Stop Flag:**
```bash
cat data/.001_emergency_stop | python3 -m json.tool
```

**2. Check Bot State:**
```bash
cat data/001_bot_state.json | python3 -m json.tool
```

**3. Check Positions on Exchange:**
```bash
python scripts/check_balance.py
```

**4. View Restoration Logs:**
```bash
grep "Restoring state\|restored\|RESTORING" logs/001_bot_$(date +%Y-%m-%d).log
```

**5. View Error Logs:**
```bash
grep -i "ERROR\|CRITICAL" logs/001_bot_$(date +%Y-%m-%d).log | tail -50
```

---

## Best Practices

### 1. Clean Startup

**Do:**
- Ensure no emergency stop flags before starting
- Verify positions on exchange before restart
- Let bot restore cleanly (no manual trading during startup)

**Don't:**
- Start bot while manually trading
- Run multiple bots on same account simultaneously
- Restart bot repeatedly without fixing issues

---

### 2. Emergency Stop Handling

**Do:**
- Read emergency stop file for diagnostics
- Fix root cause before removing flag
- Keep emergency stop files for debugging (rename if needed)

**Don't:**
- Blindly remove emergency flags without reading
- Restart bot without fixing issue (will fail again)
- Delete emergency flags before investigating

---

### 3. Order History Awareness

**Do:**
- Understand bot can restore from ~200 filled orders
- If position has > 200 grid levels → close and restart
- Monitor "Retrieved exactly 200 orders" warnings

**Don't:**
- Let positions grow beyond history depth
- Ignore truncation warnings
- Use bot for ultra-long-term positions (> 7 days without TP)

---

### 4. State File Management

**Do:**
- Let bot manage state files automatically
- Review state files for debugging
- Backup state files before manual edits

**Don't:**
- Manually edit state files unless necessary
- Delete state files while bot is running
- Share state files between accounts

---

### 5. Multi-Account Coordination

**Do:**
- Use separate API keys for each account
- Isolate accounts to separate symbols
- Monitor all account emergency flags

**Don't:**
- Use same API keys for multiple bot instances
- Trade same symbol on multiple accounts (unless intentional)
- Ignore errors on one account while others run

---

### 6. Monitoring

**Do:**
- Watch logs during startup for restoration progress
- Set up alerts for emergency stops
- Regularly check bot state matches exchange

**Don't:**
- Assume silent startup = successful restore
- Ignore restoration warnings
- Let bot run unmonitored after restart

---

### 7. Manual Intervention

**Do:**
- Stop bot before manual trading
- Document any manual changes
- Verify state after manual intervention

**Don't:**
- Trade manually while bot is running
- Partially close positions (bot expects full close)
- Open positions outside bot tracking

---

## Summary

Position restoration is a **critical safety mechanism** that ensures data integrity after bot restarts. Key takeaways:

1. **REST API = Source of Truth** at startup
2. **Four Scenarios** handled with fail-fast approach
3. **Grid Level Restoration** from order history (200 orders)
4. **Retry Mechanism** handles WebSocket interruptions (3 attempts)
5. **Force Cancel Mode** ensures clean TP order state
6. **Emergency Stops** prevent dangerous trading with bad data
7. **Periodic Sync** catches untracked closes during runtime
8. **Fail-Fast Philosophy** prioritizes safety over availability

**Golden Rule:** If bot can't verify data from exchange → STOP. Better safe than sorry!

---

**Document Version:** 1.0
**Last Updated:** 2025-10-15
**Author:** SOL-Trader Development Team
