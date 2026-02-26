import sys
import os
import time
import datetime
import json
import glob
import re
from pathlib import Path
from multiprocessing import Pool, cpu_count

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config_loader import config_loader
from mail_utils import send_alert

STATUS_OK = "OK"
STATUS_FAIL = "FAIL"
VERBOSE = True


def log(msg, force=False):
    global VERBOSE
    if VERBOSE or force:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] {msg}")


def get_today():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def load_state(state_file):
    if state_file.exists():
        try:
            with open(state_file, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"counts": {}, "alerts": {}, "status": {}}


def save_state(state_file, state):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


# -------- MULTIPROCESS WORKER --------
def process_pattern(args):

    pattern_conf, log_dir, filename_prefix, shared_counts = args

    label = pattern_conf["label"]
    grep = pattern_conf["grep"]

    today = get_today()
    file_pattern = f"{filename_prefix}*-Trace-{today}.log"
    files = glob.glob(os.path.join(log_dir, file_pattern))

    regex = re.compile(grep)
    local_counts = {}

    for file in files:

        offset_key = f"{file}::{label}::offset"
        offset = shared_counts.get(offset_key, 0)

        try:
            with open(file, "r", errors="ignore") as f:

                f.seek(0, os.SEEK_END)
                size = f.tell()

                if offset > size:
                    offset = 0

                f.seek(offset)
                new_data = f.read()
                new_offset = f.tell()

        except Exception:
            continue

        count = len(regex.findall(new_data))
        local_counts[file] = (count, new_offset)

    return (label, local_counts)


# -------- ALERT MAIL --------
def send_alert_html(failed, server_name):
    subject = f"{server_name} ALERT: Log Pattern Count Not Increasing"
    rows = ""
    for label, (prev, curr) in failed.items():
        rows += f"<tr><td>{label}</td><td>{prev}</td><td>{curr}</td></tr>"

    html = f"""
    <html><body>
        <p><b>ALERT:</b> Pattern activity stopped across all trace files.</p>
        <table border="1" cellpadding="5" cellspacing="0">
            <tr><th>Pattern</th><th>Previous</th><th>Current</th></tr>
            {rows}
        </table>
        <p>Server: <b>{server_name}</b></p>
    </body></html>
    """
    send_alert(subject, html)


def send_resolved_html(resolved, server_name):
    subject = f"RESOLVED: Pattern Activity Restored on {server_name}"
    rows = ""
    for label in resolved:
        rows += f"<tr><td>{label}</td></tr>"

    html = f"""
    <html><body>
        <p><b>RESOLVED:</b> Pattern activity resumed.</p>
        <table border="1" cellpadding="5" cellspacing="0">
            <tr><th>Pattern</th></tr>
            {rows}
        </table>
        <p>Server: <b>{server_name}</b></p>
    </body></html>
    """
    send_alert(subject, html)


# -------- MAIN --------
def monitor():

    global VERBOSE

    config = config_loader.get_config()
    conf = config["trace_responces"]

    log_dir = conf["log_dir"]
    filename_prefix = conf["filename_prefix"]
    server_name = conf["server_name"]
    state_dir = conf["state_file_dir"]
    patterns = conf["patterns"]
    VERBOSE = conf.get("log_verbose", True)

    state_file = Path(state_dir) / "monitor_responces.json"
    state = load_state(state_file)

    now = int(time.time())
    failed_alerts = {}
    resolved_alerts = {}

    pool = Pool(min(len(patterns), cpu_count()))

    results = pool.map(
        process_pattern,
        [(p, log_dir, filename_prefix, state["counts"]) for p in patterns]
    )

    pool.close()
    pool.join()

    pattern_result_map = dict(results)

    for pattern_conf in patterns:

        label = pattern_conf["label"]
        interval = pattern_conf["check_interval_seconds"]
        cooldown = pattern_conf["cooldown_seconds"]

        total_delta = 0

        for file, (curr, new_offset) in pattern_result_map.get(label, {}).items():
            offset_key = f"{file}::{label}::offset"
            state["counts"][offset_key] = new_offset
            total_delta += curr

        key = f"SUM::{label}"
        last_alert = state["alerts"].get(key, 0)
        prev_status = state["status"].get(key, STATUS_OK)

        if key not in state["counts"]:
            state["counts"][key] = {
                "baseline": total_delta,
                "delta": 0,
                "window_start": now
            }
            state["status"][key] = STATUS_OK
            log(f"[INIT] SUM Baseline set for {label} = {total_delta}")
            continue

        baseline = state["counts"][key]["baseline"]
        delta = state["counts"][key]["delta"]
        window_start = state["counts"][key]["window_start"]

        delta += total_delta
        state["counts"][key]["delta"] = delta

        if now - window_start < interval:
            continue

        new_total = baseline + delta

        log(f"[CHECK] {label} SUM matches in last {interval}s = {delta}")

        if delta == 0:
            if prev_status == STATUS_OK and now - last_alert >= cooldown:
                failed_alerts[label] = (baseline, new_total)
                state["alerts"][key] = now
                state["status"][key] = STATUS_FAIL
                log(f"[ALERT] {label} no increase across all traces", True)
        else:
            if prev_status == STATUS_FAIL:
                resolved_alerts[label] = True
                state["status"][key] = STATUS_OK
                state["alerts"].pop(key, None)
                log(f"[RESOLVED] {label} resumed", True)

        state["counts"][key] = {
            "baseline": new_total,
            "delta": 0,
            "window_start": now
        }

    if failed_alerts:
        send_alert_html(failed_alerts, server_name)

    if resolved_alerts:
        send_resolved_html(resolved_alerts, server_name)

    save_state(state_file, state)


if __name__ == "__main__":
    while True:
        monitor()
        time.sleep(60)
