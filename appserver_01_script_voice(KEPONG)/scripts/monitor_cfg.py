import sys
import os
import time
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config_loader import config_loader
from mail_utils import send_alert

STATUS_OK = "OK"
STATUS_FAIL = "FAIL"


def load_state(state_file):
    if state_file.exists():
        try:
            with open(state_file, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "status": STATUS_OK,
        "last_alert_time": None
    }


def save_state(state_file, state):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def check_cfg_files():
    config = config_loader.get_config()
    cfg_conf = config["cfg_monitor"]

    base_path = cfg_conf["cfg_base_path"]
    count = cfg_conf["cfg_count"]
    cooldown = cfg_conf["cooldown_seconds"]
    server_name = cfg_conf["server_name"]
    state_dir = cfg_conf["state_file_dir"]

    state_file = Path(state_dir) / "monitor_cfg.json"

    state = load_state(state_file)
    previous_status = state["status"]
    last_alert_time = state["last_alert_time"]

    cfg_files = [f"{base_path}{i}.cfg" for i in range(1, count + 1)]

    result = os.popen("ps -ef | grep .cfg | grep -v grep").read()
    missing_files = [cfg for cfg in cfg_files if cfg not in result]

    current_status = STATUS_FAIL if missing_files else STATUS_OK
    now = int(time.time())

    # -------- OK → FAIL --------
    if previous_status == STATUS_OK and current_status == STATUS_FAIL:
        subject = f"CFG ALERT | {server_name}"
        body = f"""
        <html><body>
            <p><b>ALERT:</b> One or more CFG processes are not running.</p>
            <p><b>Server:</b> {server_name}</p>
            <p><b>Missing CFG files:</b></p>
            <ul>
                {''.join(f'<li>{cfg}</li>' for cfg in missing_files)}
            </ul>
            <p><b>Time:</b> {time.ctime(now)}</p>
        </body></html>
        """
        send_alert(subject, body)

        state["status"] = STATUS_FAIL
        state["last_alert_time"] = now
        save_state(state_file, state)
        return

    # -------- FAIL → FAIL (cooldown applies) --------
    if previous_status == STATUS_FAIL and current_status == STATUS_FAIL:
        if last_alert_time is None or (now - last_alert_time) >= cooldown:
            subject = f"CFG ALERT | {server_name} (Still Down)"
            body = f"""
            <html><body>
                <p><b>ALERT:</b> CFG issue still ongoing.</p>
                <p><b>Server:</b> {server_name}</p>
                <ul>
                    {''.join(f'<li>{cfg}</li>' for cfg in missing_files)}
                </ul>
                <p><b>Time:</b> {time.ctime(now)}</p>
            </body></html>
            """
            send_alert(subject, body)

            state["last_alert_time"] = now
            save_state(state_file, state)
        else:
            print("[INFO] CFG issue ongoing, cooldown active.")
        return

    # -------- FAIL → OK --------
    if previous_status == STATUS_FAIL and current_status == STATUS_OK:
        subject = f"RESOLVED | CFG Processes Restored | {server_name}"
        body = f"""
        <html><body>
            <p><b>RESOLVED:</b> All CFG processes are now running.</p>
            <p><b>Server:</b> {server_name}</p>
            <p><b>Resolved At:</b> {time.ctime(now)}</p>
        </body></html>
        """
        send_alert(subject, body)

        state["status"] = STATUS_OK
        state["last_alert_time"] = None
        save_state(state_file, state)
        return

    print(f"[{datetime.now()}] OK - All CFG files are running.")


def monitor_cfg():
    print("[INFO] CFG Monitoring started...")
    while True:
        config = config_loader.get_config()
        interval = config["cfg_monitor"]["check_interval_seconds"]
        check_cfg_files()
        time.sleep(interval)


if __name__ == "__main__":
    monitor_cfg()

