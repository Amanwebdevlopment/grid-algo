import MetaTrader5 as mt5
import traceback

# ----------------- MT5 Initialization -----------------
def initialize_mt5(account, password, server):
    if not mt5.initialize():
        print(f"[ERROR] MT5 initialize failed: {mt5.last_error()}")
        return False
    if not mt5.login(account, password=password, server=server):
        print(f"[ERROR] MT5 login failed: {mt5.last_error()}")
        return False
    print("[INFO] MT5 connected successfully")
    return True

# ----------------- Fetch Positions & Orders -----------------
def fetch_positions():
    return mt5.positions_get() or []

def fetch_pending_orders():
    return mt5.orders_get() or []

# ----------------- Close a Position with Filling Type Fallback -----------------
def close_position(pos):
    try:
        symbol = pos.symbol
        volume = pos.volume
        pos_type = int(pos.type)

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            print(f"[WARN] Cannot get tick for {symbol}")
            return False

        # Buy -> close with Sell, Sell -> close with Buy
        close_type = mt5.ORDER_TYPE_SELL if pos_type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

        # List of filling types to try in order
        filling_types = [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]

        for filling_type in filling_types:
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": close_type,
                "position": pos.ticket,
                "price": price,
                "deviation": 10,
                "comment": "Panic close",
                "type_filling": filling_type,
            }

            res = mt5.order_send(req)
            if res and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE:
                print(f"[INFO] Closed position {pos.ticket} ({symbol}) @ {price} with filling {filling_type}")
                return True
            else:
                print(f"[WARN] Filling type {filling_type} failed for position {pos.ticket}: {res}")

        print(f"[ERROR] All filling types failed for position {pos.ticket} ({symbol})")
        return False

    except Exception as e:
        print("[ERROR] close_position exception:", e)
        traceback.print_exc()
        return False

# ----------------- Cancel a Pending Order -----------------
def cancel_order(order):
    try:
        req = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": order.ticket,
            "symbol": order.symbol,
            "comment": "Panic cancel",
        }
        res = mt5.order_send(req)
        if res and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE:
            print(f"[INFO] Cancelled pending order {order.ticket} ({order.symbol}) @ {order.price_open}")
            return True
        else:
            print(f"[ERROR] Failed to cancel pending order {order.ticket} ({order.symbol}): {res}")
            return False
    except Exception as e:
        print("[ERROR] cancel_order exception:", e)
        traceback.print_exc()
        return False

# ----------------- Panic Close All -----------------
def panic_close_all():
    positions = fetch_positions()
    pending_orders = fetch_pending_orders()
    print(f"[INFO] Found {len(positions)} positions and {len(pending_orders)} pending orders.")

    for pos in positions:
        close_position(pos)

    for order in pending_orders:
        cancel_order(order)

# ----------------- Main -----------------
if __name__ == "__main__":
    ACCOUNT = 5040256545
    PASSWORD = "*eQ6FpHj"
    SERVER = "MetaQuotes-Demo"

    if initialize_mt5(ACCOUNT, PASSWORD, SERVER):
        # Ensure all symbols are selected in Market Watch
        symbols = set([p.symbol for p in fetch_positions()] + [o.symbol for o in fetch_pending_orders()])
        for sym in symbols:
            mt5.symbol_select(sym, True)
        panic_close_all()
