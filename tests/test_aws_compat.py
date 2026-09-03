"""Regression tests for the native AWS CRT startup guard."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _load_aws_compat_module():
    """Load the dependency-free compatibility helpers."""
    path = ROOT / "custom_components" / "gecko" / "aws_compat.py"
    spec = importlib.util.spec_from_file_location("gecko_aws_compat_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


aws_compat = _load_aws_compat_module()


class _LegacyTlsContext:
    """Represent the class shape loaded from awscrt 0.32.x."""


class _CurrentTlsContext:
    """Represent the class shape loaded from awscrt 0.36.x."""

    __slots__ = ("_certificate_source",)


class AwsCompatibilityTests(unittest.TestCase):
    """Verify unsafe native runtimes are rejected before MQTT construction."""

    def test_python_314_arm64_rejects_crashing_legacy_runtime(self) -> None:
        problem = aws_compat.runtime_compatibility_error(
            installed_version="0.32.1",
            python_version=(3, 14, 0),
            machine="aarch64",
            tls_context_type=_LegacyTlsContext,
        )

        self.assertIn("unsafe", problem)
        self.assertIn("0.36.1", problem)

    def test_current_matched_runtime_is_accepted(self) -> None:
        problem = aws_compat.runtime_compatibility_error(
            installed_version="0.36.1",
            python_version=(3, 14, 0),
            machine="aarch64",
            tls_context_type=_CurrentTlsContext,
        )

        self.assertIsNone(problem)

    def test_partially_upgraded_runtime_requires_full_restart_on_arm64(self) -> None:
        problem = aws_compat.runtime_compatibility_error(
            installed_version="0.36.1",
            python_version=(3, 14, 0),
            machine="aarch64",
            tls_context_type=_LegacyTlsContext,
        )

        self.assertIn("full Home Assistant restart", problem)

    def test_legacy_runtime_remains_usable_before_python_314(self) -> None:
        problem = aws_compat.runtime_compatibility_error(
            installed_version="0.32.1",
            python_version=(3, 13, 9),
            machine="aarch64",
            tls_context_type=_LegacyTlsContext,
        )

        self.assertIsNone(problem)

    def test_legacy_runtime_remains_usable_on_x86_64(self) -> None:
        problem = aws_compat.runtime_compatibility_error(
            installed_version="0.32.1",
            python_version=(3, 14, 0),
            machine="x86_64",
            tls_context_type=_LegacyTlsContext,
        )

        self.assertIsNone(problem)

    def test_arm64_alias_is_guarded(self) -> None:
        problem = aws_compat.runtime_compatibility_error(
            installed_version="0.32.1",
            python_version=(3, 14, 0),
            machine="arm64",
            tls_context_type=_LegacyTlsContext,
        )

        self.assertIn("unsafe", problem)

    def test_manifest_pins_a_matched_aws_runtime(self) -> None:
        manifest = json.loads(
            (ROOT / "custom_components" / "gecko" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(manifest["version"], "2.1.3")
        self.assertIn("awscrt==0.36.1", manifest["requirements"])
        self.assertIn("awsiotsdk==1.31.0", manifest["requirements"])


if __name__ == "__main__":
    unittest.main()
