import MetaTrader5 as mt5
import json
import time
import traceback


def remove_extra_pending_orders():
    try:
        with open("config.json") as f:
            cfg = json.load(f)
    except Exception:
        return

    if not mt5.initialize():
        return

    try:
        for symbol, sym_cfg in cfg.get("symbols", {}).items():
            if not sym_cfg.get("active", False):
                continue
            if not sym_cfg.get("farClose", False):
                continue

            max_up = int(sym_cfg.get("max_up", 0))
            max_down = int(sym_cfg.get("max_down", 0))

            symbol_info = mt5.symbol_info_tick(symbol)
            if not symbol_info:
                continue

            current_price = symbol_info.bid

            orders = mt5.orders_get(symbol=symbol)
            if not orders:
                continue

            buy_orders = [o for o in orders if o.type == mt5.ORDER_TYPE_BUY_STOP]
            sell_orders = [o for o in orders if o.type == mt5.ORDER_TYPE_SELL_STOP]

            # -------- Corrected logic: keep nearest, remove farthest --------
            if len(buy_orders) > max_up:
                # sort by distance from current price (nearest first)
                buy_sorted = sorted(buy_orders, key=lambda x: abs(x.price_open - current_price))
                # delete the farthest ones
                for o in buy_sorted[max_up:]:
                    mt5.order_send({
                        "action": mt5.TRADE_ACTION_REMOVE,
                        "order": o.ticket
                    })

            if len(sell_orders) > max_down:
                # sort by distance from current price (nearest first)
                sell_sorted = sorted(sell_orders, key=lambda x: abs(x.price_open - current_price))
                # delete the farthest ones
                for o in sell_sorted[max_down:]:
                    mt5.order_send({
                        "action": mt5.TRADE_ACTION_REMOVE,
                        "order": o.ticket
                    })

    except Exception:
        print("❌ Cleaner internal error:", traceback.format_exc())
    finally:
        mt5.shutdown()


def run_auto_cleaner(interval: int = 1):
    """Background cleaner loop"""
    while True:
        try:
            remove_extra_pending_orders()
        except Exception:
            pass
        time.sleep(interval)
