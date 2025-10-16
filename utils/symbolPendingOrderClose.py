import MetaTrader5 as mt5

# -------------------- Constants --------------------
FILLING_MODES = [
    mt5.ORDER_FILLING_RETURN,
    mt5.ORDER_FILLING_FOK,
    mt5.ORDER_FILLING_IOC,
]

# -------------------- Helper Function --------------------
def send_order_fast(request) -> mt5.OrderSendResult:
    """
    Try all filling modes in sequence until order is successfully executed.
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
def close_pending_orders(symbol: str) -> None:
    """
    Close all pending orders for the given symbol without touching open positions.
    """
    if not symbol:
        print("❌ No symbol provided.")
        return

    print(f"\n🚀 Starting pending orders cancellation for: {symbol}")

    # --- Initialize MT5 if not already connected ---
    if not mt5.initialize():
        if mt5.last_error()[0] == 10013:
            # Already initialized is okay
            print("ℹ️ MT5 already initialized.")
        else:
            print(f"❌ MT5 initialization failed: {mt5.last_error()}")
            return
    else:
        print("✅ MT5 initialized successfully (or already active).")

    try:
        # ------------------ Cancel pending orders ------------------
        pending_orders = mt5.orders_get(symbol=symbol)
        if pending_orders:
            print(f"📦 Found {len(pending_orders)} pending orders for {symbol}")
            for order in pending_orders:
                request = {
                    "action": mt5.TRADE_ACTION_REMOVE,
                    "order": order.ticket,
                    "symbol": symbol,
                    "magic": order.magic,
                    "comment": "Force Cancel Pending Order",
                }

                result = send_order_fast(request)
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    print(f"✅ Canceled pending order {order.ticket} successfully")
                else:
                    print(
                        f"❌ Failed to cancel pending order {order.ticket}: "
                        f"retcode={result.retcode}, comment={result.comment}"
                    )
        else:
            print(f"ℹ️ No pending orders found for {symbol}")

        print(f"🎯 Pending orders cancellation completed for {symbol}\n")

    except Exception as e:
        print(f"⚠️ Exception during pending orders cancellation for {symbol}: {e}")
