import sys
import os
import time
import json
import psutil
from pathlib import Path
from datetime import datetime

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
    return {}


def save_state(state_file, state):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def monitor_storage():
    print("[INFO] Storage Monitoring started...")

    while True:
        config = config_loader.get_config()
        storage_conf = config.get("storage_monitor")

        if not storage_conf:
            print("[ERROR] storage_monitor section missing in config.yaml")
            time.sleep(10)
            continue

        # ---- STRICT config loading ----
        server_name = storage_conf["server_name"]
        directories = storage_conf["directories"]
        interval = storage_conf["check_interval_seconds"]
        state_dir = storage_conf["state_file_dir"]

        state_file = Path(state_dir) / "monitor_storage.json"
        state = load_state(state_file)

        now = int(time.time())

        for mount, params in directories.items():
            threshold = params["threshold"]
            cooldown = params["cooldown_seconds"]

            mount_state = state.get(mount, {
                "status": STATUS_OK,
                "last_alert_time": None
            })

            previous_status = mount_state["status"]
            last_alert_time = mount_state["last_alert_time"]

            try:
                usage = psutil.disk_usage(mount).percent
            except Exception as e:
                print(f"[ERROR] Cannot read disk usage for {mount}: {e}")
                continue

            print(f"[CHECK] {mount} => {usage:.2f}% (Threshold {threshold}%)")

            current_status = STATUS_FAIL if usage >= threshold else STATUS_OK

            # -------- OK → FAIL --------
            if previous_status == STATUS_OK and current_status == STATUS_FAIL:
                subject = f"STORAGE ALERT | {server_name} | {mount}"
                body = f"""
                <html><body>
                    <p><b>ALERT:</b> Disk usage exceeded threshold.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <p><b>Mount:</b> {mount}</p>
                    <p><b>Usage:</b> {usage:.2f}%</p>
                    <p><b>Threshold:</b> {threshold}%</p>
                    <p><b>Time:</b> {datetime.fromtimestamp(now)}</p>
                </body></html>
                """
                send_alert(subject, body)

                mount_state["status"] = STATUS_FAIL
                mount_state["last_alert_time"] = now

            # -------- FAIL → FAIL (cooldown applies) --------
            elif previous_status == STATUS_FAIL and current_status == STATUS_FAIL:
                if last_alert_time is None or (now - last_alert_time) >= cooldown:
                    subject = f"STORAGE ALERT | {server_name} | {mount} (Still High)"
                    body = f"""
                    <html><body>
                        <p><b>ALERT:</b> Disk usage still above threshold.</p>
                        <p><b>Server:</b> {server_name}</p>
                        <p><b>Mount:</b> {mount}</p>
                        <p><b>Usage:</b> {usage:.2f}%</p>
                        <p><b>Threshold:</b> {threshold}%</p>
                        <p><b>Time:</b> {datetime.fromtimestamp(now)}</p>
                    </body></html>
                    """
                    send_alert(subject, body)
                    mount_state["last_alert_time"] = now
                else:
                    print(f"[INFO] {mount} still high, cooldown active.")

            # -------- FAIL → OK --------
            elif previous_status == STATUS_FAIL and current_status == STATUS_OK:
                subject = f"RESOLVED | Storage Normal | {server_name} | {mount}"
                body = f"""
                <html><body>
                    <p><b>RESOLVED:</b> Disk usage is back within limits.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <p><b>Mount:</b> {mount}</p>
                    <p><b>Current Usage:</b> {usage:.2f}%</p>
                    <p><b>Resolved At:</b> {datetime.fromtimestamp(now)}</p>
                </body></html>
                """
                send_alert(subject, body)

                mount_state["status"] = STATUS_OK
                mount_state["last_alert_time"] = None

            state[mount] = mount_state

        save_state(state_file, state)
        time.sleep(interval)


if __name__ == "__main__":
    monitor_storage()

