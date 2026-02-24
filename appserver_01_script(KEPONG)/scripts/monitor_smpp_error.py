import sys
import os
import time
import json
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
    return {}


def save_state(state_file, state):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def is_smpp_traffic_active(config):
    """
    Checks whether SMPP traffic is active based on MAX(time_stamp)
    from public.smpp_cdr_data table.
    """
    try:
        traffic_conf = config.get("traffic_monitor", {}).get("smpp")

        if not traffic_conf:
            print("[ERROR] traffic_monitor.smpp missing in config.yaml")
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
            print("[INFO] No SMPP records found in DB.")
            return False

        seconds_diff = result[0]
        threshold = traffic_conf["inactivity_threshold_seconds"]

        if seconds_diff <= threshold:
            return True
        else:
            print(f"[INFO] SMPP traffic inactive (last record {int(seconds_diff)} seconds ago).")
            return False

    except Exception as e:
        print(f"[ERROR] Traffic check failed: {e}")
        return False


def read_error_lines(log_path, match_patterns, ignore_patterns):
    """
    Read log file and return lines that:
    - Match at least one match_pattern
    - Do NOT match any ignore_pattern
    """
    try:
        filtered = []

        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:

                if not any(pattern in line for pattern in match_patterns):
                    continue

                if any(pattern in line for pattern in ignore_patterns):
                    continue

                filtered.append(line)

        return filtered

    except Exception:
        return []


def monitor_smpp_errors():
    print("[INFO] SMPP Error Monitor started.")

    while True:
        config = config_loader.get_config()
        smpp_conf = config.get("smpp_monitor")

        if not smpp_conf:
            print("[ERROR] smpp_monitor section missing in config.yaml")
            time.sleep(5)
            continue

        # -------- TRAFFIC CHECK --------
        if not is_smpp_traffic_active(config):
            print("[INFO] SMPP traffic not active on this site. Skipping SMPP error monitoring.")
            time.sleep(smpp_conf["check_interval_seconds"])
            continue

        # ---- STRICT config loading ----
        server_name = smpp_conf["server_name"]
        instances = smpp_conf["instances"]
        cooldown = smpp_conf["cooldown_seconds"]
        interval = smpp_conf["check_interval_seconds"]
        state_dir = smpp_conf["state_file_dir"]

        match_patterns = smpp_conf.get("match_patterns", ["ERROR"])
        ignore_patterns = smpp_conf.get("ignore_patterns", [])

        state_file = Path(state_dir) / "monitor_smpp_error.json"
        state = load_state(state_file)

        now = int(time.time())

        for name, inst in instances.items():
            log_path = inst["path"]

            if not os.path.exists(log_path):
                print(f"[WARN] Log not found: {log_path}")
                continue

            inst_state = state.get(name, {
                "status": STATUS_OK,
                "last_alert_time": None,
                "error_count": 0
            })

            previous_status = inst_state["status"]
            last_alert_time = inst_state["last_alert_time"]
            old_count = inst_state["error_count"]

            error_lines = read_error_lines(log_path, match_patterns, ignore_patterns)
            new_count = len(error_lines)

            if new_count > old_count:
                current_status = STATUS_FAIL
                new_errors = error_lines[old_count:][-5:]
            else:
                current_status = STATUS_OK
                new_errors = []

            # -------- ALERT (OK → FAIL) --------
            if previous_status == STATUS_OK and current_status == STATUS_FAIL:
                subject = f"SMPP ERROR ALERT | {name} on {server_name}"
                body = f"""
                <html><body>
                    <p><b>ALERT:</b> New SMPP errors detected.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <p><b>Instance:</b> {name}</p>
                    <pre>{''.join(new_errors)}</pre>
                    <p><b>Time:</b> {datetime.fromtimestamp(now)}</p>
                </body></html>
                """
                send_alert(subject, body)

                inst_state["status"] = STATUS_FAIL
                inst_state["last_alert_time"] = now

            # -------- REPEATED FAIL --------
            elif previous_status == STATUS_FAIL and current_status == STATUS_FAIL:
                if last_alert_time is None or (now - last_alert_time) >= cooldown:
                    subject = f"SMPP ERROR ALERT | {name} Still Erroring on {server_name}"
                    body = f"""
                    <html><body>
                        <p><b>ALERT:</b> SMPP errors are still occurring.</p>
                        <p><b>Server:</b> {server_name}</p>
                        <p><b>Instance:</b> {name}</p>
                        <pre>{''.join(new_errors)}</pre>
                        <p><b>Time:</b> {datetime.fromtimestamp(now)}</p>
                    </body></html>
                    """
                    send_alert(subject, body)

                    inst_state["last_alert_time"] = now
                else:
                    print(f"[INFO] {name} errors ongoing, cooldown active.")

            # -------- RESOLVED --------
            elif previous_status == STATUS_FAIL and current_status == STATUS_OK:
                subject = f"RESOLVED | SMPP Errors Cleared for {name} on {server_name}"
                body = f"""
                <html><body>
                    <p><b>RESOLVED:</b> No new SMPP errors detected.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <p><b>Instance:</b> {name}</p>
                    <p><b>Resolved At:</b> {datetime.fromtimestamp(now)}</p>
                </body></html>
                """
                send_alert(subject, body)

                inst_state["status"] = STATUS_OK
                inst_state["last_alert_time"] = None

            inst_state["error_count"] = new_count
            state[name] = inst_state

        save_state(state_file, state)
        time.sleep(interval)


if __name__ == "__main__":
    monitor_smpp_errors()