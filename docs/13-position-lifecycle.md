# Position Lifecycle & Pending Entry Orders

## Table of Contents
1. [Overview](#overview)
2. [Core Concepts](#core-concepts)
3. [Position Opening](#position-opening)
4. [Pending Entry Orders (Insurance)](#pending-entry-orders-insurance)
5. [Position Averaging](#position-averaging)
6. [Position Closing (Take Profit)](#position-closing-take-profit)
7. [WebSocket Event Handlers](#websocket-event-handlers)
8. [Balance Checks & Safety](#balance-checks--safety)
9. [Multi-Symbol Thread Safety](#multi-symbol-thread-safety)
10. [Edge Cases & Recovery](#edge-cases--recovery)
11. [Complete Lifecycle Examples](#complete-lifecycle-examples)
12. [Best Practices](#best-practices)

---

## Overview

This document describes the **complete lifecycle** of trading positions in SOL-Trader bot, including the critical **Pending Entry Orders** mechanism that ensures position symmetry and balance safety.

### Key Innovation: Pending Entry Orders

**Problem:** In a dual-sided hedge strategy (simultaneous LONG + SHORT), if one side closes via Take Profit, we need to ensure we have **reserved funds** to rebalance positions later.

**Solution:** **Pending limit orders** placed on the opposite side to **reserve balance** on exchange.

**Example:**
```
SHORT: Levels 0+1+2+3+4 (5 real positions)
LONG:  Levels 0+1+2 (3 real positions) + Pending on 3+4 (2 reserved)
       └─ Real positions ─┘              └─ Insurance pending ─┘
```

**Benefits:**
- ✅ **Guaranteed balance** for position symmetry
- ✅ **Protection** from multi-symbol race conditions
- ✅ **Automatic rebalancing** when price moves
- ✅ **Fail-safe** against balance exhaustion

---

## Core Concepts

### 1. Position Symmetry

**Rule:** Total **invested margin** on each grid level must be **equal or reserved** on both sides.

**Why?** In panic/emergency situations, bot must be able to **immediately balance** positions to reduce risk.

**Implementation:**
- If SHORT has 5 levels: $1 + $2 + $4 + $8 + $16 = **$31 total**
- LONG must have **same $31** either in:
  - Real positions (levels 0+1+2+3+4)
  - OR Real + Pending (levels 0+1+2 + pending on 3+4)

### 2. Reference Qty (Perfect Hedging)

**Problem:** When LONG opens @ $0.21 and SHORT opens @ $0.20, same margin gives **different qty**:
```
LONG:  $1 margin × 75x leverage = $75 / $0.21 = 357 coins
SHORT: $1 margin × 75x leverage = $75 / $0.20 = 375 coins
❌ 357 ≠ 375 - NOT perfect hedging!
```

**Solution:** **Reference Qty Per Level** - first side to open a level sets qty as reference, second side uses SAME qty.

**How it works:**
```python
# LONG opens level 0 first @ $0.21
qty = ($1 × 75) / $0.21 = 357 coins
reference_qty[0] = 357  # Save as reference

# SHORT opens level 0 @ $0.20
qty = reference_qty[0]  # Use saved reference = 357 coins
margin = (357 × $0.20) / 75 = $0.95  # Slightly different margin

✅ 357 == 357 - PERFECT SYMMETRY!
```

**Benefits:**
- ✅ **Perfect hedging:** Same qty on both sides = equal P&L on price moves
- ✅ **Weighted average works correctly:** TP triggers at right price
- ✅ **Margin difference small:** ~3% max (= grid_step_percent)
- ✅ **Persistent:** Reference survives bot restart via position restoration

**Implementation:**
- `_reference_qty_per_level = {grid_level: qty}` stored in memory
- All qty calculations use `_get_qty_for_level()` method
- Restored from exchange position history on bot restart
- Thread-safe with `_reference_qty_lock`

### 3. Minus Two Steps Rule

When one side closes via TP and reopens:

**Formula:** Reopen on `opposite_max_level - 2` levels

**Example:**
- SHORT on level 5 → has levels 0+1+2+3+4 (5 levels)
- LONG closes via TP
- LONG reopens on levels 0+1+2 (3 levels) = `5 - 2 = 3` levels
- **Reserve:** Levels 3+4 kept free for emergency balancing

**With Pending:**
- LONG opens: 0+1+2 (real positions)
- LONG pending: 3+4 (reserved on exchange)
- **Result:** Full symmetry maintained!

### 4. Balance Buffer

**Configuration:** `balance_buffer_percent = 15.0%` (default)

**Rule:** ALL balance checks multiply required margin by **1.15x**

**Example:**
```python
# Need $10 for averaging
buffer_multiplier = 1 + (15.0 / 100.0)  # 1.15
required_with_buffer = $10 * 1.15 = $11.50

if available_balance >= $11.50:
    # Safe to proceed
```

**Why?**
- Protects against fees eating into margin
- Safety cushion for price slippage
- Prevents balance race conditions

### 5. Thread-Safe Multi-Symbol Operations

**Problem:** Multiple symbols checking balance simultaneously

**Solution:** `_balance_operation_lock` in TradingAccount

```python
# In TradingAccount
with self._balance_operation_lock:
    available = balance_manager.get_available_balance()
    if available >= required:
        return True  # Atomic check
```

**Prevents:**
- Symbol A checks: $100 available ✅
- Symbol B checks: $100 available ✅ (same time!)
- Both try to use $80 → **FAIL** ❌

**With lock:**
- Symbol A locks → checks → reserves → unlocks
- Symbol B waits → checks remaining balance → proceeds safely ✅

---

## Position Opening

### Opening Types

1. **Initial Position** - First position on bot startup
2. **Reopen After TP** - Position closes via TP, reopens immediately
3. **Recovery Reopen** - Position missing, detected in sync

### Method: `_open_initial_position()`

**Signature:**
```python
def _open_initial_position(
    side: str,              # 'Buy' or 'Sell'
    current_price: float,
    custom_margin_usd: float = None  # Optional adaptive margin
) -> bool:  # Returns True if successful, False if failed
```

**Process Flow:**

```
┌──────────────────────────────────────┐
│ 1. Determine target margin           │
│    - Use custom_margin if provided   │
│    - Otherwise use initial_size_usd  │
└─────────────┬────────────────────────┘
              ▼
┌──────────────────────────────────────┐
│ 2. Check reserve (if TradingAccount) │
│    - Account-level safety check      │
│    - With balance_buffer_percent     │
└─────────────┬────────────────────────┘
              ▼
       ┌──────┴──────┐
       │   Failed?   │
       └──┬───────┬──┘
      YES │       │ NO
          ▼       ▼
     ┌────────┐  ┌──────────────────────┐
     │FALLBACK│  │ 3. Calculate levels  │
     │ to     │  │    needed for margin │
     │initial │  └─────────┬────────────┘
     │ size   │            ▼
     └────┬───┘  ┌──────────────────────┐
          │      │ 4. Open each level   │
          └─────>│    separately        │
                 │    (limit → market)  │
                 └─────────┬────────────┘
                           ▼
                 ┌──────────────────────┐
                 │ 5. Track in PM       │
                 │    (grid_level set!) │
                 └─────────┬────────────┘
                           ▼
                 ┌──────────────────────┐
                 │ 6. Create TP order   │
                 └─────────┬────────────┘
                           ▼
                 ┌──────────────────────┐
                 │ 7. Place PENDING for │
                 │    symmetry          │
                 └──────────────────────┘
```

### Example: Initial Position Opening

**Scenario:** Bot starts, no positions exist

**Code Execution:**
```python
# In main.py::initialize()
success = grid_strategy._open_initial_position(
    side='Buy',
    current_price=0.2061
)
```

**Step-by-Step:**

1. **Target Margin:** `$1.00` (initial_size_usd)

2. **Reserve Check:**
   ```python
   if trading_account.check_reserve_before_averaging(
       symbol='DOGEUSDT',
       side='Buy',
       next_averaging_margin=$1.00
   ):
       # PASS - sufficient reserve for balancing
   ```

3. **Calculate Levels:**
   ```python
   levels_to_open = [0]  # Just level 0 for $1.00
   ```

4. **Open Position:**
   ```python
   # Level 0: $1.00 margin
   qty = $1.00 * 75 / $0.2061 = 364 DOGE

   order_id = limit_order_manager.place_limit_order(
       side='Buy',
       qty=364,
       current_price=0.2061,
       reason="Initial position level 0"
   )
   ```

5. **Track Position:**
   ```python
   pm.add_position(
       side='Buy',
       entry_price=0.2061,
       quantity=364,
       grid_level=0,  # CRITICAL!
       order_id=order_id
   )
   ```

6. **Create TP:**
   ```python
   tp_price = calculate_honest_tp_price('Buy', 0.2061)
   # TP at 0.2082 (+1% profit + fees)

   tp_id = client.place_tp_order(
       side='Sell',  # Close Buy with Sell
       qty=364,
       tp_price=0.2082
   )
   ```

7. **Place Pending (if opposite exists):**
   ```python
   # No opposite position yet - skip pending
   placed_count = _place_pending_for_symmetry('Buy', 0.2061)
   # Returns 0 - no pending needed
   ```

**Log Output:**
```
2025-10-16 10:20:15 - INFO - 🆕 [DOGEUSDT] Opening Buy position: $1.00 margin in 1 parts (levels [0])
2025-10-16 10:20:15 - INFO -   Level 0: $1.00 margin (364.0 DOGEUSDT)
2025-10-16 10:20:16 - INFO - ✅ [DOGEUSDT] Opened Buy: 1 levels, total $1.00 margin
2025-10-16 10:20:16 - INFO - ✅ [DOGEUSDT] TP order created: Sell 364.0 @ $0.2082 (avg entry: $0.2061, ID: abc123)
```

---

### Example: Reopen After TP (Adaptive Sizing)

**Scenario:**
- SHORT on level 5 (levels 0+1+2+3+4)
- LONG closes via TP
- Reopen LONG with "minus two steps" = levels 0+1+2

**Code Execution:**
```python
# In on_execution() - TP close detected
opposite_side = 'Sell'  # SHORT
reopen_margin = calculate_reopen_size('Buy', opposite_side)
# Returns: $1 + $2 + $4 = $7.00

success = _open_initial_position(
    side='Buy',
    current_price=0.2100,
    custom_margin_usd=$7.00  # Adaptive!
)
```

**Process:**

1. **Target Margin:** `$7.00` (adaptive)

2. **Reserve Check:** Passes (SHORT exists, can balance)

3. **Calculate Levels:**
   ```python
   levels_to_open = [0, 1, 2]  # For $7.00 total
   # Level 0: $1.00
   # Level 1: $2.00
   # Level 2: $4.00
   # Total:   $7.00 ✅
   ```

4. **Open Each Level:**
   ```python
   # Level 0
   pm.add_position('Buy', 0.2100, qty=357, grid_level=0)

   # Level 1
   pm.add_position('Buy', 0.2100, qty=714, grid_level=1)

   # Level 2
   pm.add_position('Buy', 0.2100, qty=1428, grid_level=2)
   ```

5. **Create TP:**
   ```python
   # Weighted average entry = $0.2100
   # TP at $0.2123 (3 positions = 3 fees to cover)
   ```

6. **🔑 Place Pending for Symmetry:**
   ```python
   # SHORT has levels 0+1+2+3+4 (max = 4)
   # LONG has levels 0+1+2 (max = 2)
   # Missing: levels 3+4

   place_pending_entry_order('Buy', level=3, base_price=0.2100)
   # → Limit Buy @ $0.2037 (3% below) for $8 margin

   place_pending_entry_order('Buy', level=4, base_price=0.2100)
   # → Limit Buy @ $0.1974 (4% below) for $16 margin
   ```

**Result:**
```
LONG: [0][1][2] (real) + [3][4] (pending)
       └── $7 ──┘         └── $24 ──┘
SHORT: [0][1][2][3][4] (all real)
       └──── $31 ─────┘

✅ Full symmetry: LONG has $31 reserved (real + pending)
```

**Log Output:**
```
2025-10-16 10:25:30 - INFO - 🆕 [DOGEUSDT] Opening Buy position: $7.00 margin in 3 parts (levels [0, 1, 2])
2025-10-16 10:25:30 - INFO -   Level 0: $1.00 margin (357.0 DOGEUSDT)
2025-10-16 10:25:30 - INFO -   Level 1: $2.00 margin (714.0 DOGEUSDT)
2025-10-16 10:25:30 - INFO -   Level 2: $4.00 margin (1428.0 DOGEUSDT)
2025-10-16 10:25:31 - INFO - ✅ [DOGEUSDT] Opened Buy: 3 levels, total $7.00 margin
2025-10-16 10:25:31 - INFO - 📋 [DOGEUSDT] Pending entry placed: Buy level 3 @ $0.2037 (margin=$8.00, ID: def456)
2025-10-16 10:25:31 - INFO - 📋 [DOGEUSDT] Pending entry placed: Buy level 4 @ $0.1974 (margin=$16.00, ID: ghi789)
2025-10-16 10:25:31 - INFO - ✅ [DOGEUSDT] Placed 2 pending entries for Buy (levels [3, 4]) to match opposite side level 4
2025-10-16 10:25:31 - INFO - ✅ [DOGEUSDT] Symmetry ensured: placed 2 pending entries for Buy
```

---

## Pending Entry Orders (Insurance)

### Purpose

**Reserve balance** on exchange to guarantee position symmetry.

### When Placed

1. **After position opening** - if opposite side has more levels
2. **After averaging** - immediately after adding to one side
3. **During sync** - if pending missing/cancelled

### Method: `place_pending_entry_order()`

**Signature:**
```python
def place_pending_entry_order(
    side: str,           # 'Buy' or 'Sell'
    grid_level: int,     # 3, 4, 5, ...
    base_price: float    # Current price to calculate from
) -> Optional[str]:      # Returns order_id or None
```

**Price Calculation:**

```python
# For LONG (Buy)
entry_price = base_price * (1 - (grid_step_pct / 100) * grid_level)

# For SHORT (Sell)
entry_price = base_price * (1 + (grid_step_pct / 100) * grid_level)
```

**Example:**
```python
# Placing pending LONG level 3
# Base price: $0.2100
# Grid step: 1.0%

entry_price = $0.2100 * (1 - 0.01 * 3)
            = $0.2100 * 0.97
            = $0.2037

# Order: Buy limit @ $0.2037 for qty=XXX
```

### Method: `_place_pending_for_symmetry()`

**Signature:**
```python
def _place_pending_for_symmetry(
    opened_side: str,     # Side that just opened
    base_price: float
) -> int:                 # Returns count of placed pending
```

**Logic:**

```python
# 1. Find max level on opposite side
opposite_positions = pm.long_positions if opposite_side == 'Buy' else pm.short_positions
if not opposite_positions:
    return 0  # No opposite - no pending needed

max_opposite_level = max(pos.grid_level for pos in opposite_positions)

# 2. Find max level on opened side
opened_positions = pm.long_positions if opened_side == 'Buy' else pm.short_positions
current_max_level = max(pos.grid_level for pos in opened_positions) if opened_positions else 0

# 3. Calculate missing levels
missing_levels = list(range(current_max_level + 1, max_opposite_level + 1))

if not missing_levels:
    return 0  # Already symmetric

# 4. CRITICAL: Filter out levels that already have pending orders
# This prevents duplicate pending orders on periodic sync
with self._pending_entry_lock:
    existing_pending_levels = set(self._pending_entry_orders[opened_side].keys())

levels_to_place = [level for level in missing_levels if level not in existing_pending_levels]

if not levels_to_place:
    # All missing levels already have pending orders
    self.logger.debug(
        f"[{self.symbol}] All missing levels {missing_levels} for {opened_side} "
        f"already have pending orders - skipping"
    )
    return 0

# 5. Check balance for pending orders we're about to place
total_pending_margin = sum(
    initial_size_usd * (multiplier ** level)
    for level in levels_to_place
)

buffer_multiplier = 1 + (balance_buffer_percent / 100.0)
required_with_buffer = total_pending_margin * buffer_multiplier

if available_balance < required_with_buffer:
    # Insufficient balance - cannot place pending
    return 0

# 6. Place each pending (only new levels)
for level in levels_to_place:
    order_id = place_pending_entry_order(opened_side, level, base_price)
```

### Tracking Pending Orders

**Data Structure:**
```python
# In GridStrategy
self._pending_entry_orders = {
    'Buy': {
        3: 'order_id_abc123',
        4: 'order_id_def456'
    },
    'Sell': {}
}

self._pending_entry_lock = threading.Lock()  # Thread safety
```

**Accessing:**
```python
# Thread-safe check
with self._pending_entry_lock:
    buy_pending = self._pending_entry_orders['Buy']
    if 3 in buy_pending:
        order_id = buy_pending[3]
```

### Cancelling Pending

**When:**
1. Position closes via TP → cancel ALL pending for that side
2. Bot restart → cancel ALL pending (first sync)
3. Manual recovery scenarios

**Method:**
```python
def _cancel_all_pending_entries(side: str):
    with self._pending_entry_lock:
        pending_orders = self._pending_entry_orders[side].copy()

    for level, order_id in pending_orders.items():
        try:
            client.cancel_order(symbol, order_id, category)
            logger.info(f"🗑️ Cancelled pending entry: {side} level {level}")
        except Exception as e:
            logger.warning(f"Failed to cancel {order_id}: {e}")

    # Clear tracking
    with self._pending_entry_lock:
        self._pending_entry_orders[side] = {}
```

---

## Position Averaging

### When Averaging Happens

**Trigger:** Price moves `grid_step_percent` against position

**Example:**
- LONG last entry: $0.2100
- Grid step: 1.0%
- Price drops to $0.2079 (1% down)
- **→ Add to LONG position**

### Method: `_execute_grid_order()`

**NEW LOGIC WITH PENDING:**

```
┌──────────────────────────────────────┐
│ 1. Calculate next level margin      │
│    (martingale: last * multiplier)  │
└─────────────┬────────────────────────┘
              ▼
┌──────────────────────────────────────┐
│ 2. CRITICAL CHECK:                   │
│    Can we afford BOTH:               │
│    - Averaging current side          │
│    - Pending on opposite side        │
│    Total = margin * 2 * 1.15         │
└─────────────┬────────────────────────┘
              ▼
       ┌──────┴──────┐
       │ Sufficient? │
       └──┬───────┬──┘
      NO  │       │ YES
          ▼       ▼
     ┌────────┐  ┌──────────────────────┐
     │ SKIP   │  │ 3. Place averaging   │
     │averaging│  │    order (current)   │
     └────────┘  └─────────┬────────────┘
                           ▼
                 ┌──────────────────────┐
                 │ 4. Track in PM       │
                 │    (grid_level++)    │
                 └─────────┬────────────┘
                           ▼
                 ┌──────────────────────┐
                 │ 5. Place PENDING on  │
                 │    opposite side     │
                 │    (same level)      │
                 └─────────┬────────────┘
                           ▼
                 ┌──────────────────────┐
                 │ 6. Update TP order   │
                 │    (new avg entry)   │
                 └──────────────────────┘
```

**Code:**

```python
def _execute_grid_order(self, side: str, current_price: float):
    # Calculate margin for next level
    grid_level = self.pm.get_position_count(side)
    positions = self.pm.long_positions if side == 'Buy' else self.pm.short_positions
    last_position = positions[-1]

    last_position_margin = (last_position.quantity * current_price) / leverage
    new_margin_usd = last_position_margin * self.multiplier

    opposite_side = 'Sell' if side == 'Buy' else 'Buy'

    # CRITICAL: Check balance for BOTH directions
    total_margin_needed = new_margin_usd * 2  # Averaging + Pending

    if trading_account:
        # Thread-safe multi-symbol check
        if not trading_account.check_and_reserve_balance(
            symbol=self.symbol,
            margin_needed=total_margin_needed  # Multiplied by 1.15 inside!
        ):
            logger.warning(
                f"⚠️ Skipping {side} averaging to level {grid_level}: "
                f"insufficient balance for both sides"
            )
            return  # DO NOT AVERAGE!

        # Also check reserve for balancing
        if not trading_account.check_reserve_before_averaging(
            symbol=self.symbol,
            side=side,
            next_averaging_margin=new_margin_usd
        ):
            return  # Reserve check failed

    # Place averaging order
    new_size = self._usd_to_qty(new_margin_usd, current_price)

    order_id = limit_order_manager.place_limit_order(
        side=side,
        qty=new_size,
        current_price=current_price,
        reason=f"Grid level {grid_level}"
    )

    # Track position
    pm.add_position(
        side=side,
        entry_price=current_price,
        quantity=new_size,
        grid_level=grid_level,
        order_id=order_id
    )

    # CRITICAL: Place pending on opposite side for this same level
    pending_id = self.place_pending_entry_order(
        side=opposite_side,
        grid_level=grid_level,
        base_price=current_price
    )

    if not pending_id and not self.dry_run:
        logger.warning(
            f"⚠️ {side} averaged to level {grid_level}, "
            f"but failed to place pending on {opposite_side}. "
            f"Will retry in next sync."
        )

    # Update TP
    self._update_tp_order(side)
```

### Example: Averaging with Pending

**Initial State:**
```
LONG:  [0][1] (2 levels) @ avg $0.2100
SHORT: [0][1] (2 levels) @ avg $0.2000
```

**Trigger:** Price drops to $0.2079 (1% below LONG's last entry)

**Execution:**

1. **Calculate Margin:**
   ```python
   last_margin = ($1428 * 0.2079) / 75 = $3.96
   new_margin = $3.96 * 2.0 = $7.92  # Level 2
   ```

2. **Check Balance (BOTH SIDES!):**
   ```python
   total_needed = $7.92 * 2 = $15.84  # LONG + SHORT pending
   with_buffer = $15.84 * 1.15 = $18.22

   available = $250.00

   if $250.00 >= $18.22:  # PASS ✅
   ```

3. **Place LONG Averaging:**
   ```python
   qty = ($7.92 * 75) / $0.2079 = 2856 DOGE

   order_id = place_order('Buy', qty=2856, price=0.2079)
   pm.add_position('Buy', 0.2079, 2856, grid_level=2)
   ```

4. **Place SHORT Pending:**
   ```python
   # SHORT pending level 2
   pending_price = $0.2000 * (1 + 0.01 * 2) = $0.2040

   pending_id = place_order(
       side='Sell',
       qty=...,
       price=$0.2040,
       order_type='Limit'
   )

   _pending_entry_orders['Sell'][2] = pending_id
   ```

5. **Update TP:**
   ```python
   # New LONG average: (357*0.2100 + 714*0.2100 + 2856*0.2079) / 3927
   # TP price recalculated
   ```

**Result:**
```
LONG:  [0][1][2] (3 real)
SHORT: [0][1] (2 real) + [2] (1 pending)

✅ Symmetry maintained!
```

**Log Output:**
```
2025-10-16 10:30:45 - INFO - [DOGEUSDT] Executing grid order: Buy $7.92 MARGIN (2856.0) @ $0.2079 (level 2)
2025-10-16 10:30:46 - INFO - 📋 [DOGEUSDT] Pending entry placed: Sell level 2 @ $0.2040 (margin=$7.92, ID: jkl012)
2025-10-16 10:30:46 - INFO - ✅ [DOGEUSDT] TP order created: Sell 3927.0 @ $0.2110 (avg entry: $0.2085, ID: mno345)
```

---

## Position Closing (Take Profit)

### Trigger

**Condition:** Weighted average entry price shows `take_profit_percent` gain

**Example:**
- LONG average entry: $0.2085
- TP: 1.0% + fees
- Price rises to $0.2110
- **→ TP fills, closes ALL LONG positions**

### What Happens

1. **Execution WebSocket** reports TP fill
2. **on_execution()** handler detects full close
3. **Remove ALL positions** from PositionManager
4. **🔑 Cancel ALL pending** for this side
5. **Calculate adaptive reopen size** (minus two steps)
6. **Reopen position** with new pending

### Code Flow in `on_execution()`

```python
def on_execution(self, exec_data: dict):
    # Detect position close
    if closed_qty_from_this_exec >= total_qty - 0.001:
        # FULL CLOSE detected!

        # 1. Remove positions from PM
        pm.remove_all_positions(closed_position_side)
        pm.set_tp_order_id(closed_position_side, None)

        # 2. CRITICAL: Cancel all pending for this side
        self._cancel_all_pending_entries(closed_position_side)

        # 3. Calculate adaptive reopen
        opposite_side = 'Sell' if closed_position_side == 'Buy' else 'Buy'
        reopen_margin = calculate_reopen_size(closed_position_side, opposite_side)

        # 4. Reopen with retry (3 attempts)
        max_retries = 3
        for attempt in range(max_retries):
            success = _open_initial_position(
                side=closed_position_side,
                current_price=current_price,
                custom_margin_usd=reopen_margin
            )

            if success:
                break  # Reopened successfully!

            # Retry with exponential backoff...

        # 5. If all failed, try fallback with initial_size
        if not success:
            fallback_success = _open_initial_position(
                side=closed_position_side,
                current_price=current_price,
                custom_margin_usd=self.initial_size_usd
            )

        # 6. Track failed reopens for recovery
        if not success:
            self._failed_reopen_sides.add(closed_position_side)
```

### Example: TP Close → Reopen Cycle

**State Before TP:**
```
LONG:  [0][1][2] @ avg $0.2085, TP @ $0.2110
SHORT: [0][1][2][3][4] @ avg $0.1995
       + LONG pending [3][4]
```

**Event:** Price → $0.2110, TP fills

**Execution:**

1. **WebSocket:** Execution event
   ```python
   {
       'execType': 'Trade',
       'side': 'Sell',  # Closing Buy position
       'closedSize': '3927.0',
       'execPnl': '+8.45'  # Profit!
   }
   ```

2. **Detect Close:**
   ```python
   closed_position_side = 'Buy'  # LONG closed
   ```

3. **Cancel Pending:**
   ```python
   _cancel_all_pending_entries('Buy')
   # Cancels pending [3][4] for LONG
   ```

4. **Adaptive Reopen:**
   ```python
   # SHORT has 5 levels (0+1+2+3+4)
   # Reopen LONG on 3 levels (minus two)
   reopen_margin = $1 + $2 + $4 = $7.00

   _open_initial_position('Buy', $0.2110, $7.00)
   # Opens LONG [0][1][2]
   ```

5. **Place NEW Pending:**
   ```python
   # SHORT still on level 4
   # LONG now on level 2
   # Place pending [3][4] for LONG

   _place_pending_for_symmetry('Buy', $0.2110)
   ```

**State After Reopen:**
```
LONG:  [0][1][2] @ avg $0.2110 (NEW!)
       + NEW pending [3][4]
SHORT: [0][1][2][3][4] @ avg $0.1995 (unchanged)

✅ Cycle complete, symmetry restored!
```

**Log Output:**
```
2025-10-16 10:35:20 - INFO - 💰 [DOGEUSDT] Buy position CLOSED via TP: closed=3927.0, PnL=+$8.45, reason=TAKE_PROFIT
2025-10-16 10:35:20 - INFO - [DOGEUSDT] 🗑️ Cancelled pending entry: Buy level 3 (ID=def456)
2025-10-16 10:35:20 - INFO - [DOGEUSDT] 🗑️ Cancelled pending entry: Buy level 4 (ID=ghi789)
2025-10-16 10:35:20 - INFO - 🆕 [DOGEUSDT] ADAPTIVE REOPEN: Buy with $7.00 margin after TP (attempt 1/3)
2025-10-16 10:35:21 - INFO - 🆕 [DOGEUSDT] Opening Buy position: $7.00 margin in 3 parts (levels [0, 1, 2])
2025-10-16 10:35:22 - INFO - ✅ [DOGEUSDT] Opened Buy: 3 levels, total $7.00 margin
2025-10-16 10:35:22 - INFO - 📋 [DOGEUSDT] Pending entry placed: Buy level 3 @ $0.2057 (margin=$8.00, ID: pqr678)
2025-10-16 10:35:22 - INFO - 📋 [DOGEUSDT] Pending entry placed: Buy level 4 @ $0.2005 (margin=$16.00, ID: stu901)
2025-10-16 10:35:22 - INFO - ✅ [DOGEUSDT] Symmetry ensured: placed 2 pending entries for Buy
```

---

## WebSocket Event Handlers

### 1. Order Update Handler: `on_order_update()`

**Purpose:** Track pending entry order lifecycle

**Events Handled:**
- **"Filled"** → Add position to PM
- **"PartiallyFilled"** → Log only (wait for full)
- **"Cancelled"** → Auto-retry

**Code:**

```python
def on_order_update(self, order_data: dict):
    order_id = order_data.get('orderId')
    order_status = order_data.get('orderStatus')
    side = order_data.get('side')
    reduce_only = order_data.get('reduceOnly', False)

    # Track pending ENTRY orders (NOT reduceOnly)
    if not reduce_only and order_type == 'Limit' and order_status in ['Filled', 'PartiallyFilled', 'Cancelled']:
        # Check if this is our tracked pending
        grid_level = None

        with self._pending_entry_lock:
            for level, oid in self._pending_entry_orders[side].items():
                if oid == order_id:
                    grid_level = level
                    break

        if grid_level is not None:
            # This is our pending entry order

            if order_status == 'Filled':
                # FULLY filled - add to PM
                qty = float(order_data.get('qty', 0))
                avg_price = float(order_data.get('avgPrice', 0))

                # Orphan position check
                current_positions = (pm.long_positions if side == 'Buy' else pm.short_positions)

                if not current_positions:
                    logger.warning(
                        f"⚠️ Pending entry filled AFTER position closed! "
                        f"{side} level {grid_level} filled but position already closed. "
                        f"Adding orphan position - will be closed by TP or emergency."
                    )

                # Add to PM
                pm.add_position(
                    side=side,
                    entry_price=avg_price,
                    quantity=qty,
                    grid_level=grid_level,
                    order_id=order_id
                )

                logger.info(
                    f"✅ Pending entry FILLED: {side} level {grid_level} "
                    f"@ ${avg_price:.4f} (qty={qty:.4f})"
                )

                # Remove from pending tracking
                with self._pending_entry_lock:
                    del self._pending_entry_orders[side][grid_level]

                # Update TP
                self._update_tp_order(side)

            elif order_status == 'PartiallyFilled':
                # Partial - log but wait
                filled_qty = float(order_data.get('cumExecQty', 0))
                total_qty = float(order_data.get('qty', 0))
                fill_percent = (filled_qty / total_qty) * 100

                logger.info(
                    f"📊 Pending entry partial fill: {side} level {grid_level} "
                    f"{fill_percent:.1f}% filled ({filled_qty:.4f}/{total_qty:.4f})"
                )

            elif order_status == 'Cancelled':
                # AUTO-RETRY (Critical!)
                logger.warning(
                    f"⚠️ Pending entry cancelled by exchange: "
                    f"{side} level {grid_level}, AUTO-RETRYING..."
                )

                # Remove from tracking
                with self._pending_entry_lock:
                    del self._pending_entry_orders[side][grid_level]

                # Re-place with current price
                retry_order_id = self.place_pending_entry_order(
                    side=side,
                    grid_level=grid_level,
                    base_price=self.current_price
                )

                if retry_order_id:
                    logger.info(f"✅ Pending entry re-placed: {side} level {grid_level}")
                else:
                    logger.error(f"❌ Failed to re-place pending: {side} level {grid_level}")
```

### 2. Execution Handler: Already Covered

See "Position Closing" section above.

---

## Balance Checks & Safety

### Three-Layer Safety System

#### Layer 1: Simple Balance Check
```python
# Fallback for tests/standalone
available = balance_manager.get_available_balance()

if margin_needed > available:
    return False  # Skip operation
```

#### Layer 2: Reserve Check (Account-Level)
```python
# In TradingAccount.check_reserve_before_averaging()

# Check if we can balance positions AFTER this averaging
# Accounts for ALL symbols + buffer
if trading_account:
    if not trading_account.check_reserve_before_averaging(
        symbol=self.symbol,
        side=side,
        next_averaging_margin=margin
    ):
        return False  # Skip - cannot balance later
```

**Formula:**
```python
# After averaging, calculate imbalance
imbalance_qty_after = abs(long_qty_after - short_qty_after)

# Cost to balance (buy/sell imbalance qty)
cost_to_balance = (imbalance_qty * weighted_price) / leverage

# Apply buffer
buffer_multiplier = 1 + (balance_buffer_percent / 100.0)  # 1.15
cost_with_buffer = cost_to_balance * buffer_multiplier

# Check
if available_after >= cost_with_buffer:
    return True  # SAFE to average
```

#### Layer 3: Multi-Symbol Atomic Check
```python
# In TradingAccount.check_and_reserve_balance()

with self._balance_operation_lock:  # ATOMIC!
    available = balance_manager.get_available_balance()
    buffer_multiplier = 1 + (balance_buffer_percent / 100.0)
    required = margin_needed * buffer_multiplier

    if available >= required:
        return True  # Reserved atomically
```

### Combined Usage

```python
def _execute_grid_order(side, current_price):
    # Calculate margin
    new_margin = calculate_next_level_margin()
    total_needed = new_margin * 2  # Current + Pending on opposite

    # Layer 3: Multi-symbol atomic check
    if trading_account:
        if not trading_account.check_and_reserve_balance(
            symbol=self.symbol,
            margin_needed=total_needed
        ):
            return  # BLOCKED - insufficient for both sides

        # Layer 2: Reserve check for balancing
        if not trading_account.check_reserve_before_averaging(
            symbol=self.symbol,
            side=side,
            next_averaging_margin=new_margin
        ):
            return  # BLOCKED - cannot balance later
    else:
        # Layer 1: Simple fallback
        if available < (total_needed * 1.15):
            return  # BLOCKED

    # All checks passed - safe to proceed!
    place_averaging_order(...)
```

---

## Multi-Symbol Thread Safety

### Problem

**Scenario:**
```
Time    Symbol A           Symbol B           Balance
----    --------           --------           -------
T0      Check: $100 ✅     -                  $100
T1      -                  Check: $100 ✅     $100
T2      Place $80          -                  $20
T3      -                  Place $80 ❌       ERROR!
```

### Solution: `_balance_operation_lock`

**Implementation:**
```python
# In TradingAccount.__init__()
self._balance_operation_lock = threading.Lock()

def check_and_reserve_balance(self, symbol: str, margin_needed: float) -> bool:
    with self._balance_operation_lock:  # ATOMIC SECTION
        # Get balance
        strategy = self.strategies.get(symbol)
        available = strategy.balance_manager.get_available_balance()

        # Apply buffer
        buffer_multiplier = 1 + (self.balance_buffer_percent / 100.0)
        required = margin_needed * buffer_multiplier

        # Check
        if available >= required:
            return True  # PASS - balance reserved
        else:
            return False  # FAIL - insufficient
```

**Fixed Scenario:**
```
Time    Symbol A           Symbol B           Balance
----    --------           --------           -------
T0      LOCK               -                  $100
T1      Check: $100 ✅     WAIT (locked)      $100
T2      Place $80          WAIT               $20
T3      UNLOCK             -                  $20
T4      -                  LOCK               $20
T5      -                  Check: $20 ❌      $20
T6      -                  UNLOCK (skip)      $20
```

**Result:** Symbol B correctly sees only $20 available ✅

---

## Edge Cases & Recovery

### Edge Case 1: Pending Cancelled by Exchange

**Scenario:** Exchange cancels pending (expired, rejected, etc.)

**Detection:** `on_order_update()` receives status="Cancelled"

**Recovery:** Auto-retry immediately
```python
# In on_order_update()
if order_status == 'Cancelled' and is_pending_entry:
    # Remove from tracking
    del self._pending_entry_orders[side][grid_level]

    # Re-place with current price
    retry_id = place_pending_entry_order(side, grid_level, current_price)
```

**Log:**
```
2025-10-16 11:00:00 - WARNING - ⚠️ Pending entry cancelled by exchange: Buy level 3, AUTO-RETRYING...
2025-10-16 11:00:01 - INFO - ✅ Pending entry re-placed: Buy level 3 (new orderId=xyz789)
```

---

### Edge Case 2: Pending Fills After TP Close

**Scenario:**
1. LONG closes via TP
2. Pending LONG level 3 in process of filling
3. Cancel sent, but already filled!

**Detection:** `on_order_update()` orphan position check

**Recovery:** Add orphan position, TP will close it
```python
if not current_positions:
    logger.warning(
        "⚠️ Pending entry filled AFTER position closed! "
        "Adding orphan position - will be closed by TP."
    )

# Add anyway - TP will handle
pm.add_position(...)
_update_tp_order(side)  # Creates TP for orphan
```

**Log:**
```
2025-10-16 11:05:00 - WARNING - ⚠️ Pending entry filled AFTER position closed! Buy level 3 filled but position already closed. Adding orphan position - will be closed by TP or emergency.
2025-10-16 11:05:00 - INFO - ✅ Pending entry FILLED: Buy level 3 @ $0.2037 (qty=2856.0)
2025-10-16 11:05:01 - INFO - ✅ TP order created: Sell 2856.0 @ $0.2058 (orphan cleanup)
```

---

### Edge Case 3: Bot Restart with Stale Pending

**Scenario:** Bot restarts, old pending on exchange

**Detection:** First `sync_with_exchange()` after restart

**Recovery:** Cancel ALL orders, restore fresh
```python
# In sync_with_exchange()
if not self._first_sync_done:
    logger.info("🔄 First sync after restart - cancelling all existing orders")
    self._cancel_all_orders()  # TP + Pending
    self._first_sync_done = True

# Then restore pending for current positions
for side in ['Buy', 'Sell']:
    if pm.get_position_count(side) > 0:
        _place_pending_for_symmetry(side, current_price)
```

**Log:**
```
2025-10-16 12:00:00 - INFO - [DOGEUSDT] 🔄 First sync after restart - cancelling all existing orders
2025-10-16 12:00:00 - INFO - [DOGEUSDT] 🗑️ Cancelling order: old_tp_id (type=Limit, reduceOnly=True)
2025-10-16 12:00:00 - INFO - [DOGEUSDT] 🗑️ Cancelling order: old_pending_id (type=Limit, reduceOnly=False)
2025-10-16 12:00:01 - INFO - [DOGEUSDT] ✅ Cancelled 2 order(s) on restart cleanup
2025-10-16 12:00:05 - INFO - [DOGEUSDT] 🔄 Restored 2 pending entries for Buy during sync
```

---

### Edge Case 4: Insufficient Balance for Pending

**Scenario:** Cannot afford pending after opening positions

**Detection:** Balance check in `_place_pending_for_symmetry()`

**Recovery:** Skip pending, log warning, retry in sync
```python
# In _place_pending_for_symmetry()
required_with_buffer = total_pending_margin * 1.15

if available < required_with_buffer:
    logger.warning(
        f"⚠️ Cannot place pending for symmetry: "
        f"need ${required_with_buffer:.2f}, available ${available:.2f}"
    )
    return 0  # Skip for now

# Sync will retry every 60 seconds
```

**Log:**
```
2025-10-16 13:00:00 - WARNING - ⚠️ Cannot place pending for symmetry: need $27.60 (with 15% buffer), available $15.00
2025-10-16 13:01:00 - INFO - [DOGEUSDT] 🔄 Restored 2 pending entries for Buy during sync
```

---

### Edge Case 5: Partial Fill Hanging

**Scenario:** Pending partially filled, price moved away

**Current Behavior:** Log partial, wait for full fill

**Risk:** Low (liquid markets fill quickly)

**Future Enhancement:** Cancel if partial >10 minutes
```python
# TODO: Track partial fill time
if status == 'PartiallyFilled':
    # Track time
    partial_fill_time[order_id] = time.time()

    # Check in sync: if > 10 minutes, cancel and re-place
```

---

## Complete Lifecycle Examples

### Example 1: Full Cycle (Open → Average → TP → Reopen)

**Initial State:** Bot starts, no positions

**Step 1: Open Initial Positions**
```
Action: Open LONG and SHORT
Result:
  LONG:  [0] @ $0.2100
  SHORT: [0] @ $0.2000

No pending (both at same level)
```

**Step 2: Price Drops → LONG Averages**
```
Trigger: Price → $0.2079 (1% down)
Action:  Average LONG level 1
Check:   Need $2 LONG + $2 SHORT pending = $4 * 1.15 = $4.60 ✅
Result:
  LONG:  [0][1] @ avg $0.2085
  SHORT: [0] @ $0.2000 + pending [1]

✅ Symmetry via pending!
```

**Step 3: Price Rises → SHORT Averages**
```
Trigger: Price → $0.2020 (1% up)
Action:  Average SHORT level 1
Check:   Need $2 SHORT + $2 LONG pending = $4 * 1.15 = $4.60 ✅
Result:
  LONG:  [0][1] @ avg $0.2085 + pending [removed level 1]
  SHORT: [0][1] @ avg $0.2010

Pending [1] on SHORT fills → added to PM
LONG pending [1] cancelled (not needed anymore)
```

**Step 4: Price Drops More → LONG Averages Again**
```
Trigger: Price → $0.2064 (1% down from $0.2085)
Action:  Average LONG level 2
Check:   Need $4 LONG + $4 SHORT pending = $8 * 1.15 = $9.20 ✅
Result:
  LONG:  [0][1][2] @ avg $0.2076
  SHORT: [0][1] @ avg $0.2010 + pending [2]
```

**Step 5: Price Rises → LONG TP Triggers**
```
Trigger: Price → $0.2098 (TP for LONG)
Action:
  1. Close ALL LONG positions
  2. Cancel pending LONG (none)
  3. Calculate reopen: SHORT has 2 levels, minus 2 = 0, use initial
  4. Reopen LONG level [0]
  5. Place pending [1] for LONG

Result:
  LONG:  [0] @ $0.2098 (NEW!) + pending [1]
  SHORT: [0][1] @ avg $0.2010 + pending [2]

PnL: +$8.45 ✅
```

---

### Example 2: Multi-Symbol Coordination

**Symbols:** DOGE and ETH on same account

**Balance:** $100 available

**Scenario:**

**T0:** Both check averaging simultaneously
```
DOGE: Check for $40 needed (avg + pending) * 1.15 = $46
ETH:  Check for $40 needed (avg + pending) * 1.15 = $46

Without lock: Both see $100, both proceed → FAIL!
With lock:    First locks, second waits → SUCCESS!
```

**With Lock:**
```
T0: DOGE locks
T1: DOGE checks: $100 ≥ $46 ✅
T2: DOGE places orders (reserves ~$40)
T3: DOGE unlocks
T4: ETH locks
T5: ETH checks: $60 ≥ $46 ✅
T6: ETH places orders (reserves ~$40)
T7: ETH unlocks

Final balance: ~$20 remaining ✅
Both averaged successfully!
```

---

## Best Practices

### 1. Balance Management

**Do:**
- Always check balance with buffer (1.15x)
- Use multi-symbol lock for atomic operations
- Monitor available balance regularly

**Don't:**
- Assume balance is always sufficient
- Skip buffer in calculations
- Place orders without balance check

---

### 2. Pending Orders

**Do:**
- Place pending immediately after averaging
- Cancel pending when position closes
- Re-place if exchange cancels
- Verify pending in periodic sync

**Don't:**
- Leave orphan pending after TP
- Assume pending always fills
- Ignore partial fills completely

---

### 3. Error Handling

**Do:**
- Retry failed operations (3 attempts)
- Log all pending status changes
- Track failed reopens for recovery
- Use fail-safe fallbacks

**Don't:**
- Silently ignore failures
- Retry infinitely
- Skip orphan position check

---

### 4. Monitoring

**Do:**
- Watch logs for pending events
- Verify symmetry in sync logs
- Check for "insufficient balance" warnings
- Monitor pending fill rates

**Don't:**
- Ignore partial fill warnings
- Skip log analysis
- Assume silent = success

---

### 5. Testing

**Do:**
- Test in dry_run first
- Verify pending in demo
- Check multi-symbol scenarios
- Simulate edge cases

**Don't:**
- Go straight to production
- Skip balance exhaustion tests
- Ignore edge case scenarios

---

## Summary

The Position Lifecycle with Pending Entry Orders provides:

1. **Guaranteed Symmetry** - Positions always balanced (real + pending)
2. **Balance Safety** - 15% buffer + multi-symbol lock
3. **Auto-Recovery** - Retry failed operations, restore in sync
4. **Thread Safety** - Atomic balance checks across symbols
5. **Edge Case Handling** - Orphan positions, cancelled pending, partial fills

**Key Principle:** Reserve balance on exchange through pending orders to **guarantee** future balancing capability.

**Result:** Robust, fail-safe trading system that maintains position symmetry even under adverse conditions.

---

## Critical Fixes & Improvements

### Fix 1: Duplicate Pending Orders (2025-10-16)

**Problem:**
Every 60 seconds during `sync_with_exchange()`, new pending orders were created for the same levels, leading to massive duplication.

**Root Cause:**
`_place_pending_for_symmetry()` calculated missing levels based ONLY on local positions, **without checking** `_pending_entry_orders` for existing pending orders.

**Example:**
```
03:11:59 - Placed 5 pending for Sell (levels [2,3,4,5,6])
03:12:59 - Placed 5 pending for Sell (levels [2,3,4,5,6]) ❌ DUPLICATE!
03:13:59 - Placed 5 pending for Sell (levels [2,3,4,5,6]) ❌ DUPLICATE!
...
Result: 50+ duplicate pending orders on exchange
```

**Fix:**
Added check for existing pending orders before creating new ones:

```python
# Before fix:
missing_levels = list(range(current_max_level + 1, max_opposite_level + 1))
for level in missing_levels:
    place_pending_entry_order(...)  # Creates duplicates!

# After fix:
missing_levels = list(range(current_max_level + 1, max_opposite_level + 1))

# Check existing pending
with self._pending_entry_lock:
    existing_pending_levels = set(self._pending_entry_orders[opened_side].keys())

# Only place NEW levels
levels_to_place = [level for level in missing_levels if level not in existing_pending_levels]

if not levels_to_place:
    return 0  # All levels already have pending - skip!

for level in levels_to_place:
    place_pending_entry_order(...)  # Only new levels
```

**Result:**
```
03:25:49 - Placed 5 pending for Sell (levels [2,3,4,5,6]) ✅
03:26:47 - All missing levels [2,3,4,5,6] already have pending - skipping ✅
03:27:47 - All missing levels [2,3,4,5,6] already have pending - skipping ✅
...
No more duplicates!
```

**Impact:**
- ✅ No duplicate orders on exchange
- ✅ Reduced API calls by ~90%
- ✅ Cleaner order book
- ✅ No wasted margin on duplicate pending

**Files Modified:**
- `src/strategy/grid_strategy/order_management.py:735-748`

---

### Fix 2: Missing TP Orders After Restart (2025-10-16)

**Problem:**
After bot restart, TP orders were created during `restore_state_from_exchange()`, but then **cancelled** during first `sync_with_exchange()` and **NOT recreated**, leaving positions unprotected.

**Root Cause:**
```python
# Step 1: Restore positions (creates TP orders)
restore_state_from_exchange()
  → Creates TP for LONG (ID: abc123)
  → Creates TP for SHORT (ID: def456)
  → Saves IDs to _tp_orders and pm.tp_order_id

# Step 2: First sync (cancels all orders)
sync_with_exchange()
  → _cancel_all_orders()  # Cancels abc123 and def456 on exchange ✅
  → BUT: IDs still in memory (_tp_orders = {'Buy': 'abc123', 'Sell': 'def456'}) ❌

# Step 3: TP verification
for side in ['Buy', 'Sell']:
    tp_order_id = self.pm.get_tp_order_id(side)
    if not tp_order_id:  # FALSE - ID exists in memory! ❌
        _update_tp_order(side)  # NOT CALLED ❌

# Result: No TP orders, positions UNPROTECTED ❌
```

**Fix:**
After `_cancel_all_orders()`, **clear local TP order IDs**:

```python
# In sync_with_exchange()
if not self._first_sync_done:
    self._cancel_all_orders()

    # CRITICAL: Clear local TP order IDs
    with self._tp_orders_lock:
        self._tp_orders = {'Buy': None, 'Sell': None}
    self.pm.set_tp_order_id('Buy', None)
    self.pm.set_tp_order_id('Sell', None)

    self._first_sync_done = True

# Now verification works:
for side in ['Buy', 'Sell']:
    tp_order_id = self.pm.get_tp_order_id(side)  # None!
    if not tp_order_id:  # TRUE ✅
        _update_tp_order(side)  # CREATES TP ✅
```

**Result:**
```
03:25:44 - TP created during restore: abc123 (LONG), def456 (SHORT)
03:25:47 - First sync - cancelling all orders
03:25:47 - Cancelled abc123, def456
03:25:48 - TP order missing for Buy - creating ✅
03:25:48 - TP created: xyz789 (LONG)
03:25:49 - TP order missing for Sell - creating ✅
03:25:49 - TP created: uvw012 (SHORT)

Positions now protected!
```

**Impact:**
- ✅ TP orders always recreated after restart
- ✅ Positions never left unprotected
- ✅ Risk management maintained
- ✅ No manual intervention needed

**Files Modified:**
- `src/strategy/grid_strategy/restoration.py:657-662`

---

**Document Version:** 1.1
**Last Updated:** 2025-10-16
**Author:** SOL-Trader Development Team
