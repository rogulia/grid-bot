"""State manager for persisting bot state to JSON (Multi-symbol support)"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
from src.utils.timezone import now_helsinki


class StateManager:
    """Manage bot state persistence (supports multiple trading symbols)"""

    def __init__(self, state_file: str = "data/bot_state.json", symbol: Optional[str] = None):
        """
        Initialize state manager

        Args:
            state_file: Path to state JSON file
            symbol: Trading symbol (e.g., SOLUSDT). If None, manages all symbols.
        """
        self.logger = logging.getLogger("sol-trader.state")
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.symbol = symbol

    def save_state(self, long_positions: List, short_positions: List,
                   long_tp_id: Optional[str], short_tp_id: Optional[str]):
        """
        Save current state to JSON (multi-symbol aware)

        Args:
            long_positions: List of LONG positions
            short_positions: List of SHORT positions
            long_tp_id: LONG TP order ID
            short_tp_id: SHORT TP order ID
        """
        try:
            # Load existing state for all symbols
            all_state = self._load_all_state()

            # Prepare state for this symbol (Helsinki timezone)
            symbol_state = {
                "timestamp": now_helsinki().isoformat(),
                "long_positions": [
                    {
                        "side": p.side,
                        "entry_price": p.entry_price,
                        "quantity": p.quantity,
                        "grid_level": p.grid_level,
                        "timestamp": p.timestamp.isoformat()
                    }
                    for p in long_positions
                ],
                "short_positions": [
                    {
                        "side": p.side,
                        "entry_price": p.entry_price,
                        "quantity": p.quantity,
                        "grid_level": p.grid_level,
                        "timestamp": p.timestamp.isoformat()
                    }
                    for p in short_positions
                ],
                "long_tp_order_id": long_tp_id,
                "short_tp_order_id": short_tp_id
            }

            # Update state for this symbol
            if self.symbol:
                all_state[self.symbol] = symbol_state
            else:
                # Legacy single-symbol format
                all_state = symbol_state

            with open(self.state_file, 'w') as f:
                json.dump(all_state, f, indent=2)

            self.logger.debug(f"State saved to {self.state_file}" +
                            (f" for {self.symbol}" if self.symbol else ""))

        except Exception as e:
            self.logger.error(f"Failed to save state: {e}")

    def _load_all_state(self) -> Dict:
        """
        Load all state data from JSON file

        Returns:
            Complete state dict (all symbols) or empty dict
        """
        try:
            if not self.state_file.exists():
                return {}

            with open(self.state_file, 'r') as f:
                state = json.load(f)

            # Return empty dict for legacy empty state
            if not state:
                return {}

            return state

        except Exception as e:
            self.logger.error(f"Failed to load all state: {e}")
            return {}

    def load_state(self) -> Optional[Dict]:
        """
        Load state from JSON (for this symbol or legacy single-symbol)

        Returns:
            State dict for this symbol, or None if not found
        """
        try:
            if not self.state_file.exists():
                self.logger.info("No saved state found")
                return None

            with open(self.state_file, 'r') as f:
                all_state = json.load(f)

            # Multi-symbol format: all_state is {symbol: {...}, symbol2: {...}}
            if self.symbol and isinstance(all_state, dict):
                # Check if this is multi-symbol format
                if self.symbol in all_state:
                    self.logger.info(f"State loaded for {self.symbol}")
                    return all_state[self.symbol]

                # Check if this is legacy format (no symbol keys)
                if 'long_positions' in all_state:
                    self.logger.info(f"Legacy state loaded (treating as {self.symbol})")
                    return all_state

                self.logger.info(f"No saved state for {self.symbol}")
                return None

            # Legacy single-symbol format
            if 'long_positions' in all_state:
                self.logger.info(f"State loaded (legacy format)")
                return all_state

            return None

        except Exception as e:
            self.logger.error(f"Failed to load state: {e}")
            return None

    def clear_state(self):
        """Remove state file"""
        try:
            if self.state_file.exists():
                self.state_file.unlink()
                self.logger.info("State cleared")
        except Exception as e:
            self.logger.error(f"Failed to clear state: {e}")
