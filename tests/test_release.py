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

    def release(self, *tags, fail_on="", locked=False):
        command = ["bash", str(self.root / "scripts/deploy-release.sh"), *tags]
        if locked:
            command = ["bash", "-c",
                       'cd "$1"; exec 9>.deploy.lock; flock -w 1 9; '
                       'DEPLOY_LOCK_HELD=1 source scripts/deploy-release.sh release release release',
                       "infra-release", str(self.root)]
        return subprocess.run(
            command,
            env={**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
                 "TEST_DOCKER_LOG": str(self.root / "calls"), "TEST_FAIL_ON": fail_on},
            text=True, capture_output=True, check=False, timeout=5,
        )

    def test_workflow_can_source_script_under_existing_server_lock(self):
        result = self.release(locked=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WEB_IMAGE_TAG=release", (self.root / ".release.env").read_text())

    def test_default_release_deploys_all_groups_and_replaces_old_tag_overrides(self):
        result = self.release()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / "calls").read_text()
        for service in ("sync-service", "web-frontend", "ai-service"):
            self.assertIn(service, calls)
        self.assertNotIn(" down", calls)
        self.assertEqual((self.root / ".env.docker").read_text(), "POSTGRES_PASSWORD=server-secret\n")
        self.assertEqual((self.root / ".release.env").read_text(),
                         "BACKEND_IMAGE_TAG=release\nAI_IMAGE_TAG=release\nWEB_IMAGE_TAG=release\n")

    def test_dependencies_seed_and_ai_migration_precede_application_restart(self):
        result = self.release(TAG, TAG, TAG)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / "calls").read_text().splitlines()
        dependencies = next(i for i, call in enumerate(calls) if "up -d" in call)
        seed = next(i for i, call in enumerate(calls) if "run --rm --no-deps catalog-seed" in call)
        stop = next(i for i, call in enumerate(calls) if "stop ai-service" in call)
        migrate = next(i for i, call in enumerate(calls) if "run --rm --no-deps ai-migrate" in call)
        apps = next(i for i, call in enumerate(calls) if "up -d --no-deps" in call)
        self.assertLess(dependencies, seed)
        self.assertLess(seed, stop)
        self.assertLess(stop, migrate)
        self.assertLess(migrate, apps)
        self.assertNotIn("--profile apps", calls[dependencies])
        self.assertNotIn("ai-migrate", calls[apps])
        self.assertIn(f"BACKEND_IMAGE_TAG={TAG}", (self.root / ".release.env").read_text())

    def test_failure_does_not_publish_tags_or_continue_starting_applications(self):
        for failure in ("pull", "catalog-seed", "ai-migrate", "--no-deps --wait"):
            with self.subTest(failure=failure):
                (self.root / "calls").unlink(missing_ok=True)
                result = self.release(fail_on=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.root / ".release.env").read_text(),
                                 "AI_IMAGE_TAG=old-ai\nWEB_IMAGE_TAG=old-web\n")
                if failure != "--no-deps --wait":
                    self.assertNotIn("up -d --no-deps", (self.root / "calls").read_text())

    def test_invalid_tag_is_rejected_before_docker_runs(self):
        self.assertNotEqual(self.release("unknown").returncode, 0)
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
