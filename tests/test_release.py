"""Exercise the real release shell script against a fake Docker executable."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TAG = "sha-" + "a" * 40


class ReleaseDelivery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        shutil.copy(ROOT / "scripts/deploy-release.sh", self.root / "scripts/deploy-release.sh")
        shutil.copy(ROOT / "compose.sh", self.root / "compose.sh")
        (self.root / ".env.docker").write_text("POSTGRES_PASSWORD=server-secret\n")
        (self.root / ".release.env").write_text("AI_IMAGE_TAG=old-ai\nWEB_IMAGE_TAG=old-web\n")
        (self.root / "bin").mkdir()
        docker = self.root / "bin/docker"
        docker.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$TEST_DOCKER_LOG"\nif [[ -n "${TEST_FAIL_ON:-}" && " $* " == *" $TEST_FAIL_ON "* ]]; then exit 1; fi\n')
        docker.chmod(0o755)

    def release(self, component, tag=TAG, fail_on=""):
        return subprocess.run(
            ["bash", str(self.root / "scripts/deploy-release.sh"), component, tag],
            env={**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
                 "TEST_DOCKER_LOG": str(self.root / "calls"), "TEST_FAIL_ON": fail_on},
            text=True, capture_output=True, check=False,
        )

    def test_frontend_updates_only_its_image_and_preserves_global_env(self):
        result = self.release("frontend")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / "calls").read_text()
        self.assertIn("pull web-frontend", calls)
        self.assertIn("--no-deps --wait", calls)
        self.assertNotIn("down", calls)
        self.assertNotIn("ai-service", calls)
        self.assertEqual((self.root / ".env.docker").read_text(), "POSTGRES_PASSWORD=server-secret\n")
        self.assertEqual((self.root / ".release.env").read_text(), f"AI_IMAGE_TAG=old-ai\nWEB_IMAGE_TAG={TAG}\n")

    def test_ai_stops_executor_and_migrates_before_restarting(self):
        result = self.release("ai")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / "calls").read_text()
        self.assertLess(calls.index("stop ai-service"), calls.index("run --rm --no-deps ai-migrate"))
        self.assertLess(calls.index("run --rm --no-deps ai-migrate"), calls.index("up -d"))

    def test_failed_pull_or_migration_does_not_publish_release_tags(self):
        for failure in ("pull", "run"):
            with self.subTest(failure=failure):
                result = self.release("ai", fail_on=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.root / ".release.env").read_text(), "AI_IMAGE_TAG=old-ai\nWEB_IMAGE_TAG=old-web\n")

    def test_backend_release_includes_sync_and_excludes_other_applications(self):
        result = self.release("backend")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / "calls").read_text()
        self.assertIn("sync-service", calls)
        self.assertLess(calls.index("run --rm --no-deps catalog-seed"), calls.index("up -d"))
        self.assertNotIn("web-frontend", calls)
        self.assertNotIn("ai-service", calls)

    def test_failed_backend_seed_does_not_publish_tags_or_restart_applications(self):
        result = self.release("backend", fail_on="run")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("up -d", (self.root / "calls").read_text())
        self.assertEqual((self.root / ".release.env").read_text(), "AI_IMAGE_TAG=old-ai\nWEB_IMAGE_TAG=old-web\n")

    def test_invalid_targets_are_rejected_before_docker_runs(self):
        self.assertNotEqual(self.release("unknown").returncode, 0)
        self.assertNotEqual(self.release("backend", "release").returncode, 0)
        self.assertFalse((self.root / "calls").exists())

    def test_compose_wrapper_always_loads_global_env_then_release_tags(self):
        result = subprocess.run(
            ["bash", str(self.root / "compose.sh"), "config", "--quiet"],
            env={**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
                 "TEST_DOCKER_LOG": str(self.root / "calls")},
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "calls").read_text().strip(),
                         "compose --env-file .env.docker --env-file .release.env config --quiet")


if __name__ == "__main__":
    unittest.main()
