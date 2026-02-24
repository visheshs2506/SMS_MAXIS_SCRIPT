import sys
import os
import time
import json
import psutil
from pathlib import Path

# Set path to import project modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config_loader import config_loader
from mail_utils import send_alert

STATUS_OK = "OK"
STATUS_FAIL = "FAIL"


def get_running_jar_processes():
    running = []
    for proc in psutil.process_iter(attrs=["cmdline"]):
        try:
            cmd = proc.info["cmdline"]
            if cmd and "java" in cmd[0].lower():
                running.append(" ".join(cmd))
        except Exception:
            continue
    return running


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


def monitor_jar_processes():
    print("[INFO] JAR Monitoring started...")

    while True:
        config = config_loader.get_config()
        jar_conf = config.get("jar_monitor")

        if not jar_conf:
            print("[ERROR] jar_monitor section missing in config.yaml")
            time.sleep(5)
            continue

        # ---- STRICT config loading ----
        server_name = jar_conf["server_name"]
        processes = jar_conf["processes"]
        default_cooldown = jar_conf["cooldown_seconds"]
        check_interval = jar_conf["check_interval_seconds"]
        state_dir = jar_conf["state_file_dir"]

        state_file = Path(state_dir) / "monitor_jar.json"
        state = load_state(state_file)

        running_cmds = get_running_jar_processes()
        now = int(time.time())

        for jar_name, per_jar_cooldown in processes.items():
            cooldown = per_jar_cooldown or default_cooldown

            jar_state = state.get(jar_name, {
                "status": STATUS_OK,
                "last_alert_time": None
            })

            previous_status = jar_state["status"]
            last_alert_time = jar_state["last_alert_time"]

            is_running = any(jar_name in cmd for cmd in running_cmds)
            current_status = STATUS_OK if is_running else STATUS_FAIL

            # -------- ALERT (OK → FAIL) --------
            if previous_status == STATUS_OK and current_status == STATUS_FAIL:
                subject = f"JAR ALERT | {jar_name} Down on {server_name}"
                body = f"""
                <html><body>
                    <p><b>ALERT:</b> Java application is not running.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <p><b>Process:</b> {jar_name}</p>
                    <p><b>Time:</b> {time.ctime(now)}</p>
                </body></html>
                """
                send_alert(subject, body)

                jar_state["status"] = STATUS_FAIL
                jar_state["last_alert_time"] = now
                state[jar_name] = jar_state
                save_state(state_file, state)

            # -------- REPEATED FAIL (cooldown applies) --------
            elif previous_status == STATUS_FAIL and current_status == STATUS_FAIL:
                if last_alert_time is None or (now - last_alert_time) >= cooldown:
                    subject = f"JAR ALERT | {jar_name} Still Down on {server_name}"
                    body = f"""
                    <html><body>
                        <p><b>ALERT:</b> Java application is still not running.</p>
                        <p><b>Server:</b> {server_name}</p>
                        <p><b>Process:</b> {jar_name}</p>
                        <p><b>Time:</b> {time.ctime(now)}</p>
                    </body></html>
                    """
                    send_alert(subject, body)

                    jar_state["last_alert_time"] = now
                    state[jar_name] = jar_state
                    save_state(state_file, state)
                else:
                    print(f"[INFO] {jar_name} down, alert suppressed due to cooldown.")

            # -------- RESOLVED (FAIL → OK) --------
            elif previous_status == STATUS_FAIL and current_status == STATUS_OK:
                subject = f"RESOLVED | {jar_name} Restored on {server_name}"
                body = f"""
                <html><body>
                    <p><b>RESOLVED:</b> Java application is running again.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <p><b>Process:</b> {jar_name}</p>
                    <p><b>Resolved At:</b> {time.ctime(now)}</p>
                </body></html>
                """
                send_alert(subject, body)

                jar_state["status"] = STATUS_OK
                jar_state["last_alert_time"] = None
                state[jar_name] = jar_state
                save_state(state_file, state)

            else:
                print(f"[OK] {jar_name} running normally.")

        time.sleep(check_interval)


if __name__ == "__main__":
    monitor_jar_processes()

