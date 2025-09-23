# utils.py — Symmetrical Rolling/Expanding Grid Bot (core logic only)

import MetaTrader5 as mt5
import time
import traceback
from collections import defaultdict

# ----------------- Config / Limits -----------------
MAX_ORDERS_PER_SYMBOL = 60  # total pending orders per symbol
DEFAULT_MAGIC = 123456

# ----------------- External imports -----------------
from utils.order_manager import (
    place_order, remove_order, order_exists, can_place_order,
    cancel_far_orders, cancel_far_orders_preserve
)
from utils.helpers import (  # ✅ moved helpers into helpers.py
    round_price, get_tick, fetch_pending_orders, fetch_positions,
    align_price_to_grid_symbol, highest_buy_position, lowest_sell_position
)

# ----------------- MT5 Initialization -----------------
def initialize_mt5(account, password, server):
    """Initialize and login MT5. Returns True on success."""
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

# ----------------- Rolling/Expanding Grid Logic -----------------
def update_grid(symbol, current_price, brick_size, max_up, max_down, lot,
                trade_side="both", sl_pips=None, tp_pips=None, closed_levels=None):
    """
    Places buy_stop levels above buy_base and sell_stop levels below sell_base.
    """
    try:
        current_price = round_price(symbol, current_price)
        pending = fetch_pending_orders(symbol)

        buys_pending = sorted([round_price(symbol, o.price_open) for o in pending if int(o.type) == mt5.ORDER_TYPE_BUY_STOP])
        sells_pending = sorted([round_price(symbol, o.price_open) for o in pending if int(o.type) == mt5.ORDER_TYPE_SELL_STOP])

        highest_buy_pos = highest_buy_position(symbol)
        lowest_sell_pos = lowest_sell_position(symbol)

        # determine buy_base
        if buys_pending:
            highest_pending_buy = buys_pending[-1]
            if highest_pending_buy <= current_price:
                buy_base = max(current_price, highest_buy_pos) if highest_buy_pos else current_price
            else:
                buy_base = max(highest_pending_buy, highest_buy_pos) if highest_buy_pos else highest_pending_buy
        else:
            buy_base = max(current_price, highest_buy_pos) if highest_buy_pos else current_price

        # determine sell_base
        if sells_pending:
            lowest_pending_sell = sells_pending[0]
            if lowest_pending_sell >= current_price:
                sell_base = min(current_price, lowest_sell_pos) if lowest_sell_pos else current_price
            else:
                sell_base = min(lowest_pending_sell, lowest_sell_pos) if lowest_sell_pos else lowest_pending_sell
        else:
            sell_base = min(current_price, lowest_sell_pos) if lowest_sell_pos else current_price

        # --- Place expansion orders ---
        if trade_side in ("buy", "both"):
            existing_up_orders = [p for p in buys_pending if p > buy_base]
            for i in range(1, max_up + 1):
                price = round_price(symbol, buy_base + brick_size * i)
                if closed_levels and price in closed_levels:
                    continue
                if not order_exists(symbol, price, mt5.ORDER_TYPE_BUY_STOP) and price not in existing_up_orders:
                    place_order(mt5.ORDER_TYPE_BUY_STOP, symbol, price, lot, sl_pips=sl_pips, tp_pips=tp_pips)

        if trade_side in ("sell", "both"):
            existing_down_orders = [p for p in sells_pending if p < sell_base]
            for i in range(1, max_down + 1):
                price = round_price(symbol, sell_base - brick_size * i)
                if closed_levels and price in closed_levels:
                    continue
                if not order_exists(symbol, price, mt5.ORDER_TYPE_SELL_STOP) and price not in existing_down_orders:
                    place_order(mt5.ORDER_TYPE_SELL_STOP, symbol, price, lot, sl_pips=sl_pips, tp_pips=tp_pips)

    except Exception as e:
        print("[ERROR] update_grid exception:", e)
        traceback.print_exc()

# ----------------- Mirror-on-execution logic -----------------
def handle_new_positions_and_create_mirrors(symbol, brick_size, lot, seen_tickets, sym_cfg,
                                            trade_side="both", sl_pips=None, tp_pips=None,
                                            closed_levels=None, closed_block_seconds=300):
    """When new positions appear, create mirror pending orders."""
    try:
        positions = fetch_positions(symbol)
        created = False
        current_tickets = set()

        initial_buy = sym_cfg.get("initial_levels_buy", sym_cfg.get("max_up", 0))
        initial_sell = sym_cfg.get("initial_levels_sell", sym_cfg.get("max_down", 0))

        for p in positions:
            ticket = getattr(p, "ticket", None) or f"{getattr(p,'price_open',0)}_{getattr(p,'volume',0)}"
            current_tickets.add(ticket)

            if ticket not in seen_tickets:
                typ = int(getattr(p, "type", -1))
                price_open = round_price(symbol, getattr(p, "price_open", getattr(p, "price", None)))
                vol = getattr(p, "volume", lot)

                if typ in (0, getattr(mt5, "ORDER_TYPE_BUY", 0)) and trade_side in ("sell", "both"):
                    for i in range(1, initial_sell + 1):
                        sell_price = round_price(symbol, price_open - brick_size * i)
                        if closed_levels and sell_price in closed_levels:
                            continue
                        if not order_exists(symbol, sell_price, mt5.ORDER_TYPE_SELL_STOP):
                            place_order(mt5.ORDER_TYPE_SELL_STOP, symbol, sell_price, vol, sl_pips=sl_pips, tp_pips=tp_pips)
                            created = True

                elif typ in (1, getattr(mt5, "ORDER_TYPE_SELL", 1)) and trade_side in ("buy", "both"):
                    for i in range(1, initial_buy + 1):
                        buy_price = round_price(symbol, price_open + brick_size * i)
                        if closed_levels and buy_price in closed_levels:
                            continue
                        if not order_exists(symbol, buy_price, mt5.ORDER_TYPE_BUY_STOP):
                            place_order(mt5.ORDER_TYPE_BUY_STOP, symbol, buy_price, vol, sl_pips=sl_pips, tp_pips=tp_pips)
                            created = True

                seen_tickets.add(ticket)

        # cleanup closed tickets
        for t in list(seen_tickets):
            if t not in current_tickets:
                try:
                    if isinstance(t, str) and "_" in t:
                        pstr = t.split("_")[0]
                        closed_price = float(pstr)
                        if closed_levels is not None:
                            closed_levels[closed_price] = time.time()
                except Exception:
                    pass
                seen_tickets.discard(t)

        return created

    except Exception as e:
        print("[ERROR] handle_new_positions_and_create_mirrors exception:", e)
        traceback.print_exc()
        return False

# ----------------- Global Stop Loss -----------------
def check_global_stop_loss(max_loss):
    """Returns True if global stop loss is hit."""
    try:
        info = mt5.account_info()
        if not info:
            return False
        drawdown = info.balance - info.equity
        if drawdown >= max_loss:
            print(f"[ALERT] Global stop loss triggered: drawdown={drawdown} >= {max_loss}")
            return True
        return False
    except Exception as e:
        print("[ERROR] check_global_stop_loss exception:", e)
        traceback.print_exc()
        return False

# ----------------- Main Grid Loop -----------------
def run_dynamic_grid(config, trading_active_flag=lambda: True):
    """Main bot loop: place anchors, expand grid, mirror positions, cancel far orders."""
    if not initialize_mt5(config["account"], config["password"], config["server"]):
        return

    print("[INFO] Starting Symmetrical Rolling Grid Bot...")
    last_price = {}
    seen_position_tickets = {sym: set() for sym in config["symbols"]}
    closed_levels = defaultdict(float)
    initial_anchors = {sym: set() for sym in config["symbols"]}

    def ensure_anchors_exist(symbol, anchors_set, aligned_base, brick_size, lot_size, trade_side, sl_pips, tp_pips):
        """Recreate missing anchors if user removed them manually."""
        for anchor_price in list(anchors_set):
            try:
                if order_exists(symbol, anchor_price, mt5.ORDER_TYPE_BUY_STOP) or order_exists(symbol, anchor_price, mt5.ORDER_TYPE_SELL_STOP):
                    continue
                if anchor_price > aligned_base and trade_side in ("buy", "both"):
                    place_order(mt5.ORDER_TYPE_BUY_STOP, symbol, anchor_price, lot_size, sl_pips=sl_pips, tp_pips=tp_pips)
                elif anchor_price < aligned_base and trade_side in ("sell", "both"):
                    place_order(mt5.ORDER_TYPE_SELL_STOP, symbol, anchor_price, lot_size, sl_pips=sl_pips, tp_pips=tp_pips)
            except Exception:
                continue

    while trading_active_flag():
        try:
            if check_global_stop_loss(config.get("global_stop_loss", 9999999)):
                print("[INFO] Stopping bot due to global stop loss")
                break

            now = time.time()
            block_seconds = config.get("closed_level_block_seconds", 300)
            for price_key in list(closed_levels.keys()):
                if now - closed_levels[price_key] > block_seconds:
                    del closed_levels[price_key]

            for symbol, sym_cfg in config["symbols"].items():
                if not trading_active_flag():
                    print("[INFO] Trading stop triggered. Exiting loop.")
                    return

                if not sym_cfg.get("active", True):
                    continue

                tick = get_tick(symbol)
                if not tick or tick.ask is None:
                    continue
                price = tick.ask

                if config.get("grid_tolerance", 0.0) > 0 and symbol in last_price and abs(price - last_price[symbol]) < config.get("grid_tolerance", 0.0):
                    continue

                brick_size = sym_cfg["brick_size"]
                max_up = sym_cfg.get("max_up", 0)
                max_down = sym_cfg.get("max_down", 0)
                lot_size = sym_cfg["lot_size"]
                trade_side = sym_cfg.get("trade_side", "both")
                rounding_mode = sym_cfg.get("grid_rounding", "nearest")
                sl_pips = sym_cfg.get("stop_loss_pips")
                tp_pips = sym_cfg.get("take_profit_pips")

                aligned_base = align_price_to_grid_symbol(symbol, price, brick_size, mode=rounding_mode)

                pending = fetch_pending_orders(symbol)
                pending_prices = {round_price(symbol, o.price_open) for o in pending if getattr(o, "price_open", None) is not None}

                initial_buy = sym_cfg.get("initial_levels_buy", 0)
                initial_sell = sym_cfg.get("initial_levels_sell", 0)

                if (not pending) and (not initial_anchors.get(symbol)):
                    if trade_side in ("buy", "both"):
                        for i in range(1, int(initial_buy) + 1):
                            p = round_price(symbol, aligned_base + brick_size * i)
                            if p not in pending_prices:
                                place_order(mt5.ORDER_TYPE_BUY_STOP, symbol, p, lot_size, sl_pips=sl_pips, tp_pips=tp_pips)
                                initial_anchors[symbol].add(p)
                    if trade_side in ("sell", "both"):
                        for i in range(1, int(initial_sell) + 1):
                            p = round_price(symbol, aligned_base - brick_size * i)
                            if p not in pending_prices:
                                place_order(mt5.ORDER_TYPE_SELL_STOP, symbol, p, lot_size, sl_pips=sl_pips, tp_pips=tp_pips)
                                initial_anchors[symbol].add(p)

                if initial_anchors.get(symbol):
                    ensure_anchors_exist(symbol, initial_anchors[symbol], aligned_base, brick_size, lot_size, trade_side, sl_pips, tp_pips)

                cancel_far_orders_preserve(symbol, price, brick_size, max_up, max_down, preserve_prices=initial_anchors.get(symbol, set()))

                created_by_mirror = handle_new_positions_and_create_mirrors(
                    symbol, brick_size, lot_size, seen_position_tickets[symbol], sym_cfg,
                    trade_side, sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels,
                    closed_block_seconds=config.get("closed_level_block_seconds", 300)
                )
                if created_by_mirror:
                    print(f"[MIRROR] created mirror orders for {symbol}")

                update_grid(symbol, price, brick_size, max_up, max_down, lot_size,
                            trade_side, sl_pips=sl_pips, tp_pips=tp_pips, closed_levels=closed_levels)

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
