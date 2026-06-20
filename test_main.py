from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main


class LauncherConfigTests(unittest.TestCase):
    def test_init_creates_user_editable_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"WEB_PROXY_HOME": temp_dir}, clear=False):
                paths = main.runtime_paths()
                config = main.ensure_runtime_files(paths, force=False)

                self.assertEqual(config.port, main.DEFAULT_PORT)
                self.assertTrue(paths.config.exists())
                self.assertTrue(paths.rules.exists())
                self.assertTrue(paths.logs.exists())
                self.assertEqual(config.rules_file, paths.rules)
                self.assertEqual(config.log_dir, paths.logs)

    def test_environment_overrides_config_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            config_path = home / "config.json"
            custom_rules = home / "custom-rules.json"
            custom_logs = home / "custom-logs"
            home.mkdir(exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "port": 8080,
                        "log_dir": str(home / "logs"),
                        "rules_file": str(home / "rules.json"),
                        "max_body": 12,
                        "block_global": False,
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "WEB_PROXY_HOME": temp_dir,
                "PORT": "9090",
                "WEB_PROXY_LOG_DIR": str(custom_logs),
                "WEB_PROXY_RULES_FILE": str(custom_rules),
                "WEB_PROXY_MAX_BODY": "99",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                config = main.load_config(main.runtime_paths())

        self.assertEqual(config.port, 9090)
        self.assertEqual(config.log_dir, custom_logs)
        self.assertEqual(config.rules_file, custom_rules)
        self.assertEqual(config.max_body, 99)


if __name__ == "__main__":
    unittest.main()
