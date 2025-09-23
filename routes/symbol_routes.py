from flask import Blueprint, render_template, request, redirect, flash
import json
import MetaTrader5 as mt5

symbol_bp = Blueprint("symbol", __name__)
CONFIG_FILE = "config.json"

# ----------------- Config Helpers -----------------
def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "account": 10007658661,
            "password": "Yn*iGn6e",
            "server": "MetaQuotes-Demo",
            "global_stop_loss": 50,
            "loop_delay": 1,
            "closed_level_block_seconds": 300,
            "grid_tolerance": 0.0,
            "symbols": {}
        }

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

# ----------------- Fetch Broker Symbols -----------------
def fetch_broker_symbols():
    if not mt5.initialize():
        print("[DEBUG] MT5 not initialized for symbols fetch")
        return []
    all_symbols = mt5.symbols_get()
    return [s.name for s in all_symbols] if all_symbols else []

# ----------------- Routes -----------------
@symbol_bp.route("/symbols", methods=["GET", "POST"])
def symbols():
    config = load_config()

    if request.method == "POST":
        # Fetch form data
        new_symbol = request.form.get("new_symbol", "").strip()  # keep broker exact, no .upper()
        remove_symbol = request.form.get("remove_symbol")
        selected_symbol = request.form.get("active_symbol")

        # ---------------- Remove Symbol ----------------
        if remove_symbol and remove_symbol in config["symbols"]:
            config["symbols"].pop(remove_symbol)
            if selected_symbol == remove_symbol:
                selected_symbol = None

        # ---------------- Add / Update Symbol ----------------
        if new_symbol:
            # Optional: validate symbol against broker list
            broker_symbols = fetch_broker_symbols()
            if new_symbol not in broker_symbols:
                flash(f"Symbol '{new_symbol}' not found in broker Market Watch", "error")
                return redirect("/symbols")

            lot_size = float(request.form.get("lot_size", 0.1))
            brick_size = float(request.form.get("brick_size", 0.0001))
            max_up = int(request.form.get("max_up", 5))
            max_down = int(request.form.get("max_down", 5))
            trade_side = request.form.get("trade_side", "both")
            grid_rounding = request.form.get("grid_rounding", "nearest")
            stop_loss_pips = request.form.get("stop_loss_pips")
            take_profit_pips = request.form.get("take_profit_pips")
            stop_loss_pips = float(stop_loss_pips) if stop_loss_pips else None
            take_profit_pips = float(take_profit_pips) if take_profit_pips else None
            initial_levels_buy = int(request.form.get("initial_levels_buy", 3))
            initial_levels_sell = int(request.form.get("initial_levels_sell", 3))

            config.setdefault("symbols", {})
            config["symbols"][new_symbol] = {
                "lot_size": lot_size,
                "brick_size": brick_size,
                "max_up": max_up,
                "max_down": max_down,
                "trade_side": trade_side,
                "grid_rounding": grid_rounding,
                "stop_loss_pips": stop_loss_pips,
                "take_profit_pips": take_profit_pips,
                "initial_levels_buy": initial_levels_buy,
                "initial_levels_sell": initial_levels_sell,
                "active": True
            }
            selected_symbol = new_symbol

        # ---------------- Activate Selected Symbol Only ----------------
        if selected_symbol:
            for sym in config["symbols"]:
                config["symbols"][sym]["active"] = (sym == selected_symbol)

        save_config(config)
        return redirect("/symbols")

    # ---------------- GET Request ----------------
    symbols_list = [{"name": k, **v} for k, v in config.get("symbols", {}).items()]
    broker_symbols = fetch_broker_symbols()  # exact names for dropdown/search
    return render_template("symbols.html", symbols=symbols_list, broker_symbols=broker_symbols)
