import sys
import os
import time
import subprocess
import json
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config_loader import config_loader
from mail_utils import send_alert

STATUS_OK = "OK"
STATUS_FAIL = "FAIL"


def timestamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def is_service_running(command):
    try:
        result = subprocess.run(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


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


def monitor_services():
    print(f"[{timestamp()}] [INFO] Service Monitoring started")

    while True:
        config = config_loader.get_config()
        svc_conf = config.get("service_monitor")

        if not svc_conf:
            print(f"[{timestamp()}] [ERROR] service_monitor section missing in config.yaml")
            time.sleep(5)
            continue

        # ---- STRICT config loading ----
        server_name = svc_conf["server_name"]
        services = svc_conf["services"]
        loop_interval = svc_conf["check_interval_seconds"]
        state_dir = svc_conf["state_file_dir"]

        state_file = Path(state_dir) / "monitor_services.json"
        state = load_state(state_file)

        now = int(time.time())

        for svc in services:
            name = svc["name"]
            command = svc["check_command"]
            interval = svc["check_interval_seconds"]
            cooldown = svc["cooldown_seconds"]

            svc_state = state.get(name, {
                "status": STATUS_OK,
                "last_check_time": 0,
                "last_alert_time": None
            })

            # Respect per-service check interval
            if now - svc_state["last_check_time"] < interval:
                continue

            svc_state["last_check_time"] = now

            running = is_service_running(command)
            current_status = STATUS_OK if running else STATUS_FAIL
            previous_status = svc_state["status"]
            last_alert_time = svc_state["last_alert_time"]

            # -------- ALERT (OK → FAIL) --------
            if previous_status == STATUS_OK and current_status == STATUS_FAIL:
                subject = f"SERVICE ALERT | {name} Down on {server_name}"
                body = f"""
                <html><body>
                    <p><b>ALERT:</b> Service is not running.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <p><b>Service:</b> {name}</p>
                    <p><b>Check:</b> <code>{command}</code></p>
                    <p><b>Time:</b> {time.ctime(now)}</p>
                </body></html>
                """
                send_alert(subject, body)

                svc_state["status"] = STATUS_FAIL
                svc_state["last_alert_time"] = now

            # -------- REPEATED FAIL (cooldown applies) --------
            elif previous_status == STATUS_FAIL and current_status == STATUS_FAIL:
                if last_alert_time is None or (now - last_alert_time) >= cooldown:
                    subject = f"SERVICE ALERT | {name} Still Down on {server_name}"
                    body = f"""
                    <html><body>
                        <p><b>ALERT:</b> Service is still not running.</p>
                        <p><b>Server:</b> {server_name}</p>
                        <p><b>Service:</b> {name}</p>
                        <p><b>Time:</b> {time.ctime(now)}</p>
                    </body></html>
                    """
                    send_alert(subject, body)

                    svc_state["last_alert_time"] = now
                else:
                    print(f"[{timestamp()}] [INFO] {name} down, alert suppressed (cooldown)")

            # -------- RESOLVED (FAIL → OK) --------
            elif previous_status == STATUS_FAIL and current_status == STATUS_OK:
                subject = f"RESOLVED | {name} Restored on {server_name}"
                body = f"""
                <html><body>
                    <p><b>RESOLVED:</b> Service is running again.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <p><b>Service:</b> {name}</p>
                    <p><b>Resolved At:</b> {time.ctime(now)}</p>
                </body></html>
                """
                send_alert(subject, body)

                svc_state["status"] = STATUS_OK
                svc_state["last_alert_time"] = None

            state[name] = svc_state

        save_state(state_file, state)
        time.sleep(loop_interval)


if __name__ == "__main__":
    monitor_services()

