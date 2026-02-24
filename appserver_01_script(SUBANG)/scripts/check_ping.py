import sys
import os
import platform
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


def check_server():
    config = config_loader.get_config()
    ping_conf = config["ping_check"]

    # ---- STRICT config loading ----
    target_ip = ping_conf["target_ip"]
    cooldown = ping_conf["cooldown_seconds"]
    server_name = ping_conf["server_name"]
    state_dir = ping_conf["state_file_dir"]

    state_file = Path(state_dir) / "check_ping.json"

    state = load_state(state_file)
    previous_status = state["status"]
    last_alert_time = state["last_alert_time"]

    ping_param = "-n 1" if platform.system().lower() == "windows" else "-c 1"
    response = os.system(
        f"ping {ping_param} {target_ip} > nul 2>&1"
        if os.name == "nt"
        else f"ping {ping_param} {target_ip} > /dev/null 2>&1"
    )

    current_status = STATUS_OK if response == 0 else STATUS_FAIL
    now = int(time.time())

    # -------- OK → FAIL --------
    if previous_status == STATUS_OK and current_status == STATUS_FAIL:
        subject = f"PING ALERT | {server_name}"
        body = f"""
        <html><body>
            <p><b>ALERT:</b> Ping check failed.</p>
            <p><b>Server:</b> {server_name}</p>
            <p><b>Target:</b> {target_ip}</p>
            <p><b>Status:</b> Unreachable</p>
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
            subject = f"PING ALERT | {server_name} (Still Unreachable)"
            body = f"""
            <html><body>
                <p><b>ALERT:</b> Ping issue still ongoing.</p>
                <p><b>Server:</b> {server_name}</p>
                <p><b>Target:</b> {target_ip}</p>
                <p><b>Status:</b> Still unreachable</p>
                <p><b>Time:</b> {time.ctime(now)}</p>
            </body></html>
            """
            send_alert(subject, body)

            state["last_alert_time"] = now
            save_state(state_file, state)
        else:
            print("[INFO] Ping failure ongoing, cooldown active.")
        return

    # -------- FAIL → OK --------
    if previous_status == STATUS_FAIL and current_status == STATUS_OK:
        subject = f"RESOLVED | Ping Restored | {server_name}"
        body = f"""
        <html><body>
            <p><b>RESOLVED:</b> Ping connectivity restored.</p>
            <p><b>Server:</b> {server_name}</p>
            <p><b>Target:</b> {target_ip}</p>
            <p><b>Resolved At:</b> {time.ctime(now)}</p>
        </body></html>
        """
        send_alert(subject, body)

        state["status"] = STATUS_OK
        state["last_alert_time"] = None
        save_state(state_file, state)
        return

    print(f"[OK] Ping successful for {target_ip}")


if __name__ == "__main__":
    check_server()

