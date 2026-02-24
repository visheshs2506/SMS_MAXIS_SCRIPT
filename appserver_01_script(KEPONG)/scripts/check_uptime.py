import sys
import os
import time
import json
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


def get_uptime_seconds():
    try:
        with open("/proc/uptime", "r") as f:
            return float(f.read().split()[0])
    except Exception as e:
        print(f"[ERROR] Could not read uptime: {e}")
        return None


def check_uptime():
    config = config_loader.get_config()
    uptime_conf = config["uptime_check"]

    # ---- STRICT config loading ----
    uptime_threshold = uptime_conf["threshold_minutes"]
    cooldown = uptime_conf["cooldown_seconds"]
    server_name = uptime_conf["server_name"]
    state_dir = uptime_conf["state_file_dir"]

    state_file = Path(state_dir) / "check_uptime.json"

    state = load_state(state_file)
    previous_status = state["status"]
    last_alert_time = state["last_alert_time"]

    uptime_seconds = get_uptime_seconds()
    if uptime_seconds is None:
        return

    uptime_minutes = uptime_seconds / 60
    now = int(time.time())

    current_status = STATUS_OK if uptime_minutes >= uptime_threshold else STATUS_FAIL

    # -------- OK → FAIL --------
    if previous_status == STATUS_OK and current_status == STATUS_FAIL:
        subject = f"UPTIME ALERT | {server_name}"
        body = f"""
        <html><body>
            <p><b>ALERT:</b> Server restart detected.</p>
            <p><b>Server:</b> {server_name}</p>
            <p><b>Current Uptime:</b> {uptime_minutes:.2f} minutes</p>
            <p><b>Threshold:</b> {uptime_threshold} minutes</p>
            <p><b>Time:</b> {time.ctime(now)}</p>
            <p>Please verify whether this restart was planned or unplanned.</p>
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
            subject = f"UPTIME ALERT | {server_name} (Still Below Threshold)"
            body = f"""
            <html><body>
                <p><b>ALERT:</b> Server uptime still below threshold.</p>
                <p><b>Server:</b> {server_name}</p>
                <p><b>Current Uptime:</b> {uptime_minutes:.2f} minutes</p>
                <p><b>Threshold:</b> {uptime_threshold} minutes</p>
                <p><b>Time:</b> {time.ctime(now)}</p>
            </body></html>
            """
            send_alert(subject, body)

            state["last_alert_time"] = now
            save_state(state_file, state)
        else:
            print("[INFO] Uptime issue ongoing, cooldown active.")
        return

    # -------- FAIL → OK --------
    if previous_status == STATUS_FAIL and current_status == STATUS_OK:
        subject = f"RESOLVED | Uptime Stable | {server_name}"
        body = f"""
        <html><body>
            <p><b>RESOLVED:</b> Server uptime is back above threshold.</p>
            <p><b>Server:</b> {server_name}</p>
            <p><b>Current Uptime:</b> {uptime_minutes:.2f} minutes</p>
            <p><b>Resolved At:</b> {time.ctime(now)}</p>
        </body></html>
        """
        send_alert(subject, body)

        state["status"] = STATUS_OK
        state["last_alert_time"] = None
        save_state(state_file, state)
        return

    print(f"[OK] Uptime is {uptime_minutes:.2f} minutes.")


if __name__ == "__main__":
    check_uptime()

