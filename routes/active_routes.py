# active_trades.py
from flask import Blueprint, render_template, request
import MetaTrader5 as mt5

active_bp = Blueprint("active", __name__)

# ---------------- MT5 Initialization ----------------
def initialize_mt5():
    if not mt5.initialize():
        print("[DEBUG] MT5 initialization failed:", mt5.last_error())
        return False
    account_info = mt5.account_info()
    if account_info:
        print(f"[DEBUG] MT5 initialized successfully. Logged in as account {account_info.login}")
        return True
    else:
        print("[DEBUG] MT5 initialized but account info not available!")
        return False

# Initialize once at startup
initialize_mt5()

# ---------------- Helper: Get current price ----------------
def get_current_price(symbol, order_type):
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return None
    # For Buy positions, current price = bid; for Sell positions, current price = ask
    return tick.bid if order_type == mt5.ORDER_TYPE_BUY else tick.ask

# ---------------- Route: Active Trades ----------------
@active_bp.route("/active-trades", methods=["GET"])
def active_trades():
    reason = None

    # Ensure MT5 is initialized
    if not mt5.initialize():
        print("[DEBUG] MT5 not initialized in route, attempting to re-initialize...")
        if not initialize_mt5():
            reason = "MT5 not initialized. Start your terminal or check login."
            return render_template(
                "active-trades.html",
                trades=[],
                symbols=[],
                selected_symbol=None,
                reason=reason
            )

    positions = mt5.positions_get()
    trades = []

    if positions is None:
        reason = "MT5 terminal not connected or login missing."
    elif len(positions) == 0:
        reason = "No open positions for your account."
    else:
        for p in positions:
            price_current = get_current_price(p.symbol, p.type) or p.price_open
            trade_info = {
                "symbol": p.symbol,
                "type": "Buy" if p.type == mt5.ORDER_TYPE_BUY else "Sell",
                "volume": p.volume,
                "price_open": p.price_open,
                "price_current": price_current,
                "profit": p.profit,
                "ticket": p.ticket
            }
            trades.append(trade_info)

    # ---------------- Symbol Filter ----------------
    all_symbols = sorted(set(t["symbol"] for t in trades))
    selected_symbol = request.args.get("symbol")
    if selected_symbol:
        trades = [t for t in trades if t["symbol"].upper() == selected_symbol.upper()]

    return render_template(
        "active-trades.html",
        trades=trades,
        symbols=all_symbols,
        selected_symbol=selected_symbol,
        reason=reason
    )
