#!/usr/bin/env python3
"""
Analytics script for SOL-Trader performance analysis

Usage:
    python scripts/analyze.py                           # All accounts, yesterday
    python scripts/analyze.py --account-id 001          # Specific account, yesterday
    python scripts/analyze.py --date 2025-10-13         # All accounts, specific date
    python scripts/analyze.py --account-id 001 --plot   # With plots
"""

import argparse
import sys
import glob
import re
from pathlib import Path
from datetime import datetime, timedelta
import json

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.timezone import now_helsinki, format_helsinki

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

try:
    import pandas as pd
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    HAS_PLOTTING = True
except ImportError:
    HAS_PLOTTING = False
    print("⚠️  Warning: pandas/matplotlib not installed. Plotting disabled.")
    print("   Install with: pip install pandas matplotlib")


def get_yesterday_date():
    """
    Get yesterday's date in YYYY-MM-DD format (Helsinki timezone)

    Returns:
        str: Yesterday's date (e.g., "2025-10-13")
    """
    yesterday = now_helsinki() - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")


def discover_accounts():
    """
    Discover all account IDs by scanning data/ directory for account-prefixed files

    Returns:
        list: Sorted list of account IDs (e.g., ["001", "002"])
    """
    data_dir = Path("data")
    if not data_dir.exists():
        return []

    # Pattern to match account IDs (e.g., 001_performance_metrics_*.csv)
    account_ids = set()
    for file in data_dir.glob("*_*.csv"):
        # Extract account ID from filename (e.g., "001" from "001_performance_metrics_2025-10-13.csv")
        match = re.match(r'^(\d{3})_', file.name)
        if match:
            account_ids.add(match.group(1))

    return sorted(list(account_ids))


def get_account_info_from_config(account_id):
    """
    Get account name and trading symbols from config.yaml

    Args:
        account_id: Account ID (e.g., "001")

    Returns:
        dict: {"name": str, "symbols": list} or None if not found
    """
    if not HAS_YAML:
        return None

    config_path = Path("config/config.yaml")
    if not config_path.exists():
        return None

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # Find account by ID
        for account in config.get('accounts', []):
            if str(account.get('id')).zfill(3) == account_id:
                symbols = [s.get('symbol') for s in account.get('strategies', [])]
                return {
                    'name': account.get('name', f'Account {account_id}'),
                    'symbols': symbols
                }
    except Exception:
        pass

    return None


def load_performance_metrics(account_id="001", date=None):
    """
    Load performance metrics CSV

    Args:
        account_id: Account ID (default: "001")
        date: Specific date in YYYY-MM-DD format. If None, uses yesterday's date.

    Returns:
        DataFrame with metrics or None if file not found
    """
    if date is None:
        # Use yesterday's date by default (analyzing completed days)
        date = get_yesterday_date()

    file_path = f"data/{account_id}_performance_metrics_{date}.csv"

    if not Path(file_path).exists():
        print(f"❌ File not found: {file_path}")
        return None

    df = pd.read_csv(file_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df


def load_trades_history(account_id="001", date=None):
    """
    Load trades history CSV

    Args:
        account_id: Account ID (default: "001")
        date: Specific date in YYYY-MM-DD format. If None, uses yesterday's date.

    Returns:
        DataFrame with trades or None if file not found
    """
    if date is None:
        # Use yesterday's date by default (analyzing completed days)
        date = get_yesterday_date()

    file_path = f"data/{account_id}_trades_history_{date}.csv"

    if not Path(file_path).exists():
        print(f"❌ File not found: {file_path}")
        return None

    df = pd.read_csv(file_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df


def load_summary_report(account_id="001", date=None):
    """
    Load summary report JSON

    Args:
        account_id: Account ID (default: "001")
        date: Specific date in YYYY-MM-DD format. If None, uses yesterday's date.

    Returns:
        dict: Summary report or None if file not found
    """
    if date is None:
        # Use yesterday's date by default (analyzing completed days)
        date = get_yesterday_date()

    file_path = Path(f"data/{account_id}_summary_report_{date}.json")

    if not file_path.exists():
        return None

    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def filter_by_period(df, period):
    """Filter dataframe by time period"""
    if period == 'all':
        return df

    now = now_helsinki()

    if period.endswith('h'):
        hours = int(period[:-1])
        start_time = now - timedelta(hours=hours)
    elif period.endswith('d'):
        days = int(period[:-1])
        start_time = now - timedelta(days=days)
    else:
        print(f"⚠️  Unknown period format: {period}. Using 'all'")
        return df

    return df[df['timestamp'] >= start_time]


def print_account_header(account_id, date):
    """
    Print account header with name and symbols

    Args:
        account_id: Account ID (e.g., "001")
        date: Analysis date (e.g., "2025-10-13")
    """
    print("\n" + "═" * 80)

    # Try to get account info from config
    account_info = get_account_info_from_config(account_id)
    if account_info:
        symbols_str = ", ".join(account_info['symbols']) if account_info['symbols'] else "No symbols"
        print(f"🔷 Account {account_id}: {account_info['name']} ({symbols_str}) - {date}")
    else:
        print(f"🔷 Account {account_id} - {date}")

    print("═" * 80)


def print_summary(summary, account_id=None):
    """
    Print summary report

    Args:
        summary: Summary report dict or None
        account_id: Optional account ID for informative messages
    """
    print("\n📊 Performance Summary")
    print("─" * 60)

    if summary is None:
        account_str = f" for account {account_id}" if account_id else ""
        print(f"⚠️  No summary report found{account_str}.")
        return

    # Period
    print(f"\n📅 Period:")
    print(f"  Start:    {summary['period']['start']}")
    print(f"  End:      {summary['period']['end']}")
    print(f"  Duration: {summary['period']['duration_hours']:.2f} hours")

    # Performance
    perf = summary['performance']
    print(f"\n💰 Performance:")
    print(f"  Initial Balance:  ${perf['initial_balance']:.2f}")
    print(f"  Final Balance:    ${perf['final_balance']:.2f}")
    print(f"  Realized PnL:     ${perf['realized_pnl']:.2f}")
    print(f"  Unrealized PnL:   ${perf['unrealized_pnl']:.2f}")

    pnl_sign = "+" if perf['total_pnl'] >= 0 else ""
    roi_sign = "+" if perf['roi_percent'] >= 0 else ""
    print(f"  Total PnL:        ${pnl_sign}{perf['total_pnl']:.2f} ({roi_sign}{perf['roi_percent']:.2f}%)")

    print(f"\n  Best Trade:       ${perf['best_trade']:.2f}")
    print(f"  Worst Trade:      ${perf['worst_trade']:.2f}")
    print(f"  Average Trade:    ${perf['avg_trade']:.2f}")
    print(f"  Win Rate:         {perf['win_rate']:.1f}%")

    # Trading Stats
    stats = summary['trading_stats']
    print(f"\n📈 Trading Statistics:")
    print(f"  Total Trades:     {stats['total_trades']}")
    print(f"  Winning Trades:   {stats['winning_trades']}")
    print(f"  Losing Trades:    {stats['losing_trades']}")
    print(f"  Open Trades:      {stats['open_trades']}")

    # Risk Metrics
    risk = summary['risk_metrics']
    print(f"\n⚠️  Risk Metrics:")
    print(f"  Max Drawdown:     ${risk['max_drawdown']:.2f} ({risk['max_drawdown_percent']:.2f}%)")
    print(f"  Peak Balance:     ${risk['peak_balance']:.2f}")


def analyze_metrics(df, period='all'):
    """Analyze performance metrics"""
    if df is None or df.empty:
        print("❌ No metrics data available")
        return

    df = filter_by_period(df, period)

    if df.empty:
        print(f"❌ No data for period: {period}")
        return

    print(f"\n📊 Metrics Analysis (Period: {period})")
    print("─" * 60)

    # Basic stats
    print(f"\n📈 Price Statistics:")
    print(f"  Start Price:  ${df.iloc[0]['price']:.4f}")
    print(f"  End Price:    ${df.iloc[-1]['price']:.4f}")
    print(f"  Highest:      ${df['price'].max():.4f}")
    print(f"  Lowest:       ${df['price'].min():.4f}")
    price_change = ((df.iloc[-1]['price'] - df.iloc[0]['price']) / df.iloc[0]['price']) * 100
    print(f"  Change:       {price_change:+.2f}%")

    # PnL stats
    print(f"\n💰 PnL Statistics:")
    print(f"  Final Total PnL:    ${df.iloc[-1]['total_pnl']:.2f}")
    print(f"  Max Total PnL:      ${df['total_pnl'].max():.2f}")
    print(f"  Min Total PnL:      ${df['total_pnl'].min():.2f}")
    print(f"  Final Balance:      ${df.iloc[-1]['balance']:.2f}")

    # Position stats
    print(f"\n📊 Position Statistics:")
    print(f"  Max LONG positions:  {int(df['long_positions'].max())}")
    print(f"  Max SHORT positions: {int(df['short_positions'].max())}")
    print(f"  Total trades:        {int(df.iloc[-1]['total_trades'])}")


def plot_performance(df, period='all'):
    """Plot performance charts"""
    if not HAS_PLOTTING:
        print("❌ Plotting not available. Install pandas and matplotlib.")
        return

    if df is None or df.empty:
        print("❌ No data to plot")
        return

    df = filter_by_period(df, period)

    if df.empty:
        print(f"❌ No data for period: {period}")
        return

    # Create subplots
    fig, axes = plt.subplots(3, 1, figsize=(12, 10))
    fig.suptitle(f'SOL-Trader Performance Analysis ({period})', fontsize=16)

    # Plot 1: Price and PnL
    ax1 = axes[0]
    ax1_twin = ax1.twinx()

    ax1.plot(df['timestamp'], df['price'], 'b-', label='Price', linewidth=1.5)
    ax1_twin.plot(df['timestamp'], df['total_pnl'], 'g-', label='Total PnL', linewidth=1.5)

    ax1.set_ylabel('Price (USDT)', color='b')
    ax1_twin.set_ylabel('Total PnL (USDT)', color='g')
    ax1.set_title('Price vs Total PnL')
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc='upper left')
    ax1_twin.legend(loc='upper right')

    # Plot 2: LONG vs SHORT PnL
    ax2 = axes[1]
    ax2.plot(df['timestamp'], df['long_pnl'], 'g-', label='LONG PnL', linewidth=1.5)
    ax2.plot(df['timestamp'], df['short_pnl'], 'r-', label='SHORT PnL', linewidth=1.5)
    ax2.axhline(y=0, color='black', linestyle='--', alpha=0.3)
    ax2.set_ylabel('PnL (USDT)')
    ax2.set_title('LONG vs SHORT PnL')
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    # Plot 3: Number of positions
    ax3 = axes[2]
    ax3.plot(df['timestamp'], df['long_positions'], 'g-', label='LONG Positions', linewidth=1.5)
    ax3.plot(df['timestamp'], df['short_positions'], 'r-', label='SHORT Positions', linewidth=1.5)
    ax3.set_ylabel('Number of Positions')
    ax3.set_xlabel('Time')
    ax3.set_title('Active Positions Over Time')
    ax3.legend()
    ax3.grid(True, alpha=0.3)

    # Format x-axis
    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

    plt.tight_layout()
    plt.show()


def analyze_account(account_id, date, period='all', show_plots=False):
    """
    Analyze a single account

    Args:
        account_id: Account ID (e.g., "001")
        date: Analysis date (YYYY-MM-DD)
        period: Time period filter (e.g., "24h", "all")
        show_plots: Whether to show plots
    """
    # Print account header
    print_account_header(account_id, date)

    # Load data
    summary = load_summary_report(account_id, date)
    metrics_df = load_performance_metrics(account_id, date)
    trades_df = load_trades_history(account_id, date)

    # Show summary
    print_summary(summary, account_id)

    # Analyze metrics
    if metrics_df is not None:
        analyze_metrics(metrics_df, period)
    else:
        print(f"\n❌ No metrics data found for account {account_id} on {date}")

    # Plot if requested
    if show_plots:
        if HAS_PLOTTING:
            if metrics_df is not None:
                print(f"\n📈 Generating plots for account {account_id}...")
                plot_performance(metrics_df, period)
            else:
                print(f"\n⚠️  Cannot plot: no data for account {account_id}")
        else:
            print("\n❌ Plotting requires pandas and matplotlib")
            print("   Install with: pip install pandas matplotlib")


def main():
    parser = argparse.ArgumentParser(
        description='Analyze SOL-Trader performance (multi-account support)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/analyze.py                          # All accounts, yesterday
  python scripts/analyze.py --account-id 001         # Specific account, yesterday
  python scripts/analyze.py --date 2025-10-13        # All accounts, specific date
  python scripts/analyze.py --account-id 001 --plot  # With plots
        """
    )
    parser.add_argument('--account-id', help='Specific account ID (e.g., 001, 002). If not specified, analyzes all accounts.')
    parser.add_argument('--date', help='Analysis date (YYYY-MM-DD). Default: yesterday.')
    parser.add_argument('--period', default='all', help='Time period filter (e.g., 24h, 7d, all)')
    parser.add_argument('--plot', action='store_true', help='Show performance plots')
    parser.add_argument('--export', help='Export report to file (not yet implemented)')

    args = parser.parse_args()

    # Determine date (default: yesterday)
    analysis_date = args.date if args.date else get_yesterday_date()

    print(f"\n🔍 SOL-Trader Analytics")
    print(f"📅 Analysis Date: {analysis_date}")
    print(f"⏱️  Period Filter: {args.period}")

    # Determine which accounts to analyze
    if args.account_id:
        # Single account analysis
        accounts_to_analyze = [args.account_id]
    else:
        # Multi-account analysis: discover all accounts
        accounts_to_analyze = discover_accounts()
        if not accounts_to_analyze:
            print("\n❌ No accounts found in data/ directory.")
            print("   Run the bot first to generate data files.")
            return

        print(f"📊 Found {len(accounts_to_analyze)} account(s): {', '.join(accounts_to_analyze)}")

    # Analyze each account
    for i, account_id in enumerate(accounts_to_analyze):
        # Add separator between accounts (but not before first)
        if i > 0:
            print("\n" + "─" * 80 + "\n")

        try:
            analyze_account(account_id, analysis_date, args.period, args.plot)
        except Exception as e:
            print(f"\n❌ Error analyzing account {account_id}: {e}")
            continue

    # Export if requested
    if args.export:
        print(f"\n💾 Export to {args.export} - not yet implemented")

    # Final message
    print("\n" + "═" * 80)
    print("✅ Analysis complete!")
    if not args.plot:
        print("\n💡 Tip: Use --plot to see visual charts")
    print(f"💡 Example: python scripts/analyze.py --account-id 001 --plot --period 24h\n")


if __name__ == "__main__":
    main()
