import MetaTrader5 as mt5
import traceback
from utils.helpers import round_price, fetch_pending_orders, fetch_positions, symbol_precision

DEFAULT_MAGIC = 123456
MAX_ORDERS_PER_SYMBOL = 60

# ----------------- Check & Exists -----------------
def order_exists(symbol, price, order_type):
    try:
        price = round_price(symbol, price)
        for o in fetch_pending_orders(symbol):
            if int(o.type) == int(order_type) and round_price(symbol, o.price_open) == price:
                print(f"[DEBUG] order_exists: {symbol} order_type={order_type} price={price} FOUND existing pending order {o.ticket}")
                return True
        print(f"[DEBUG] order_exists: {symbol} order_type={order_type} price={price} NOT FOUND")
    except Exception as e:
        print(f"[ERROR] order_exists exception for {symbol} @ {price}: {e}")
        traceback.print_exc()
    return False

def can_place_order(symbol):
    try:
        pending = fetch_pending_orders(symbol)
        if len(pending) >= MAX_ORDERS_PER_SYMBOL:
            print(f"[WARN] can_place_order: Max pending orders reached for {symbol} ({len(pending)})")
            return False
        print(f"[DEBUG] can_place_order: {symbol} pending={len(pending)} OK")
        return True
    except Exception as e:
        print(f"[ERROR] can_place_order exception for {symbol}: {e}")
        traceback.print_exc()
        return False

# ----------------- Place / Remove -----------------
def place_order(order_type, symbol, price, lot, magic=DEFAULT_MAGIC, sl_pips=None, tp_pips=None):
    try:
        print(f"[DEBUG] place_order called: {symbol} type={order_type} price={price} lot={lot}")
        if not can_place_order(symbol):
            print(f"[DEBUG] place_order rejected: cannot place more orders for {symbol}")
            return None

        price = round_price(symbol, price)

        if order_exists(symbol, price, order_type):
            print(f"[DEBUG] place_order rejected: order already exists for {symbol} @ {price}")
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

        print(f"[DEBUG] place_order sending request: {request}")
        result = mt5.order_send(request)
        if not result or getattr(result, "retcode", None) != mt5.TRADE_RETCODE_DONE:
            print(f"[ERROR] place_order failed for {symbol} @ {price}: {result}")
            return None

        print(f"[INFO] Order placed: {symbol} type={order_type} @ {price} lot={lot} sl={sl} tp={tp}")
        return result
    except Exception as e:
        print(f"[ERROR] place_order exception for {symbol} @ {price}: {e}")
        traceback.print_exc()
        return None

def remove_order(order):
    try:
        print(f"[DEBUG] remove_order called: {getattr(order, 'ticket', 'N/A')} {getattr(order, 'symbol', 'N/A')} @ {getattr(order,'price_open','N/A')}")
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
    try:
        upper_limit = current_price + brick_size * max_up
        lower_limit = current_price - brick_size * max_down
        print(f"[DEBUG] cancel_far_orders: symbol={symbol} upper_limit={upper_limit} lower_limit={lower_limit}")
        for o in fetch_pending_orders(symbol):
            try:
                price_open = getattr(o, "price_open", None)
                if price_open is None:
                    continue
                if int(o.type) == mt5.ORDER_TYPE_BUY_STOP and price_open > upper_limit:
                    print(f"[DEBUG] cancel_far_orders: removing BUY_STOP {o.ticket} @ {price_open}")
                    remove_order(o)
                elif int(o.type) == mt5.ORDER_TYPE_SELL_STOP and price_open < lower_limit:
                    print(f"[DEBUG] cancel_far_orders: removing SELL_STOP {o.ticket} @ {price_open}")
                    remove_order(o)
            except Exception as e:
                print(f"[ERROR] cancel_far_orders inner exception: {e}")
                traceback.print_exc()
    except Exception as e:
        print(f"[ERROR] cancel_far_orders exception: {e}")
        traceback.print_exc()


def cancel_far_orders_preserve(symbol, current_price, brick_size, max_up, max_down, preserve_prices=None):
    try:
        preserve_prices = preserve_prices or set()
        upper_limit = current_price + brick_size * (max_up * 3)
        lower_limit = current_price - brick_size * (max_down * 3)
        print(f"[DEBUG] cancel_far_orders_preserve: symbol={symbol} upper_limit={upper_limit} lower_limit={lower_limit} preserve={preserve_prices}")

        for o in fetch_pending_orders(symbol):
            try:
                price_open = getattr(o, "price_open", None)
                if price_open is None:
                    continue
                rprice = round_price(symbol, price_open)
                if rprice in preserve_prices:
                    print(f"[DEBUG] cancel_far_orders_preserve: preserving {o.ticket} @ {rprice}")
                    continue
                if int(o.type) == mt5.ORDER_TYPE_BUY_STOP and price_open > upper_limit:
                    print(f"[DEBUG] cancel_far_orders_preserve: removing BUY_STOP {o.ticket} @ {price_open}")
                    remove_order(o)
                elif int(o.type) == mt5.ORDER_TYPE_SELL_STOP and price_open < lower_limit:
                    print(f"[DEBUG] cancel_far_orders_preserve: removing SELL_STOP {o.ticket} @ {price_open}")
                    remove_order(o)
            except Exception as e:
                print(f"[ERROR] cancel_far_orders_preserve inner exception: {e}")
                traceback.print_exc()
    except Exception as e:
        print(f"[ERROR] cancel_far_orders_preserve exception: {e}")
        traceback.print_exc()
