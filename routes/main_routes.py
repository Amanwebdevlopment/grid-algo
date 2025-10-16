from flask import Blueprint, render_template, redirect, url_for, jsonify
import json
import threading
import MetaTrader5 as mt5
import traceback
import os
import time

from utils.utils import run_dynamic_grid      # Your actual bot
from utils.panic_close import panic_close_all # Panic close all positions
from utils.cancel_all import cancel_pending_grid_orders # Cancel all pending orders

main_bp = Blueprint("main", __name__)
CONFIG_FILE = "config.json"

# ----------------- Globals -----------------
trading_active = False
status_message = None
status_type = None
_trading_thread = None
_trading_lock = threading.Lock()  # Lock for thread-safe stop
_config_lock = threading.Lock()
_last_config_mtime = 0

# ----------------- Config -----------------
def load_config():
    with _config_lock:
        with open(CONFIG_FILE) as f:
            return json.load(f)

# ----------------- MT5 Helpers -----------------
def ensure_mt5():
    if not mt5.initialize():
        print("[DEBUG] Initializing MT5...")
        if not mt5.initialize():
            print("[ERROR] MT5 initialization failed:", mt5.last_error())
            return False
    return True

def fetch_positions():
    try:
        return mt5.positions_get() or []
    except Exception as e:
        print("[ERROR] fetch_positions:", e)
        traceback.print_exc()
        return []

def fetch_pending_orders():
    try:
        return mt5.orders_get() or []
    except Exception as e:
        print("[ERROR] fetch_pending_orders:", e)
        traceback.print_exc()
        return []

def serialize_positions_orders():
    positions_raw = fetch_positions()
    orders_raw = fetch_pending_orders()

    positions = [{"symbol": p.symbol, "type": int(p.type), "volume": getattr(p, "volume", 0),
                  "ticket": p.ticket, "profit": round(getattr(p, "profit", 0), 2)} for p in positions_raw]

    orders = [{"symbol": o.symbol, "type": int(o.type), "volume": getattr(o, "volume_current", 0),
               "price": getattr(o, "price_open", 0), "ticket": o.ticket} for o in orders_raw]

    return positions, orders

# ----------------- Trading -----------------
def trading_active_flag():
    return trading_active

def trading_wrapper(config):
    global trading_active, status_message, status_type
    try:
        print("[DEBUG] Trading wrapper started")
        run_dynamic_grid(config, trading_active_flag=trading_active_flag)
    except Exception as e:
        status_message = f"Bot error: {str(e)}"
        status_type = "error"
        print("[ERROR] Trading wrapper exception:", e)
        traceback.print_exc()
    finally:
        trading_active = False
        print("[INFO] Trading bot stopped")

def start_trading_loop(config=None):
    global trading_active, status_message, status_type, _trading_thread
    if trading_active:
        print("[DEBUG] Trading already active, skipping start")
        return

    if config is None:
        config = load_config()

    trading_active = True
    status_message = "Trading started successfully!"
    status_type = "success"

    _trading_thread = threading.Thread(target=trading_wrapper, args=(config,), daemon=True)
    _trading_thread.start()
    print("[INFO] Trading loop started in background thread")

def stop_trading_loop():
    global trading_active, status_message, status_type
    with _trading_lock:
        if not trading_active:
            print("[DEBUG] Stop requested but trading not active")
            return

        trading_active = False
        status_message = "Trading stopped immediately!"
        status_type = "success"
        print("[INFO] Trading stop requested immediately")

# ----------------- Auto-Restart Config Watcher -----------------
def watch_config_changes():
    global _last_config_mtime
    while True:
        try:
            mtime = os.path.getmtime(CONFIG_FILE)
            if _last_config_mtime == 0:
                _last_config_mtime = mtime

            elif mtime != _last_config_mtime:
                print("[INFO] Config changed, restarting trading loop")
                _last_config_mtime = mtime
                stop_trading_loop()
                time.sleep(1)  # short delay to ensure thread stopped
                start_trading_loop()
        except Exception as e:
            print("[ERROR] Config watcher exception:", e)
        time.sleep(1)  # check every 1 second

# Start watcher thread on module load
threading.Thread(target=watch_config_changes, daemon=True).start()

# ----------------- Routes -----------------
@main_bp.route("/")
def index():
    global status_message, status_type
    message = status_message
    message_type = status_type
    status_message = None
    status_type = None

    if not ensure_mt5():
        positions, orders = [], []
    else:
        positions, orders = serialize_positions_orders()

    return render_template(
        "index.html",
        trading_active=trading_active,
        status_message=message,
        status_type=message_type,
        positions=positions,
        orders=orders
    )

@main_bp.route("/start-trading", methods=["POST"])
def start_trading():
    print("[DEBUG] /start-trading called")
    start_trading_loop()
    return redirect(url_for("main.index"))

@main_bp.route("/stop-trading", methods=["POST"])
def stop_trading():
    print("[DEBUG] /stop-trading called")
    stop_trading_loop()
    return redirect(url_for("main.index"))

@main_bp.route("/panic-close", methods=["POST"])
def panic_close():
    global status_message, status_type
    print("[DEBUG] /panic-close called")
    try:
        if ensure_mt5():
            panic_close_all()
            status_message = "All positions and pending orders closed successfully!"
            status_type = "success"
        else:
            status_message = "MT5 not initialized, cannot panic close."
            status_type = "error"
    except Exception as e:
        status_message = f"Panic close failed: {e}"
        status_type = "error"
        print("[ERROR] Panic close exception:", e)
        traceback.print_exc()
    return redirect(url_for("main.index"))

@main_bp.route("/cancel-all", methods=["POST"])
def cancel_all():
    global status_message, status_type
    print("[DEBUG] /cancel-all called")
    try:
        if ensure_mt5():
            cancel_pending_grid_orders(symbols=None)
            status_message = "All pending grid orders canceled successfully!"
            status_type = "success"
        else:
            status_message = "MT5 not initialized, cannot cancel orders."
            status_type = "error"
    except Exception as e:
        status_message = f"Cancel all orders failed: {e}"
        status_type = "error"
        print("[ERROR] Cancel all exception:", e)
        traceback.print_exc()
    return redirect(url_for("main.index"))

@main_bp.route("/live-data")
def live_data():
    if not ensure_mt5():
        return jsonify({"positions": [], "orders": [], "trading_active": trading_active})

    positions, orders = serialize_positions_orders()
    return jsonify({
        "positions": positions,
        "orders": orders,
        "trading_active": trading_active
    })
