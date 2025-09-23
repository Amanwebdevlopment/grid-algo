import MetaTrader5 as mt5
import traceback
from utils.helpers import round_price, fetch_pending_orders, fetch_positions, symbol_precision  # ✅ utils.helpers se lo

DEFAULT_MAGIC = 123456
MAX_ORDERS_PER_SYMBOL = 60


# ----------------- Check & Exists -----------------
def order_exists(symbol, price, order_type):
    try:
        price = round_price(symbol, price)
        for o in fetch_pending_orders(symbol):
            if int(o.type) == int(order_type) and round_price(symbol, o.price_open) == price:
                return True
    except Exception:
        pass
    return False

def can_place_order(symbol):
    try:
        pending = fetch_pending_orders(symbol)
        if len(pending) >= MAX_ORDERS_PER_SYMBOL:
            print(f"[WARN] Max pending orders reached for {symbol} ({len(pending)})")
            return False
        return True
    except Exception as e:
        print(f"[WARN] can_place_order exception: {e}")
        return False

# ----------------- Place / Remove -----------------
def place_order(order_type, symbol, price, lot, magic=DEFAULT_MAGIC, sl_pips=None, tp_pips=None):
    try:
        if not can_place_order(symbol):
            return None
        price = round_price(symbol, price)
        if order_exists(symbol, price, order_type):
            return None

        sl, tp = None, None
        if sl_pips is not None:
            sl = round_price(symbol, price - sl_pips) if order_type == mt5.ORDER_TYPE_BUY_STOP else round_price(symbol, price + sl_pips)
        if tp_pips is not None:
            tp = round_price(symbol, price + tp_pips) if order_type == mt5.ORDER_TYPE_BUY_STOP else round_price(symbol, price - tp_pips)

        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "deviation": 10,
            "magic": int(magic),
            "comment": "GridBot",
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        if sl: request["sl"] = sl
        if tp: request["tp"] = tp

        result = mt5.order_send(request)
        if not result or getattr(result, "retcode", None) != mt5.TRADE_RETCODE_DONE:
            print(f"[ERROR] Failed to place {order_type} {symbol} @ {price}: {result}")
            return None

        print(f"[INFO] Order placed: {order_type} {symbol} @ {price} lot={lot} sl={sl} tp={tp}")
        return result
    except Exception as e:
        print(f"[ERROR] place_order exception for {symbol} @ {price}: {e}")
        traceback.print_exc()
        return None

def remove_order(order):
    try:
        req = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": int(order.ticket),
            "symbol": order.symbol,
            "magic": getattr(order, "magic", DEFAULT_MAGIC),
            "comment": "GridBot cancel",
        }
        res = mt5.order_send(req)
        if not res or getattr(res, "retcode", None) != mt5.TRADE_RETCODE_DONE:
            print(f"[ERROR] Failed to cancel order {order.ticket}: {res}")
            return None
        print(f"[INFO] Cancelled order {order.ticket} @ {order.price_open}")
        return res
    except Exception as e:
        print(f"[ERROR] remove_order exception: {e}")
        traceback.print_exc()
        return None

# ----------------- Cancel Orders -----------------
def cancel_far_orders(symbol, current_price, brick_size, max_up, max_down):
    """
    Remove pending orders that are too far away from current price.
    """
    try:
        upper_limit = current_price + brick_size * max_up
        lower_limit = current_price - brick_size * max_down
        for o in fetch_pending_orders(symbol):
            try:
                price_open = getattr(o, "price_open", None)
                if price_open is None:
                    continue
                if int(o.type) == mt5.ORDER_TYPE_BUY_STOP and price_open > upper_limit:
                    remove_order(o)
                elif int(o.type) == mt5.ORDER_TYPE_SELL_STOP and price_open < lower_limit:
                    remove_order(o)
            except Exception:
                continue
    except Exception as e:
        print("[ERROR] cancel_far_orders exception:", e)
        traceback.print_exc()


def cancel_far_orders_preserve(symbol, current_price, brick_size, max_up, max_down, preserve_prices=None):
    """
    Cancel far orders but preserve anchor levels (preserve_prices set).
    """
    try:
        preserve_prices = preserve_prices or set()
        upper_limit = current_price + brick_size * (max_up * 3)
        lower_limit = current_price - brick_size * (max_down * 3)

        for o in fetch_pending_orders(symbol):
            try:
                price_open = getattr(o, "price_open", None)
                if price_open is None:
                    continue
                rprice = round_price(symbol, price_open)
                if rprice in preserve_prices:
                    continue
                if int(o.type) == mt5.ORDER_TYPE_BUY_STOP and price_open > upper_limit:
                    remove_order(o)
                elif int(o.type) == mt5.ORDER_TYPE_SELL_STOP and price_open < lower_limit:
                    remove_order(o)
            except Exception:
                continue
    except Exception as e:
        print("[ERROR] cancel_far_orders_preserve exception:", e)
        traceback.print_exc()
