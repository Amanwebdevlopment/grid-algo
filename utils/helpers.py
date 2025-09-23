# utils/helpers.py — Common MT5 Helper Functions

import MetaTrader5 as mt5
import math
import traceback

# ----------------- Precision & Rounding -----------------
def symbol_precision(symbol):
    info = mt5.symbol_info(symbol)
    return getattr(info, "digits", 5) if info else 5

def round_price(symbol, price):
    """Round price to instrument precision (uses symbol precision)."""
    try:
        if price is None:
            return None
        digits = symbol_precision(symbol)
        return round(price, digits)
    except Exception:
        return price

def get_point(symbol):
    info = mt5.symbol_info(symbol)
    if not info:
        return 10 ** (-symbol_precision(symbol))
    return getattr(info, "point", 10 ** (-symbol_precision(symbol)))

def get_tick(symbol):
    """Ensure symbol is selected and return latest tick."""
    try:
        if not mt5.symbol_select(symbol, True):
            print(f"[WARN] Symbol {symbol} not in Market Watch")
            return None
        return mt5.symbol_info_tick(symbol)
    except Exception as e:
        print(f"[ERROR] get_tick exception for {symbol}: {e}")
        return None

# ----------------- Fetch Data -----------------
def fetch_pending_orders(symbol):
    try:
        return mt5.orders_get(symbol=symbol) or []
    except Exception:
        return []

def fetch_positions(symbol):
    try:
        return mt5.positions_get(symbol=symbol) or []
    except Exception:
        return []

# ----------------- Grid Alignment -----------------
def align_price_to_grid(price, brick_size, mode="nearest"):
    """Align numeric price to brick_size multiples."""
    try:
        if brick_size == 0:
            return price
        ratio = price / brick_size
        if mode == "floor":
            aligned = math.floor(ratio) * brick_size
        elif mode == "ceil":
            aligned = math.ceil(ratio) * brick_size
        else:
            aligned = round(ratio) * brick_size
        return aligned
    except Exception:
        return price

def align_price_to_grid_symbol(symbol, price, brick_size, mode="nearest"):
    """Align and round to symbol precision."""
    aligned = align_price_to_grid(price, brick_size, mode)
    return round_price(symbol, aligned)

# ----------------- Position Helpers -----------------
def highest_buy_position(symbol):
    """Return highest price_open among current buy positions."""
    try:
        positions = fetch_positions(symbol)
        buys = [p.price_open for p in positions if int(p.type) in (0, getattr(mt5, "ORDER_TYPE_BUY", 0))]
        return max(buys) if buys else None
    except Exception:
        return None

def lowest_sell_position(symbol):
    """Return lowest price_open among current sell positions."""
    try:
        positions = fetch_positions(symbol)
        sells = [p.price_open for p in positions if int(p.type) in (1, getattr(mt5, "ORDER_TYPE_SELL", 1))]
        return min(sells) if sells else None
    except Exception:
        return None
