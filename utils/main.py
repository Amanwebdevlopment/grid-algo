from flask import Blueprint, render_template, redirect, url_for
import MetaTrader5 as mt5
import json
import threading
from utils.utils import run_dynamic_grid  # your existing grid strategy

main_bp = Blueprint("main", __name__)
CONFIG_FILE = "config.json"

# ----------------- Config Helpers -----------------
def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)

# ----------------- MT5 Helpers (same as before) -----------------
def get_positions():
    positions = mt5.positions_get() or []
    return [{
        "symbol": p.symbol,
        "volume": p.volume,
        "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
        "price_open": p.price_open,
        "profit": getattr(p, "profit", 0),
        "ticket": p.ticket
    } for p in positions]

def get_orders():
    orders = mt5.orders_get() or []
    return [{
        "symbol": o.symbol,
        "volume": getattr(o, "volume_initial", 0),
        "remaining": getattr(o, "volume_current", 0),
        "type": "BUY" if o.type == mt5.ORDER_TYPE_BUY else "SELL",
        "price_open": getattr(o, "price_open", None),
        "ticket": o.ticket
    } for o in orders]

def get_grid_data():
    cfg = load_config()
    grid = {}
    for sym in cfg.get("symbols", []):
        grid[sym] = [100.1, 100.2, 100.3]  # dummy; replace with real logic
    return grid

# ----------------- Trading Control -----------------
trading_active = False
status_message = None
status_type = None
_trading_thread = None

def trading_active_flag():
    return trading_active

def trading_wrapper(config):
    """Run your existing grid bot in background."""
    global trading_active, status_message, status_type
    try:
        run_dynamic_grid(config)
    except Exception as e:
        status_message = f"Bot error: {str(e)}"
        status_type = "error"
    finally:
        trading_active = False  # mark inactive after bot finishes

def start_trading_loop():
    """Start the trading bot in a separate thread."""
    global trading_active, status_message, status_type, _trading_thread
    if trading_active:
        status_message = "Trading is already active!"
        status_type = "info"
        return

    try:
        trading_active = True
        status_message = "Trading started successfully!"
        status_type = "success"

        config = load_config()
        _trading_thread = threading.Thread(target=trading_wrapper, args=(config,), daemon=True)
        _trading_thread.start()

    except Exception as e:
        status_message = f"Failed to start trading: {str(e)}"
        status_type = "error"

def stop_trading_loop():
    global trading_active, status_message, status_type
    # Note: stop functionality depends on your run_dynamic_grid supporting a stop flag
    try:
        if not trading_active:
            status_message = "Trading is not active!"
            status_type = "info"
            return
        trading_active = False
        status_message = "Trading stopped successfully! (Bot must support stop)"
        status_type = "info"
    except Exception as e:
        status_message = f"Failed to stop trading: {str(e)}"
        status_type = "error"

# ----------------- Routes -----------------
@main_bp.route("/")
def index():
    global status_message, status_type
    message = status_message
    message_type = status_type
    status_message = None
    status_type = None
    return render_template(
        "index.html",
        positions=get_positions(),
        orders=get_orders(),
        grid_data=get_grid_data(),
        trading_active=trading_active_flag(),
        status_message=message,
        status_type=message_type
    )

@main_bp.route("/start-trading", methods=["POST"])
def start_trading():
    start_trading_loop()
    return redirect(url_for("main.index"))

@main_bp.route("/stop-trading", methods=["POST"])
def stop_trading():
    stop_trading_loop()
    return redirect(url_for("main.index"))

# You can keep panic-close and cancel-all routes same as before
