#!/usr/bin/env python3
"""
Patch script to fix incorrect close reasons in trades_history.csv
Fixes records where:
- action = CLOSE
- reason = "Stop-Loss or Liquidation"
- pnl > 0 (should be Take Profit!)
"""

import csv
import sys
from pathlib import Path

def fix_close_reasons(csv_path):
    """Fix incorrect close reasons in CSV file"""

    # Read all rows
    rows = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)

    print(f"Read {len(rows)} rows from {csv_path}")

    # Find and fix incorrect records
    fixed_count = 0
    for row in rows:
        if (row['action'] == 'CLOSE' and
            row['reason'] == 'Stop-Loss or Liquidation'):

            try:
                pnl = float(row['pnl'])

                if pnl > 0:
                    # This is incorrect! Should be Take Profit
                    print(f"\n❌ Found incorrect record:")
                    print(f"   {row['timestamp']} - {row['symbol']} {row['side']} CLOSE")
                    print(f"   Reason: '{row['reason']}' but PnL: +${pnl:.4f}")

                    # Calculate percentage (rough estimate, may not match original)
                    # We can't calculate exact % without entry price, so just mark as Take Profit
                    old_reason = row['reason']
                    row['reason'] = "Take Profit (corrected)"

                    print(f"   ✅ Fixed to: '{row['reason']}'")
                    fixed_count += 1

            except (ValueError, KeyError) as e:
                print(f"⚠️  Error processing row: {e}")
                continue

    if fixed_count == 0:
        print("\n✅ No incorrect records found! CSV is already correct.")
        return

    # Write back to file
    backup_path = csv_path.parent / f"{csv_path.stem}_backup{csv_path.suffix}"
    print(f"\n💾 Creating backup: {backup_path}")

    # Backup original
    import shutil
    shutil.copy2(csv_path, backup_path)

    # Write fixed data
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"✅ Fixed {fixed_count} record(s) in {csv_path}")
    print(f"📁 Original backed up to {backup_path}")

if __name__ == "__main__":
    # Find all account CSV files
    data_dir = Path(__file__).parent.parent / "data"
    csv_files = list(data_dir.glob("*_trades_history.csv"))

    if not csv_files:
        print("❌ No trades_history.csv files found in data/")
        sys.exit(1)

    print(f"Found {len(csv_files)} CSV file(s) to check:\n")

    for csv_file in csv_files:
        print(f"\n{'='*60}")
        print(f"Processing: {csv_file.name}")
        print('='*60)
        fix_close_reasons(csv_file)

    print(f"\n{'='*60}")
    print("✅ Patch complete!")
    print('='*60)
