# trailing_single_fn.py
import MetaTrader5 as mt5
import time
import json
import os
from typing import Dict, Optional, Callable

CONFIG_DEFAULT_PATH = "config.json"

def start_trailing_loop(
    config_path: str = CONFIG_DEFAULT_PATH,
    trading_active_flag: Callable[[], bool] = lambda: True,
    mt5_lock = None,
    loop_delay_override: Optional[float] = None
) -> None:
    """
    Start a blocking trailing-stop loop. This function does NOT initialize or shutdown MT5.
    Call from your launcher (or run it in a daemon thread). It will run until trading_active_flag() is False.

    Args:
        config_path: path to config.json
        trading_active_flag: callable returning True while loop should run
        mt5_lock: optional threading.Lock/RLock to serialize MT5 calls with other threads
        loop_delay_override: if provided, overrides config's loop_delay
    """

    def load_config_file(path: str) -> Dict:
        with open(path, "r") as f:
            cfg = json.load(f)
        if "account" not in cfg or "password" not in cfg or "server" not in cfg:
            raise ValueError("config.json missing required keys (account,password,server).")
        if "symbols" not in cfg or not isinstance(cfg["symbols"], dict):
            raise ValueError("config.json must contain a 'symbols' dict.")
        return cfg

    # initial load
    try:
        CONFIG = load_config_file(config_path)
    except Exception as e:
        raise SystemExit(f"Failed to load config: {e}")

    _AUTH = (CONFIG.get("account"), CONFIG.get("password"), CONFIG.get("server"))
    try:
        _last_mtime: Optional[float] = os.path.getmtime(config_path)
    except Exception:
        _last_mtime = None

    def round_price(price: float, rounding: float) -> float:
        if rounding is None or rounding == 0:
            return price
        try:
            return round(price / rounding) * rounding
        except Exception:
            return price

    def try_reload_config() -> bool:
        nonlocal CONFIG, _last_mtime, _AUTH
        try:
            mtime = os.path.getmtime(config_path)
        except FileNotFoundError:
            print("Config file missing.")
            return False

        if _last_mtime is None or mtime > _last_mtime:
            try:
                new_cfg = load_config_file(config_path)
            except Exception as e:
                print(f"Config reload failed (invalid JSON or missing keys): {e}")
                _last_mtime = mtime  # avoid repeated parse attempts until file changes again
                return False

            new_auth = (new_cfg.get("account"), new_cfg.get("password"), new_cfg.get("server"))
            if new_auth != _AUTH:
                print("Warning: account/server/password changed in config.json. Restart required to apply auth changes.")
                # keep old auth in-place so caller's MT5 connection remains valid
                new_cfg["account"] = CONFIG["account"]
                new_cfg["password"] = CONFIG["password"]
                new_cfg["server"] = CONFIG["server"]

            CONFIG = new_cfg
            _last_mtime = mtime
            print("Config reloaded from file.")
            return True

        return False

    def update_trailing_stop(symbol: str, trailing_pips: float) -> None:
        """
        Update trailing stop for all open positions of a symbol.
        Assumes active MT5 connection exists. Call under mt5_lock if provided.
        """
        try:
            positions = mt5.positions_get(symbol=symbol)
        except Exception:
            positions = None

        if not positions:
            return

        brick = CONFIG["symbols"].get(symbol, {}).get("brick_size", 1.0)

        for pos in positions:
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                continue

            # pick current price based on position type
            pos_type = getattr(pos, "type", None)
            current_price: float = tick.bid if pos_type == getattr(mt5, "ORDER_TYPE_BUY", 0) else tick.ask
            new_sl: Optional[float] = None

            if pos_type == getattr(mt5, "ORDER_TYPE_BUY", 0):
                if getattr(pos, "sl", 0) == 0 or (current_price - getattr(pos, "sl", 0)) > trailing_pips:
                    candidate = current_price - trailing_pips
                    new_sl = round_price(candidate, brick)

            elif pos_type == getattr(mt5, "ORDER_TYPE_SELL", 1):
                if getattr(pos, "sl", 0) == 0 or (getattr(pos, "sl", 0) - current_price) > trailing_pips:
                    candidate = current_price + trailing_pips
                    new_sl = round_price(candidate, brick)

            if new_sl is not None and new_sl != getattr(pos, "sl", None):
                request = {
                    "action": getattr(mt5, "TRADE_ACTION_SLTP", None),
                    "position": getattr(pos, "ticket", None),
                    "sl": new_sl,
                    "tp": getattr(pos, "tp", None)
                }
                try:
                    result = mt5.order_send(request)
                except Exception as e:
                    print(f"[{symbol}] Error sending SL update for ticket {getattr(pos,'ticket',None)}: {e}")
                    continue

                if getattr(result, "retcode", None) != getattr(mt5, "TRADE_RETCODE_DONE", None):
                    print(f"[{symbol}] Failed to update SL for ticket {getattr(pos,'ticket',None)}: {getattr(result, 'retcode', result)}")
                else:
                    print(f"[{symbol}] Trailing SL updated for ticket {getattr(pos,'ticket',None)} to {new_sl}")

    # main loop
    print("[INFO] Trailing loop started (no MT5 init/shutdown here).")
    try:
        while trading_active_flag():
            # try reload config if changed
            try:
                try_reload_config()
            except Exception:
                pass

            active_symbols = [s for s, c in CONFIG["symbols"].items() if c.get("active", False)]
            for symbol in active_symbols:
                if not trading_active_flag():
                    break
                trailing_pips = CONFIG["symbols"].get(symbol, {}).get("trailing_stop_pips", 0.0)
                if not trailing_pips:
                    continue
                try:
                    if mt5_lock is not None:
                        with mt5_lock:
                            update_trailing_stop(symbol, trailing_pips)
                    else:
                        update_trailing_stop(symbol, trailing_pips)
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    print(f"[WARN] trailing update for {symbol} failed: {e}")

            # sleep but remain responsive to trading_active_flag
            delay = loop_delay_override if loop_delay_override is not None else CONFIG.get("loop_delay", 1)
            slept = 0.0
            step = 0.2
            while trading_active_flag() and slept < delay:
                time.sleep(step)
                slept += step

    except KeyboardInterrupt:
        print("[INFO] Trailing loop stopped by KeyboardInterrupt.")
    except Exception as e:
        print(f"[ERROR] Trailing loop exception: {e}")
    finally:
        print("[INFO] Trailing loop exiting.")
