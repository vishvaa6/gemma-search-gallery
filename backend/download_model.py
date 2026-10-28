"""
Model Download & Cache Manager for EmbeddingGemma 2.
Ensures model weights are downloaded upfront with live progress bars and Hugging Face authentication guidance.
"""

import os
import sys
import argparse
import json
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Load optional .env file if available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

MODEL_ID = os.getenv("EMBEDDING_GEMMA_MODEL_ID", "google/embeddinggemma-2")

def snapshot_complete(snapshot):
    """Check required local metadata, tokenizer, and every referenced weight shard."""
    snapshot = Path(snapshot)
    try:
        for name in ("config.json", "tokenizer_config.json"):
            if not isinstance(json.loads((snapshot / name).read_text(encoding="utf-8")), dict):
                return False
        if not any((snapshot / name).is_file() and (snapshot / name).stat().st_size > 0
                   for name in ("tokenizer.json", "tokenizer.model", "spiece.model", "vocab.json", "vocab.txt")):
            return False
        for name in ("model.safetensors", "model.safetensors.index.json", "pytorch_model.bin", "pytorch_model.bin.index.json"):
            weight = snapshot / name
            if not weight.is_file():
                continue
            if not name.endswith(".index.json"):
                return weight.stat().st_size > 0
            mapping = json.loads(weight.read_text(encoding="utf-8")).get("weight_map", {})
            if not isinstance(mapping, dict) or not mapping:
                return False
            shards = set(mapping.values())
            return all(isinstance(shard, str) and not Path(shard).is_absolute()
                       and ".." not in Path(shard).parts
                       and (snapshot / shard).is_file() and (snapshot / shard).stat().st_size > 0
                       for shard in shards)
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    return False


def check_cached():
    """A cached config or the cache's non-existence sentinel is not a full model."""
    try:
        from huggingface_hub import try_to_load_from_cache
        config_path = try_to_load_from_cache(MODEL_ID, "config.json")
        return isinstance(config_path, str) and snapshot_complete(Path(config_path).parent)
    except Exception:
        return False

def download_model(force=False):
    print("=" * 65)
    print(f"      EmbeddingGemma 2 Model Setup: {MODEL_ID}")
    print("=" * 65)

    if not force and check_cached():
        print(f"[✓] Model '{MODEL_ID}' is already downloaded in local cache!")
        print("    Skipping download. Use --force to re-download.")
        return True

    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")

    print(f"[*] Target Model : {MODEL_ID}")
    print("-" * 65)

    try:
        from huggingface_hub import snapshot_download, get_token
    except ImportError:
        print("[!] ERROR: 'huggingface_hub' is not installed.")
        print("    Run: pip install huggingface_hub")
        return False

    # Check for authentication
    current_token = hf_token or get_token()
    if not current_token:
        print("\n[!] IMPORTANT: EmbeddingGemma 2 is distributed under the Gemma License.")
        print(f"    1. Accept the model terms at: https://huggingface.co/{MODEL_ID}")
        print("    2. Get your User Access Token at: https://huggingface.co/settings/tokens")
        print("    (You can also save it in backend/.env as HF_TOKEN=your_token_here)\n")

        print("    Authenticate with `hf auth login` or set HF_TOKEN, then re-run setup.")
        print("    Attempting this download with the existing authentication configuration.")

    print(f"\n[*] Starting download of '{MODEL_ID}' (showing live progress)...")
    try:
        local_dir = snapshot_download(
            repo_id=MODEL_ID,
            token=current_token,
            force_download=force,
            local_files_only=False,
        )
        if not snapshot_complete(local_dir):
            print("[!] Download finished but required model files are missing or incomplete.")
            return False
        print(f"\n[✓] Successfully downloaded and checked model files in:")
        print(f"    {local_dir}")
        print("=" * 65)
        return True
    except Exception as e:
        error_msg = str(e)
        print(f"\n[!] Download encounter an error: {error_msg}")
        if "403" in error_msg or "gated" in error_msg.lower() or "restricted" in error_msg.lower():
            print("\n[!] ACCESS REQUIRED:")
            print(f"    Please visit https://huggingface.co/{MODEL_ID} and click 'Acknowledge access'")
            print("    Then provide your Hugging Face token in backend/.env or when prompted.")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download EmbeddingGemma 2 weights")
    parser.add_argument("--check-only", action="store_true", help="Only verify if model is already downloaded")
    parser.add_argument("--force", action="store_true", help="Force re-download even if already present")
    args = parser.parse_args()

    if args.check_only:
        is_ready = check_cached()
        if is_ready:
            print(f"[✓] Model '{MODEL_ID}' is ready in local cache.")
            sys.exit(0)
        else:
            print(f"[!] Model '{MODEL_ID}' is NOT yet cached.")
            sys.exit(1)

    success = download_model(force=args.force)
    sys.exit(0 if success else 1)
