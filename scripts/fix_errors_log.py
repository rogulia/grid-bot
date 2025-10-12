#!/usr/bin/env python3
"""
Patch script to fix incorrect close reasons in errors.log
Fixes records where:
- PnL > 0
- reason = "Stop-Loss or Liquidation"
"""

import re
import sys
from pathlib import Path

def fix_errors_log(log_path):
    """Fix incorrect close reasons in errors.log"""

    # Read all lines
    with open(log_path, 'r') as f:
        lines = f.readlines()

    print(f"Read {len(lines)} lines from {log_path}")

    # Find and fix incorrect records
    fixed_count = 0
    new_lines = []

    for line in lines:
        # Match pattern: PnL=$X.XX (Stop-Loss or Liquidation)
        match = re.search(r'PnL=\$(-?\d+\.\d+)\s+\((Stop-Loss or Liquidation)\)', line)

        if match:
            pnl_str = match.group(1)
            pnl = float(pnl_str)

            if pnl > 0:
                # This is incorrect! Should be Take Profit
                print(f"\n❌ Found incorrect log entry:")
                print(f"   {line.strip()}")
                print(f"   PnL: +${pnl:.4f} but labeled as 'Stop-Loss or Liquidation'")

                # Replace with Take Profit (corrected)
                new_line = line.replace(
                    '(Stop-Loss or Liquidation)',
                    '(Take Profit - corrected)'
                )
                new_lines.append(new_line)
                print(f"   ✅ Fixed!")
                fixed_count += 1
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    if fixed_count == 0:
        print("\n✅ No incorrect records found! errors.log is already correct.")
        return

    # Backup original
    backup_path = log_path.parent / f"{log_path.stem}_backup{log_path.suffix}"
    print(f"\n💾 Creating backup: {backup_path}")

    import shutil
    shutil.copy2(log_path, backup_path)

    # Write fixed data
    with open(log_path, 'w') as f:
        f.writelines(new_lines)

    print(f"✅ Fixed {fixed_count} record(s) in {log_path}")
    print(f"📁 Original backed up to {backup_path}")

if __name__ == "__main__":
    # Fix errors.log
    logs_dir = Path(__file__).parent.parent / "logs"
    errors_log = logs_dir / "errors.log"

    if not errors_log.exists():
        print(f"❌ File not found: {errors_log}")
        sys.exit(1)

    print(f"{'='*60}")
    print(f"Processing: {errors_log.name}")
    print('='*60)
    fix_errors_log(errors_log)

    print(f"\n{'='*60}")
    print("✅ Patch complete!")
    print('='*60)
