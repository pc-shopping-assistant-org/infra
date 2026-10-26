"""The server release env must select exactly one valid version per application."""

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/release_images.py"


class ReleaseVersion(unittest.TestCase):
    def read(self, content: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".release.env"
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

    def test_release_env_example_is_valid(self):
        result = subprocess.run(
            ["python3", str(SCRIPT), ".release.env.example"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
