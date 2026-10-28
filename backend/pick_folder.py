import sys
import os
import subprocess
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

CURRENT_DIR = Path(__file__).parent.resolve()
POWERSHELL_SCRIPT = CURRENT_DIR / "pick_folder.ps1"

def pick_folder():
    # Use the native Windows folder dialog. The web UI cannot read an absolute
    # folder path from an <input type="file">, which the local indexer needs.
    if sys.platform == "win32" and POWERSHELL_SCRIPT.exists():
        try:
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(POWERSHELL_SCRIPT)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
            out = res.stdout.strip()
            if out == "__CANCELLED__":
                return
            if res.returncode == 0 and out and os.path.isdir(out):
                print(os.path.abspath(out))
                return
            if res.stderr.strip():
                print(f"[!] Windows folder picker failed: {res.stderr.strip()}", file=sys.stderr)
        except Exception as exc:
            print(f"[!] Windows folder picker failed: {exc}", file=sys.stderr)

    # Fallback: Tkinter askdirectory with topmost focus
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.title("Select Photo Folder")
        root.geometry("1x1+100+100")
        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
        root.update()
        folder = filedialog.askdirectory(parent=root, title="Select Photo Directory to Index", mustexist=True)
        root.destroy()
        if folder and os.path.isdir(folder):
            print(os.path.abspath(folder))
            return
    except Exception as exc:
        print(f"[!] Fallback folder picker failed: {exc}", file=sys.stderr)

if __name__ == "__main__":
    pick_folder()
