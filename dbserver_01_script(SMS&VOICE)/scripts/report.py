import sys
import os
import datetime
import subprocess
import shutil
import psutil
import json

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
        storage_status = check_storage_status(config)
        memory_status = check_memory_usage(config)
        cpu_idle_status = check_cpu_idle(config)
        haproxy_keepalived = check_ha_services_status(config)
        tomcat_kafka_zookeeper = check_generic_service_status(config)

        all_lines = (
            header +
            [safe_format_line(row) for row in storage_status] +
            [safe_format_line(row) for row in memory_status] +
            [safe_format_line(row) for row in cpu_idle_status] +
            [safe_format_line(row) for row in tomcat_kafka_zookeeper]
        )


        for line in all_lines:
            log_file.write(f"{line}\n")


# -----------------------------
# ✅ Run the script and log the results
# -----------------------------
if __name__ == "__main__":
    log_results()
    print(f"Monitoring report written to: {LOG_FILE}")
