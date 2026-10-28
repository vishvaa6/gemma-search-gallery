"""
Standalone GPU verification and benchmark script for NVIDIA RTX 3050 Laptop GPU.
Verifies PyTorch CUDA runtime, GPU device details, and FP16 tensor performance.
"""

import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def verify_gpu():
    print("=" * 60)
    print("       NVIDIA GPU & PyTorch CUDA Diagnostic Utility")
    print("=" * 60)

    try:
        import torch
    except ImportError:
        print("[!] ERROR: PyTorch is not installed in the current environment.")
        print("    Please run: install.bat to set up dependencies.")
        sys.exit(1)

    print(f"[*] PyTorch Version : {torch.__version__}")
    print(f"[*] CUDA Available  : {torch.cuda.is_available()}")

    if not torch.cuda.is_available():
        print("[!] WARNING: CUDA is NOT detected by PyTorch!")
        print("    Ensure you installed the CUDA wheel index:")
        print("    uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124")
        return False

    device_count = torch.cuda.device_count()
    current_device = torch.cuda.current_device()
    device_name = torch.cuda.get_device_name(current_device)
    cuda_version = torch.version.cuda
    total_memory_gb = torch.cuda.get_device_properties(current_device).total_memory / (1024 ** 3)

    print(f"[*] CUDA Version    : {cuda_version}")
    print(f"[*] Device Count    : {device_count}")
    print(f"[*] Active Device   : cuda:{current_device} ({device_name})")
    print(f"[*] Total VRAM      : {total_memory_gb:.2f} GB")

    # Run Benchmark: FP16 Matrix Multiplication on GPU
    print("\n[*] Running FP16 GPU Benchmark on cuda:0...")
    try:
        t0 = time.perf_counter()
        # Allocate two 4096 x 4096 half-precision tensors on GPU
        a = torch.randn(4096, 4096, device="cuda", dtype=torch.float16)
        b = torch.randn(4096, 4096, device="cuda", dtype=torch.float16)
        torch.cuda.synchronize()

        # Warmup and timed multiplication
        _ = torch.matmul(a, b)
        torch.cuda.synchronize()

        t1 = time.perf_counter()
        _ = torch.matmul(a, b)
        torch.cuda.synchronize()
        elapsed_ms = (time.perf_counter() - t1) * 1000

        allocated_mb = torch.cuda.memory_allocated() / (1024 ** 2)
        reserved_mb = torch.cuda.memory_reserved() / (1024 ** 2)

        print(f"[+] Benchmark PASS  : 4096 x 4096 FP16 matmul took {elapsed_ms:.2f} ms")
        print(f"[+] Memory Usage    : {allocated_mb:.1f} MB allocated / {reserved_mb:.1f} MB reserved")
        print(f"\n[✓] SUCCESS: NVIDIA {device_name} is fully operational for EmbeddingGemma 2!")
        print("=" * 60)
        return True
    except Exception as e:
        print(f"[!] GPU benchmark failed with error: {e}")
        return False

if __name__ == "__main__":
    success = verify_gpu()
    sys.exit(0 if success else 1)
