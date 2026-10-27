"""Exercise the real release shell script against a fake Docker executable."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TAG = "sha-" + "a" * 40
VERSION = "1.0.0"
APPLICATION_SERVICES = (
    "discovery-server", "identity-service", "catalog-service", "order-service",
    "payment-service", "promotion-service", "search-service", "media-service",
    "sync-service", "api-gateway", "ai-service", "web-frontend",
)


class ReleaseDelivery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        shutil.copy(ROOT / "scripts/deploy-release.sh", self.root / "scripts/deploy-release.sh")
        shutil.copy(ROOT / "scripts/release_images.py", self.root / "scripts/release_images.py")
        shutil.copy(ROOT / "compose.sh", self.root / "compose.sh")
        (self.root / ".env.docker").write_text("POSTGRES_PASSWORD=server-secret\n")
        (self.root / "release.toml").write_text("[images]\nbackend='1.0.0'\nai='1.0.0'\nweb='1.0.0'\n")
        (self.root / "bin").mkdir()
        (self.root / "compose-config.json").write_text(json.dumps({"services": {
            name: {"image": f"ghcr.io/pc-shopping-assistant-org/{name}:1.0.0"}
            for name in APPLICATION_SERVICES
        }}))
        docker = self.root / "bin/docker"
        docker.write_text('''#!/usr/bin/env bash
printf "%s\\n" "$*" >> "$TEST_DOCKER_LOG"
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  if [[ "${args[i]}" == --env-file && ! -f "${args[i+1]}" ]]; then exit 99; fi
done
if [[ -n "${TEST_FAIL_ON:-}" && " $* " == *" $TEST_FAIL_ON "* ]]; then exit 1; fi
if [[ " $* " == *" config --format json "* ]]; then
  if [[ -n "${TEST_EXPECTED_TAG:-}" && "$BACKEND_IMAGE_TAG" != "$TEST_EXPECTED_TAG" ]]; then exit 98; fi
  cat "$TEST_COMPOSE_CONFIG"
elif [[ " $* " == *" images -q "* ]]; then
  if [[ $(grep -c 'images -q' "$TEST_DOCKER_LOG") == 1 ]]; then
    [[ "${TEST_FIRST_DEPLOY:-0}" == 1 ]] || printf 'sha256:old\\nsha256:shared\\n'
  else
    printf 'sha256:new\\nsha256:shared\\n'
  fi
fi
exit 0
''')
        docker.chmod(0o755)

    def release(self, *tags, fail_on="", locked=False, first_deploy=False, ambient_tag=""):
        if tags:
            values = (tags[0], tags[1] if len(tags) > 1 else tags[0],
                      tags[2] if len(tags) > 2 else tags[0])
            (self.root / "release.toml").write_text(
                "[images]\n" + "".join(f"{key}='{value}'\n" for key, value in zip(
                    ("backend", "ai", "web"), values
                ))
            )
        command = ["bash", str(self.root / "scripts/deploy-release.sh")]
        if locked:
            command = ["bash", "-c",
                       'cd "$1"; exec 9>.deploy.lock; flock -w 1 9; '
                       'DEPLOY_LOCK_HELD=1 bash scripts/deploy-release.sh',
                       "infra-release", str(self.root)]
        return subprocess.run(
            command,
            env={**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
                 "TEST_DOCKER_LOG": str(self.root / "calls"), "TEST_FAIL_ON": fail_on,
                 "TEST_COMPOSE_CONFIG": str(self.root / "compose-config.json"),
                 "BACKEND_IMAGE_TAG": ambient_tag,
                 "TEST_EXPECTED_TAG": tags[0] if ambient_tag else "",
                 "TEST_FIRST_DEPLOY": "1" if first_deploy else "0"},
            text=True, capture_output=True, check=False, timeout=5,
        )

    def test_workflow_can_run_script_under_existing_server_lock(self):
        result = self.release(locked=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("web='1.0.0'", (self.root / "release.toml").read_text())

    def test_file_versions_deploy_all_groups_without_rewriting_configuration(self):
        result = self.release(VERSION)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / "calls").read_text()
        for service in ("sync-service", "web-frontend", "ai-service"):
            self.assertIn(service, calls)
        self.assertNotIn(" down", calls)
        self.assertEqual((self.root / ".env.docker").read_text(), "POSTGRES_PASSWORD=server-secret\n")
        self.assertIn("backend='1.0.0'", (self.root / "release.toml").read_text())

    def test_independent_versions_and_prerelease_are_preserved(self):
        result = self.release("1.0.1", "1.1.0-rc.1", TAG)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ai='1.1.0-rc.1'", (self.root / "release.toml").read_text())

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
        self.assertIn(f"backend='{TAG}'", (self.root / "release.toml").read_text())

    def test_failure_preserves_selected_versions_and_stops_deployment(self):
        for failure in ("manifest", "pull", "catalog-seed", "ai-migrate", "--no-deps --wait"):
            with self.subTest(failure=failure):
                (self.root / "calls").unlink(missing_ok=True)
                result = self.release(VERSION, fail_on=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("backend='1.0.0'", (self.root / "release.toml").read_text())
                if failure != "--no-deps --wait":
                    self.assertNotIn("up -d --no-deps", (self.root / "calls").read_text())
                self.assertNotIn("image rm", (self.root / "calls").read_text())

    def test_all_application_versions_are_checked_before_pull_or_any_start(self):
        result = self.release(VERSION)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / "calls").read_text().splitlines()
        checks = [call for call in calls if call.startswith("manifest inspect ")]
        self.assertEqual(len(checks), len(APPLICATION_SERVICES))
        for service in APPLICATION_SERVICES:
            self.assertIn(f"manifest inspect ghcr.io/pc-shopping-assistant-org/{service}:1.0.0", checks)
        pull = next(i for i, call in enumerate(calls) if " pull" in call)
        self.assertTrue(all(calls.index(check) < pull for check in checks))

    def test_only_replaced_local_images_are_removed_after_health_passes(self):
        result = self.release(VERSION)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / "calls").read_text()
        self.assertIn("image rm sha256:old", calls)
        self.assertLess(calls.index("up -d --no-deps --wait"), calls.index("image rm"))
        self.assertNotIn("image rm sha256:shared", calls)
        self.assertNotIn("image rm sha256:new", calls)
        self.assertNotIn("prune", calls)
        self.assertNotIn("image rm --force", calls)

    def test_first_deploy_has_no_old_images_to_remove(self):
        result = self.release(VERSION, first_deploy=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("image rm", (self.root / "calls").read_text())

    def test_selected_version_overrides_ambient_shell_image_tag(self):
        result = self.release(VERSION, ambient_tag="old-ambient-tag")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_image_in_use_elsewhere_does_not_fail_successful_deploy(self):
        result = self.release(VERSION, fail_on="image rm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("backend='1.0.0'", (self.root / "release.toml").read_text())

    def test_invalid_tag_is_rejected_before_docker_runs(self):
        for tags in (("release",), ("latest",), ("v1.0.0",), ("01.0.0",),
                     ("1.0",), ("1.0.0;echo bad",), ("1.0.0", "unknown")):
            with self.subTest(tags=tags):
                self.assertNotEqual(self.release(*tags).returncode, 0)
        self.assertFalse((self.root / "calls").exists())

    def test_missing_release_file_is_rejected_before_docker_runs(self):
        (self.root / "release.toml").unlink()
        self.assertNotEqual(self.release().returncode, 0)
        self.assertFalse((self.root / "calls").exists())

    def test_cli_cannot_override_release_file(self):
        result = subprocess.run(
            ["bash", str(self.root / "scripts/deploy-release.sh"), "2.0.0"],
            text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set image versions in release.toml", result.stderr)
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
                         "compose --env-file .env.docker config --quiet")


if __name__ == "__main__":
    unittest.main()
