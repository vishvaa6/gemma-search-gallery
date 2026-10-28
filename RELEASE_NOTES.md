# GemmaGallery v2.1.1

This release adds dependable terminal progress for installing and checking dependencies, fixes setup checks that could falsely report success, and updates vulnerable frontend development dependencies.

- Eight-stage progress in interactive terminals and readable lines in logs.
- Verification mode that does not install packages, create an environment, rebuild, or download models.
- Correct pip failure handling, Python/uv bootstrapping, Node/npm validation, package version checks, and frontend build checks.
- Runtime checks for Torch, OpenCV face APIs, and an isolated LanceDB write/read.
- Model-cache validation for metadata, tokenizer files, and every weight shard; updated Hugging Face download API and working force downloads.
- Patched Vite 6.4.5 and PostCSS dependencies, with zero known npm audit findings at release verification.

## Installation

Extract `gemma-search-gallery-v2.1.1.zip` to a folder, run `install.bat`, and then run `start.bat`. The ZIP includes the frontend production bundle. Python 3.10+ and Node.js 18, 20, or 22+ are required. The installer prefers Python 3.11 when creating an environment with uv.

The release does not include Python packages, model weights, credentials, or media-library data. Configure model access when required. For an existing setup, run `install.bat --check-only --no-launch --no-pause`. Use `--skip-model` only to explicitly leave model readiness unverified.

## Verification

- 33 installer/model-cache regression tests passed.
- 19 application regression tests passed.
- 6 priority/scheduling regression tests passed.
- Production frontend build passed with Vite 6.4.5.
- npm audit reported zero known vulnerabilities.
- All eight real dependency checks passed on Windows with Python 3.11.17, Node.js 26.10.0, PyTorch 2.6.0+cu124, and CUDA 12.4.

Application regressions use isolated database/model doubles and real image/video codecs. The dependency verifier checks local model files, not full model inference or every hardware/codec combination. A fresh package installation and gated model download were covered by regression doubles rather than reinstalling the working environment. Passing checks do not guarantee the absence of every bug.
