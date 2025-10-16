# utils.py — Symmetrical Rolling/Expanding Grid Bot (multi-symbol, thread-safe, robust ticks)
import MetaTrader5 as mt5
import time
import traceback
import math
from collections import defaultdict
import datetime
from threading import Thread, RLock
from .trailingStopLoss import start_trailing_loop
from threading import Thread
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

# ----------------- Thread-safety primitives -----------------
mt5_lock = RLock()

# per-symbol last_price mapping used for grid_tolerance checks
last_price = {}

# ----------------- Helper caches / utilities -----------------
_pending_cache = defaultdict(set)

# ----------------- Utility: ensure symbol available -----------------
def ensure_symbol_available(symbol, tries=4, delay=0.25):
    """Ensure symbol is in MarketWatch and returns a valid tick/info.
    Returns True if symbol appears selectable and has a tick; False otherwise.
    This function acquires mt5_lock only around MT5 calls so it's safe to call from threads.
    """
    try:
        for attempt in range(tries):
            try:
                with mt5_lock:
                    # try to add/select the symbol in MarketWatch
                    try:
                        mt5.symbol_select(symbol, True)
                    except Exception:
                        pass
                    info = mt5.symbol_info(symbol)
                    tick = mt5.symbol_info_tick(symbol)
            except Exception:
                info = None
                tick = None

            if info is not None and tick is not None and getattr(tick, 'ask', None) is not None:
                return True
            time.sleep(delay)
    except Exception:
        pass
    return False

# ----------------- Robust tick fetcher -----------------
def fetch_tick_safe(symbol):
    """Try multiple ways to get a tick for symbol. Prefer direct MT5 api under lock, fallback to get_tick helper.
    Returns a tick object or None.
    """
    # 1) Try direct MT5 symbol_info_tick under lock
    try:
        with mt5_lock:
            tick = mt5.symbol_info_tick(symbol)
        if tick is not None and getattr(tick, 'ask', None) is not None:
            return tick
    except Exception:
        pass

    # 2) Try helper get_tick(symbol)
    try:
        tk = get_tick(symbol)
        if tk is not None and getattr(tk, 'ask', None) is not None:
            return tk
    except Exception:
        pass

    # 3) As last resort, attempt to ensure symbol available and try mt5 again
    ok = ensure_symbol_available(symbol, tries=2, delay=0.1)
    if ok:
        try:
            with mt5_lock:
                tick = mt5.symbol_info_tick(symbol)
            return tick
        except Exception:
            return None
    return None

# ----------------- Price Alignment -----------------
def align_price_to_grid_symbol(symbol, price, brick_size, mode="nearest"):
    if brick_size is None or brick_size <= 0:
        try:
            return round(float(price), 8)
        except Exception:
            return price
    try:
        ratio = float(price) / float(brick_size)
    except Exception:
        try:
            return round(float(price), 8)
        except Exception:
            return price

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
            return False
        if not mt5.login(account, password=password, server=server):
            return False
        return True
    except Exception as e:
        return False

# after mt5.initialize() in your main grid launcher
t = Thread(target=start_trailing_loop, kwargs={
    "config_path": "config.json",
    "trading_active_flag": lambda: True,
    "mt5_lock": mt5_lock
}, daemon=True)
t.start()
# ----------------- Helper caches / utilities -----------------
def sync_pending_cache(symbol, brick_size):
    """Populate _pending_cache[symbol] with aligned pending prices from broker."""
    try:
        with mt5_lock:
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
    try:
        with mt5_lock:
            positions = fetch_positions(symbol) or []
        return {align_price_to_grid_symbol(symbol, getattr(p, "price_open", getattr(p, "price", 0)), brick_size) for p in positions}
    except Exception:
        return set()

def get_open_positions_info(symbol, brick_size):
    """Return list of dicts: [{'ticket':..., 'type':0/1, 'raw_price':..., 'aligned':...}, ...]"""
    out = []
    try:
        with mt5_lock:
            positions = fetch_positions(symbol) or []
        for p in positions:
            try:
                ticket = getattr(p, "ticket", None) or f"{getattr(p,'price_open',0)}_{getattr(p,'volume',0)}"
                typ = int(getattr(p, "type", -1))  # 0=BUY,1=SELL typically
                raw = getattr(p, "price_open", getattr(p, "price", None))
                aligned = align_price_to_grid_symbol(symbol, raw, brick_size)
                out.append({"ticket": ticket, "type": typ, "raw": raw, "aligned": aligned})
            except Exception:
                continue
    except Exception:
        pass
    return out

def _place_order_and_handle_return(order_type, symbol, price_aligned, volume, sl_pips=None, tp_pips=None):
    try:
        with mt5_lock:
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

# ----------------- New utility check: ensure level has no existing pending/order/position -----------------
def level_has_existing_order_or_position(symbol, price_aligned, brick_size):
    """
    Returns True if there already exists a pending order (broker-side or cached) OR an open position
    at the given aligned level for this symbol.
    This function acquires mt5_lock where needed.
    """
    try:
        # 1) local cache quick-check
        if price_aligned in _pending_cache.get(symbol, set()):
            return True

        # 2) broker pending orders
        with mt5_lock:
            pending = fetch_pending_orders(symbol) or []
        for o in pending:
            po = getattr(o, "price_open", None)
            if po is None:
                continue
            if align_price_to_grid_symbol(symbol, po, brick_size) == price_aligned:
                # update cache for future
                _pending_cache[symbol].add(price_aligned)
                return True

        # 3) open positions check
        with mt5_lock:
            positions = fetch_positions(symbol) or []
        for p in positions:
            raw = getattr(p, "price_open", getattr(p, "price", None))
            if raw is None:
                continue
            if align_price_to_grid_symbol(symbol, raw, brick_size) == price_aligned:
                return True

        return False
    except Exception:
        # be conservative: if we can't be sure, assume exists (to avoid duplicate)
        return True

# ----------------- safe_place_order (locks minimally) -----------------
def safe_place_order(order_type, symbol, price, volume, brick_size, sl_pips=None, tp_pips=None, closed_levels=None):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    prefix = f"[{timestamp}] [{symbol}]"
    try:
        # 1️⃣ Align price (pure CPU)
        price_aligned = align_price_to_grid_symbol(symbol, price, brick_size)

        # 2️⃣ Closed levels
        if closed_levels and price_aligned in closed_levels:
            return False

        # 3️⃣ Check if level already has existing pending or open pos
        if level_has_existing_order_or_position(symbol, price_aligned, brick_size):
            return False

        # 4️⃣ Server-side pending check via order_exists as last confirmation
        try:
            with mt5_lock:
                exists = order_exists(symbol, price_aligned, order_type)
        except Exception:
            exists = False
        if exists:
            _pending_cache[symbol].add(price_aligned)
            return False

        # 5️⃣ symbol info & tick (under lock)
        with mt5_lock:
            info = mt5.symbol_info(symbol)
            tick = mt5.symbol_info_tick(symbol)

        digits = getattr(info, "digits", 5) if info else 5
        point = getattr(info, "point", 10**-digits if digits else 1e-5)

        # compute a conservative SMALL threshold (avoid blocking whole bricks):
        if brick_size and brick_size > 0:
            threshold = min(float(brick_size) / 10.0, float(point) / 10.0)
        else:
            threshold = float(point) / 10.0
        if threshold <= 0:
            threshold = float(point) or 1e-5

        # 6️⃣ Open positions check (type-aware)  -> already covered by level_has_existing..., but keep extra guard
        open_info = get_open_positions_info(symbol, brick_size)

        # Decide blocking: only block if an OPPOSING open position exists too-close to candidate.
        buy_constant = getattr(mt5, "ORDER_TYPE_BUY", 0)
        sell_constant = getattr(mt5, "ORDER_TYPE_SELL", 1)
        buy_stop_const = getattr(mt5, "ORDER_TYPE_BUY_STOP", 2)
        sell_stop_const = getattr(mt5, "ORDER_TYPE_SELL_STOP", 3)

        for oi in open_info:
            pos_type = int(oi.get("type", -1))
            aligned_op = oi.get("aligned", None)
            if aligned_op is None:
                continue
            # If placing BUY_STOP, block only if there's an existing SELL open near the same aligned price
            if order_type == buy_stop_const:
                if pos_type in (sell_constant, getattr(mt5, "POSITION_TYPE_SELL", 1)):
                    if abs(price_aligned - aligned_op) < threshold:
                        return False
            # If placing SELL_STOP, block only if there's an existing BUY open near the same aligned price
            if order_type == sell_stop_const:
                if pos_type in (buy_constant, getattr(mt5, "POSITION_TYPE_BUY", 0)):
                    if abs(price_aligned - aligned_op) < threshold:
                        return False

        # 7️⃣ Broker min distance
        if info and tick:
            stops_level = getattr(info, "trade_stops_level", 0) or 0
            min_dist = stops_level * (point or 1)

            if order_type == buy_stop_const:
                ask_val = (tick.ask if tick and getattr(tick, 'ask', None) is not None else 0)
                if price_aligned <= ask_val + min_dist:
                    return False
            if order_type == sell_stop_const:
                bid_val = (tick.bid if tick and getattr(tick, 'bid', None) is not None else 0)
                if price_aligned >= bid_val - min_dist:
                    return False

        # 8️⃣ Place order
        ok, msg = _place_order_and_handle_return(order_type, symbol, price_aligned, volume, sl_pips=sl_pips, tp_pips=tp_pips)
        if ok:
            _pending_cache[symbol].add(price_aligned)
            return True
        else:
            return False

    except Exception as e:
        traceback.print_exc()
        return False

# ----------------- update_grid (fixed logic using config levels) -----------------
def update_grid(symbol, current_price, brick_size, lot,
                trade_side="both", sl_pips=None, tp_pips=None, closed_levels=None,
                initial_buy_levels=0, initial_sell_levels=0):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    prefix = f"[{timestamp}] [{symbol}]"
    try:
        # Set number of grid levels from config
        CHECK_UP = int(initial_buy_levels) if initial_buy_levels else 0
        CHECK_DOWN = int(initial_sell_levels) if initial_sell_levels else 0

        pending_prices = sync_pending_cache(symbol, brick_size) or set()
        with mt5_lock:
            tick = mt5.symbol_info_tick(symbol)
            info = mt5.symbol_info(symbol)
        stops_level = getattr(info, "trade_stops_level", 0) if info else 0
        digits = getattr(info, "digits", 5) if info else 5
        point = getattr(info, "point", 10**-digits if digits else 1e-5)
        min_dist = stops_level * (point or 1)

        base_nearest = align_price_to_grid_symbol(symbol, current_price, brick_size, mode="nearest")

        # BUY_STOPs
        if trade_side in ("buy", "both"):
            for i in range(1, CHECK_UP + 1):
                candidate_raw = base_nearest + i * brick_size
                candidate = align_price_to_grid_symbol(symbol, candidate_raw, brick_size, mode="up")

                if candidate in pending_prices:
                    continue
                if closed_levels and candidate in closed_levels:
                    continue

                # additional robust check: existing pending or positions at same level
                if level_has_existing_order_or_position(symbol, candidate, brick_size):
                    continue

                if tick and candidate <= tick.ask + min_dist:
                    continue

                placed = safe_place_order(getattr(mt5, "ORDER_TYPE_BUY_STOP", 2),
                                         symbol, candidate, lot, brick_size,
                                         sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels)
                if placed:
                    pending_prices.add(candidate)
                else:
                    pass

        # SELL_STOPs
        if trade_side in ("sell", "both"):
            for i in range(1, CHECK_DOWN + 1):
                candidate_raw = base_nearest - i * brick_size
                candidate = align_price_to_grid_symbol(symbol, candidate_raw, brick_size, mode="down")

                if candidate in pending_prices:
                    continue
                if closed_levels and candidate in closed_levels:
                    continue

                if level_has_existing_order_or_position(symbol, candidate, brick_size):
                    continue

                if tick and candidate >= tick.bid - min_dist:
                    continue

                placed = safe_place_order(getattr(mt5, "ORDER_TYPE_SELL_STOP", 3),
                                         symbol, candidate, lot, brick_size,
                                         sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels)
                if placed:
                    pending_prices.add(candidate)
                else:
                    pass

    except Exception as e:
        traceback.print_exc()

# ----------------- Mirror-on-execution logic (unchanged but type-aware) -----------------
def handle_new_positions_and_create_mirrors(symbol, brick_size, lot, seen_tickets, sym_cfg,
                                            trade_side="both", sl_pips=None, tp_pips=None,
                                            closed_levels=None, closed_block_seconds=300):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    prefix = f"[{timestamp}] [{symbol}]"
    try:
        positions = fetch_positions(symbol) or []
        created = False
        current_tickets = set()

        initial_buy = sym_cfg.get("initial_levels_buy", sym_cfg.get("max_up", 0))
        initial_sell = sym_cfg.get("initial_levels_sell", sym_cfg.get("max_down", 0))

        open_pos_prices = {align_price_to_grid_symbol(symbol, getattr(p, "price_open", getattr(p, "price", 0)), brick_size) for p in positions}

        with mt5_lock:
            info = mt5.symbol_info(symbol)
        digits = getattr(info, "digits", 5) if info else 5
        point = getattr(info, "point", 10**-digits if digits else 1e-5)

        # threshold small: min(brick/10, point/10) to avoid blocking neighbours due to rounding
        if brick_size and brick_size > 0:
            threshold = min(float(brick_size) / 10.0, float(point) / 10.0)
        else:
            threshold = float(point) / 10.0
        if threshold <= 0:
            threshold = float(point) or 1e-5

        for p in positions:
            ticket = getattr(p, "ticket", None) or f"{getattr(p,'price_open',0)}_{getattr(p,'volume',0)}"
            current_tickets.add(ticket)

            if ticket not in seen_tickets:
                typ = int(getattr(p, "type", -1))  # 0=BUY,1=SELL usually
                price_open = align_price_to_grid_symbol(symbol, getattr(p, "price_open", getattr(p, "price", None)), brick_size)
                vol = getattr(p, "volume", lot)

                # If this is a BUY position, create SELL_STOP mirrors below (if allowed)
                if typ in (0, getattr(mt5, "ORDER_TYPE_BUY", 0)) and trade_side in ("sell", "both"):
                    for i in range(1, int(initial_sell) + 1):
                        sell_price = align_price_to_grid_symbol(symbol, price_open - brick_size * i, brick_size, mode="down")
                        if closed_levels and sell_price in closed_levels:
                            continue
                        # Skip if any existing order/position at that level
                        if level_has_existing_order_or_position(symbol, sell_price, brick_size):
                            continue
                        if sell_price in _pending_cache.get(symbol, set()):
                            continue
                        # server-side order_exists
                        try:
                            with mt5_lock:
                                exists = order_exists(symbol, sell_price, mt5.ORDER_TYPE_SELL_STOP)
                        except Exception:
                            exists = False
                        if exists:
                            _pending_cache[symbol].add(sell_price)
                            continue
                        placed = safe_place_order(mt5.ORDER_TYPE_SELL_STOP, symbol, sell_price, vol, brick_size, sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels)
                        if placed:
                            created = True

                # If this is a SELL position, create BUY_STOP mirrors above (if allowed)
                elif typ in (1, getattr(mt5, "ORDER_TYPE_SELL", 1)) and trade_side in ("buy", "both"):
                    for i in range(1, int(initial_buy) + 1):
                        buy_price = align_price_to_grid_symbol(symbol, price_open + brick_size * i, brick_size, mode="up")
                        if closed_levels and buy_price in closed_levels:
                            continue
                        if level_has_existing_order_or_position(symbol, buy_price, brick_size):
                            continue
                        if buy_price in _pending_cache.get(symbol, set()):
                            continue
                        try:
                            with mt5_lock:
                                exists = order_exists(symbol, buy_price, mt5.ORDER_TYPE_BUY_STOP)
                        except Exception:
                            exists = False
                        if exists:
                            _pending_cache[symbol].add(buy_price)
                            continue
                        placed = safe_place_order(mt5.ORDER_TYPE_BUY_STOP, symbol, buy_price, vol, brick_size, sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels)
                        if placed:
                            created = True

                seen_tickets.add(ticket)

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
        traceback.print_exc()
        return False

# ----------------- Per-symbol worker -----------------
def run_symbol_loop(symbol, sym_cfg, config, seen_tickets, closed_levels, initial_anchors, trading_active_flag):
    lot_size = sym_cfg["lot_size"]
    brick_size = sym_cfg["brick_size"]
    trade_side = sym_cfg.get("trade_side", "both")
    sl_pips = sym_cfg.get("stop_loss_pips")
    tp_pips = sym_cfg.get("take_profit_pips")
    rounding_mode = sym_cfg.get("grid_rounding", "nearest")
    loop_delay = config.get("loop_delay", 1)
    max_up = sym_cfg.get("max_up", 0)
    max_down = sym_cfg.get("max_down", 0)

    # try to make symbol available early
    ok = ensure_symbol_available(symbol, tries=3, delay=0.2)
    if not ok:
        pass

    while trading_active_flag():
        try:
            # cleanup closed levels
            now = time.time()
            block_seconds = config.get("closed_level_block_seconds", 300)
            for price_key in list(closed_levels.keys()):
                if now - closed_levels[price_key] > block_seconds:
                    del closed_levels[price_key]

            # fetch tick robustly
            tick = fetch_tick_safe(symbol)
            if not tick or getattr(tick, 'ask', None) is None:
                # no tick; try to ensure symbol and continue
                ensure_symbol_available(symbol, tries=1, delay=0.1)
                time.sleep(loop_delay)
                continue

            price = tick.ask

            # grid_tolerance check
            if symbol in last_price and abs(price - last_price.get(symbol, 0)) < config.get("grid_tolerance", 0.0):
                time.sleep(loop_delay)
                continue

            # keep local copies (do not change strategy logic)
            brick_size = sym_cfg["brick_size"]
            max_up = sym_cfg.get("max_up", 0)
            max_down = sym_cfg.get("max_down", 0)
            lot_size = sym_cfg["lot_size"]
            trade_side = sym_cfg.get("trade_side", "both")
            sl_pips = sym_cfg.get("stop_loss_pips")
            tp_pips = sym_cfg.get("take_profit_pips")
            rounding_mode = sym_cfg.get("grid_rounding", "nearest")

            aligned_base = align_price_to_grid_symbol(symbol, price, brick_size, mode=rounding_mode)

            # sync pending
            pending = fetch_pending_orders(symbol) or []
            pending_prices = {align_price_to_grid_symbol(symbol, o.price_open, brick_size)
                              for o in pending if getattr(o, "price_open", None) is not None}
            _pending_cache[symbol] = pending_prices

            initial_buy = sym_cfg.get("initial_levels_buy", max_up)
            initial_sell = sym_cfg.get("initial_levels_sell", max_down)

            # INITIAL GRID
            if (not pending) and (not initial_anchors.get(symbol)):
                if trade_side in ("buy", "both"):
                    for i in range(1, int(initial_buy) + 1):
                        p = align_price_to_grid_symbol(symbol, aligned_base + brick_size * i, brick_size, mode="up")
                        if p not in pending_prices and not level_has_existing_order_or_position(symbol, p, brick_size):
                            safe_place_order(mt5.ORDER_TYPE_BUY_STOP, symbol, p, lot_size, brick_size, sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels)
                            initial_anchors[symbol].add(p)
                if trade_side in ("sell", "both"):
                    for i in range(1, int(initial_sell) + 1):
                        p = align_price_to_grid_symbol(symbol, aligned_base - brick_size * i, brick_size, mode="down")
                        if p not in pending_prices and not level_has_existing_order_or_position(symbol, p, brick_size):
                            safe_place_order(mt5.ORDER_TYPE_SELL_STOP, symbol, p, lot_size, brick_size, sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels)
                            initial_anchors[symbol].add(p)

            # UPDATE GRID
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

            # HANDLE MIRRORS
            handle_new_positions_and_create_mirrors(
                symbol, brick_size, lot_size, seen_tickets, sym_cfg,
                trade_side, sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels,
                closed_block_seconds=config.get("closed_level_block_seconds", 300)
            )

            last_price[symbol] = price

            time.sleep(loop_delay)

        except Exception as e:
            traceback.print_exc()
            time.sleep(1)

# ----------------- Main Grid Loop -----------------
def run_dynamic_grid(config, trading_active_flag=lambda: True):
    if not initialize_mt5(config["account"], config["password"], config["server"]):
        return

    closed_levels = defaultdict(float)
    initial_anchors = {sym: set() for sym in config["symbols"]}
    seen_position_tickets = {sym: set() for sym in config["symbols"]}

    threads = []

    for symbol, sym_cfg in config["symbols"].items():
        if not sym_cfg.get("active", True):
            continue

        ok = ensure_symbol_available(symbol, tries=2, delay=0.1)
        if not ok:
            pass

        t = Thread(target=run_symbol_loop, args=(symbol, sym_cfg, config, seen_position_tickets[symbol], closed_levels, initial_anchors, trading_active_flag), daemon=True)
        t.start()
        threads.append(t)

    if not threads:
        return

    try:
        while trading_active_flag():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        pass
