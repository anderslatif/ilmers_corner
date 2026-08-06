"""Browse GitHub Action versions and generate hash-pinned `uses:` lines."""

from importlib.metadata import PackageNotFoundError, version as _installed_version

try:
    # pyproject.toml is the single source of truth for the version. Reading it
    # back from the installed metadata keeps this from drifting out of sync.
    __version__ = _installed_version("ilmers_corner")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"
