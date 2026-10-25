"""Validate donor CLI configuration without spawning a server."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bridge import main


class LauncherTests(unittest.TestCase):
    def test_configuration_is_forwarded_and_default_size_respects_quota(self):
        with patch("sys.argv", ["bridge.py", "donor", "--key", "unused",
                                 "--config", "config/local.toml"]), \
             patch("bridge.private_key", return_value=Path("/tmp/unused.psk")), \
             patch("bridge.shutil.which", return_value="/usr/bin/nbdkit"), \
             patch("bridge.os.execv") as execute, redirect_stdout(io.StringIO()):
            main()
        arguments = execute.call_args.args[1]
        self.assertIn("size_mib=2", arguments)
        self.assertIn("--tls=require", arguments)
        self.assertIn("config_file=" + str(Path("config/local.toml").resolve()), arguments)

    def test_oversized_export_fails_before_reading_key(self):
        with patch("sys.argv", ["bridge.py", "donor", "--key", "unused",
                                 "--config", "config/local.toml", "--size-mib", "3"]), \
             patch("bridge.private_key") as key, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main()
            key.assert_not_called()
