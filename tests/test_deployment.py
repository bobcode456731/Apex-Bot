from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentLayoutTests(unittest.TestCase):
    def test_repository_root_has_launcher_dependencies_and_package(self):
        self.assertTrue((ROOT / "main.py").is_file())
        self.assertTrue((ROOT / "requirements.txt").is_file())
        self.assertTrue((ROOT / "apexbot" / "__init__.py").is_file())
        self.assertFalse((ROOT / "apexbot" / "requirements.txt").exists())

        requirements = {
            line.strip()
            for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn("discord.py>=2.5,<3.0", requirements)
        self.assertIn("python-dotenv>=1.0,<2.0", requirements)

    def test_root_launcher_reports_missing_token_before_discord_import(self):
        environment = os.environ.copy()
        environment["DISCORD_TOKEN"] = ""
        environment["PYTHON_DOTENV_DISABLED"] = "1"
        environment.pop("PYTHONPATH", None)

        result = subprocess.run(
            [sys.executable, str(ROOT / "main.py")],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("DISCORD_TOKEN is missing", result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_docker_build_context_uses_repository_root_entry_and_dependencies(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY requirements.txt", dockerfile)
        self.assertIn('COPY --chown=botuser:botuser main.py ./main.py', dockerfile)
        self.assertIn('CMD ["python", "main.py"]', dockerfile)


if __name__ == "__main__":
    unittest.main()
