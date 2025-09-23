# stop_trading.py
"""
Standalone module to stop the GridSystem trading bot safely and immediately.
"""

import threading
import traceback

# Attempt to import the trading_active flag from your main_routes
try:
    from routes.main_routes import trading_active
except ImportError:
    # Placeholder if running standalone or import fails
    trading_active = False

# Lock to prevent race conditions
_trading_lock = threading.Lock()

def stop_trading_bot():
    """
    Safely stop the trading bot by setting the active flag to False.
    Returns a tuple: (status_message, status_type)
    """
    global trading_active

    with _trading_lock:
        try:
            if not trading_active:
                return "Trading is not active!", "info"

            trading_active = False
            print("[INFO] Trading stop requested successfully!")
            return "Trading stop requested successfully!", "success"

        except Exception as e:
            print("[ERROR] Failed to stop trading bot:", e)
            traceback.print_exc()
            return f"Failed to stop trading bot: {e}", "error"

def stop_trading_and_mt5():
    """
    Optional helper: stops the bot and shuts down MT5 cleanly.
    """
    try:
        import MetaTrader5 as mt5
        msg, msg_type = stop_trading_bot()
        try:
            mt5.shutdown()
            print("[INFO] MT5 shutdown executed")
        except Exception as e:
            print("[WARN] MT5 shutdown failed:", e)
        return msg, msg_type
    except ImportError:
        # MT5 not installed, just stop the bot
        return stop_trading_bot()

# ----------------- CLI / Test -----------------
if __name__ == "__main__":
    msg, msg_type = stop_trading_bot()
    print(f"[{msg_type.upper()}] {msg}")
