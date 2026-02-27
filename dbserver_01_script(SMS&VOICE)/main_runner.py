import sys
import os
import time
import subprocess
import signal
import logging
from logging.handlers import RotatingFileHandler
import ctypes
import ctypes.util

# ==========================================================
# CONFIGURATION
# ==========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
LOG_DIR = os.path.join(BASE_DIR, "logs")
PID_FILE = os.path.join(BASE_DIR, "main_runner.pid")

LOG_FILE = os.path.join(LOG_DIR, "main_runner.log")
LOG_LEVEL = logging.INFO

MAX_LOG_SIZE = 50 * 1024 * 1024  # 50MB
BACKUP_COUNT = 5
CHECK_INTERVAL = 30  # seconds

monitor_scripts = [
    "monitor_cpu",
    "monitor_services",
    "monitor_storage",
]

# ==========================================================
# LOGGING SETUP
# ==========================================================

os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("MainRunner")
logger.setLevel(LOG_LEVEL)

handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=MAX_LOG_SIZE,
    backupCount=BACKUP_COUNT
)

formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

# ==========================================================
# PID CONTROL
# ==========================================================

def check_existing_instance():
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            old_pid = int(f.read().strip())

        if os.path.exists(f"/proc/{old_pid}"):
            print(f"Main runner already running with PID {old_pid}")
            sys.exit(1)
        else:
            logger.warning("Stale PID file found. Cleaning.")
            os.remove(PID_FILE)

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

def cleanup_pid():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

# ==========================================================
# FORCE CHILDREN TO DIE IF PARENT DIES (kill -9 safe)
# ==========================================================

def set_pdeathsig():
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    PR_SET_PDEATHSIG = 1
    result = libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
    if result != 0:
        raise OSError("Failed to set PR_SET_PDEATHSIG")

# ==========================================================
# CLEAN OLD ORPHAN MONITORS
# ==========================================================

def kill_existing_monitors():
    logger.info("Checking for existing monitor processes...")
    for script in monitor_scripts:
        script_path = os.path.join(SCRIPTS_DIR, f"{script}.py")
        try:
            pids = subprocess.check_output(
                ["pgrep", "-f", script_path]
            ).decode().strip().split("\n")

            for pid in pids:
                if pid.strip() and int(pid) != os.getpid():
                    os.kill(int(pid), signal.SIGKILL)
                    logger.warning(f"Killed orphan monitor {script} PID {pid}")

        except subprocess.CalledProcessError:
            continue

# ==========================================================
# PROCESS MANAGEMENT
# ==========================================================

running_processes = {}

def start_script(script_name):
    script_path = os.path.join(SCRIPTS_DIR, f"{script_name}.py")

    if not os.path.exists(script_path):
        logger.error(f"Script not found: {script_path}")
        return

    try:
        def preexec():
            os.setsid()
            set_pdeathsig()

        process = subprocess.Popen(
            ["python3", script_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=preexec
        )

        running_processes[script_name] = process
        logger.info(f"Started {script_name} (PID {process.pid})")

    except Exception as e:
        logger.error(f"Failed to start {script_name}: {e}")

def stop_all():
    logger.info("Stopping all monitor scripts...")
    for name, proc in running_processes.items():
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            logger.info(f"Killed {name} (PID {proc.pid})")
        except Exception as e:
            logger.error(f"Error killing {name}: {e}")

    cleanup_pid()

def signal_handler(sig, frame):
    logger.info(f"Received signal {sig}. Shutting down.")
    stop_all()
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ==========================================================
# MAIN LOOP
# ==========================================================

def main():
    check_existing_instance()
    kill_existing_monitors()

    logger.info("Main runner started.")

    for script in monitor_scripts:
        start_script(script)

    try:
        while True:
            for script_name, process in list(running_processes.items()):
                if process.poll() is not None:
                    logger.warning(f"{script_name} exited. Restarting...")
                    start_script(script_name)

            time.sleep(CHECK_INTERVAL)

    except Exception as e:
        logger.error(f"Unexpected failure: {e}")
        stop_all()

if __name__ == "__main__":
    main()

