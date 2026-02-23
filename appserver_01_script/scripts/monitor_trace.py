import sys
import os
import time
import json
import glob
import psycopg2
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


def is_ss7_traffic_active(config):
    """
    Checks whether SS7 traffic is active using MAX(timestamp_column)
    from public.ss7_log.
    """
    try:
        traffic_conf = config.get("traffic_monitor", {}).get("ss7")

        if not traffic_conf:
            print("[ERROR] traffic_monitor.ss7 missing in config.yaml")
            return False

        conn = psycopg2.connect(
            host=traffic_conf["db_host"],
            port=traffic_conf["db_port"],
            dbname=traffic_conf["db_name"],
            user=traffic_conf["db_user"],
            password=traffic_conf["db_password"],
            connect_timeout=5
        )

        query = f"""
            SELECT EXTRACT(EPOCH FROM (NOW() - MAX({traffic_conf['timestamp_column']})))
            FROM {traffic_conf['table']};
        """

        with conn.cursor() as cur:
            cur.execute(query)
            result = cur.fetchone()

        conn.close()

        if not result or result[0] is None:
            print("[INFO] No SS7 records found in DB.")
            return False

        seconds_diff = result[0]
        threshold = traffic_conf["inactivity_threshold_seconds"]

        if seconds_diff <= threshold:
            return True
        else:
            print(f"[INFO] SS7 traffic inactive (last record {int(seconds_diff)} seconds ago).")
            return False

    except Exception as e:
        print(f"[ERROR] SS7 traffic check failed: {e}")
        return False


def monitor_trace():
    print("[INFO] Trace Monitoring started...")

    while True:
        config = config_loader.get_config()
        trace_conf = config.get("trace_monitor")

        if not trace_conf:
            print("[ERROR] trace_monitor section missing in config.yaml")
            time.sleep(10)
            continue

        # -------- TRAFFIC CHECK --------
        if not is_ss7_traffic_active(config):
            print("[INFO] SS7 traffic not active on this site. Skipping trace monitoring.")
            time.sleep(trace_conf["check_interval_seconds"])
            continue

        # -------- STRICT CONFIG --------
        server_name = trace_conf["server_name"]
        trace_dir = trace_conf["trace_dir"]
        filename_prefix = trace_conf["filename_prefix"]
        check_interval = trace_conf["check_interval_seconds"]
        cooldown = trace_conf["cooldown_seconds"]
        max_idle = trace_conf["max_idle_seconds"]
        expected_file_count = trace_conf["trace_file_count"]
        state_dir = trace_conf["state_file_dir"]

        state_file = Path(state_dir) / "monitor_trace.json"
        state = load_state(state_file)

        previous_status = state["status"]
        last_alert_time = state["last_alert_time"]
        file_state = state.get("files", {})

        file_state = {
            f: v for f, v in file_state.items()
            if os.path.exists(f)
        }

        today = datetime.now().strftime("%Y-%m-%d")
        pattern = os.path.join(trace_dir, f"{filename_prefix}*-Trace-{today}.log")
        matched_files = glob.glob(pattern)

        now = int(time.time())
        current_status = STATUS_OK
        reason = ""

        if not matched_files:
            current_status = STATUS_FAIL
            reason = "No trace files found"

        elif len(matched_files) < expected_file_count:
            current_status = STATUS_FAIL
            reason = f"Expected {expected_file_count} trace files but found only {len(matched_files)}"

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

        # -------- FAIL → FAIL --------
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