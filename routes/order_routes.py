from flask import Blueprint, redirect
import json
from utils.utils import fetch_pending_orders, fetch_positions, place_order, remove_order

CONFIG_FILE = "config.json"

def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

order_bp = Blueprint("order", __name__)

@order_bp.route("/panic-close", methods=["POST"])
def panic_close():
    cfg = load_config()
    # yahan panic_close logic call karo utils se
    return redirect("/")

@order_bp.route("/cancel-all", methods=["POST"])
def cancel_all():
    cfg = load_config()
    # yahan cancel orders logic call karo utils se
    return redirect("/")
