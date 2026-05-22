"""One-shot launcher: starts train.py as a detached subprocess and prints the PID."""
import subprocess, sys, os

log_path = os.path.join(os.path.dirname(__file__), "training_v2.log")
# Truncate log so we start clean
open(log_path, "w").close()

proc = subprocess.Popen(
    [
        sys.executable, "train.py",
        "--timesteps", "2000000",
        "--checkpoint-freq", "100000",
        "--data", "data/btc_daily.csv",
    ],
    cwd=os.path.dirname(__file__),
    stdout=open(log_path, "w"),
    stderr=subprocess.STDOUT,
    start_new_session=True,   # detach from parent so osascript exit doesn't kill it
)
print(f"Training PID: {proc.pid}")
