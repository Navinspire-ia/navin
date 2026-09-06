"""Report which pseudo-console backend pywinpty picks, and from where."""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("frozen:", getattr(sys, "frozen", False))
print("meipass:", getattr(sys, "_MEIPASS", None))

import winpty  # noqa: E402

print("winpty file:", winpty.__file__)
print("winpty dir :", sorted(os.listdir(os.path.dirname(winpty.__file__))))
print("version    :", getattr(winpty, "__version__", "?"))
print("exports    :", [n for n in dir(winpty) if not n.startswith("_")])

try:
    from winpty import Backend

    print("Backend enum:", list(Backend))
except Exception as exc:
    print("no Backend enum:", exc)

proc = winpty.PtyProcess.spawn(["cmd.exe"], dimensions=(24, 80))
print("spawned pid:", proc.pid)
inner = getattr(proc, "pty", None)
print("pty object :", type(inner).__name__ if inner is not None else None)
for name in ("backend", "_backend", "conpty", "_conpty"):
    if hasattr(proc, name):
        print(f"proc.{name} =", getattr(proc, name))
    if inner is not None and hasattr(inner, name):
        print(f"pty.{name} =", getattr(inner, name))
proc.terminate(force=True)
