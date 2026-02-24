import sys
import os
import datetime
import subprocess
import shutil
import psutil
import json
import psycopg2

# Add parent directory to import custom modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config_loader import config_loader
from mail_utils import send_alert

log_config = config_loader.get_config().get("log", {})

# Fallback values in case keys are missing
LOG_DIR = log_config.get("directory", "/tmp/monitor_logs")
FILENAME_PREFIX = log_config.get("filename_prefix", "report")
TIMESTAMP_FORMAT = log_config.get("timestamp_format", "%Y-%m-%d_%H-%M-%S")
EXTENSION = log_config.get("extension", ".log")

# Ensure directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# Build log file path
timestamp = datetime.datetime.now().strftime(TIMESTAMP_FORMAT)
log_filename = f"{FILENAME_PREFIX}_{timestamp}{EXTENSION}"
LOG_FILE = os.path.join(LOG_DIR, log_filename)

# Current date for report header
CURRENT_DATE = datetime.datetime.now().strftime("%Y-%m-%d")

def format_line(label, actual, expected, label_width=40, actual_width=20, expected_width=30):
    label = str(label)[:label_width]    # truncate if longer than width
    actual = str(actual)[:actual_width]
    expected = str(expected)[:expected_width]

    return (
        f"{label:<{label_width}}| "
        f"{actual:<{actual_width}}| "
        f"{expected:<{expected_width}}"
    )

def safe_format_line(row, label_width=40, actual_width=20, expected_width=30):
    if isinstance(row, list) and len(row) == 3:
        return format_line(row[0], row[1], row[2], label_width, actual_width, expected_width)
    elif isinstance(row, str):
        return row
    else:
        return format_line(str(row), "Invalid", "Invalid", label_width, actual_width, expected_width)

# -----------------------------
# ✅ Header of the report
# -----------------------------

def generate_table_header():
    header = format_line("Task/Process", "Actual Result", "Expected Result")
    divider = "-" * len(header)
    return [header, divider]

# -----------------------------
# ✅ Check JAR Status Function
# -----------------------------

def check_jar_status(config):
    jar_config = config.get("jar_monitor", {})
    jar_list = list(jar_config.get("processes", {}).keys())
    expected_count = len(jar_list)
    CURRENT_DATE = datetime.datetime.now().strftime("%Y-%m-%d")

    running_count = 0
    today_running_count = 0

    for jar in jar_list:
        result = subprocess.run(
            f"ps -ef | grep '{jar}' | grep -v grep",
            shell=True, text=True, capture_output=True
        )
        if result.stdout.strip():
            running_count += 1
            for line in result.stdout.strip().split('\n'):
                try:
                    pid = line.split()[1]
                    start_time_result = subprocess.run(
                        f"ps -p {pid} -o lstart=",
                        shell=True, text=True, capture_output=True
                    )
                    if start_time_result.stdout.strip():
                        start_time = datetime.datetime.strptime(
                            start_time_result.stdout.strip(), "%a %b %d %H:%M:%S %Y"
                        )
                        if start_time.strftime("%Y-%m-%d") == CURRENT_DATE:
                            today_running_count += 1
                except Exception:
                    continue

    return [
        f"{'JAR Status for SMPP':<30} | {running_count}/{expected_count:<3}       | Expected: {expected_count}/{expected_count}",
        f"{'JARs started today':<30} | {today_running_count}/{expected_count:<3}       | Expected: 0/{expected_count}"
    ]


# -----------------------------
# ✅ Check CFG Status Function
# -----------------------------
def check_cfg_status(config, current_date):
    base_path = config['cfg_monitor']['cfg_base_path']
    cfg_count = config['cfg_monitor']['cfg_count']
    running_count = 0
    today_running_count = 0

    for i in range(1, cfg_count + 1):
        cfg_name = f"{base_path}_{i}.cfg"
        result = subprocess.run(f"ps -ef | grep '{cfg_name}' | grep -v grep", shell=True, text=True, capture_output=True)
        if result.stdout.strip():
            running_count += 1
            for line in result.stdout.strip().split('\n'):
                pid = line.split()[1]
                start_time_result = subprocess.run(f"ps -p {pid} -o lstart=", shell=True, text=True, capture_output=True)
                if start_time_result.stdout.strip():
                    try:
                        start_time = datetime.datetime.strptime(start_time_result.stdout.strip(), "%a %b %d %H:%M:%S %Y")
                        if start_time.strftime("%Y-%m-%d") == current_date:
                            today_running_count += 1
                    except:
                        pass

    return [
        ["CFG Status", f"{running_count}/{cfg_count}", f"Expected: {cfg_count}/{cfg_count}"],
        ["CFG Started Today", f"{today_running_count}/{cfg_count}", f"Expected: 0/{cfg_count}"]
    ]


# -----------------------------
# ✅ Check Storage Status Function
# -----------------------------

def check_storage_status(config):
    storage_config = config.get("storage_monitor", {})
    directories = storage_config.get("directories", {})
    storage_status = []

    for directory, dir_conf in directories.items():
        threshold = dir_conf.get("threshold", 90)  # Default to 90% if not set

        try:
            total, used, free = shutil.disk_usage(directory)
            usage_percentage = (used / total) * 100
            line = f"{'Storage for ' + directory:<30} | {usage_percentage:.2f}%       | Not Exceed: {threshold}%"
        except FileNotFoundError:
            line = f"{'Storage for ' + directory:<30} | Directory Not Found | Expected Threshold: {threshold}%"

        storage_status.append(line)

    return storage_status


# -----------------------------
# ✅ Check Memory Status Function
# -----------------------------

def check_memory_usage(config):
    memory_config = config.get("memory_monitor", {})
    threshold = memory_config.get("threshold", 70)  # Default to 70% if not specified
    mem = psutil.virtual_memory()
    usage_percent = mem.percent

    return [f"{'Memory Usage':<30} | {usage_percent:.2f}%      | Not Exceed: {threshold}%"]



# -----------------------------
# ✅ Check CPU Status Function
# -----------------------------

def check_cpu_idle(config):
    cpu_config = config.get("cpu_monitor", {})
    threshold = cpu_config.get("threshold", 80)  # Default threshold if not in YAML

    try:
        result = subprocess.run(['top', '-b', '-n', '1'], capture_output=True, text=True)
        lines = result.stdout.split('\n')
        for line in lines:
            if "%Cpu(s):" in line:
                parts = line.split(',')
                for part in parts:
                    if "id" in part:
                        cpu_idle = float(part.strip().split()[0])
                        return [f'CPU "id" Usage                 | {cpu_idle:.1f}        | Not Below: {100 - threshold}']
        return ['CPU "id" Usage                 | Not Found | Missing CPU id data']
    except Exception as e:
        return [f'CPU "id" Usage                 | Error     | {str(e)}']


# -----------------------------
# ✅ Check ss7 connection Function
# -----------------------------

def check_ss7_connections(config):
    ss7_conf = config.get("ss7_connection_check", {})
    expected = ss7_conf.get("expected", {})
    expected_tcap = expected.get("tcap", 0)
    expected_m3ua = expected.get("m3ua", 0)
    expected_link = expected.get("link", 0)
    working_dir = ss7_conf.get("working_dir", "/usr/local/aculab/v6")

    try:
        command = f"""
        cd {working_dir} &&
        source setV6.sh &&
        cd cfg &&
        ss7maint ips
        """
        result = subprocess.run(command, shell=True, capture_output=True, text=True, executable="/bin/bash")
        output = result.stdout.strip()

        if not output:
            return ["SS7 Connection Status          | No Output     | Check environment/setup"]

        connection_lines = [line for line in output.splitlines() if "connected" in line]
        tcap_count = sum(1 for line in connection_lines if "TCAP" in line)
        m3ua_count = sum(1 for line in connection_lines if "M3UA" in line)
        link_count = sum(1 for line in connection_lines if "LINK" in line)

        return [
            f"TCAP connected                 | {tcap_count}/{expected_tcap:<5}    | Expected: {expected_tcap}/{expected_tcap}",
            f"M3UA connected                 | {m3ua_count}/{expected_m3ua:<5}     | Expected: {expected_m3ua}/{expected_m3ua}",
            f"LINK connected                 | {link_count}/{expected_link:<5}     | Expected: {expected_link}/{expected_link}"
        ]

    except subprocess.CalledProcessError as e:
        return [f"SS7 Connection Status          | Error         | {str(e)}"]


# -----------------------------
# ✅ Check smpp cdr
# -----------------------------

def check_smpp_cdr(config):
    try:
        # ==================================================
        # 🔹 DB ACTIVITY CHECK (traffic_monitor.smpp)
        # ==================================================
        traffic_conf = config.get("traffic_monitor", {}).get("smpp", {})

        db_host = traffic_conf.get("db_host")
        db_port = traffic_conf.get("db_port")
        db_name = traffic_conf.get("db_name")
        db_user = traffic_conf.get("db_user")
        db_password = traffic_conf.get("db_password")
        table = traffic_conf.get("table")
        timestamp_column = traffic_conf.get("timestamp_column")
        inactivity_threshold = traffic_conf.get("inactivity_threshold_seconds", 120)

        if all([db_host, db_port, db_name, db_user, db_password, table, timestamp_column]):
            conn = psycopg2.connect(
                host=db_host,
                port=db_port,
                dbname=db_name,
                user=db_user,
                password=db_password
            )
            cursor = conn.cursor()
            cursor.execute(f"SELECT MAX({timestamp_column}) FROM {table};")
            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if not result or not result[0]:
                return ["SMPP CDR Monitor               | Not Active     | No DB Records"]

            last_record_time = result[0]
            now_db = datetime.datetime.now(last_record_time.tzinfo)
            time_diff = (now_db - last_record_time).total_seconds()

            if time_diff > inactivity_threshold:
                return [
                    f"SMPP CDR Monitor               | Not Active     | No traffic in last {inactivity_threshold}s"
                ]

        # ==================================================
        # 🔹 EXISTING FILE LOGIC (UNCHANGED)
        # ==================================================

        smpp_conf = config.get("smpp_cdr_monitor", {})
        dir_path = smpp_conf.get("watch_dir", "/data/armour/smpp/SMPP_CDR/Folder1")
        expected = smpp_conf.get("expected_last_files", 3)

        now = datetime.datetime.now()
        grace_period = datetime.timedelta(minutes=18)

        command = f"ls -ltr --time-style=full-iso {dir_path}"
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, executable="/bin/bash"
        )
        lines = result.stdout.strip().split('\n')

        csv_files = [
            line for line in lines
            if "ARMOURSMPP" in line and line.strip().endswith(".csv")
        ]

        if len(csv_files) >= expected + 2:
            last_files = csv_files[-(expected + 2):-2]
        else:
            last_files = csv_files[-expected:]

        recent_count = 0
        writing_count = 0

        for line in last_files:
            parts = line.split()
            if len(parts) < 8:
                continue

            date_str = parts[5]
            time_str = parts[6]
            filename = parts[-1]

            try:
                file_time = datetime.datetime.strptime(
                    f"{date_str} {time_str.split('.')[0]}",
                    "%Y-%m-%d %H:%M:%S"
                )
                full_path = os.path.join(dir_path, filename)

                if (now - file_time) <= grace_period:
                    recent_count += 1

                if os.path.getsize(full_path) > 0:
                    writing_count += 1

            except Exception:
                continue

        time_status = (
            f"SMPP CDR last {expected} file created   | "
            f"{recent_count}/{expected:<3}       | Expected: {expected}/{expected}"
        )

        write_status = (
            f"SMPP CDR last {expected} file writing   | "
            f"{writing_count}/{expected:<3}       | Expected: {expected}/{expected}"
        )

        return [time_status, write_status]

    except Exception as e:
        return [f"SMPP CDR Monitor               | Error         | {str(e)}"]


# -----------------------------
# ✅ Check ss7 cdr
# -----------------------------


def check_ss7_cdr(config):
    try:
        traffic_conf = config.get("traffic_monitor", {}).get("ss7", {})

        db_host = traffic_conf.get("db_host")
        db_port = traffic_conf.get("db_port")
        db_name = traffic_conf.get("db_name")
        db_user = traffic_conf.get("db_user")
        db_password = traffic_conf.get("db_password")
        table = traffic_conf.get("table")
        timestamp_column = traffic_conf.get("timestamp_column")
        inactivity_threshold = traffic_conf.get("inactivity_threshold_seconds", 120)

        if all([db_host, db_port, db_name, db_user, db_password, table, timestamp_column]):

            conn = psycopg2.connect(
                host=db_host,
                port=db_port,
                dbname=db_name,
                user=db_user,
                password=db_password
            )
            cursor = conn.cursor()

            query = f"SELECT MAX({timestamp_column}) FROM {table};"
            cursor.execute(query)
            result = cursor.fetchone()

            cursor.close()
            conn.close()

            if not result or not result[0]:
                return ["SS7 CDR Monitor                | Not Active     | No DB Records"]

            last_record_time = result[0]
            now = datetime.datetime.now(last_record_time.tzinfo)
            time_diff = (now - last_record_time).total_seconds()

            if time_diff > inactivity_threshold:
                return [
                    f"SS7 CDR Monitor                | Not Active     | No traffic in last {inactivity_threshold}s"
                ]

        # ===============================
        # 🔹 EXISTING FILE LOGIC (UNCHANGED)
        # ===============================

        ss7_conf = config.get("ss7_monitor", {})
        dir_path = ss7_conf.get("watch_dir", "/data/armour/rule_engine/SS7_CDR/CDR_RawFiles/")
        expected = ss7_conf.get("expected_last_files", 3)

        now = datetime.datetime.now()
        grace_period = datetime.timedelta(minutes=18)

        command = f"ls -ltr --time-style=full-iso {dir_path}"
        result = subprocess.run(command, shell=True, capture_output=True, text=True, executable="/bin/bash")
        lines = result.stdout.strip().split('\n')

        csv_files = [line for line in lines if "ARMOURSS7" in line and line.strip().endswith(".csv")]

        if len(csv_files) >= expected + 2:
            last_files = csv_files[-(expected + 2):-2]
        else:
            last_files = csv_files[-expected:]

        recent_count = 0
        writing_count = 0

        for line in last_files:
            parts = line.split()
            if len(parts) < 8:
                continue

            date_str = parts[5]
            time_str = parts[6]
            filename = parts[-1]

            try:
                file_time = datetime.datetime.strptime(
                    f"{date_str} {time_str.split('.')[0]}",
                    "%Y-%m-%d %H:%M:%S"
                )
                full_path = os.path.join(dir_path, filename)

                if (now - file_time) <= grace_period:
                    recent_count += 1

                if os.path.getsize(full_path) > 0:
                    writing_count += 1
            except Exception:
                continue

        time_status = f"SS7 CDR last {expected} file created    | {recent_count}/{expected:<3}       | Expected: {expected}/{expected}"
        write_status = f"SS7 CDR last {expected} file writing    | {writing_count}/{expected:<3}       | Expected: {expected}/{expected}"

        return [time_status, write_status]

    except Exception as e:
        return [f"SS7 CDR Monitor                | Error         | {str(e)}"]


# -----------------------------
# ✅ Check smpp logs writing
# -----------------------------


def smpp_logs_writing(config):
    try:
        # ==================================================
        # 🔹 DB ACTIVITY CHECK (traffic_monitor.smpp)
        # ==================================================
        traffic_conf = config.get("traffic_monitor", {}).get("smpp", {})

        db_host = traffic_conf.get("db_host")
        db_port = traffic_conf.get("db_port")
        db_name = traffic_conf.get("db_name")
        db_user = traffic_conf.get("db_user")
        db_password = traffic_conf.get("db_password")
        table = traffic_conf.get("table")
        timestamp_column = traffic_conf.get("timestamp_column")
        inactivity_threshold = traffic_conf.get("inactivity_threshold_seconds", 120)

        if all([db_host, db_port, db_name, db_user, db_password, table, timestamp_column]):
            conn = psycopg2.connect(
                host=db_host,
                port=db_port,
                dbname=db_name,
                user=db_user,
                password=db_password
            )
            cursor = conn.cursor()
            cursor.execute(f"SELECT MAX({timestamp_column}) FROM {table};")
            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if not result or not result[0]:
                return ["SMPP Log Monitor               | Not Active     | No DB Records"]

            last_record_time = result[0]
            now_db = datetime.datetime.now(last_record_time.tzinfo)
            time_diff = (now_db - last_record_time).total_seconds()

            if time_diff > inactivity_threshold:
                return [
                    f"SMPP Log Monitor               | Not Active     | No traffic in last {inactivity_threshold}s"
                ]

        # ==================================================
        # 🔹 EXISTING LOG LOGIC (UNCHANGED)
        # ==================================================

        smpp_conf = config.get("smpp_monitor", {})
        instances = smpp_conf.get("instances", {})
        now = datetime.datetime.now()

        output_lines = []

        for instance_label, instance_info in instances.items():
            if isinstance(instance_info, dict):
                path = instance_info.get("path")
                should_write = instance_info.get("expected_writing", True)
            else:
                path = instance_info
                should_write = True

            try:
                mod_time = datetime.datetime.fromtimestamp(os.path.getmtime(path))
                time_diff = (now - mod_time).total_seconds() / 60
                writing_status = "writing" if time_diff <= 5 else "not writing"
            except Exception:
                writing_status = "not writing"

            expected_status = "writing" if should_write else "not writing"
            output = f"{instance_label:<25} | {writing_status:<12} | {expected_status}"
            output_lines.append(output)

        return output_lines

    except Exception as e:
        return [f"SMPP Log Monitor               | Error         | {str(e)}"]



# -----------------------------
# ✅ Check ss7 Trace writing
# -----------------------------

def ss7_logs_writing(config):
    try:
        # ==================================================
        # 🔹 DB ACTIVITY CHECK (using traffic_monitor.ss7)
        # ==================================================
        traffic_conf = config.get("traffic_monitor", {}).get("ss7", {})

        db_host = traffic_conf.get("db_host")
        db_port = traffic_conf.get("db_port")
        db_name = traffic_conf.get("db_name")
        db_user = traffic_conf.get("db_user")
        db_password = traffic_conf.get("db_password")
        table = traffic_conf.get("table")
        timestamp_column = traffic_conf.get("timestamp_column")
        inactivity_threshold = traffic_conf.get("inactivity_threshold_seconds", 120)

        if all([db_host, db_port, db_name, db_user, db_password, table, timestamp_column]):
            conn = psycopg2.connect(
                host=db_host,
                port=db_port,
                dbname=db_name,
                user=db_user,
                password=db_password
            )
            cursor = conn.cursor()
            cursor.execute(f"SELECT MAX({timestamp_column}) FROM {table};")
            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if not result or not result[0]:
                return ["SS7 Trace Monitor              | Not Active     | No DB Records"]

            last_record_time = result[0]
            now_db = datetime.datetime.now(last_record_time.tzinfo)
            time_diff = (now_db - last_record_time).total_seconds()

            if time_diff > inactivity_threshold:
                return [
                    f"SS7 Trace Monitor              | Not Active     | No traffic in last {inactivity_threshold}s"
                ]

        # ==================================================
        # 🔹 EXISTING LOGIC (UNCHANGED)
        # ==================================================

        trace_conf = config.get("trace_monitor", {})
        server_name = trace_conf.get("server_name", "App Server")
        trace_dir = trace_conf.get("trace_dir", "/data/armour/ss7/log")
        filename_prefix = trace_conf.get("filename_prefix", "armour_1001")
        trace_file_count = trace_conf.get("trace_file_count", 3)

        now = datetime.datetime.now()

        output_lines = []
        written_count = 0

        for i in range(1, trace_file_count + 1):
            filename = f"{filename_prefix}_{i}-Trace-{now.strftime('%Y-%m-%d')}.log"
            file_path = os.path.join(trace_dir, filename)

            if not os.path.exists(file_path):
                output_lines.append(f"{filename:<35} | Missing File        | Expected Today")
                continue

            try:
                mod_time = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
                time_diff = (now - mod_time).total_seconds() / 60

                if time_diff <= 5:
                    written_count += 1
            except Exception as e:
                output_lines.append(f"{filename:<35} | Error checking      | {str(e)}")

        summary = f"{'SS7 Trace Files Written':<35} | {written_count}/{trace_file_count:<15} | Expected: {trace_file_count}/{trace_file_count}"
        output_lines.append(summary)

        return output_lines

    except Exception as e:
        return [f"SS7 Trace Monitor              | Error         | {str(e)}"]

# -----------------------------
# ✅ Check smpp Error
# -----------------------------

def smpp_error_count(config):
    smpp_conf = config.get("smpp_monitor", {})
    instances = smpp_conf.get("instances", {})

    match_patterns = smpp_conf.get("match_patterns", ["ERROR"])
    ignore_patterns = smpp_conf.get("ignore_patterns", [])

    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    output_lines = []

    for instance_name, inst in instances.items():
        log_path = inst.get("path")
        error_count = 0

        try:
            if not os.path.exists(log_path):
                raise FileNotFoundError

            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:

                    # Only count today's entries
                    if today_str not in line:
                        continue

                    # Must match configured trigger patterns
                    if not any(p in line for p in match_patterns):
                        continue

                    # Skip ignored patterns
                    if any(p in line for p in ignore_patterns):
                        continue

                    error_count += 1

        except Exception:
            error_count = 0

        status = "Not Exceed: 50" if error_count < 50 else "! Exceeds Limit"

        label = f"SMPP {instance_name}  ERROR"
        output_line = f"{label:<30} | {error_count:<13} | {status}"
        output_lines.append(output_line)

    return output_lines


# -----------------------------
# ✅ Check ss7 Trace Error
# -----------------------------

def ss7_trace_count(config):
    try:
        # ==================================================
        # 🔹 DB ACTIVITY CHECK (traffic_monitor.ss7)
        # ==================================================
        traffic_conf = config.get("traffic_monitor", {}).get("ss7", {})

        db_host = traffic_conf.get("db_host")
        db_port = traffic_conf.get("db_port")
        db_name = traffic_conf.get("db_name")
        db_user = traffic_conf.get("db_user")
        db_password = traffic_conf.get("db_password")
        table = traffic_conf.get("table")
        timestamp_column = traffic_conf.get("timestamp_column")
        inactivity_threshold = traffic_conf.get("inactivity_threshold_seconds", 120)

        if all([db_host, db_port, db_name, db_user, db_password, table, timestamp_column]):
            conn = psycopg2.connect(
                host=db_host,
                port=db_port,
                dbname=db_name,
                user=db_user,
                password=db_password
            )
            cursor = conn.cursor()
            cursor.execute(f"SELECT MAX({timestamp_column}) FROM {table};")
            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if not result or not result[0]:
                return ["SS7 Trace Monitor              | Not Active     | No DB Records"]

            last_record_time = result[0]
            now_db = datetime.datetime.now(last_record_time.tzinfo)
            time_diff = (now_db - last_record_time).total_seconds()

            if time_diff > inactivity_threshold:
                return [
                    f"SS7 Trace Monitor              | Not Active     | No traffic in last {inactivity_threshold}s"
                ]

        # ==================================================
        # 🔹 EXISTING TRACE LOGIC (UNCHANGED)
        # ==================================================

        trace_conf = config.get("trace_monitor", {})
        trace_dir = trace_conf.get("trace_dir", "/data/armour/ss7/log")
        prefix = trace_conf.get("filename_prefix", "armour_1001")
        file_count = trace_conf.get("trace_file_count", 10)
        latency_threshold = trace_conf.get("latency_threshold", 2000)

        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        output_lines = []

        for i in range(1, file_count + 1):
            filename = f"{prefix}_{i}-Trace-{today_str}.log"
            full_path = os.path.join(trace_dir, filename)

            if not os.path.isfile(full_path):
                output_lines.append(
                    f"SS7 Trace File {i:<2}              | File Missing | Skipped"
                )
                continue

            try:
                cmd = f'grep -c "Latency:-1" "{full_path}"'
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True
                )
                latency_count = (
                    int(result.stdout.strip())
                    if result.returncode == 0 and result.stdout.strip()
                    else 0
                )
            except Exception as e:
                output_lines.append(
                    f"SS7 Trace File {i:<2}              | Error        | {str(e)}"
                )
                continue

            status = (
                f"Not Exceed: {latency_threshold}"
                if latency_count < latency_threshold
                else "! Exceeds Limit"
            )

            line = (
                f"SS7 Trace File {i:<2}              | "
                f"{latency_count:<5}       | {status}"
            )

            output_lines.append(line)

        return output_lines

    except Exception as e:
        return [f"SS7 Trace Monitor              | Error         | {str(e)}"]


# -----------------------------
# ✅ Check HAProxy and Keepalived
# -----------------------------

def check_ha_services_status(config):
    ha_conf = config.get("ha_monitor", {})
    services = ha_conf.get("services", [])
    output_lines = []

    for service in services:
        try:
            cmd = f"systemctl status {service} --no-pager | grep 'Active:'"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

            if "active (running)" in result.stdout:
                status = "running"
            else:
                status = "not running"

        except Exception:
            status = "not running"

        output_lines.append(f"{service.capitalize():<30} | {status:<13} | running")

    return output_lines



# -----------------------------
# ✅ Check tomcat kafka zookeeper
# -----------------------------

def check_generic_service_status(config):
    service_conf = config.get("service_monitor", {})
    services = service_conf.get("services", [])
    output_lines = []

    for service in services:
        name = service.get("name", "Unknown")
        check_command = service.get("check_command")

        if not check_command:
            output_lines.append(f"{name:<30} | skipped         | no command")
            continue

        try:
            result = subprocess.run(check_command, shell=True, capture_output=True, text=True)
            status = "running" if result.returncode == 0 or result.stdout.strip() else "not running"
        except Exception:
            status = "not running"

        output_lines.append(f"{name:<30} | {status:<13} | running")

    return output_lines


# -----------------------------
# ✅ Check Armour MT responce
# -----------------------------


def armour_mt_response_count(config):
    try:
        # ==================================================
        # 🔹 DB ACTIVITY CHECK (traffic_monitor.ss7)
        # ==================================================
        traffic_conf = config.get("traffic_monitor", {}).get("ss7", {})

        db_host = traffic_conf.get("db_host")
        db_port = traffic_conf.get("db_port")
        db_name = traffic_conf.get("db_name")
        db_user = traffic_conf.get("db_user")
        db_password = traffic_conf.get("db_password")
        table = traffic_conf.get("table")
        timestamp_column = traffic_conf.get("timestamp_column")
        inactivity_threshold = traffic_conf.get("inactivity_threshold_seconds", 120)

        if all([db_host, db_port, db_name, db_user, db_password, table, timestamp_column]):
            conn = psycopg2.connect(
                host=db_host,
                port=db_port,
                dbname=db_name,
                user=db_user,
                password=db_password
            )
            cursor = conn.cursor()
            cursor.execute(f"SELECT MAX({timestamp_column}) FROM {table};")
            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if not result or not result[0]:
                return ["Armour MT Response           | Not Active     | No DB Records"]

            last_record_time = result[0]
            now_db = datetime.datetime.now(last_record_time.tzinfo)
            time_diff = (now_db - last_record_time).total_seconds()

            if time_diff > inactivity_threshold:
                return [
                    f"Armour MT Response           | Not Active     | No traffic in last {inactivity_threshold}s"
                ]

        # ==================================================
        # 🔹 EXISTING LOGIC (UNCHANGED)
        # ==================================================

        log_date = datetime.datetime.now().strftime("%Y-%m-%d")
        trace_config = config.get("trace_responces", {})
        log_dir = trace_config.get("log_dir", "/data/armour/ss7/log")
        state_dir = trace_config.get("state_file_dir", "/tmp/monitor_state")
        filename_prefix = trace_config.get("filename_prefix", "armour_1001")
        os.makedirs(state_dir, exist_ok=True)

        state_file = os.path.join(state_dir, "armour_mt_prev_counts.json")
        patterns = trace_config.get("patterns", [])

        # Load previous state
        try:
            with open(state_file, "r") as f:
                prev_counts = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            prev_counts = {}

        results = {}
        output_lines = []

        for item in patterns:
            label = item.get("label", "Unknown")
            grep_pattern = item.get("grep")

            if not grep_pattern:
                output_lines.append(
                    f"{label:<35} | Current: N/A     | Previous: N/A (Missing grep)"
                )
                continue

            try:
                grep_cmd = (
                    f"grep -c '{grep_pattern}' "
                    f"{log_dir}/{filename_prefix}_*-Trace-{log_date}.log"
                )
                result = subprocess.check_output(grep_cmd, shell=True, text=True)
                current_count = sum(
                    int(line.split(":")[1])
                    for line in result.strip().split("\n")
                    if ":" in line
                )
            except subprocess.CalledProcessError:
                current_count = 0

            previous_count = prev_counts.get(label, 0)

            if current_count > previous_count:
                status = "Increased"
            elif current_count < previous_count:
                status = "Decreased"
            else:
                status = "Same"

            output_lines.append(
                f"{label:<35} | Current: {str(current_count):<8} | Previous: {str(previous_count)} ({status})"
            )

            results[label] = current_count

        # Save current state
        with open(state_file, "w") as f:
            json.dump(results, f, indent=2)

        return output_lines

    except Exception as e:
        return [f"Armour MT Response           | Error         | {str(e)}"]



# -----------------------------
# ✅ Function to Log Results to File
# -----------------------------

def log_results():
    config = config_loader.get_config()

    # Prepare log file path
    log_config = config.get("log", {})
    LOG_DIR = log_config.get("directory", "/tmp/monitor_logs")
    FILENAME_PREFIX = log_config.get("filename_prefix", "report")
    TIMESTAMP_FORMAT = log_config.get("timestamp_format", "%Y-%m-%d_%H-%M-%S")
    EXTENSION = log_config.get("extension", ".log")
    timestamp = datetime.datetime.now().strftime(TIMESTAMP_FORMAT)
    LOG_FILE = os.path.join(LOG_DIR, f"{FILENAME_PREFIX}_{timestamp}{EXTENSION}")
    os.makedirs(LOG_DIR, exist_ok=True)
    current_date = datetime.datetime.now().strftime("%Y-%m-%d")

    with open(LOG_FILE, "w") as log_file:
        log_file.write(f"--- Monitoring Report ({datetime.datetime.now().strftime('%Y-%m-%d')}) ---\n")
        log_file.write(f"Date: {datetime.datetime.now()}\n")
        log_file.write("-" * 40 + "\n")

        header = generate_table_header()
        jar_status = check_jar_status(config)
        cfg_status = check_cfg_status(config, current_date)
        storage_status = check_storage_status(config)
        memory_status = check_memory_usage(config)
        cpu_idle_status = check_cpu_idle(config)
        ss7_status_lines = check_ss7_connections(config)
        smpp_cdr_lines = check_smpp_cdr(config)
        ss7_cdr_lines = check_ss7_cdr(config)
        smpp_log = smpp_logs_writing(config)
        ss7_log = ss7_logs_writing(config)
        smpp_error = smpp_error_count(config)
        ss7_trace = ss7_trace_count(config)
        haproxy_keepalived = check_ha_services_status(config)
        tomcat_kafka_zookeeper = check_generic_service_status(config)
        mo_mt = armour_mt_response_count(config)

        all_lines = (
            header +
            [safe_format_line(row) for row in jar_status] +
            [safe_format_line(row) for row in cfg_status] +
            [safe_format_line(row) for row in storage_status] +
            [safe_format_line(row) for row in memory_status] +
            [safe_format_line(row) for row in cpu_idle_status] +
            [safe_format_line(row) for row in ss7_status_lines] +
            [safe_format_line(row) for row in smpp_cdr_lines] +
            [safe_format_line(row) for row in ss7_cdr_lines] +
            [safe_format_line(row) for row in smpp_log] +
            [safe_format_line(row) for row in ss7_log] +
            [safe_format_line(row) for row in smpp_error] +
            [safe_format_line(row) for row in ss7_trace] +
            [safe_format_line(row) for row in haproxy_keepalived] +
            [safe_format_line(row) for row in tomcat_kafka_zookeeper] +
            [safe_format_line(row) for row in mo_mt]
        )


        for line in all_lines:
            log_file.write(f"{line}\n")


# -----------------------------
# ✅ Run the script and log the results
# -----------------------------
if __name__ == "__main__":
    log_results()
    print(f"Monitoring report written to: {LOG_FILE}")
