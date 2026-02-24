import sys
import os
import time
import json
import psutil
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config_loader import config_loader
from mail_utils import send_alert

STATUS_OK = "OK"
STATUS_FAIL = "FAIL"


def get_cpu_user_usage():
    return psutil.cpu_times_percent(interval=1).user


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


def monitor_cpu():
    print("[INFO] CPU Monitoring started...")

    high_cpu_start = None

    while True:
        config = config_loader.get_config()
        cpu_conf = config.get("cpu_monitor")

        if not cpu_conf:
            print("[ERROR] cpu_monitor section missing in config.yaml")
            time.sleep(5)
            continue

        # ---- STRICT config loading (no defaults) ----
        cpu_threshold = cpu_conf["threshold"]
        monitor_duration = cpu_conf["monitor_duration"]
        cooldown = cpu_conf["cooldown_seconds"]
        server_name = cpu_conf["server_name"]
        state_dir = cpu_conf["state_file_dir"]

        state_file = Path(state_dir) / "monitor_cpu.json"

        state = load_state(state_file)
        previous_status = state["status"]
        last_alert_time = state["last_alert_time"]

        cpu_usage = get_cpu_user_usage()
        now = int(time.time())

        # -------- CPU detection logic --------
        if cpu_usage > cpu_threshold:
            if high_cpu_start is None:
                high_cpu_start = now
        else:
            high_cpu_start = None

        if high_cpu_start and (now - high_cpu_start) >= monitor_duration:
            current_status = STATUS_FAIL
        else:
            current_status = STATUS_OK

        # -------- ALERT (OK → FAIL) --------
        if previous_status == STATUS_OK and current_status == STATUS_FAIL:
            subject = f"CPU ALERT | High CPU Usage on {server_name}"
            body = f"""
            <html><body>
                <p><b>ALERT:</b> High CPU usage detected.</p>
                <p><b>Server:</b> {server_name}</p>
                <p><b>Threshold:</b> {cpu_threshold}%</p>
                <p><b>Current Usage:</b> {cpu_usage:.2f}%</p>
                <p><b>Duration:</b> {monitor_duration} seconds</p>
                <p><b>Time:</b> {time.ctime(now)}</p>
            </body></html>
            """
            send_alert(subject, body)

            state["status"] = STATUS_FAIL
            state["last_alert_time"] = now
            save_state(state_file, state)

        # -------- REPEATED FAIL (cooldown applies) --------
        elif previous_status == STATUS_FAIL and current_status == STATUS_FAIL:
            if last_alert_time is None or (now - last_alert_time) >= cooldown:
                subject = f"CPU ALERT | High CPU Still Ongoing on {server_name}"
                body = f"""
                <html><body>
                    <p><b>ALERT:</b> High CPU usage is still ongoing.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <p><b>Current Usage:</b> {cpu_usage:.2f}%</p>
                    <p><b>Threshold:</b> {cpu_threshold}%</p>
                    <p><b>Time:</b> {time.ctime(now)}</p>
                </body></html>
                """
                send_alert(subject, body)

                state["last_alert_time"] = now
                save_state(state_file, state)
            else:
                print("[INFO] CPU high, alert suppressed due to cooldown.")

        # -------- RESOLVED (FAIL → OK) --------
        elif previous_status == STATUS_FAIL and current_status == STATUS_OK:
            subject = f"RESOLVED | CPU Usage Normalized on {server_name}"
            body = f"""
            <html><body>
                <p><b>RESOLVED:</b> CPU usage is back to normal.</p>
                <p><b>Server:</b> {server_name}</p>
                <p><b>Current Usage:</b> {cpu_usage:.2f}%</p>
                <p><b>Threshold:</b> {cpu_threshold}%</p>
                <p><b>Resolved At:</b> {time.ctime(now)}</p>
            </body></html>
            """
            send_alert(subject, body)

            state["status"] = STATUS_OK
            state["last_alert_time"] = None
            save_state(state_file, state)

        time.sleep(1)


if __name__ == "__main__":
    monitor_cpu()

