# Changelog

## 2.1.1 - 2026-10-28

The Windows installer now shows progress while installing and checking dependencies, and reports failures without displaying a successful completion message.

- Added readable progress output for redirected terminals; animation stops between completed stages.
- Made `--check-only` verify the existing installation without installing packages, creating a virtual environment, downloading models, or rebuilding the frontend.
- Fixed virtual environment creation when uv or the Python launcher is selected; uv environments now include pip.
- Fixed ignored pip errors, invalid Node/npm versions, incomplete backend/frontend dependency checks, and stale frontend builds.
- Verify Torch with a real runtime operation and report actual CPU/CUDA availability.
- Updated model downloads for the current Hugging Face API; `--force` now reaches the download API.
- Reject partial model caches, missing weight shards, and cached non-existence markers.
- Added an explicit `--skip-model` option that reports the unverified model state.
- Check OpenCV face APIs and an isolated LanceDB write/read before completing setup.
- Fixed the launcher losing the installer's failure exit code.
- Added installer regressions and aligned API/frontend release versions through `VERSION`.
- Updated Vite to 6.4.5 and patched the PostCSS selector parser; npm audit reports zero known vulnerabilities for this lockfile.

Passing checks establish the tested behavior; they do not guarantee that every hardware, codec, network, or model combination is free of defects. Model inference requires a compatible model and sufficient runtime resources.
