import sys
import os
import time
import datetime
import json
from pathlib import Path

# Add base directory to sys path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config_loader import config_loader
from mail_utils import send_alert

STATUS_OK = "OK"
STATUS_FAIL = "FAIL"


def log(msg):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}")


def get_today():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def run_grep_count(log_dir, pattern):
    cmd = f"cd {log_dir} && grep -c '{pattern}' *-Trace-{get_today()}.log"
    output = os.popen(cmd).read().strip()
    result = {}
    for line in output.splitlines():
        if ":" in line:
            filename, count = line.split(":")
            result[os.path.join(log_dir, filename)] = int(count)
    return result


def load_state(state_file):
    if state_file.exists():
        try:
            with open(state_file, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "counts": {},
        "alerts": {},
        "status": {}
    }


def save_state(state_file, state):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def send_alert_html(failed, server_name):
    subject = f"{server_name} ALERT: Log Pattern Count Not Increasing"
    rows = ""
    for file, items in failed.items():
        for label, (prev, curr) in items.items():
            rows += f"<tr><td>{file}</td><td>{label}</td><td>{prev}</td><td>{curr}</td></tr>"

    html = f"""
    <html><body>
        <p><b>ALERT:</b> The following log patterns have stopped increasing.</p>
        <table border="1" cellpadding="5" cellspacing="0">
            <tr><th>Log File</th><th>Pattern</th><th>Previous</th><th>Current</th></tr>
            {rows}
        </table>
        <p>Server: <b>{server_name}</b></p>
    </body></html>
    """
    send_alert(subject, html)


def send_resolved_html(resolved, server_name):
    subject = f"RESOLVED: Log Pattern Activity Restored on {server_name}"
    rows = ""
    for file, labels in resolved.items():
        for label in labels:
            rows += f"<tr><td>{file}</td><td>{label}</td></tr>"

    html = f"""
    <html><body>
        <p><b>RESOLVED:</b> Log pattern activity has resumed.</p>
        <table border="1" cellpadding="5" cellspacing="0">
            <tr><th>Log File</th><th>Pattern</th></tr>
            {rows}
        </table>
        <p>Server: <b>{server_name}</b></p>
    </body></html>
    """
    send_alert(subject, html)


def monitor():
    config = config_loader.get_config()
    conf = config["trace_responces"]

    log_dir = conf["log_dir"]
    server_name = conf["server_name"]
    state_dir = conf["state_file_dir"]
    patterns = conf["patterns"]

    state_file = Path(state_dir) / "monitor_responces.json"
    state = load_state(state_file)

    now = int(time.time())
    failed_alerts = {}
    resolved_alerts = {}

    for pattern_conf in patterns:
        label = pattern_conf["label"]
        grep = pattern_conf["grep"]
        interval = pattern_conf["check_interval_seconds"]
        cooldown = pattern_conf["cooldown_seconds"]

        current_counts = run_grep_count(log_dir, grep)

        for file, curr in current_counts.items():
            key = f"{file}::{label}"
            last_entry = state["counts"].get(key)
            last_alert = state["alerts"].get(key, 0)
            prev_status = state["status"].get(key, STATUS_OK)

            # First run → baseline
            if not last_entry:
                state["counts"][key] = {"count": curr, "time": now}
                state["status"][key] = STATUS_OK
                log(f"[INIT] Baseline set for {key} = {curr}")
                continue

            prev = last_entry["count"]
            last_time = last_entry["time"]

            if now - last_time < interval:
                continue

            if curr <= prev:
                if prev_status == STATUS_OK and now - last_alert >= cooldown:
                    failed_alerts.setdefault(file, {})[label] = (prev, curr)
                    state["alerts"][key] = now
                    state["status"][key] = STATUS_FAIL
                    log(f"[ALERT] {key} not increasing")
            else:
                if prev_status == STATUS_FAIL:
                    resolved_alerts.setdefault(file, []).append(label)
                    state["status"][key] = STATUS_OK
                    state["alerts"].pop(key, None)
                    log(f"[RESOLVED] {key} resumed")

            state["counts"][key] = {"count": curr, "time": now}

    if failed_alerts:
        send_alert_html(failed_alerts, server_name)

    if resolved_alerts:
        send_resolved_html(resolved_alerts, server_name)

    save_state(state_file, state)


if __name__ == "__main__":
    while True:
        monitor()
        time.sleep(60)

