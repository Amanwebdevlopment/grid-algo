# account_routes.py
from flask import Blueprint, render_template, request, redirect, url_for
import json
import os
import MetaTrader5 as mt5

account_bp = Blueprint("account", __name__)
CONFIG_FILE = "config.json"

# ---------------- Config helpers ----------------
def load_config():
    if not os.path.exists(CONFIG_FILE):
        # Create default config if not exists
        default_config = {
            "account": "",
            "password": "",
            "server": "",
            "global_stop_loss": 50,
            "loop_delay": 1,
            "closed_level_block_seconds": 300,
            "grid_tolerance": 0.0,
            "symbols": {}
        }
        save_config(default_config)
        return default_config
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

# ---------------- MT5 connect helper ----------------
def connect_mt5(account, password, server):
    if not mt5.initialize():
        return False, "MT5 initialize failed"
    if not mt5.login(account, password=password, server=server):
        return False, f"MT5 login failed: {mt5.last_error()}"
    return True, None

# ---------------- Account route ----------------
@account_bp.route("/account", methods=["GET", "POST"])
def account():
    config = load_config()
    message = None

    if request.method == "POST":
        try:
            # Update only the account-related fields
            account_val = request.form.get("account", "").strip()
            password_val = request.form.get("password", "").strip()
            server_val = request.form.get("server", "").strip()

            if account_val:
                config["account"] = int(account_val)
            if password_val:
                config["password"] = password_val
            if server_val:
                config["server"] = server_val

            save_config(config)
            message = "Account updated successfully."
            return redirect(url_for("account.account"))
        except Exception as e:
            message = f"Error updating account: {e}"

    # Connect to MT5 to fetch live account info
    account_info = None
    try:
        success, err = connect_mt5(
            config.get("account"),
            config.get("password"),
            config.get("server")
        )
        if success:
            info = mt5.account_info()
            if info:
                account_info = {
                    "account": info.login,
                    "balance": info.balance,
                    "equity": info.equity,
                    "margin": info.margin,
                    "leverage": info.leverage,
                    "server": info.server
                }
        else:
            message = err
    except Exception as e:
        message = str(e)

    return render_template(
        "account.html",
        account=account_info,
        message=message,
        config=config
    )
