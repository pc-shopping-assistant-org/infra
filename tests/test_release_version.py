"""Execute the workflow's actual branch-to-image-version resolution offline."""

import os
from pathlib import Path
import subprocess
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


class ReleaseVersion(unittest.TestCase):
    def resolve(self, branch, **overrides):
        workflow = WORKFLOW.read_text()
        script = workflow.split("        run: |\n")[-1].split("          latest_sha=", 1)[0]
        script = "\n".join(line.removeprefix("          ") for line in script.splitlines())
        script += '\nprintf "%s\\n" "$BACKEND_IMAGE_TAG" "$AI_IMAGE_TAG" "$WEB_IMAGE_TAG"\n'
        return subprocess.run(
            ["bash", "-c", script], text=True, capture_output=True, timeout=5,
            env={**os.environ, "GITHUB_REF_NAME": branch, "BACKEND_IMAGE_TAG": "",
                 "AI_IMAGE_TAG": "", "WEB_IMAGE_TAG": "", **overrides},
        )

    def test_branch_version_selects_the_same_version_for_all_applications(self):
        for version in ("1.0.0", "2.10.3", "1.1.0-rc.1"):
            with self.subTest(version=version):
                result = self.resolve(f"release/v{version}")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), [version] * 3)

    def test_dispatch_can_override_one_application_without_changing_the_others(self):
        result = self.resolve("release/v1.0.0", AI_IMAGE_TAG="1.0.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["1.0.0", "1.0.1", "1.0.0"])

    def test_non_version_and_invalid_release_branches_fail_before_any_external_call(self):
        for branch in ("main", "release", "release/1.0.0", "release/vlatest",
                       "release/v01.0.0", "release/v1.0.0-rc.0", "release/v1.0.0;echo bad"):
            with self.subTest(branch=branch):
                result = self.resolve(branch)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
