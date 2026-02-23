import sys
import os
import time
import json
import glob
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
        "last_alert_time": None,
        "files": {}
    }


def save_state(state_file, state):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def monitor_trace():
    print("[INFO] Trace Monitoring started...")

    while True:
        config = config_loader.get_config()
        trace_conf = config.get("trace_monitor")

        if not trace_conf:
            print("[ERROR] trace_monitor section missing in config.yaml")
            time.sleep(10)
            continue

        # ---- STRICT config loading ----
        server_name = trace_conf["server_name"]
        trace_dir = trace_conf["trace_dir"]
        filename_prefix = trace_conf["filename_prefix"]
        check_interval = trace_conf["check_interval_seconds"]
        cooldown = trace_conf["cooldown_seconds"]
        max_idle = trace_conf["max_idle_seconds"]
        state_dir = trace_conf["state_file_dir"]

        state_file = Path(state_dir) / "monitor_trace.json"
        state = load_state(state_file)

        previous_status = state["status"]
        last_alert_time = state["last_alert_time"]
        file_state = state.get("files", {})

        today = datetime.now().strftime("%Y-%m-%d")
        pattern = os.path.join(trace_dir, f"{filename_prefix}_*-Trace-{today}.log")
        matched_files = glob.glob(pattern)

        now = int(time.time())
        current_status = STATUS_OK
        reason = ""

        if not matched_files:
            current_status = STATUS_FAIL
            reason = "No trace files found"
        else:
            for trace_file in matched_files:
                try:
                    stat = os.stat(trace_file)
                except FileNotFoundError:
                    current_status = STATUS_FAIL
                    reason = f"Trace file missing: {os.path.basename(trace_file)}"
                    break

                last_seen = file_state.get(trace_file, {}).get("mtime")
                current_mtime = int(stat.st_mtime)

                if last_seen is not None and (now - current_mtime) >= max_idle:
                    current_status = STATUS_FAIL
                    reason = f"Trace file not growing: {os.path.basename(trace_file)}"
                    break

                file_state[trace_file] = {"mtime": current_mtime}

        # -------- OK → FAIL --------
        if previous_status == STATUS_OK and current_status == STATUS_FAIL:
            subject = f"TRACE ALERT | {server_name}"
            body = f"""
            <html><body>
                <p><b>ALERT:</b> SS7 trace activity issue detected.</p>
                <p><b>Server:</b> {server_name}</p>
                <p><b>Reason:</b> {reason}</p>
                <p><b>Time:</b> {datetime.fromtimestamp(now)}</p>
            </body></html>
            """
            send_alert(subject, body)

            state["status"] = STATUS_FAIL
            state["last_alert_time"] = now

        # -------- FAIL → FAIL (cooldown applies) --------
        elif previous_status == STATUS_FAIL and current_status == STATUS_FAIL:
            if last_alert_time is None or (now - last_alert_time) >= cooldown:
                subject = f"TRACE ALERT | {server_name} (Still Failing)"
                body = f"""
                <html><body>
                    <p><b>ALERT:</b> SS7 trace issue still ongoing.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <p><b>Reason:</b> {reason}</p>
                    <p><b>Time:</b> {datetime.fromtimestamp(now)}</p>
                </body></html>
                """
                send_alert(subject, body)
                state["last_alert_time"] = now
            else:
                print("[INFO] Trace issue ongoing, cooldown active.")

        # -------- FAIL → OK --------
        elif previous_status == STATUS_FAIL and current_status == STATUS_OK:
            subject = f"RESOLVED | Trace Normal on {server_name}"
            body = f"""
            <html><body>
                <p><b>RESOLVED:</b> SS7 trace files are updating normally.</p>
                <p><b>Server:</b> {server_name}</p>
                <p><b>Resolved At:</b> {datetime.fromtimestamp(now)}</p>
            </body></html>
            """
            send_alert(subject, body)

            state["status"] = STATUS_OK
            state["last_alert_time"] = None

        state["files"] = file_state
        save_state(state_file, state)

        time.sleep(check_interval)


if __name__ == "__main__":
    monitor_trace()

