import MetaTrader5 as mt5

# -------------------- Constants --------------------
FILLING_MODES = [
    mt5.ORDER_FILLING_RETURN,
    mt5.ORDER_FILLING_FOK,
    mt5.ORDER_FILLING_IOC,
]


# -------------------- Helper Function --------------------
def send_order_fast(request):
    """
    Try all filling modes in sequence immediately until order is successful.
    """
    for filling in FILLING_MODES:
        request["type_filling"] = filling
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            return result
        else:
            print(
                f"Attempt with filling {filling} failed: "
                f"retcode={result.retcode}, comment={result.comment}"
            )
    return result


# -------------------- Main Function --------------------
def force_close_symbol(symbol: str) -> None:
    """
    Closes all open positions and pending orders for the given symbol
    without disconnecting MT5 (safe for multi-symbol environment).
    """
    if not symbol:
        print("❌ No symbol provided.")
        return

    print(f"\n🚀 Starting force-close process for: {symbol}")

    # --- Initialize MT5 if not already connected ---
    if not mt5.initialize():
        if mt5.last_error()[0] == 10013:
            # Already initialized error is fine
            print("ℹ️ MT5 already initialized.")
        else:
            print(f"❌ MT5 initialization failed: {mt5.last_error()}")
            return
    else:
        print("✅ MT5 initialized successfully (or already active).")

    try:
        # ------------------ Close open positions ------------------
        positions = mt5.positions_get(symbol=symbol)
        if positions:
            print(f"📊 Found {len(positions)} open positions for {symbol}")
            for pos in positions:
                tick = mt5.symbol_info_tick(symbol)
                if not tick:
                    print(f"⚠️ No tick data for {symbol}, skipping position {pos.ticket}")
                    continue

                if pos.type == mt5.ORDER_TYPE_BUY:
                    close_type = mt5.ORDER_TYPE_SELL
                    price = tick.bid
                    comment = "Force Close Buy"
                elif pos.type == mt5.ORDER_TYPE_SELL:
                    close_type = mt5.ORDER_TYPE_BUY
                    price = tick.ask
                    comment = "Force Close Sell"
                else:
                    continue

                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": pos.volume,
                    "type": close_type,
                    "position": pos.ticket,
                    "price": price,
                    "deviation": 10,
                    "magic": pos.magic,
                    "comment": comment,
                    "type_time": mt5.ORDER_TIME_GTC,
                }

                result = send_order_fast(request)
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    print(f"✅ Closed position {pos.ticket} successfully")
                else:
                    print(
                        f"❌ Failed to close position {pos.ticket}: "
                        f"retcode={result.retcode}, comment={result.comment}"
                    )
        else:
            print(f"ℹ️ No open positions for {symbol}")

        # ------------------ Cancel pending orders ------------------
        orders = mt5.orders_get(symbol=symbol)
        if orders:
            print(f"📦 Found {len(orders)} pending orders for {symbol}")
            for order in orders:
                request = {
                    "action": mt5.TRADE_ACTION_REMOVE,
                    "order": order.ticket,
                    "symbol": symbol,
                    "magic": order.magic,
                    "comment": "Force Cancel Order",
                }

                result = send_order_fast(request)
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    print(f"✅ Canceled order {order.ticket} successfully")
                else:
                    print(
                        f"❌ Failed to cancel order {order.ticket}: "
                        f"retcode={result.retcode}, comment={result.comment}"
                    )
        else:
            print(f"ℹ️ No pending orders for {symbol}")

        print(f"🎯 Force-close completed for {symbol}\n")

    except Exception as e:
        print(f"⚠️ Exception during force close for {symbol}: {e}")


