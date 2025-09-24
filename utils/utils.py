# utils.py — Symmetrical Rolling/Expanding Grid Bot (core logic only)

import MetaTrader5 as mt5
import time
import traceback
import math
from collections import defaultdict
import datetime

# ----------------- Config / Limits -----------------
MAX_ORDERS_PER_SYMBOL = 60  # total pending orders per symbol
DEFAULT_MAGIC = 123456

# ----------------- External imports -----------------
from utils.order_manager import (
    place_order, remove_order, order_exists, can_place_order,
    cancel_far_orders, cancel_far_orders_preserve
)
from utils.helpers import (
    round_price, get_tick, fetch_pending_orders, fetch_positions,
    highest_buy_position, lowest_sell_position
)

# ----------------- Price Alignment -----------------
def align_price_to_grid_symbol(symbol, price, brick_size, mode="nearest"):
    """
    Align price to a multiple of brick_size with predictable semantics.
    mode: "nearest" | "down" | "up"
    Uses math.floor/ceil/round on ratio to avoid float // oddities.
    """
    if brick_size is None or brick_size <= 0:
        return round(price, 8)
    try:
        ratio = float(price) / float(brick_size)
    except Exception:
        return round(price, 8)

    if mode == "nearest":
        n = int(round(ratio))
    elif mode == "down":
        n = math.floor(ratio + 1e-12)
    elif mode == "up":
        n = math.ceil(ratio - 1e-12)
    else:
        n = int(round(ratio))

    aligned = round(n * float(brick_size), 8)
    return aligned

# ----------------- MT5 Initialization -----------------
def initialize_mt5(account, password, server):
    try:
        if not mt5.initialize():
            print(f"[ERROR] MT5 initialize failed: {mt5.last_error()}")
            return False
        if not mt5.login(account, password=password, server=server):
            print(f"[ERROR] MT5 login failed: {mt5.last_error()}")
            return False
        print("[INFO] MT5 connected successfully")
        return True
    except Exception as e:
        print("[ERROR] initialize_mt5 exception:", e)
        return False

# ----------------- Helper caches / utilities -----------------
_pending_cache = defaultdict(set)

def sync_pending_cache(symbol, brick_size):
    """Fetch pending orders from server and sync _pending_cache[symbol] to aligned prices."""
    try:
        pending = fetch_pending_orders(symbol) or []
        prices = set()
        for o in pending:
            po = getattr(o, "price_open", None)
            if po is None:
                continue
            prices.add(align_price_to_grid_symbol(symbol, po, brick_size))
        _pending_cache[symbol] = prices
        return prices
    except Exception:
        return _pending_cache.get(symbol, set())

def get_open_positions_prices(symbol, brick_size):
    """Return set of aligned prices where there are open positions for symbol."""
    try:
        positions = fetch_positions(symbol) or []
        return {align_price_to_grid_symbol(symbol, getattr(p, "price_open", getattr(p, "price", 0)), brick_size) for p in positions}
    except Exception:
        return set()

def _place_order_and_handle_return(order_type, symbol, price_aligned, volume, sl_pips=None, tp_pips=None):
    """
    Wrapper to call place_order (from utils.order_manager) and unify return types.
    place_order may return bool or (bool, msg). Handle both.
    """
    try:
        res = place_order(order_type, symbol, price_aligned, volume, sl_pips=sl_pips, tp_pips=tp_pips)
        if isinstance(res, tuple):
            ok = bool(res[0])
            msg = res[1] if len(res) > 1 else ""
        else:
            ok = bool(res)
            msg = ""
        return ok, msg
    except Exception as e:
        traceback.print_exc()
        return False, f"exception in place_order wrapper: {e}"

def safe_place_order(order_type, symbol, price, volume, brick_size, sl_pips=None, tp_pips=None, closed_levels=None):
    """
    Fully debugged version: prints every check and MT5 broker response.
    Added: range-based open-position check so duplicate orders near an open position are prevented.
    """
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        print(f"[{timestamp}] [DEBUG] Attempting to place order: type={order_type}, symbol={symbol}, price={price}, volume={volume}")

        # 1️⃣ Align price
        price_aligned = align_price_to_grid_symbol(symbol, price, brick_size)
        print(f"[{timestamp}] [DEBUG] Aligned price: {price_aligned}")

        # 2️⃣ Closed levels check
        if closed_levels and price_aligned in closed_levels:
            print(f"[{timestamp}] [DEBUG] Rejected: price in closed_levels")
            return False

        # 3️⃣ Pending cache check
        pending_prices = sync_pending_cache(symbol, brick_size)
        if price_aligned in pending_prices:
            print(f"[{timestamp}] [DEBUG] Rejected: price already pending in cache")
            return False

        # 4️⃣ Server-side pending check
        if order_exists(symbol, price_aligned, order_type):
            _pending_cache[symbol].add(price_aligned)
            print(f"[{timestamp}] [DEBUG] Rejected: order_exists server-side")
            return False

        # Prepare symbol info for point/min distance
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        digits = getattr(info, "digits", 5) if info else 5
        point = getattr(info, "point", 10**-digits if digits else 1e-5)

        # 5️⃣ Open positions check (range-based)
        open_prices = get_open_positions_prices(symbol, brick_size)
        # threshold: use brick_size if provided else fallback to point
        threshold = brick_size if (brick_size and brick_size > 0) else (point or 1e-5)

        # If any open position exists within +/- threshold, reject
        for op in open_prices:
            if abs(price_aligned - op) < threshold:
                print(f"[{timestamp}] [DEBUG] Rejected: open position exists near {op} (threshold={threshold})")
                return False

        # 6️⃣ Broker min distance check
        if info and tick:
            stops_level = getattr(info, "trade_stops_level", 0) or 0
            min_dist = stops_level * (point or 1)
            print(f"[{timestamp}] [DEBUG] Tick: ask={tick.ask}, bid={tick.bid}, min_dist={min_dist}")

            if order_type == getattr(mt5, "ORDER_TYPE_BUY_STOP", 2):
                if price_aligned <= tick.ask + min_dist:
                    print(f"[{timestamp}] [DEBUG] Rejected: BUY_STOP too close to ask+min_dist")
                    return False
            if order_type == getattr(mt5, "ORDER_TYPE_SELL_STOP", 3):
                if price_aligned >= tick.bid - min_dist:
                    print(f"[{timestamp}] [DEBUG] Rejected: SELL_STOP too close to bid-min_dist")
                    return False

        # 7️⃣ Attempt to place order
        ok, msg = _place_order_and_handle_return(order_type, symbol, price_aligned, volume, sl_pips=sl_pips, tp_pips=tp_pips)
        if ok:
            _pending_cache[symbol].add(price_aligned)
            print(f"[{timestamp}] [INFO] Order PLACED: {symbol} @ {price_aligned}, volume={volume}")
            return True
        else:
            print(f"[{timestamp}] [ERROR] Order REJECTED: {symbol} @ {price_aligned}, reason: {msg}")
            return False

    except Exception as e:
        print(f"[{timestamp}] [EXCEPTION] safe_place_order exception: {e}")
        traceback.print_exc()
        return False


def update_grid(symbol, current_price, brick_size, lot,
                trade_side="both", sl_pips=None, tp_pips=None, closed_levels=None,
                initial_buy_levels=0, initial_sell_levels=0):
    """
    Fully debugged version with detailed logs per order attempt.
    """
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        CHECK_UP = 5
        CHECK_DOWN = 5

        pending_prices = sync_pending_cache(symbol, brick_size) or set()
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
        stops_level = getattr(info, "trade_stops_level", 0) if info else 0
        digits = getattr(info, "digits", 5) if info else 5
        point = getattr(info, "point", 10**-digits if digits else 1e-5)
        min_dist = stops_level * (point or 1)

        print(f"[{timestamp}] [GRID] {symbol} tick={getattr(tick,'ask',None)} base_price={current_price} pending_count={len(pending_prices)}")

        base_nearest = align_price_to_grid_symbol(symbol, current_price, brick_size, mode="nearest")

        # BUY_STOPs above
        if trade_side in ("buy", "both"):
            for i in range(1, CHECK_UP + 1):
                candidate_raw = base_nearest + i * brick_size
                candidate = align_price_to_grid_symbol(symbol, candidate_raw, brick_size, mode="up")

                print(f"[{timestamp}] [GRID] Candidate BUY_STOP: {candidate}")

                if candidate in pending_prices:
                    print(f"[{timestamp}] [DEBUG] BUY_STOP rejected: already pending")
                    continue
                if closed_levels and candidate in closed_levels:
                    print(f"[{timestamp}] [DEBUG] BUY_STOP rejected: in closed_levels")
                    continue
                if tick and candidate <= tick.ask + min_dist:
                    print(f"[{timestamp}] [DEBUG] BUY_STOP rejected: too close to ask+min_dist")
                    continue

                print(f"[{timestamp}] [GRID] Placing BUY_STOP @ {candidate}")
                placed = safe_place_order(getattr(mt5, "ORDER_TYPE_BUY_STOP", 2), symbol, candidate, lot, brick_size,
                                         sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels)
                if placed:
                    print(f"[{timestamp}] [GRID] BUY_STOP placed @ {candidate}")
                    pending_prices.add(candidate)
                else:
                    print(f"[{timestamp}] [GRID] BUY_STOP FAILED/REJECTED @ {candidate}")

        # SELL_STOPs below
        if trade_side in ("sell", "both"):
            for i in range(1, CHECK_DOWN + 1):
                candidate_raw = base_nearest - i * brick_size
                candidate = align_price_to_grid_symbol(symbol, candidate_raw, brick_size, mode="down")

                print(f"[{timestamp}] [GRID] Candidate SELL_STOP: {candidate}")

                if candidate in pending_prices:
                    print(f"[{timestamp}] [DEBUG] SELL_STOP rejected: already pending")
                    continue
                if closed_levels and candidate in closed_levels:
                    print(f"[{timestamp}] [DEBUG] SELL_STOP rejected: in closed_levels")
                    continue
                if tick and candidate >= tick.bid - min_dist:
                    print(f"[{timestamp}] [DEBUG] SELL_STOP rejected: too close to bid-min_dist")
                    continue

                print(f"[{timestamp}] [GRID] Placing SELL_STOP @ {candidate}")
                placed = safe_place_order(getattr(mt5, "ORDER_TYPE_SELL_STOP", 3), symbol, candidate, lot, brick_size,
                                         sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels)
                if placed:
                    print(f"[{timestamp}] [GRID] SELL_STOP placed @ {candidate}")
                    pending_prices.add(candidate)
                else:
                    print(f"[{timestamp}] [GRID] SELL_STOP FAILED/REJECTED @ {candidate}")

    except Exception as e:
        print(f"[{timestamp}] [ERROR] update_grid exception: {e}")
        traceback.print_exc()

# ----------------- Mirror-on-execution logic -----------------
def handle_new_positions_and_create_mirrors(symbol, brick_size, lot, seen_tickets, sym_cfg,
                                            trade_side="both", sl_pips=None, tp_pips=None,
                                            closed_levels=None, closed_block_seconds=300):
    """
    When new positions appear, create mirror pending orders on the opposite side.
    Uses safe_place_order to avoid re-adding duplicates (checks pending cache and open positions).
    Also updates closed_levels for positions that disappeared.
    Range-based open-pos checks are used so mirrors are not placed if an open pos exists near the target price.
    """
    try:
        positions = fetch_positions(symbol) or []
        created = False
        current_tickets = set()

        initial_buy = sym_cfg.get("initial_levels_buy", sym_cfg.get("max_up", 0))
        initial_sell = sym_cfg.get("initial_levels_sell", sym_cfg.get("max_down", 0))

        open_pos_prices = {align_price_to_grid_symbol(symbol, getattr(p, "price_open", getattr(p, "price", 0)), brick_size) for p in positions}

        # threshold for "near" check
        info = mt5.symbol_info(symbol)
        digits = getattr(info, "digits", 5) if info else 5
        point = getattr(info, "point", 10**-digits if digits else 1e-5)
        threshold = brick_size if (brick_size and brick_size > 0) else (point or 1e-5)

        for p in positions:
            ticket = getattr(p, "ticket", None) or f"{getattr(p,'price_open',0)}_{getattr(p,'volume',0)}"
            current_tickets.add(ticket)

            if ticket not in seen_tickets:
                typ = int(getattr(p, "type", -1))
                price_open = align_price_to_grid_symbol(symbol, getattr(p, "price_open", getattr(p, "price", None)), brick_size)
                vol = getattr(p, "volume", lot)

                # Mirror logic: create opposite orders dynamically
                if typ in (0, getattr(mt5, "ORDER_TYPE_BUY", 0)) and trade_side in ("sell", "both"):
                    for i in range(1, int(initial_sell) + 1):
                        sell_price = align_price_to_grid_symbol(symbol, price_open - brick_size * i, brick_size, mode="down")
                        # range-based open-pos check
                        if closed_levels and sell_price in closed_levels:
                            continue
                        if any(abs(sell_price - op) < threshold for op in open_pos_prices):
                            continue
                        if sell_price in _pending_cache.get(symbol, set()):
                            continue
                        if not order_exists(symbol, sell_price, mt5.ORDER_TYPE_SELL_STOP):
                            placed = safe_place_order(mt5.ORDER_TYPE_SELL_STOP, symbol, sell_price, vol, brick_size, sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels)
                            if placed:
                                created = True

                elif typ in (1, getattr(mt5, "ORDER_TYPE_SELL", 1)) and trade_side in ("buy", "both"):
                    for i in range(1, int(initial_buy) + 1):
                        buy_price = align_price_to_grid_symbol(symbol, price_open + brick_size * i, brick_size, mode="up")
                        if closed_levels and buy_price in closed_levels:
                            continue
                        if any(abs(buy_price - op) < threshold for op in open_pos_prices):
                            continue
                        if buy_price in _pending_cache.get(symbol, set()):
                            continue
                        if not order_exists(symbol, buy_price, mt5.ORDER_TYPE_BUY_STOP):
                            placed = safe_place_order(mt5.ORDER_TYPE_BUY_STOP, symbol, buy_price, vol, brick_size, sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels)
                            if placed:
                                created = True

                seen_tickets.add(ticket)

        # Cleanup closed tickets
        for t in list(seen_tickets):
            if t not in current_tickets:
                try:
                    if isinstance(t, str) and "_" in t:
                        pstr = t.split("_")[0]
                        closed_price = float(pstr)
                        if closed_levels is not None:
                            closed_levels[closed_price] = time.time()
                        for sym in _pending_cache:
                            if closed_price in _pending_cache[sym]:
                                _pending_cache[sym].discard(closed_price)
                except Exception:
                    pass
                seen_tickets.discard(t)

        return created

    except Exception as e:
        print("[ERROR] handle_new_positions_and_create_mirrors exception:", e)
        traceback.print_exc()
        return False

# ----------------- Main Grid Loop -----------------
def run_dynamic_grid(config, trading_active_flag=lambda: True):
    """Main bot loop: strict single-step rolling grid with mirrors and anchors."""
    if not initialize_mt5(config["account"], config["password"], config["server"]):
        return

    print("[INFO] Starting Symmetrical Rolling Grid Bot...")
    last_price = {}
    seen_position_tickets = {sym: set() for sym in config["symbols"]}
    closed_levels = defaultdict(float)
    initial_anchors = {sym: set() for sym in config["symbols"]}

    while trading_active_flag():
        try:
            # Remove expired closed levels
            now = time.time()
            block_seconds = config.get("closed_level_block_seconds", 300)
            for price_key in list(closed_levels.keys()):
                if now - closed_levels[price_key] > block_seconds:
                    del closed_levels[price_key]

            for symbol, sym_cfg in config["symbols"].items():
                if not trading_active_flag() or not sym_cfg.get("active", True):
                    continue

                tick = get_tick(symbol)
                if not tick or tick.ask is None:
                    continue
                price = tick.ask

                if symbol in last_price and abs(price - last_price[symbol]) < config.get("grid_tolerance", 0.0):
                    continue

                brick_size = sym_cfg["brick_size"]
                max_up = sym_cfg.get("max_up", 0)
                max_down = sym_cfg.get("max_down", 0)
                lot_size = sym_cfg["lot_size"]
                trade_side = sym_cfg.get("trade_side", "both")
                sl_pips = sym_cfg.get("stop_loss_pips")
                tp_pips = sym_cfg.get("take_profit_pips")
                rounding_mode = sym_cfg.get("grid_rounding", "nearest")

                aligned_base = align_price_to_grid_symbol(symbol, price, brick_size, mode=rounding_mode)

                # Sync pending at start of symbol loop to reduce race conditions
                pending = fetch_pending_orders(symbol) or []
                pending_prices = {align_price_to_grid_symbol(symbol, o.price_open, brick_size)
                                  for o in pending if getattr(o, "price_open", None) is not None}
                _pending_cache[symbol] = pending_prices

                initial_buy = sym_cfg.get("initial_levels_buy", max_up)
                initial_sell = sym_cfg.get("initial_levels_sell", max_down)

                # ---- INITIAL GRID ----
                if (not pending) and (not initial_anchors.get(symbol)):
                    if trade_side in ("buy", "both"):
                        for i in range(1, int(initial_buy) + 1):
                            p = align_price_to_grid_symbol(symbol, aligned_base + brick_size * i, brick_size, mode="up")
                            if p not in pending_prices:
                                safe_place_order(mt5.ORDER_TYPE_BUY_STOP, symbol, p, lot_size, brick_size, sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels)
                                initial_anchors[symbol].add(p)
                    if trade_side in ("sell", "both"):
                        for i in range(1, int(initial_sell) + 1):
                            p = align_price_to_grid_symbol(symbol, aligned_base - brick_size * i, brick_size, mode="down")
                            if p not in pending_prices:
                                safe_place_order(mt5.ORDER_TYPE_SELL_STOP, symbol, p, lot_size, brick_size, sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels)
                                initial_anchors[symbol].add(p)

                # ---- UPDATE GRID (ADVANCED: fills missing levels every tick) ----
                update_grid(
                    symbol=symbol,
                    current_price=price,
                    brick_size=brick_size,
                    lot=lot_size,
                    trade_side=trade_side,
                    sl_pips=sl_pips,
                    tp_pips=tp_pips,
                    closed_levels=closed_levels,
                    initial_buy_levels=max_up,
                    initial_sell_levels=max_down
                )

                # ---- HANDLE MIRRORS ----
                handle_new_positions_and_create_mirrors(
                    symbol, brick_size, lot_size, seen_position_tickets[symbol], sym_cfg,
                    trade_side, sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels,
                    closed_block_seconds=config.get("closed_level_block_seconds", 300)
                )

                last_price[symbol] = price

            time.sleep(config.get("loop_delay", 1))

        except KeyboardInterrupt:
            print("[INFO] Stopping manually (Ctrl+C)")
            break
        except Exception as e:
            print("[ERROR] run_dynamic_grid exception:", e)
            traceback.print_exc()
            time.sleep(1)

    print("[INFO] Exiting run_dynamic_grid loop")
