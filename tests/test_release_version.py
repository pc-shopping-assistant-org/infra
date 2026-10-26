"""The deploy manifest must pin exactly one valid version per application."""

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/release_images.py"
PROPOSE = ROOT / "scripts/propose_image.py"


class ReleaseVersion(unittest.TestCase):
    def read(self, content: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "images.lock"
            path.write_text(content)
            return subprocess.run(
                ["python3", str(SCRIPT), str(path)],
                text=True,
                capture_output=True,
                timeout=5,
            )

    def test_independent_image_versions_are_preserved(self):
        result = self.read("BACKEND_IMAGE_TAG=1.0.0\nAI_IMAGE_TAG=1.0.1\nWEB_IMAGE_TAG=sha-" + "a" * 40 + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "AI_IMAGE_TAG=1.0.1", "BACKEND_IMAGE_TAG=1.0.0", "WEB_IMAGE_TAG=sha-" + "a" * 40,
        ])

    def test_missing_duplicate_unknown_or_invalid_pin_fails(self):
        valid = "BACKEND_IMAGE_TAG=1.0.0\nAI_IMAGE_TAG=1.0.1\nWEB_IMAGE_TAG=1.0.0\n"
        for content in (
            valid.replace("AI_IMAGE_TAG=1.0.1\n", ""),
            valid + "AI_IMAGE_TAG=1.0.2\n",
            valid + "OTHER_TAG=1.0.0\n",
            valid.replace("1.0.1", "latest"),
            valid.replace("1.0.1", "1.0.1;echo bad"),
        ):
            with self.subTest(content=content):
                self.assertNotEqual(self.read(content).returncode, 0)

    def test_repository_manifest_is_valid(self):
        result = subprocess.run(["python3", str(SCRIPT)], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_proposal_updates_only_selected_app(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "release").mkdir()
            manifest = root / "release/images.lock"
            manifest.write_text("BACKEND_IMAGE_TAG=1.0.0\nAI_IMAGE_TAG=1.0.0\nWEB_IMAGE_TAG=1.0.0\n")
            output = root / "output"
            result = subprocess.run(
                ["python3", str(PROPOSE), "ai", "release/v1.0.1", str(output)],
                cwd=root, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(manifest.read_text(),
                             "BACKEND_IMAGE_TAG=1.0.0\nAI_IMAGE_TAG=1.0.1\nWEB_IMAGE_TAG=1.0.0\n")
            self.assertIn("changed=true", output.read_text())
            second = subprocess.run(
                ["python3", str(PROPOSE), "ai", "release/v1.0.1", str(output)],
                cwd=root, text=True, capture_output=True,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("changed=false", output.read_text())


if __name__ == "__main__":
    unittest.main()
