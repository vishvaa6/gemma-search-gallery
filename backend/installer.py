"""
GemmaGallery Comprehensive Installer & Dependency Verifier.
Provides automated setup, total progress bar, animated spinners,
and complete health-check verification across backend, GPU, frontend, and AI models.
"""

import os
import sys
import time
import shutil
import subprocess
import threading
import importlib.metadata
import json
import re
from collections import deque
from pathlib import Path
from typing import Optional, Callable, List, Tuple

# Keep redirected logs readable as well as interactive Windows terminals.
if sys.platform == "win32":
    try:
        if sys.stdout.isatty():
            os.system("")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Terminal Color Palette
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[1;36m"
BLUE = "\033[1;34m"
GREEN = "\033[1;32m"
YELLOW = "\033[1;33m"
RED = "\033[1;31m"
MAGENTA = "\033[1;35m"
WHITE = "\033[1;37m"

if not sys.stdout.isatty() or os.getenv("NO_COLOR") is not None:
    RESET = BOLD = DIM = CYAN = BLUE = GREEN = YELLOW = RED = MAGENTA = WHITE = ""

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
VENV_PYTHON = BACKEND_DIR / ".venv" / "Scripts" / "python.exe" if sys.platform == "win32" else BACKEND_DIR / ".venv" / "bin" / "python"
if not VENV_PYTHON.exists():
    VENV_PYTHON = Path(sys.executable)

TOTAL_STEPS = 8

class ProgressManager:
    """Show completed checks, with animation only on an interactive terminal."""
    def __init__(self, total_steps: int = TOTAL_STEPS, stream=None):
        self.stream = stream if stream is not None else sys.stdout
        self.interactive = bool(self.stream.isatty())
        self.total_steps = total_steps
        self.current_step = 0
        self.current_percent = 0.0
        self.current_title = ""
        self.status_detail = ""
        self.start_time = time.monotonic()
        self.step_start_time = time.monotonic()
        self.spinner_idx = 0
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._active = False
        self._last_log = 0.0
        self.warnings = []
        self.hardware_mode = "Not checked"

    def format_bar(self, percent: float, width: int = 26) -> str:
        clamped = max(0.0, min(100.0, percent))
        filled = int(round(width * (clamped / 100.0)))
        unfilled = max(0, width - filled)
        bar = "#" * filled + "-" * unfilled
        if not self.interactive:
            return f"[{bar}] {clamped:5.1f}%"
        return f"[{CYAN}{bar}{RESET}] {WHITE}{clamped:5.1f}%{RESET}"

    def _write(self, text):
        self.stream.write(text)
        self.stream.flush()

    def _line(self):
        return (f"[{self.current_step}/{self.total_steps}] "
                f"{self.format_bar(self.current_percent)} | "
                f"{self.current_title}: {self.status_detail[:100]}")

    def start_step(self, step_num: int, title: str, start_pct: float = 0):
        with self._lock:
            self.current_step = step_num
            self.current_title = title
            self.current_percent = (step_num - 1) / self.total_steps * 100
            self.status_detail = "Starting..."
            self.step_start_time = time.monotonic()
            self._active = True
            if not self.interactive:
                self._write(self._line() + "\n")

    def update_detail(self, detail: str, percent: Optional[float] = None):
        with self._lock:
            # A subprocess line is status, not a measured completion percentage.
            self.status_detail = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(detail)).replace("\r", " ").replace("\n", " ")
            now = time.monotonic()
            if not self.interactive and now - self._last_log >= 1:
                self._write(self._line() + "\n")
                self._last_log = now

    def finish_step(self, end_pct: float, success_msg: str):
        with self._lock:
            self.current_percent = self.current_step / self.total_steps * 100
            self._active = False
            elapsed = time.monotonic() - self.step_start_time
            prefix = "\r\033[K" if self.interactive else ""
            self._write(f"{prefix}[OK] {self._line()} ({elapsed:.1f}s)\n")
            if success_msg:
                self._write(f"      {success_msg}\n")

    def fail_step(self, error_msg: str):
        with self._lock:
            self._active = False
            elapsed = time.monotonic() - self.step_start_time
            prefix = "\r\033[K" if self.interactive else ""
            self._write(f"{prefix}[FAIL] {self._line()} ({elapsed:.1f}s)\n")
            if error_msg:
                self._write(f"      {error_msg}\n")

    def _render_loop(self):
        while self.is_running:
            with self._lock:
                if self.current_step > 0 and self._active:
                    frame = SPINNER_FRAMES[self.spinner_idx % len(SPINNER_FRAMES)]
                    self.spinner_idx += 1
                    total_elapsed = int(time.monotonic() - self.start_time)
                    mins, secs = divmod(total_elapsed, 60)
                    time_str = f"{mins:02d}:{secs:02d}"

                    bar_str = self.format_bar(self.current_percent)
                    line = (
                        f"\r\033[K  {CYAN}{frame}{RESET} [{self.current_step}/{self.total_steps}] "
                        f"{bar_str} | {BOLD}{self.current_title}{RESET}: {DIM}{self.status_detail[:35]}{RESET} [{time_str}]"
                    )
                    self._write(line)
            time.sleep(0.08)

    def start_animation(self):
        if not self.interactive:
            return
        self.is_running = True
        self._thread = threading.Thread(target=self._render_loop, daemon=True)
        self._thread.start()

    def stop_animation(self):
        self.is_running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        if self.interactive:
            self._write("\r\033[K")


def run_command(cmd: List[str], cwd: Optional[Path] = None, on_output: Optional[Callable[[str], None]] = None) -> Tuple[int, str]:
    """Runs a subprocess with Windows shell safety and captured output."""
    proc = None
    try:
        use_shell = sys.platform == "win32" and (
            cmd[0].lower().endswith(".cmd") or
            cmd[0].lower().endswith(".bat")
        )
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=use_shell
        )
        output_lines = deque(maxlen=200)
        if proc.stdout:
            for line in iter(proc.stdout.readline, ""):
                output_lines.append(line)
                clean_line = line.strip()
                if clean_line and on_output:
                    on_output(clean_line)
        proc.wait()
        return proc.returncode, "".join(output_lines)
    except OSError as exc:
        return 1, str(exc)
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            if proc.stdout:
                proc.stdout.close()


def print_banner():
    print()
    print(f"{CYAN}  ____                                ____       _ _                 {RESET}")
    print(f"{CYAN} / ___| ___ _ __ ___  _ __ ___   __ _ / ___| __ _| | | ___ _ __ _   _  {RESET}")
    print(f"{CYAN}| |  _ / _ \\ '_ ` _ \\| '_ ` _ \\ / _` | |  _ / _` | | |/ _ \\ '__| | | | {RESET}")
    print(f"{CYAN}| |_| |  __/ | | | | | | | | | | (_| | |_| | (_| | | |  __/ |  | |_| | {RESET}")
    print(f"{CYAN} \\____|\\___|_| |_| |_|_| |_| |_|\\__,_|\\____|\\__,_|_|_|\\___|_|   \\__, | {RESET}")
    print(f"{CYAN}                                                               |___/  {RESET}")
    print(f"  {WHITE}{BOLD}AI-Powered Multimodal Semantic Gallery · Setup & Verification{RESET}")
    print("  " + BLUE + "=" * 72 + RESET)
    print()


def detect_nvidia_gpu() -> Tuple[bool, str, float]:
    """Detects NVIDIA GPU presence, model name, and approximate VRAM in GB."""
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0 and res.stdout.strip():
            parts = res.stdout.strip().split("\n")[0].split(",")
            name = parts[0].strip()
            vram_mb = float(parts[1].strip()) if len(parts) > 1 else 0.0
            return True, name, round(vram_mb / 1024.0, 1)
    except Exception:
        pass
    return False, "None (CPU Mode)", 0.0


def fail_command(pm, message, output):
    """Retain actionable subprocess errors instead of hiding them behind a spinner."""
    tail = "\n".join(output.strip().splitlines()[-12:])
    pm.fail_step(message + ("\n" + tail if tail else ""))
    return False


def requirement_issues(requirements):
    try:
        from packaging.requirements import Requirement
    except ImportError:
        from pip._vendor.packaging.requirements import Requirement
    issues = []
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        if req.marker and not req.marker.evaluate():
            continue
        try:
            version = importlib.metadata.version(req.name)
        except importlib.metadata.PackageNotFoundError:
            issues.append(f"{req.name}: missing (requires {req.specifier or 'any version'})")
            continue
        if req.specifier and not req.specifier.contains(version, prereleases=True):
            issues.append(f"{req.name}: {version} does not satisfy {req.specifier}")
    return issues


def npm_binary():
    return shutil.which("npm.cmd") or shutil.which("npm") or "npm"


def frontend_packages_ready():
    code, output = run_command([npm_binary(), "ls", "--depth=0", "--json"], cwd=FRONTEND_DIR)
    return code == 0, output


def frontend_build_current():
    index = FRONTEND_DIR / "dist" / "index.html"
    if not index.is_file():
        return False
    sources = [FRONTEND_DIR / name for name in ("index.html", "package.json", "package-lock.json", "vite.config.js", "tailwind.config.js", "postcss.config.js")]
    sources.extend(FRONTEND_DIR.glob("src/**/*"))
    if any(p.is_file() and p.stat().st_mtime_ns > index.stat().st_mtime_ns for p in sources):
        return False
    # An index alone does not prove that its generated JS/CSS files exist.
    html = index.read_text(encoding="utf-8")
    for asset in re.findall(r'''(?:src|href)=["'](/?assets/[^"']+)["']''', html):
        if not (index.parent / asset.lstrip("/").split("?", 1)[0]).is_file():
            return False
    return True


def torch_status():
    script = """
import json, torch, torchvision
available = torch.cuda.is_available()
mode = 'CUDA ' + str(torch.version.cuda) if available else 'CPU (CUDA unavailable)'
if available:
    torch.zeros(1, device='cuda').cpu()
else:
    torch.zeros(1).sum().item()
print('TORCH_STATUS=' + json.dumps({'version': torch.__version__, 'mode': mode}))
"""
    code, output = run_command([str(VENV_PYTHON), "-u", "-c", script])
    if code == 0:
        for line in reversed(output.splitlines()):
            if line.startswith("TORCH_STATUS="):
                status = json.loads(line.removeprefix("TORCH_STATUS="))
                return True, f"PyTorch {status['version']}: {status['mode']}"
    return False, output


def step_1_prerequisites(pm: ProgressManager) -> bool:
    pm.start_step(1, "Verifying System & Python Architecture", 5.0)
    pm.update_detail("Checking Python 3.10+ and 64-bit platform...", 8.0)
    time.sleep(0.15)

    is_64bit = sys.maxsize > 2**32
    major, minor = sys.version_info.major, sys.version_info.minor

    if not is_64bit:
        pm.fail_step("64-bit Python is strictly required for PyTorch & LanceDB.")
        return False
    if major != 3 or minor < 10:
        pm.fail_step(f"Python 3.10+ is required (found {major}.{minor}).")
        return False

    pm.finish_step(12.0, f"Python {major}.{minor}.{sys.version_info.micro} (64-bit) verified.")
    return True


def step_2_nodejs(pm: ProgressManager) -> bool:
    pm.start_step(2, "Verifying Node.js, npm & System Tools", 15.0)
    pm.update_detail("Checking npm and node binaries...", 18.0)

    npm_bin = shutil.which("npm.cmd") or shutil.which("npm")
    node_bin = shutil.which("node.exe") or shutil.which("node")

    if not npm_bin or not node_bin:
        pm.fail_step("Node.js 18+ and npm are required. Install from: https://nodejs.org/")
        return False

    code, out = run_command([node_bin, "-v"])
    node_ver = out.strip()
    match = re.fullmatch(r"v?(\d+)\.\d+\.\d+(?:[-+].*)?", node_ver)
    node_major = int(match.group(1)) if match else 0
    if code != 0 or not match or not (node_major in (18, 20) or node_major >= 22):
        return fail_command(pm, "Node.js 18, 20, or 22+ is required and must run successfully.", out)

    code, out = run_command([npm_bin, "-v"])
    npm_ver = out.strip()
    if code != 0 or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].*)?", npm_ver):
        return fail_command(pm, "npm could not be verified.", out)

    # Check for optional high-performance video extraction tool: FFmpeg
    ffmpeg_bin = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    ffmpeg_note = " + FFmpeg active" if ffmpeg_bin else " (FFmpeg optional for video)"

    pm.finish_step(25.0, f"Node.js {node_ver} & npm {npm_ver}{ffmpeg_note}.")
    return True


def step_3_pip_upgrade(pm: ProgressManager, force: bool = False, check_only: bool = False) -> bool:
    pm.start_step(3, "Optimizing Virtual Environment & Pip", 28.0)
    pm.update_detail("Checking pip..." if check_only else "Preparing pip, setuptools, and wheel...", 32.0)
    code, out = run_command([str(VENV_PYTHON), "-m", "pip", "--version"])
    if code != 0:
        if check_only:
            return fail_command(pm, "pip is missing. Run install.bat to repair the environment.", out)
        code, out = run_command([str(VENV_PYTHON), "-m", "ensurepip", "--upgrade"])
        if code != 0:
            return fail_command(pm, "Could not bootstrap pip.", out)
    if check_only:
        code, out = run_command([str(VENV_PYTHON), "-m", "pip", "check"])
        if code != 0:
            return fail_command(pm, "Installed Python packages have dependency conflicts.", out)
        pm.finish_step(35.0, "pip is available; installed dependency constraints pass.")
        return True

    code, out = run_command([
        str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel", "--no-input"
    ], on_output=lambda line: pm.update_detail(line[:35]))

    if code != 0:
        return fail_command(pm, "Failed to prepare pip, setuptools, and wheel.", out)
    pm.finish_step(35.0, "Pip package manager ready.")
    return True


def step_4_pytorch_cuda(pm: ProgressManager, force: bool = False, check_only: bool = False) -> bool:
    pm.start_step(4, "PyTorch & Hardware Acceleration Engine", 38.0)
    has_nvidia, gpu_name, vram_gb = detect_nvidia_gpu()

    if has_nvidia:
        pm.update_detail(f"Detected NVIDIA GPU: {gpu_name} ({vram_gb} GB VRAM)...", 40.0)
    else:
        pm.update_detail("No NVIDIA GPU detected. Configuring PyTorch for CPU mode...", 40.0)

    ready, detail = torch_status()
    if ready and not force:
        pm.hardware_mode = detail
        pm.finish_step(52.0, detail)
        return True
    if check_only:
        return fail_command(pm, "PyTorch or torchvision failed its runtime check. Run install.bat to repair it.", detail)

    if has_nvidia:
        pm.update_detail("Installing PyTorch with CUDA 12.4 acceleration...", 45.0)
        cmd = [
            str(VENV_PYTHON), "-m", "pip", "install",
            "torch", "torchvision",
            "--index-url", "https://download.pytorch.org/whl/cu124"
        ]
    else:
        pm.update_detail("Installing PyTorch for CPU...", 45.0)
        cmd = [str(VENV_PYTHON), "-m", "pip", "install", "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cpu"]

    if force:
        cmd.append("--force-reinstall")
    cmd.append("--no-input")
    code, out = run_command(cmd, on_output=lambda line: pm.update_detail(f"Torch: {line}"))
    if code != 0:
        return fail_command(pm, "PyTorch installation failed.", out)

    ready, detail = torch_status()
    if not ready:
        return fail_command(pm, "Installed PyTorch failed its runtime check.", detail)
    pm.hardware_mode = detail
    pm.finish_step(52.0, detail)
    return True


def step_5_backend_requirements(pm: ProgressManager, force: bool = False, check_only: bool = False) -> bool:
    pm.start_step(5, "Installing Core Backend & AI Vision Packages", 55.0)
    pm.update_detail("Verifying requirements from backend/requirements.txt...", 58.0)

    req_file = BACKEND_DIR / "requirements.txt"
    if not req_file.exists():
        pm.fail_step("backend/requirements.txt not found!")
        return False

    issues = requirement_issues(req_file)
    if not force and not issues:
        pm.finish_step(68.0, "All backend requirement versions satisfy requirements.txt.")
        return True
    if check_only:
        return fail_command(pm, "Backend requirements are not satisfied. Run install.bat.", "\n".join(issues))

    cmd = [str(VENV_PYTHON), "-m", "pip", "install", "-r", str(req_file), "--no-input"]
    if force:
        cmd.append("--force-reinstall")
    code, out = run_command(cmd, on_output=lambda line: pm.update_detail(f"Pip: {line[:30]}"))
    if code != 0:
        return fail_command(pm, "Failed to install backend requirements.", out)
    issues = requirement_issues(req_file)
    if issues:
        return fail_command(pm, "Backend requirement verification failed after installation.", "\n".join(issues))

    pm.finish_step(68.0, "FastAPI, LanceDB, OpenCV Contrib, & Transformers ready.")
    return True


def step_6_frontend_packages(pm: ProgressManager, force: bool = False, check_only: bool = False) -> bool:
    pm.start_step(6, "Installing Frontend Packages (node_modules)", 70.0)
    pm.update_detail("Checking node_modules...", 73.0)

    ready, out = frontend_packages_ready()
    if ready and not force:
        pm.finish_step(80.0, "All declared frontend packages verified by npm.")
        return True
    if check_only:
        return fail_command(pm, "Frontend dependencies are missing or invalid. Run install.bat.", out)

    npm_bin = npm_binary()
    lock_file = FRONTEND_DIR / "package-lock.json"

    cmd = [npm_bin, "ci" if lock_file.exists() else "install", "--no-audit", "--no-fund"]
    code, out = run_command(cmd, cwd=FRONTEND_DIR, on_output=lambda line: pm.update_detail(f"npm: {line[:30]}"))

    if code != 0:
        return fail_command(pm, "Failed to install frontend packages. Check package.json and package-lock.json.", out)
    ready, out = frontend_packages_ready()
    if not ready:
        return fail_command(pm, "Frontend packages failed verification after installation.", out)

    pm.finish_step(80.0, "React, Vite, Tailwind CSS, & Lucide icons installed.")
    return True


def step_7_frontend_build(pm: ProgressManager, force: bool = False, check_only: bool = False) -> bool:
    pm.start_step(7, "Compiling Frontend Production Bundle", 82.0)
    pm.update_detail("Checking production build in dist/...", 85.0)

    dist_index = FRONTEND_DIR / "dist" / "index.html"
    if frontend_build_current() and not force:
        pm.finish_step(90.0, "Production bundle and generated assets match current sources.")
        return True
    if check_only:
        pm.fail_step("Production bundle is missing, incomplete, or stale. Run install.bat to rebuild it.")
        return False

    npm_bin = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
    code, out = run_command([npm_bin, "run", "build"], cwd=FRONTEND_DIR, on_output=lambda line: pm.update_detail(f"Vite: {line[:30]}"))

    if code != 0 or not dist_index.exists():
        return fail_command(pm, "Frontend Vite production build failed.", out)

    pm.finish_step(90.0, f"Production bundle compiled ({dist_index.name}).")
    return True


def step_8_model_and_verification(pm: ProgressManager, force: bool = False, check_only: bool = False, skip_model: bool = False) -> bool:
    pm.start_step(8, "Verifying AI Model & Running Diagnostics", 92.0)
    pm.update_detail("Checking EmbeddingGemma 2 AI model cache...", 94.0)

    # 1. Download or check model
    download_script = BACKEND_DIR / "download_model.py"
    if skip_model:
        pm.warnings.append("AI model cache verification was explicitly skipped; semantic search is not verified.")
    elif download_script.exists():
        code, out = run_command([str(VENV_PYTHON), "-u", str(download_script), "--check-only"])
        if (code != 0 or force) and not check_only:
            pm.update_detail("Downloading EmbeddingGemma 2 model weights...", 95.0)
            cmd = [str(VENV_PYTHON), "-u", str(download_script)]
            if force:
                cmd.append("--force")
            code, out = run_command(cmd, on_output=pm.update_detail)
        if code != 0:
            return fail_command(pm, "Model cache is incomplete or download failed. Run install.bat after configuring model access, or explicitly use --skip-model.", out)
    else:
        pm.fail_step("backend/download_model.py is missing.")
        return False

    # 2. Comprehensive health checks
    pm.update_detail("Running LanceDB vector database verification...", 97.0)
    diag_script = """
import tempfile, lancedb, pyarrow as pa, cv2
assert hasattr(cv2, 'FaceDetectorYN'), 'OpenCV FaceDetectorYN missing'
assert hasattr(cv2, 'FaceRecognizerSF'), 'OpenCV FaceRecognizerSF missing'
import fastapi, uvicorn, pydantic, multipart, PIL, transformers, accelerate
import dateutil, requests, huggingface_hub, tqdm, sse_starlette, numpy, dotenv
from transformers import AutoModel, AutoProcessor
with tempfile.TemporaryDirectory(prefix='gemmagallery-diagnostics-') as directory:
    db = lancedb.connect(directory)
    table = db.create_table('probe', data=[{'id': 1, 'vector': [1.0, 0.0, 0.0]}])
    assert table.count_rows() == 1, 'LanceDB write/read verification failed'
    db.drop_table('probe')
    del table, db
print('DIAGNOSTICS_OK')
"""
    code, out = run_command([str(VENV_PYTHON), "-u", "-c", diag_script])
    if code != 0 or "DIAGNOSTICS_OK" not in out.splitlines():
        return fail_command(pm, "Dependency runtime diagnostics failed.", out)

    pm.finish_step(100.0, "Runtime imports, OpenCV face APIs, and isolated LanceDB write/read passed.")
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="GemmaGallery Dependency Installer & Verifier")
    parser.add_argument("--check-only", action="store_true", help="Run verification without reinstalling")
    parser.add_argument("--force", action="store_true", help="Force reinstallation and rebuild of all components")
    parser.add_argument("--no-launch", action="store_true", help="Do not prompt to launch start.bat after setup")
    parser.add_argument("--no-pause", action="store_true", help="Exit without pausing for user input")
    parser.add_argument("--skip-model", action="store_true", help="Explicitly skip model cache/download checks (semantic search remains unverified)")
    args = parser.parse_args()
    if args.check_only and args.force:
        parser.error("--check-only cannot be combined with --force")

    print_banner()

    pm = ProgressManager(total_steps=TOTAL_STEPS)
    pm.start_animation()

    success = False
    try:
        steps = [
            lambda: step_1_prerequisites(pm),
            lambda: step_2_nodejs(pm),
            lambda: step_3_pip_upgrade(pm, force=args.force, check_only=args.check_only),
            lambda: step_4_pytorch_cuda(pm, force=args.force, check_only=args.check_only),
            lambda: step_5_backend_requirements(pm, force=args.force, check_only=args.check_only),
            lambda: step_6_frontend_packages(pm, force=args.force, check_only=args.check_only),
            lambda: step_7_frontend_build(pm, force=args.force, check_only=args.check_only),
            lambda: step_8_model_and_verification(pm, force=args.force, check_only=args.check_only, skip_model=args.skip_model),
        ]
        success = all(step() for step in steps)
    except KeyboardInterrupt:
        pm.fail_step("Setup interrupted. Re-run install.bat when ready.")
    except Exception as exc:
        pm.fail_step(f"Setup failed: {exc}")
    finally:
        pm.stop_animation()

    total_time = int(time.monotonic() - pm.start_time)
    mins, secs = divmod(total_time, 60)

    print()
    if success:
        gpu_label = pm.hardware_mode
        ffmpeg_bin = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
        video_label = "FFmpeg available (decoder depends on codec/runtime)" if ffmpeg_bin else "OpenCV VideoCapture (FFmpeg optional)"

        print(f"  {GREEN}╔═════════════════════════════════════════════════════════════════════════╗{RESET}")
        print(f"  {GREEN}║{RESET}  {WHITE}{BOLD}GEMMAGALLERY DEPENDENCY CHECKS COMPLETE{RESET}                    {GREEN}║{RESET}")
        print(f"  {GREEN}╚═════════════════════════════════════════════════════════════════════════╝{RESET}")
        print()
        print(f"    {WHITE}• Total Progress  :{RESET} {CYAN}[██████████████████████████] 100.0% Complete{RESET}")
        print(f"    {WHITE}• Elapsed Time    :{RESET} {mins}m {secs:02d}s")
        print(f"    {WHITE}• Hardware Mode   :{RESET} {GREEN}{gpu_label}{RESET}")
        print(f"    {WHITE}• Video Decoding  :{RESET} {video_label}")
        print(f"    {WHITE}• Vector Engine   :{RESET} LanceDB isolated write/read passed")
        print(f"    {WHITE}• Face Matching   :{RESET} OpenCV Contrib FaceDetectorYN (Local / Private)")
        print(f"    {WHITE}• Frontend UI     :{RESET} Production Vite Bundle Ready ({FRONTEND_DIR / 'dist' / 'index.html'})")
        print()
        for warning in pm.warnings:
            print(f"  [WARNING] {warning}")
        if not pm.warnings:
            print("  Model files are cached; model inference is checked when the app starts.")
        print(f"  {BLUE}─────────────────────────────────────────────────────────────────────────{RESET}")
        print(f"  {BOLD}To start the gallery anytime, double-click:{RESET} {CYAN}start.bat{RESET}")
        print(f"  {BLUE}─────────────────────────────────────────────────────────────────────────{RESET}")
        print()

        if not args.no_launch and not args.check_only:
            try:
                answer = input(f"  {WHITE}Would you like to start GemmaGallery now? [Y/n]: {RESET}").strip().lower()
                if answer in ("", "y", "yes"):
                    start_bat = ROOT_DIR / "start.bat"
                    if start_bat.exists():
                        print(f"\n  {CYAN}[*] Launching GemmaGallery...{RESET}\n")
                        return subprocess.call([str(start_bat), "--no-pause"], cwd=ROOT_DIR, shell=(os.name == "nt"))
            except (KeyboardInterrupt, EOFError):
                pass

        if not args.no_pause:
            try:
                input(f"\n  {DIM}Press Enter to exit...{RESET}")
            except (KeyboardInterrupt, EOFError):
                pass
        return 0
    else:
        print()
        print(f"  {RED}╔═════════════════════════════════════════════════════════════════════════╗{RESET}")
        print(f"  {RED}║{RESET}  {RED}{BOLD}✖  SETUP ENCOUNTERED AN ISSUE{RESET}                                          {RED}║{RESET}")
        print(f"  {RED}╚═════════════════════════════════════════════════════════════════════════╝{RESET}")
        print(f"  {YELLOW}Please review the error above. Ensure your internet connection is stable{RESET}")
        print(f"  {YELLOW}and Python 3.10+ / Node.js 18+ are properly installed.{RESET}")
        print()
        if not args.no_pause:
            try:
                input(f"\n  {DIM}Press Enter to exit...{RESET}")
            except (KeyboardInterrupt, EOFError):
                pass
        return 1

if __name__ == "__main__":
    sys.exit(main())
