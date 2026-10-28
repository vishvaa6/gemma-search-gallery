"""Installer regressions. No package downloads, installation, or media-library writes."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import installer
import download_model


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.pm = installer.ProgressManager(stream=self.output)

    def test_redirected_progress_is_readable_and_finishes_only_after_all_steps(self):
        self.pm.start_animation()
        self.assertIsNone(self.pm._thread)
        for step in range(1, 9):
            self.pm.start_step(step, "Checking dependencies", 99)
            self.pm.update_detail("working", 100)
            self.assertLess(self.pm.current_percent, 100)
            self.pm.finish_step(100, "Verified")
        self.pm.stop_animation()
        output = self.output.getvalue()
        self.assertIn("[8/8]", output)
        self.assertIn("100.0%", output)
        self.assertNotIn("\r", output)
        self.assertNotIn("\x1b", output)

    def test_failure_does_not_report_complete(self):
        self.pm.start_step(3, "pip", 100)
        self.pm.fail_step("Network unavailable")
        self.assertNotIn("100.0%", self.output.getvalue())
        self.assertIn("[FAIL]", self.output.getvalue())

    def test_pip_upgrade_failure_is_not_swallowed(self):
        with patch.object(installer, "run_command", side_effect=[(0, "pip 26"), (1, "certificate verification failed")]):
            self.assertFalse(installer.step_3_pip_upgrade(self.pm))
        self.assertIn("certificate verification failed", self.output.getvalue())

    def test_check_only_pip_never_installs(self):
        calls = []
        def command(cmd, **kwargs):
            calls.append(cmd)
            return 0, "No broken requirements found."
        with patch.object(installer, "run_command", side_effect=command):
            self.assertTrue(installer.step_3_pip_upgrade(self.pm, check_only=True))
        self.assertTrue(any(cmd[-1] == "check" for cmd in calls))
        self.assertFalse(any("install" in cmd or "ensurepip" in cmd for cmd in calls))

    def test_pip_dependency_conflict_fails_verification(self):
        with patch.object(installer, "run_command", side_effect=[(0, "pip 26"), (1, "package A requires package B")]):
            self.assertFalse(installer.step_3_pip_upgrade(self.pm, check_only=True))

    def test_old_node_version_fails(self):
        with patch.object(installer.shutil, "which", return_value="node"), patch.object(installer, "run_command", return_value=(0, "v16.20.2")):
            self.assertFalse(installer.step_2_nodejs(self.pm))

    def test_broken_npm_fails(self):
        with patch.object(installer.shutil, "which", return_value="tool"), patch.object(installer, "run_command", side_effect=[(0, "v20.19.0"), (1, "npm broken")]):
            self.assertFalse(installer.step_2_nodejs(self.pm))

    def test_node_release_not_supported_by_vite_is_rejected(self):
        for version in ("v19.9.0", "v21.7.0"):
            with self.subTest(version=version), patch.object(installer.shutil, "which", return_value="node"), patch.object(installer, "run_command", return_value=(0, version)):
                self.assertFalse(installer.step_2_nodejs(self.pm))

    def test_cpu_runtime_is_not_misreported_as_cuda(self):
        with patch.object(installer, "detect_nvidia_gpu", return_value=(True, "NVIDIA GPU", 4)), patch.object(installer, "torch_status", return_value=(True, "PyTorch: CPU (CUDA unavailable)")):
            self.assertTrue(installer.step_4_pytorch_cuda(self.pm, check_only=True))
        self.assertIn("CPU (CUDA unavailable)", self.pm.hardware_mode)

    def test_missing_torch_in_check_only_never_installs(self):
        with patch.object(installer, "detect_nvidia_gpu", return_value=(False, "None", 0)), patch.object(installer, "torch_status", return_value=(False, "torch missing")), patch.object(installer, "run_command") as run:
            self.assertFalse(installer.step_4_pytorch_cuda(self.pm, check_only=True))
            run.assert_not_called()

    def test_requirement_versions_and_markers_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            requirements = Path(directory) / "requirements.txt"
            requirements.write_text('lancedb>=0.40,<0.41\nmissing-package>=1\nskipped; python_version < "2"\n', encoding="utf-8")
            def version(name):
                if name == "lancedb":
                    return "0.39.0"
                raise installer.importlib.metadata.PackageNotFoundError(name)
            with patch.object(installer.importlib.metadata, "version", side_effect=version):
                issues = installer.requirement_issues(requirements)
            self.assertEqual(len(issues), 2)
            self.assertIn("0.39.0", issues[0])
            self.assertIn("missing", issues[1])

    def test_missing_backend_dependency_is_read_only_failure(self):
        with patch.object(installer, "requirement_issues", return_value=["requests: missing"]), patch.object(installer, "run_command") as run:
            self.assertFalse(installer.step_5_backend_requirements(self.pm, check_only=True))
            run.assert_not_called()

    def test_npm_checks_all_packages_instead_of_only_vite(self):
        with patch.object(installer, "run_command", return_value=(1, 'missing: react@18')) as run:
            self.assertFalse(installer.step_6_frontend_packages(self.pm, check_only=True))
            self.assertEqual(run.call_args.args[0][1:], ["ls", "--depth=0", "--json"])

    def test_failed_npm_ci_does_not_silently_rewrite_lockfile(self):
        with patch.object(installer, "frontend_packages_ready", return_value=(False, "missing")), patch.object(installer, "run_command", return_value=(1, "lockfile mismatch")) as run:
            self.assertFalse(installer.step_6_frontend_packages(self.pm))
            self.assertEqual(run.call_count, 1)
            self.assertIn("ci", run.call_args.args[0])

    def test_stale_build_fails_without_building_in_check_only(self):
        with patch.object(installer, "frontend_build_current", return_value=False), patch.object(installer, "run_command") as run:
            self.assertFalse(installer.step_7_frontend_build(self.pm, check_only=True))
            run.assert_not_called()

    def test_build_requires_current_sources_and_generated_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            frontend = Path(directory)
            (frontend / "dist" / "assets").mkdir(parents=True)
            (frontend / "src").mkdir()
            source = frontend / "src" / "App.jsx"
            source.write_text("export default function App() {}", encoding="utf-8")
            index = frontend / "dist" / "index.html"
            index.write_text('<script src="/assets/app.js"></script>', encoding="utf-8")
            with patch.object(installer, "FRONTEND_DIR", frontend):
                self.assertFalse(installer.frontend_build_current())
                (frontend / "dist" / "assets" / "app.js").write_text("built", encoding="utf-8")
                self.assertTrue(installer.frontend_build_current())
                os.utime(source, ns=(index.stat().st_mtime_ns + 10_000_000, index.stat().st_mtime_ns + 10_000_000))
                self.assertFalse(installer.frontend_build_current())

    def test_missing_model_fails_check_only(self):
        with patch.object(installer, "run_command", return_value=(1, "Model incomplete")) as run:
            self.assertFalse(installer.step_8_model_and_verification(self.pm, check_only=True))
            self.assertEqual(run.call_count, 1)
            self.assertIn("--check-only", run.call_args.args[0])

    def test_failed_model_download_is_not_success(self):
        with patch.object(installer, "run_command", side_effect=[(1, "missing"), (1, "access denied")]):
            self.assertFalse(installer.step_8_model_and_verification(self.pm))

    def test_diagnostic_marker_cannot_hide_nonzero_exit(self):
        with patch.object(installer, "run_command", side_effect=[(0, "cached"), (1, "DIAGNOSTICS_OK\n")]):
            self.assertFalse(installer.step_8_model_and_verification(self.pm, check_only=True))

    def test_skipping_model_produces_explicit_warning(self):
        with patch.object(installer, "run_command", return_value=(0, "DIAGNOSTICS_OK\n")):
            self.assertTrue(installer.step_8_model_and_verification(self.pm, check_only=True, skip_model=True))
        self.assertIn("skipped", self.pm.warnings[0])

    def test_failure_reaches_summary_instead_of_returning_early(self):
        with patch.object(sys, "argv", ["installer.py", "--no-pause", "--no-launch"]), patch.object(installer, "step_1_prerequisites", return_value=False), patch.object(sys, "stdout", new_callable=io.StringIO) as output:
            self.assertEqual(installer.main(), 1)
            self.assertIn("SETUP ENCOUNTERED AN ISSUE", output.getvalue())

    def test_main_propagates_verification_mode_to_every_mutating_step(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["installer.py", "--check-only", "--no-pause", "--no-launch"]))
            stack.enter_context(patch.object(sys, "stdout", new_callable=io.StringIO))
            for name in ("step_1_prerequisites", "step_2_nodejs"):
                stack.enter_context(patch.object(installer, name, return_value=True))
            mocks = [stack.enter_context(patch.object(installer, name, return_value=True)) for name in (
                "step_3_pip_upgrade", "step_4_pytorch_cuda", "step_5_backend_requirements", "step_6_frontend_packages", "step_7_frontend_build", "step_8_model_and_verification")]
            self.assertEqual(installer.main(), 0)
            for mock in mocks:
                self.assertTrue(mock.call_args.kwargs["check_only"])

    def test_force_and_check_only_are_rejected_together(self):
        with patch.object(sys, "argv", ["installer.py", "--check-only", "--force"]), patch.object(sys, "stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as error:
                installer.main()
            self.assertEqual(error.exception.code, 2)

    def test_subprocess_output_and_exit_code_are_preserved(self):
        code, output = installer.run_command([sys.executable, "-c", "print('real failure'); raise SystemExit(7)"])
        self.assertEqual(code, 7)
        self.assertIn("real failure", output)

    @unittest.skipUnless(os.name == "nt", "Windows batch entry point")
    def test_batch_bootstrap_selects_uv_and_seeds_pip(self):
        with tempfile.TemporaryDirectory(prefix="gallery setup ") as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            (root / "install.bat").write_bytes((installer.ROOT_DIR / "install.bat").read_bytes())
            (root / "bin" / "uv.cmd").write_text('@echo off\necho %* > "%~dp0args.txt"\nexit /b 7\n', encoding="ascii")
            environment = dict(os.environ, USERPROFILE=str(root), LOCALAPPDATA=str(root), APPDATA=str(root), PATH=str(root / "bin") + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"))
            result = subprocess.run(["cmd.exe", "/d", "/c", str(root / "install.bat"), "--no-pause"], env=environment, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            args = (root / "bin" / "args.txt").read_text(encoding="ascii")
            self.assertIn("venv --python 3.11 --seed", args)
            self.assertIn("could not prepare", result.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows batch entry point")
    def test_batch_check_only_does_not_create_a_missing_venv(self):
        with tempfile.TemporaryDirectory(prefix="gallery setup ") as directory:
            entry = Path(directory) / "install.bat"
            entry.write_bytes((installer.ROOT_DIR / "install.bat").read_bytes())
            result = subprocess.run(["cmd.exe", "/d", "/c", str(entry), "--check-only", "--no-pause"], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("No virtual environment", result.stdout)
            self.assertFalse((Path(directory) / "backend" / ".venv").exists())


class ModelCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.snapshot = Path(self.temp.name)
        for name in ("config.json", "tokenizer_config.json", "tokenizer.json"):
            (self.snapshot / name).write_text("{}", encoding="utf-8")

    def test_config_without_weights_is_incomplete(self):
        self.assertFalse(download_model.snapshot_complete(self.snapshot))

    def test_complete_unsharded_snapshot(self):
        (self.snapshot / "model.safetensors").write_bytes(b"weights")
        self.assertTrue(download_model.snapshot_complete(self.snapshot))

    def test_all_weight_shards_are_required(self):
        index = {"weight_map": {"a": "model-1.safetensors", "b": "model-2.safetensors"}}
        (self.snapshot / "model.safetensors.index.json").write_text(json.dumps(index), encoding="utf-8")
        (self.snapshot / "model-1.safetensors").write_bytes(b"weights")
        self.assertFalse(download_model.snapshot_complete(self.snapshot))
        (self.snapshot / "model-2.safetensors").write_bytes(b"weights")
        self.assertTrue(download_model.snapshot_complete(self.snapshot))

    def test_invalid_config_is_not_ready(self):
        (self.snapshot / "config.json").write_text("not JSON", encoding="utf-8")
        (self.snapshot / "model.safetensors").write_bytes(b"weights")
        self.assertFalse(download_model.snapshot_complete(self.snapshot))

    def test_missing_tokenizer_is_not_ready(self):
        (self.snapshot / "tokenizer.json").unlink()
        (self.snapshot / "model.safetensors").write_bytes(b"weights")
        self.assertFalse(download_model.snapshot_complete(self.snapshot))

    def test_cached_nonexistence_sentinel_is_not_a_model(self):
        import huggingface_hub
        with patch.object(huggingface_hub, "try_to_load_from_cache", return_value=huggingface_hub._CACHED_NO_EXIST):
            self.assertFalse(download_model.check_cached())

    def test_download_uses_current_api_and_passes_force(self):
        import huggingface_hub
        (self.snapshot / "model.safetensors").write_bytes(b"weights")
        with patch.object(huggingface_hub, "snapshot_download", return_value=str(self.snapshot)) as download, patch.object(huggingface_hub, "get_token", return_value="test-token"), patch.object(sys, "stdout", new_callable=io.StringIO):
            self.assertTrue(download_model.download_model(force=True))
        self.assertTrue(download.call_args.kwargs["force_download"])
        self.assertNotIn("resume_download", download.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
