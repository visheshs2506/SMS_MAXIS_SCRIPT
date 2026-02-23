import sys
import os
import time
import glob
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


def monitor_smpp_cdr():
    print("[INFO] SMPP CDR Monitoring started...")

    while True:
        config = config_loader.get_config()
        cdr_conf = config.get("smpp_cdr_monitor")

        if not cdr_conf:
            print("[ERROR] smpp_cdr_monitor section missing in config.yaml")
            time.sleep(10)
            continue

        # ---- STRICT config loading ----
        server_name = cdr_conf["server_name"]
        watch_dir = cdr_conf["watch_dir"]
        cooldown = cdr_conf["cooldown_seconds"]
        loop_interval = cdr_conf["check_interval_seconds"]
        state_dir = cdr_conf["state_file_dir"]

        state_file = Path(state_dir) / "monitor_smpp_cdr.json"
        state = load_state(state_file)

        previous_status = state["status"]
        last_alert_time = state["last_alert_time"]
        now = int(time.time())

        all_files = sorted(
            glob.glob(os.path.join(watch_dir, "*.csv")),
            key=os.path.getmtime,
            reverse=True
        )

        current_status = STATUS_OK
        reason = ""

        if not all_files:
            current_status = STATUS_FAIL
            reason = "No CDR files found"

        else:
            latest_files = all_files[:2]
            sizes = [os.path.getsize(f) for f in latest_files]

            # Both zero
            if len(latest_files) == 2 and sizes[0] == 0 and sizes[1] == 0:
                current_status = STATUS_FAIL
                reason = "Both latest CDR files are 0 bytes"

            # Single file zero – check growth
            elif len(latest_files) == 1 and sizes[0] == 0:
                file = latest_files[0]
                size_before = os.path.getsize(file)
                time.sleep(120)
                size_after = os.path.getsize(file)
                if size_after == size_before:
                    current_status = STATUS_FAIL
                    reason = f"{os.path.basename(file)} not growing"

            # Otherwise OK
            else:
                current_status = STATUS_OK

        # -------- ALERT (OK → FAIL) --------
        if previous_status == STATUS_OK and current_status == STATUS_FAIL:
            subject = f"SMPP CDR ALERT | {server_name}"
            body = f"""
            <html><body>
                <p><b>ALERT:</b> SMPP CDR issue detected.</p>
                <p><b>Server:</b> {server_name}</p>
                <p><b>Reason:</b> {reason}</p>
                <p><b>Time:</b> {datetime.fromtimestamp(now)}</p>
            </body></html>
            """
            send_alert(subject, body)

            state["status"] = STATUS_FAIL
            state["last_alert_time"] = now
            save_state(state_file, state)

        # -------- REPEATED FAIL (cooldown applies) --------
        elif previous_status == STATUS_FAIL and current_status == STATUS_FAIL:
            if last_alert_time is None or (now - last_alert_time) >= cooldown:
                subject = f"SMPP CDR ALERT | {server_name} (Still Failing)"
                body = f"""
                <html><body>
                    <p><b>ALERT:</b> SMPP CDR issue still ongoing.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <p><b>Reason:</b> {reason}</p>
                    <p><b>Time:</b> {datetime.fromtimestamp(now)}</p>
                </body></html>
                """
                send_alert(subject, body)

                state["last_alert_time"] = now
                save_state(state_file, state)
            else:
                print("[INFO] SMPP CDR issue ongoing, cooldown active.")

        # -------- RESOLVED (FAIL → OK) --------
        elif previous_status == STATUS_FAIL and current_status == STATUS_OK:
            subject = f"RESOLVED | SMPP CDR Normal on {server_name}"
            body = f"""
            <html><body>
                <p><b>RESOLVED:</b> SMPP CDR file generation is back to normal.</p>
                <p><b>Server:</b> {server_name}</p>
                <p><b>Resolved At:</b> {datetime.fromtimestamp(now)}</p>
            </body></html>
            """
            send_alert(subject, body)

            state["status"] = STATUS_OK
            state["last_alert_time"] = None
            save_state(state_file, state)

        time.sleep(loop_interval)


if __name__ == "__main__":
    monitor_smpp_cdr()

