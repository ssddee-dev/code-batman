from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = ROOT / "install.sh"


class InstallScriptTests(unittest.TestCase):
    def test_installer_has_valid_bash_syntax(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(INSTALL_SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_installer_covers_bootstrap_contract(self) -> None:
        source = INSTALL_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("sys.version_info >= (3, 11)", source)
        self.assertIn("git clone --depth 1", source)
        self.assertIn(' -m venv "${INSTALL_DIR}/.venv"', source)
        self.assertIn("-m pip install", source)
        self.assertIn("-m watchman.setup", source)


if __name__ == "__main__":
    unittest.main()
