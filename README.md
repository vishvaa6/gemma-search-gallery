# GemmaGallery

Version 2.1.1

A local React/FastAPI gallery for semantic photo search and sampled video-moment search, using the configured EmbeddingGemma model and LanceDB.

## Quick Start

### 1. Install & Verify Dependencies
Run `install.bat` once to set up all dependencies with an interactive total progress bar and diagnostics:

```cmd
install.bat
```

`install.bat` handles the entire environment setup:
- Automatically detects Python (3.10+) and Node.js (18, 20, or 22+).
- Creates the isolated virtual environment (`backend\.venv`).
- Automatically detects NVIDIA GPUs and installs PyTorch with CUDA 12.4 acceleration (falling back to CPU mode if no GPU is found).
- Installs backend packages, OpenCV Contrib (for local face recognition), and frontend dependencies.
- Compiles the frontend production bundle (`dist/index.html`).
- Pre-downloads and verifies the EmbeddingGemma 2 AI model cache.
- Shows an eight-stage terminal progress bar. Percentages advance when checks complete; long installs show their latest output and a spinner.
- Writes readable progress lines when output is redirected, and reports the failing command's error instead of claiming success.
- Checks requirement versions, installed frontend packages, build freshness, Torch runtime availability, OpenCV face APIs, and an isolated LanceDB write/read.

### 2. Start the App
To start GemmaGallery anytime, double-click `start.bat`:

```cmd
start.bat
```

`start.bat` is dedicated solely to launching the application:
- Instantly checks that the environment is ready (offers to run `install.bat` if missing).
- Frees ports 8000/5173 if stale processes linger.
- Launches the gallery server and opens **http://127.0.0.1:8000** in your default browser.

### Optional Flags

- `start.bat --dev` — Starts Vite dev server with Hot Module Replacement at **http://127.0.0.1:5173**.
- `start.bat --no-browser` — Suppresses opening the browser automatically.
- `start.bat --no-pause` — Suppresses the terminal pause on exit.
- `install.bat --check-only` — Runs the full verification diagnostics without reinstalling.
- `install.bat --force` — Forces re-download and reinstallation of all dependencies.
- `install.bat --skip-model` — Explicitly skips model cache/download checks; semantic search remains unverified.
- `install.bat --no-launch --no-pause` — Runs setup without a launch prompt or exit pause.

For an existing installation, verify dependencies without installing packages or rebuilding:

```cmd
install.bat --check-only --no-launch --no-pause
```

`--check-only` fails if the virtual environment, required packages, current frontend build, or complete model cache is missing. It cannot be combined with `--force`. Model cache checks verify local metadata, a tokenizer, and all weight shards; actual model inference is checked when the app starts. For a gated model, accept its terms and authenticate with `hf auth login` or configure `HF_TOKEN` in `backend/.env` before installation.

Select a folder, inspect it, and start indexing. Photos are processed before videos. The viewer supports image zoom/pan, video playback, match timestamps, and a sampled-frame strip. Optional local face search is available in Settings.

The gallery is available while the model loads. Semantic search and indexing require the real model; synthetic fallback embeddings cannot be added through those API routes.

## Performance behavior

- GPU work is serialized in a dedicated worker; indexing and preview decoding run outside the API event loop.
- Photos and video frames share versioned, atomic JPEG previews with a maximum 400-pixel edge.
- Concurrent requests for the same preview share a job. Two preview workers bound decoder load.
- ETags allow reuse of unchanged previews. Source changes invalidate disk cache keys.
- Search and gallery listing use cached video durations without opening every video.
- Frame metadata caches are bounded and refreshed when the table version changes.
- Idle maintenance compacts the database and creates missing scalar/vector indexes while preserving historical versions. Its first run can take time; indexing cannot start or resume during maintenance.
- Reduced MRL dimensions rerank a bounded candidate set retrieved at 768 dimensions. Displayed relevance is a ranking aid, not a probability.

The first request for an uncached preview still requires decoding. GPU readiness, video codec seeking, collection size, storage performance, and database maintenance all affect latency. No fixed millisecond search guarantee is implied.

## Validation

From the project directory:

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s backend -p test_performance.py -v
backend\.venv\Scripts\python.exe -m unittest discover -s backend -p test_installer.py -v
backend\.venv\Scripts\python.exe -m unittest discover -s backend -p test_priority.py -v
backend\.venv\Scripts\python.exe backend\benchmark_previews.py
cd frontend
npm.cmd run build
```

The regression suite uses real image/video codecs with isolated database/model doubles. It does not mutate the media library or benchmark GPU search.

To run maintenance manually, close the application first:

```powershell
backend\.venv\Scripts\python.exe backend\index_maintenance.py
```

`GALLERY_DATA_DIR` optionally selects a separate application data directory for isolated deployments. The default remains `backend/data`. Media previews may use the selected folder's `.gallery_index` sidecar, with an application-data fallback.

See [CHANGELOG.md](CHANGELOG.md) for release changes and verification scope.
