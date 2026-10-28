"""Validate donor CLI configuration without spawning a server."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bridge import main


class LauncherTests(unittest.TestCase):
    def test_keygen_entropy_failure_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new.psk"
            with patch("sys.argv", ["bridge.py", "keygen", str(path)]), \
                 patch("bridge.secrets.token_hex", side_effect=OSError("entropy unavailable")):
                with self.assertRaises(OSError):
                    main()
            self.assertFalse(path.exists())

    def test_memlock_failure_precedes_key_access(self):
        # The limit only binds without CAP_IPC_LOCK; pin that so a root runner
        # exercises the same refusal path instead of launching nbdkit.
        with patch("sys.argv", ["bridge.py", "donor", "--key", "unused", "--size-mib", "1"]), \
             patch("resource.getrlimit", return_value=(4096, 4096)), \
             patch("memory_bridge.launcher.memlock_limit_applies", return_value=True), \
             patch("bridge.private_key") as key, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main()
            key.assert_not_called()

    def test_invalid_nbd_config_precedes_key_access(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "bad.toml"
            config.write_text('[memory]\nmax_request_bytes=6000\n')
            with patch("sys.argv", ["bridge.py", "donor", "--key", "unused", "--config", str(config)]), \
                 patch("bridge.private_key") as key, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main()
                key.assert_not_called()
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
