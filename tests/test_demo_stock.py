from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


class DemoStockCommand(unittest.TestCase):
    def test_stock_reset_is_an_explicit_command_without_starting_other_services(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            (fixture / "scripts").mkdir()
            script = fixture / "scripts/seed-demo-stock.sh"
            shutil.copyfile(root / "scripts/seed-demo-stock.sh", script)
            compose = fixture / "compose.sh"
            compose.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$STOCK_ARGS_FILE"\n')
            compose.chmod(0o755)
            args = fixture / "arguments"
            result = subprocess.run(
                ["bash", str(script)], cwd="/tmp",
                env={"PATH": os.environ["PATH"], "STOCK_ARGS_FILE": str(args)},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(args.read_text().splitlines(), [
                "--profile", "setup", "run", "--rm", "--no-deps", "catalog-seed", "--seed-demo-stock",
            ])
