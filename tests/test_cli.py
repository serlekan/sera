from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class SeraCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        (self.root / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
        self.env = os.environ.copy()
        package_root = Path(__file__).resolve().parents[1] / "src"
        self.env["PYTHONPATH"] = str(package_root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-m", "sera", *args],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, expected, msg=result.stderr + result.stdout)
        return result

    def test_end_to_end_packet_generation(self) -> None:
        self.run_cli("init")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "baseline"], cwd=self.root, check=True, capture_output=True)
        self.run_cli("map")
        result = self.run_cli(
            "task",
            "new",
            "change return",
            "--objective",
            "Return one",
            "--mode",
            "fast",
            "--risk",
            "low",
            "--uncertainty",
            "0",
            "--file",
            "app.py",
        )
        self.assertIn(".sera/tasks/", result.stdout)
        packet = self.run_cli("packet", "build")
        self.assertIn("estimated packet size", packet.stdout)
        packet_files = list((self.root / ".sera" / "tasks").glob("*/packet-build.md"))
        self.assertEqual(len(packet_files), 1)
        self.assertIn("Exact ownership", packet_files[0].read_text())


if __name__ == "__main__":
    unittest.main()
