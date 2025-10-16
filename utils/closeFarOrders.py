
import MetaTrader5 as mt5
import json
import time  # ✅ ye add karo
import traceback
def remove_extra_pending_orders():
    try:
        with open("config.json") as f:
            cfg = json.load(f)
    except Exception:
        print("⚠️ Could not load config.json")
        return

    if not mt5.initialize():
        print("⚠️ MT5 init failed")
        return

    for symbol, sym_cfg in cfg.get("symbols", {}).items():
        # 🔹 Skip inactive symbols
        if not sym_cfg.get("active", False):
            continue

        # 🔹 NEW CHECK: run only if farClose == true
        if not sym_cfg.get("farClose", False):
            continue

        max_up = int(sym_cfg.get("max_up", 0))
        max_down = int(sym_cfg.get("max_down", 0))

        # ✅ Only if farClose true, perform cleaning
        orders = mt5.orders_get(symbol=symbol)
        if not orders:
            continue

        buy_orders = [o for o in orders if o.type == mt5.ORDER_TYPE_BUY_STOP]
        sell_orders = [o for o in orders if o.type == mt5.ORDER_TYPE_SELL_STOP]

        if len(buy_orders) > max_up:
            # sort descending price (remove farthest)
            buy_orders_sorted = sorted(buy_orders, key=lambda x: x.price_open, reverse=True)
            for o in buy_orders_sorted[max_up:]:
                mt5.order_delete(o.ticket)
                print(f"[{symbol}] Removed extra BUY_STOP {o.ticket}")

        if len(sell_orders) > max_down:
            # sort ascending price (remove farthest)
            sell_orders_sorted = sorted(sell_orders, key=lambda x: x.price_open)
            for o in sell_orders_sorted[max_down:]:
                mt5.order_delete(o.ticket)
                print(f"[{symbol}] Removed extra SELL_STOP {o.ticket}")
def run_auto_cleaner(interval: int = 10):
    while True:
        try:
            remove_extra_pending_orders()
        except Exception as e:
            print("Cleaner error:", e)
        time.sleep(interval)