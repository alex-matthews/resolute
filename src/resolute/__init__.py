"""resolute: Seerr-first TV resolution policy engine."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("resolute")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.1.0"
