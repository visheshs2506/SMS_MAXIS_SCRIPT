import sys
import os
import time
import glob
import json
import psycopg2
from pathlib import Path
from datetime import datetime

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


def monitor_ss7_cdr():
    print("[INFO] SS7 CDR Monitoring started...")

    while True:
        config = config_loader.get_config()
        ss7_conf = config.get("ss7_monitor")

        if not ss7_conf:
            print("[ERROR] ss7_monitor section missing in config.yaml")
            time.sleep(10)
            continue

        # -------- TRAFFIC CHECK --------
        if not is_ss7_traffic_active(config):
            print("[INFO] SS7 traffic not active on this site. Skipping monitoring.")
            time.sleep(ss7_conf["check_interval_seconds"])
            continue

        # ---- STRICT config loading ----
        server_name = ss7_conf["server_name"]
        watch_dir = ss7_conf["watch_dir"]
        cooldown = ss7_conf["cooldown_seconds"]
        loop_interval = ss7_conf["check_interval_seconds"]
        state_dir = ss7_conf["state_file_dir"]

        state_file = Path(state_dir) / "monitor_ss7_cdr.json"
        state = load_state(state_file)

        previous_status = state["status"]
        last_alert_time = state["last_alert_time"]
        now = int(time.time())

        files = sorted(
            glob.glob(os.path.join(watch_dir, "*.csv")),
            key=os.path.getmtime,
            reverse=True
        )

        current_status = STATUS_OK
        reason = ""

        if not files:
            current_status = STATUS_FAIL
            reason = "No SS7 CDR files found"

        else:
            latest_files = files[:2]
            sizes = [os.path.getsize(f) for f in latest_files]

            if len(latest_files) == 2 and sizes[0] == 0 and sizes[1] == 0:
                current_status = STATUS_FAIL
                reason = "Both latest SS7 CDR files are 0 bytes"

            elif len(latest_files) == 1 and sizes[0] == 0:
                file = latest_files[0]
                size_before = os.path.getsize(file)
                time.sleep(120)
                size_after = os.path.getsize(file)

                if size_after == size_before:
                    current_status = STATUS_FAIL
                    reason = f"{os.path.basename(file)} stuck at 0 bytes"

            else:
                current_status = STATUS_OK

        # -------- ALERT --------
        if previous_status == STATUS_OK and current_status == STATUS_FAIL:
            subject = f"SS7 CDR ALERT | {server_name}"
            body = f"""
            <html><body>
                <p><b>ALERT:</b> SS7 CDR issue detected.</p>
                <p><b>Server:</b> {server_name}</p>
                <p><b>Reason:</b> {reason}</p>
                <p><b>Time:</b> {datetime.fromtimestamp(now)}</p>
            </body></html>
            """
            send_alert(subject, body)

            state["status"] = STATUS_FAIL
            state["last_alert_time"] = now
            save_state(state_file, state)

        elif previous_status == STATUS_FAIL and current_status == STATUS_FAIL:
            if last_alert_time is None or (now - last_alert_time) >= cooldown:
                subject = f"SS7 CDR ALERT | {server_name} (Still Failing)"
                body = f"""
                <html><body>
                    <p><b>ALERT:</b> SS7 CDR issue still ongoing.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <p><b>Reason:</b> {reason}</p>
                    <p><b>Time:</b> {datetime.fromtimestamp(now)}</p>
                </body></html>
                """
                send_alert(subject, body)

                state["last_alert_time"] = now
                save_state(state_file, state)
            else:
                print("[INFO] SS7 CDR issue ongoing, cooldown active.")

        elif previous_status == STATUS_FAIL and current_status == STATUS_OK:
            subject = f"RESOLVED | SS7 CDR Normal on {server_name}"
            body = f"""
            <html><body>
                <p><b>RESOLVED:</b> SS7 CDR file generation is back to normal.</p>
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
    monitor_ss7_cdr()