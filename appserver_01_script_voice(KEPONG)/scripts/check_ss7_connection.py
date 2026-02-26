import sys
import os
import subprocess
import time
import json
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


def check_connections():
    config = config_loader.get_config()
    ss7_conf = config["ss7_connection_check"]

    # ---- STRICT config loading ----
    expected = ss7_conf["expected"]
    server_name = ss7_conf["server_name"]
    working_dir = ss7_conf["working_dir"]
    cooldown = ss7_conf["cooldown_seconds"]
    state_dir = ss7_conf["state_file_dir"]

    total_expected = expected["tcap"] + expected["link"] + expected["m3ua"]
    state_file = Path(state_dir) / "check_ss7_connection.json"

    state = load_state(state_file)
    previous_status = state["status"]
    last_alert_time = state["last_alert_time"]

    command = f"""
    cd {working_dir} &&
    source setV6.sh &&
    cd {working_dir}/cfg &&
    ss7maint ips
    """

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            executable="/bin/bash"
        )

        output = result.stdout.strip()

        connection_lines = [line for line in output.splitlines() if "connected" in line]
        tcap = sum(1 for line in connection_lines if "TCAP" in line)
        link = sum(1 for line in connection_lines if "LINK" in line)
        m3ua = sum(1 for line in connection_lines if "M3UA" in line)
        total_found = tcap + link + m3ua

        print(f"[CHECK] TCAP={tcap}, LINK={link}, M3UA={m3ua}, TOTAL={total_found}")

        current_status = STATUS_OK if total_found == total_expected else STATUS_FAIL
        now = int(time.time())

        # -------- OK → FAIL --------
        if previous_status == STATUS_OK and current_status == STATUS_FAIL:
            subject = f"SS7 ALERT | {server_name}"
            body = f"""
            <html><body>
                <p><b>ALERT:</b> SS7 connection mismatch detected.</p>
                <p><b>Server:</b> {server_name}</p>
                <p><b>Expected:</b> {total_expected}</p>
                <p><b>Found:</b> {total_found}</p>
                <pre style="background:#f4f4f4;padding:10px;border-left:4px solid red;">
{output}
                </pre>
                <p><b>Time:</b> {time.ctime(now)}</p>
            </body></html>
            """
            send_alert(subject, body)

            state["status"] = STATUS_FAIL
            state["last_alert_time"] = now
            save_state(state_file, state)
            return

        # -------- FAIL → FAIL (cooldown applies) --------
        if previous_status == STATUS_FAIL and current_status == STATUS_FAIL:
            if last_alert_time is None or (now - last_alert_time) >= cooldown:
                subject = f"SS7 ALERT | {server_name} (Still Failing)"
                body = f"""
                <html><body>
                    <p><b>ALERT:</b> SS7 issue still ongoing.</p>
                    <p><b>Server:</b> {server_name}</p>
                    <p><b>Expected:</b> {total_expected}</p>
                    <p><b>Found:</b> {total_found}</p>
                    <pre style="background:#f4f4f4;padding:10px;border-left:4px solid red;">
{output}
                    </pre>
                    <p><b>Time:</b> {time.ctime(now)}</p>
                </body></html>
                """
                send_alert(subject, body)

                state["last_alert_time"] = now
                save_state(state_file, state)
            else:
                print("[INFO] SS7 issue ongoing, cooldown active.")
            return

        # -------- FAIL → OK --------
        if previous_status == STATUS_FAIL and current_status == STATUS_OK:
            subject = f"RESOLVED | SS7 Connections Restored | {server_name}"
            body = f"""
            <html><body>
                <p><b>RESOLVED:</b> All SS7 connections are now active.</p>
                <p><b>Server:</b> {server_name}</p>
                <p><b>Total Connections:</b> {total_found}</p>
                <p><b>Resolved At:</b> {time.ctime(now)}</p>
            </body></html>
            """
            send_alert(subject, body)

            state["status"] = STATUS_OK
            state["last_alert_time"] = None
            save_state(state_file, state)
            return

        print("[OK] All SS7 connections are active.")

    except Exception as e:
        print(f"[ERROR] Failed to check SS7 connections: {e}")


if __name__ == "__main__":
    check_connections()

