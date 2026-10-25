"""The imported library and distribution must report the same version."""
from pathlib import Path
import tomllib
import unittest
import memory_bridge


class PackageTests(unittest.TestCase):
    def test_version_matches_distribution(self):
        root = Path(__file__).resolve().parents[1]
        with (root / 'pyproject.toml').open('rb') as source:
            self.assertEqual(memory_bridge.__version__, tomllib.load(source)['project']['version'])
