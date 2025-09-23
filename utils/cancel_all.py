import MetaTrader5 as mt5

# ----------------- Initialize MT5 -----------------
def initialize_mt5(account=None, password=None, server=None):
    if not mt5.initialize():
        print(f"[ERROR] MT5 initialize failed: {mt5.last_error()}")
        return False
    if account and password and server:
        if not mt5.login(account, password=password, server=server):
            print(f"[ERROR] MT5 login failed: {mt5.last_error()}")
            return False
    print("[INFO] MT5 connected successfully")
    return True

# ----------------- Cancel Pending Grid Orders -----------------
def cancel_pending_grid_orders(magic_number=123456, symbols=None):
    """
    Cancels all pending orders placed by the bot (based on magic number).
    Optional: filter by symbols.
    """
    if symbols:
        orders = []
        for symbol in symbols:
            orders += mt5.orders_get(symbol=symbol) or []
    else:
        orders = mt5.orders_get() or []

    if not orders:
        print("[INFO] No pending orders found.")
        return

    canceled_count = 0
    for order in orders:
        if order.magic == magic_number and order.type in [
            mt5.ORDER_TYPE_BUY_STOP,
            mt5.ORDER_TYPE_SELL_STOP,
            mt5.ORDER_TYPE_BUY_LIMIT,
            mt5.ORDER_TYPE_SELL_LIMIT
        ]:
            # MT5 cancel pending order
            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": order.ticket,
                "symbol": order.symbol,
                "magic": order.magic,
                "comment": "Cancel GridBot order"
            }
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"[INFO] Cancelled order {order.ticket} ({order.symbol}) @ {order.price_open}")
                canceled_count += 1
            else:
                print(f"[ERROR] Failed to cancel order {order.ticket} ({order.symbol}): {result.retcode}")
    
    print(f"[INFO] Total pending grid orders cancelled: {canceled_count}")

# ----------------- Run -----------------
if initialize_mt5():
    # Optional: pass a list of your grid symbols to limit cancellation
    grid_symbols = ["GBPUSD", "EURUSD"]
    cancel_pending_grid_orders(symbols=grid_symbols)
