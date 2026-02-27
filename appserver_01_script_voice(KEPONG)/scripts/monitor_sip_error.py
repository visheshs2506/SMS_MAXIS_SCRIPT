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


# ---------------- TRAFFIC CHECK ----------------
def is_sip_traffic_active(config):

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
            print("[INFO] No SIP records found in DB.")
            return False

        seconds_diff = result[0]
        threshold = traffic_conf["inactivity_threshold_seconds"]

        if seconds_diff <= threshold:
            return True
        else:
            print(f"[INFO] SIP traffic inactive ({int(seconds_diff)} seconds idle).")
            return False

    except Exception as e:
        print(f"[ERROR] SIP traffic check failed: {e}")
        return False


# ---------------- EXISTING FUNCTIONS ----------------

def get_active_sip_log(prefix):

    log_dir = os.path.dirname(prefix)

    try:
        files = [
            os.path.join(log_dir, f)
            for f in os.listdir(log_dir)
            if f.startswith(os.path.basename(prefix))
            and f.endswith(".log")
        ]

        if not files:
            return None

        return max(files, key=os.path.getmtime)

    except Exception:
        return None


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


def read_new_errors(log_path, offset, match_patterns, ignore_patterns):

    try:

        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:

            f.seek(0, os.SEEK_END)
            size = f.tell()

            if offset > size:
                offset = 0

            f.seek(offset)
            new_data = f.readlines()
            new_offset = f.tell()

        filtered = []

        for line in new_data:

            if not any(p in line for p in match_patterns):
                continue

            if any(p in line for p in ignore_patterns):
                continue

            filtered.append(line)

        return filtered, new_offset

    except Exception:
        return [], offset


# ---------------- MAIN ----------------

def monitor_sip_errors():

    print("[INFO] SIP Error Monitor started...")

    while True:

        config = config_loader.get_config()

        sip_conf = config.get("sip_monitor")

        if not sip_conf:
            print("[ERROR] sip_monitor section missing in config.yaml")
            time.sleep(5)
            continue

        interval = sip_conf["check_interval_seconds"]

        # -------- TRAFFIC CHECK --------
        if not is_sip_traffic_active(config):

            print("[INFO] SIP traffic not active on this site. Skipping monitoring.")
            time.sleep(interval)
            continue

        server_name = sip_conf["server_name"]
        instances = sip_conf["instances"]
        cooldown = sip_conf["cooldown_seconds"]
        state_dir = sip_conf["state_file_dir"]

        match_patterns = sip_conf.get("match_patterns", ["ERROR"])
        ignore_patterns = sip_conf.get("ignore_patterns", [])

        state_file = Path(state_dir) / "monitor_sip_error.json"
        state = load_state(state_file)

        now = int(time.time())

        for name, inst in instances.items():

            prefix = inst["path"]

            log_path = get_active_sip_log(prefix)

            if not log_path:
                print(f"[WARN] Today SIP log not found for {name}")
                continue

            inst_state = state.get(name, {
                "status": STATUS_OK,
                "last_alert_time": None,
                "offset": 0,
                "file": log_path
            })

            if inst_state.get("file") != log_path:
                inst_state["offset"] = 0
                inst_state["file"] = log_path

            previous_status = inst_state["status"]
            last_alert_time = inst_state["last_alert_time"]
            offset = inst_state.get("offset", 0)

            errors, new_offset = read_new_errors(
                log_path,
                offset,
                match_patterns,
                ignore_patterns
            )

            inst_state["offset"] = new_offset

            if errors:
                current_status = STATUS_FAIL
                sample = errors[-5:]
            else:
                current_status = STATUS_OK
                sample = []

            # -------- ALERT --------
            if previous_status == STATUS_OK and current_status == STATUS_FAIL:

                subject = f"SIP ERROR ALERT | {name} on {server_name}"

                body = f"""
                <html><body>
                <p><b>ALERT:</b> New SIP errors detected.</p>
                <p><b>Server:</b> {server_name}</p>
                <p><b>Instance:</b> {name}</p>
                <pre>{''.join(sample)}</pre>
                <p><b>Time:</b> {datetime.fromtimestamp(now)}</p>
                </body></html>
                """

                send_alert(subject, body)

                inst_state["status"] = STATUS_FAIL
                inst_state["last_alert_time"] = now

            elif previous_status == STATUS_FAIL and current_status == STATUS_FAIL:

                if last_alert_time is None or (now - last_alert_time) >= cooldown:

                    subject = f"SIP ERROR ALERT | {name} Still Erroring on {server_name}"

                    body = f"""
                    <html><body>
                    <p><b>ALERT:</b> SIP errors still ongoing.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <p><b>Instance:</b> {name}</p>
                    <pre>{''.join(sample)}</pre>
                    <p><b>Time:</b> {datetime.fromtimestamp(now)}</p>
                    </body></html>
                    """

                    send_alert(subject, body)

                    inst_state["last_alert_time"] = now

            elif previous_status == STATUS_FAIL and current_status == STATUS_OK:

                subject = f"RESOLVED | SIP Errors Cleared for {name} on {server_name}"

                body = f"""
                <html><body>
                <p><b>RESOLVED:</b> No new SIP errors detected.</p>
                <p><b>Server:</b> {server_name}</p>
                <p><b>Instance:</b> {name}</p>
                <p><b>Resolved At:</b> {datetime.fromtimestamp(now)}</p>
                </body></html>
                """

                send_alert(subject, body)

                inst_state["status"] = STATUS_OK
                inst_state["last_alert_time"] = None

            state[name] = inst_state

        save_state(state_file, state)

        time.sleep(interval)


if __name__ == "__main__":
    monitor_sip_errors()