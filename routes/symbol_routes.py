from flask import Blueprint, render_template, request, redirect, flash, jsonify
import json
import MetaTrader5 as mt5
from threading import Thread

# ---------------- Blueprint ----------------
symbol_bp = Blueprint("symbol", __name__)
CONFIG_FILE = "config.json"

# ----------------- Config Helpers -----------------
def load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "account": 96861621,
            "password": "!6EyTkJn",
            "server": "MetaQuotes-Demo",
            "loop_delay": 1,
            "closed_level_block_seconds": 300,
            "grid_tolerance": 0.0,
            "symbols": {}
        }

def save_config(config: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

# ----------------- Fetch Broker Symbols -----------------
def fetch_broker_symbols() -> list:
    if not mt5.initialize():
        print("[DEBUG] MT5 not initialized for symbols fetch")
        return []
    all_symbols = mt5.symbols_get()
    mt5.shutdown()
    return [s.name for s in all_symbols] if all_symbols else []

# ----------------- MT5 Helpers -----------------
FILLING_MODES = [mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC]

def send_order_fast(request):
    for filling in FILLING_MODES:
        request["type_filling"] = filling
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            return result
    return result

# ----------------- Close Pending Orders Only -----------------
def close_pending_orders(symbol: str):
    if not mt5.initialize():
        print("[DEBUG] MT5 init failed for pending order close")
        return

    pending_orders = mt5.orders_get(symbol=symbol) or []

    if not pending_orders:
        print(f"[INFO] No pending orders found for {symbol}")
        mt5.shutdown()
        return

    print(f"[INFO] Closing {len(pending_orders)} pending orders for {symbol}")

    for order in pending_orders:
        req = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": order.ticket,
            "symbol": symbol,
            "magic": order.magic,
            "comment": "Cancel Pending Order",
        }
        result = send_order_fast(req)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"✅ Canceled pending order {order.ticket}")
        else:
            print(f"❌ Failed to cancel pending order {order.ticket}: {result.comment}")

    mt5.shutdown()

# ----------------- Force Close Symbol (All Positions) -----------------
def force_close_symbol(symbol: str):
    if not mt5.initialize():
        print("[DEBUG] MT5 init failed for force close")
        return

    positions = mt5.positions_get(symbol=symbol) or []

    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        print(f"[WARN] No tick data for {symbol}")
        mt5.shutdown()
        return

    for pos in positions:
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": pos.ticket,
            "price": price,
            "deviation": 10,
            "magic": pos.magic,
            "comment": "Panic Close",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        send_order_fast(req)

    mt5.shutdown()
    print(f"[INFO] All positions closed for {symbol}")

# ----------------- Routes -----------------
@symbol_bp.route("/symbols", methods=["GET", "POST"])
def symbols():
    config = load_config()

    if request.method == "POST":
        new_symbol = request.form.get("new_symbol", "").strip()
        remove_symbol = request.form.get("remove_symbol")

        if remove_symbol and remove_symbol in config["symbols"]:
            config["symbols"].pop(remove_symbol)

        if new_symbol:
            broker_symbols = fetch_broker_symbols()
            if new_symbol not in broker_symbols:
                flash(f"Symbol '{new_symbol}' not found in broker Market Watch", "error")
                return redirect("/symbols")

            lot_size = float(request.form.get("lot_size", 0.1))
            brick_size = float(request.form.get("brick_size", 1))
            max_up = int(request.form.get("max_up", 2))
            max_down = int(request.form.get("max_down", 2))
            trade_side = request.form.get("trade_side", "both")
            stop_loss_pips = float(request.form.get("stop_loss_pips")) if request.form.get("stop_loss_pips") else None
            take_profit_pips = float(request.form.get("take_profit_pips")) if request.form.get("take_profit_pips") else None
            trailing_stop_pips = float(request.form.get("trailing_stop_pips")) if request.form.get("trailing_stop_pips") else None

            prev_active = config["symbols"].get(new_symbol, {}).get("active", False)

            config["symbols"][new_symbol] = {
                "lot_size": lot_size,
                "brick_size": brick_size,
                "max_up": max_up,
                "max_down": max_down,
                "trade_side": trade_side,
                "stop_loss_pips": stop_loss_pips,
                "take_profit_pips": take_profit_pips,
                "trailing_stop_pips": trailing_stop_pips,
                "active": True if new_symbol not in config["symbols"] else prev_active
            }

        save_config(config)
        return redirect("/symbols")

    symbols_list = [{"name": k, **v} for k, v in config.get("symbols", {}).items()]
    broker_symbols = fetch_broker_symbols()
    return render_template("symbols.html", symbols=symbols_list, broker_symbols=broker_symbols)

# ----------------- Toggle Active Status -----------------
@symbol_bp.route("/symbols/toggle", methods=["POST"])
def toggle_symbol():
    config = load_config()
    data = request.get_json()
    symbol = data.get("symbol")
    if symbol and symbol in config.get("symbols", {}):
        config["symbols"][symbol]["active"] = not config["symbols"][symbol].get("active", False)
        save_config(config)
        return jsonify({"success": True, "active": config["symbols"][symbol]["active"]})
    return jsonify({"success": False, "message": "Symbol not found"}), 404

# ----------------- Close Pending Orders Route -----------------
@symbol_bp.route("/symbols/close-pending", methods=["POST"])
def close_pending_route():
    data = request.get_json()
    symbol = data.get("symbol")
    if not symbol:
        return jsonify({"success": False, "message": "No symbol provided"}), 400

    try:
        Thread(target=close_pending_orders, args=(symbol,), daemon=True).start()
        return jsonify({"success": True, "message": f"Pending orders close started for {symbol}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ----------------- Panic Close Route -----------------
@symbol_bp.route("/symbols/panic-close", methods=["POST"])
def panic_close_route():
    data = request.get_json()
    symbol = data.get("symbol")
    if not symbol:
        return jsonify({"success": False, "message": "No symbol provided"}), 400

    try:
        Thread(target=force_close_symbol, args=(symbol,), daemon=True).start()
        return jsonify({"success": True, "message": f"Force close started for {symbol}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
