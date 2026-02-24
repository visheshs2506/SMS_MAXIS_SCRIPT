import sys
import os
import time
import json
import subprocess
from pathlib import Path
from datetime import datetime

# Ensure parent dir is in PYTHONPATH
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


def check_service_status(service_name):
    try:
        result = subprocess.check_output(
            ["systemctl", "is-active", service_name],
            text=True
        ).strip()
        return result == "active"
    except subprocess.CalledProcessError:
        return False


def monitor_services():
    print("[INFO] HAProxy & Keepalived Monitoring started...")

    while True:
        config = config_loader.get_config()
        ha_conf = config.get("ha_monitor")

        if not ha_conf:
            print("[ERROR] ha_monitor section missing in config.yaml")
            time.sleep(5)
            continue

        # ---- STRICT config loading (no defaults) ----
        server_name = ha_conf["server_name"]
        services = ha_conf["services"]
        check_interval = ha_conf["check_interval_seconds"]
        cooldown = ha_conf["cooldown_seconds"]
        state_dir = ha_conf["state_file_dir"]

        state_file = Path(state_dir) / "monitor_ha_proxy_keepalived.json"

        state = load_state(state_file)
        previous_status = state["status"]
        last_alert_time = state["last_alert_time"]

        down_services = []
        status_report = ""

        for service in services:
            active = check_service_status(service)
            color = "green" if active else "red"
            status = "Running" if active else "NOT RUNNING"
            status_report += f"<li><b>{service}</b>: <span style='color:{color};'>{status}</span></li>"

            if not active:
                down_services.append(service)

        current_status = STATUS_FAIL if down_services else STATUS_OK
        now = int(time.time())

        # ---------------- ALERT (OK → FAIL) ----------------
        if previous_status == STATUS_OK and current_status == STATUS_FAIL:
            subject = f"HA ALERT | {server_name} - HA Services Down"
            body = f"""
            <html><body>
                <p><b>ALERT:</b> One or more HA services are not running.</p>
                <p><b>Server:</b> {server_name}</p>
                <ul>{status_report}</ul>
                <p><b>Time:</b> {time.ctime(now)}</p>
                <p>Please investigate immediately.</p>
            </body></html>
            """
            send_alert(subject, body)

            state["status"] = STATUS_FAIL
            state["last_alert_time"] = now
            save_state(state_file, state)

        # ---------------- REPEATED FAIL (cooldown applies) ----------------
        elif previous_status == STATUS_FAIL and current_status == STATUS_FAIL:
            if last_alert_time is None or (now - last_alert_time) >= cooldown:
                subject = f"HA ALERT | {server_name} - HA Issue Still Ongoing"
                body = f"""
                <html><body>
                    <p><b>ALERT:</b> HA services are still not running.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <ul>{status_report}</ul>
                    <p><b>Time:</b> {time.ctime(now)}</p>
                </body></html>
                """
                send_alert(subject, body)

                state["last_alert_time"] = now
                save_state(state_file, state)
            else:
                print("[INFO] HA issue ongoing, alert suppressed due to cooldown.")

        # ---------------- RESOLVED (FAIL → OK) ----------------
        elif previous_status == STATUS_FAIL and current_status == STATUS_OK:
            subject = f"RESOLVED | HA Services Restored on {server_name}"
            body = f"""
            <html><body>
                <p><b>RESOLVED:</b> All HA services are now running.</p>
                <p><b>Server:</b> {server_name}</p>
                <ul>{status_report}</ul>
                <p><b>Resolved At:</b> {time.ctime(now)}</p>
            </body></html>
            """
            send_alert(subject, body)

            state["status"] = STATUS_OK
            state["last_alert_time"] = None
            save_state(state_file, state)

        else:
            print(f"[{datetime.now()}] OK - All HA services running on {server_name}")

        time.sleep(check_interval)


if __name__ == "__main__":
    monitor_services()

