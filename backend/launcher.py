"""Launch the production UI and API together; use --dev for Vite hot reload."""
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"


def _ensure_node_path():
    if os.name == "nt":
        paths_to_add = [
            r"C:\Program Files\nodejs",
            os.path.expandvars(r"%APPDATA%\npm"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311"),
        ]
        curr_path = os.environ.get("PATH", "")
        for p in paths_to_add:
            if os.path.isdir(p) and p.lower() not in curr_path.lower():
                os.environ["PATH"] = p + os.path.pathsep + os.environ["PATH"]


def wait_for_port(host, port, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def port_in_use(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def free_port(port: int) -> bool:
    """Attempts to terminate any stale process currently listening on the given port."""
    if not port_in_use(port):
        return True
    if os.name == "nt":
        try:
            cmd = f'powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique"'
            res = subprocess.run(cmd, capture_output=True, text=True, shell=True)
            pids = set(res.stdout.strip().split())

            # Fallback: check netstat if PowerShell returned empty
            if not pids or not any(p.isdigit() for p in pids):
                netstat_res = subprocess.run(f"netstat -ano | findstr :{port}", capture_output=True, text=True, shell=True)
                for line in netstat_res.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 5 and parts[-1].isdigit():
                        pids.add(parts[-1])

            current_pid = os.getpid()
            freed = False
            for pid_str in pids:
                if pid_str.isdigit():
                    pid = int(pid_str)
                    if pid != current_pid and pid > 0:
                        print(f"[*] Port {port} is occupied by PID {pid}. Terminating stale process...", flush=True)
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        subprocess.run(
                            f'powershell -NoProfile -Command "Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"',
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            shell=True,
                        )
                        freed = True

            # Poll for port to be released (Windows sockets can take 1-3s to unbind)
            deadline = time.monotonic() + 3.5
            while time.monotonic() < deadline:
                time.sleep(0.25)
                if not port_in_use(port):
                    return True

            return not port_in_use(port)
        except Exception as e:
            print(f"[!] Could not free port {port}: {e}")
            return False
    return False


def build_if_needed():
    index = FRONTEND_DIR / "dist" / "index.html"
    sources = [FRONTEND_DIR / name for name in ("index.html", "package.json", "package-lock.json", "vite.config.js", "tailwind.config.js", "postcss.config.js")]
    sources.extend(FRONTEND_DIR.glob("src/**/*"))
    if index.exists() and all(not file.is_file() or file.stat().st_mtime <= index.stat().st_mtime for file in sources):
        return
    print("[*] Building the frontend once for the updated source...", flush=True)
    _ensure_node_path()
    subprocess.run(
        ["npm.cmd" if os.name == "nt" else "npm", "run", "build"],
        cwd=FRONTEND_DIR,
        check=True,
        shell=(os.name == "nt"),
    )


def main():
    dev = "--dev" in sys.argv
    ports = (8000, 5173) if dev else (8000,)
    for port in ports:
        if port_in_use(port):
            if not free_port(port):
                raise SystemExit(f"Port {port} is already in use. Close the existing app before starting another instance.")
    frontend = None
    if dev:
        _ensure_node_path()
        frontend = subprocess.Popen(
            ["npm.cmd" if os.name == "nt" else "npm", "run", "dev", "--", "--host", "127.0.0.1", "--strictPort"],
            cwd=FRONTEND_DIR, stdin=subprocess.DEVNULL, shell=(os.name == "nt"),
        )
    else:
        build_if_needed()
    port = 5173 if dev else 8000
    print(f"[*] Gallery: http://127.0.0.1:{port}  (Ctrl+C to stop)", flush=True)
    if "--no-browser" not in sys.argv:
        def open_when_ready():
            if wait_for_port("127.0.0.1", 8000) and (port == 8000 or wait_for_port("127.0.0.1", port)):
                webbrowser.open(f"http://127.0.0.1:{port}")
        threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        os.chdir(BACKEND_DIR)
        sys.path.insert(0, str(BACKEND_DIR))
        uvicorn.run("main:app", host="127.0.0.1", port=8000, log_level="info")
    except KeyboardInterrupt:
        print("\n[*] Shutting down GemmaGallery...")
    finally:
        if frontend and frontend.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(frontend.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                frontend.terminate()


if __name__ == "__main__":
    main()
