# Run tests
import subprocess, sys
proc = subprocess.run(
    [r"C:\Users\Derek Yung\Market-Analysis\.venv\Scripts\pytest", "-q"],
    cwd=r"C:\Users\Derek Yung\Market-Analysis",
    capture_output=True, text=True, timeout=60
)
print("STDOUT:", proc.stdout)
print("STDERR:", proc.stderr[-500:] if proc.stderr else "")
print("EXIT:", proc.returncode)
