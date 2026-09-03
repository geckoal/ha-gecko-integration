"""Runtime compatibility checks for the AWS Common Runtime.

The AWS IoT SDK uses a native extension.  Calling an incompatible build can
terminate the entire Home Assistant process, so validate the loaded runtime
before Gecko constructs an MQTT client.
"""

from __future__ import annotations

import platform
import re
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

MINIMUM_AWSCRT_VERSION = (0, 36, 1)
REQUIRED_AWSCRT_VERSION = "0.36.1"
UNSAFE_PYTHON_VERSION = (3, 14)
ARM64_MACHINES = frozenset({"aarch64", "arm64"})


class AwsCrtCompatibilityError(OSError):
    """Raised before entering an unsafe or inconsistent AWS CRT runtime."""


def _version_tuple(value: str) -> tuple[int, int, int]:
    """Return a comparable three-part tuple for a package version."""
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", value)
    if match is None:
        return (0, 0, 0)
    return tuple(int(part or 0) for part in match.groups())


def runtime_compatibility_error(
    *,
    installed_version: str,
    python_version: tuple[int, ...],
    machine: str,
    tls_context_type: type[Any],
) -> str | None:
    """Describe an unsafe AWS CRT runtime, or return ``None`` when usable."""
    # The native crash this guard protects against is specific to Python 3.14+
    # on ARM64. Older CRT releases were the integration's working runtime on
    # other platforms, so do not turn a platform-specific safety check into a
    # global startup requirement.
    if (
        python_version[:2] < UNSAFE_PYTHON_VERSION
        or machine.casefold() not in ARM64_MACHINES
    ):
        return None

    if _version_tuple(installed_version) < MINIMUM_AWSCRT_VERSION:
        environment = f"Python {python_version[0]}.{python_version[1]} on {machine}"
        return (
            f"awscrt {installed_version} is unsafe for Gecko on {environment}; "
            f"awscrt {REQUIRED_AWSCRT_VERSION} or newer is required. "
            "Home Assistant was not allowed to start the native MQTT client."
        )

    # awscrt 0.36.x added this slot together with the IoT metrics path used by
    # awsiotsdk 1.31.x. Its absence while package metadata reports a newer
    # version means modules from different installations are resident in the
    # same Python process. Only reject that state where entering the native
    # client is known to be unsafe.
    if not hasattr(tls_context_type, "_certificate_source"):
        return (
            "The loaded AWS CRT modules do not match the installed package. "
            "Perform a full Home Assistant restart before reloading Gecko."
        )

    return None


def ensure_aws_crt_compatible() -> None:
    """Refuse to enter the native MQTT client with an incompatible CRT."""
    try:
        installed_version = version("awscrt")
    except PackageNotFoundError as err:
        raise AwsCrtCompatibilityError("The awscrt package is not installed") from err

    try:
        from awscrt.io import ClientTlsContext
    except (ImportError, AttributeError) as err:
        raise AwsCrtCompatibilityError(
            "The AWS CRT Python modules could not be loaded consistently"
        ) from err

    problem = runtime_compatibility_error(
        installed_version=installed_version,
        python_version=sys.version_info[:3],
        machine=platform.machine(),
        tls_context_type=ClientTlsContext,
    )
    if problem is not None:
        raise AwsCrtCompatibilityError(problem)
