"""Version parsing and ordering for tag names."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .github import Tag

# Matches v1, v1.2, v1.2.3, 1.2.3-beta.1, v2.0.0-rc1 and similar.
VERSION_PATTERN = re.compile(
    r"""^v?
    (?P<major>\d+)
    (?:\.(?P<minor>\d+))?
    (?:\.(?P<patch>\d+))?
    (?:[-+.](?P<prerelease>.+))?
    $""",
    re.VERBOSE,
)


@dataclass
class Version:
    tag: str
    commit_sha: str
    major: Optional[int] = None
    minor: Optional[int] = None
    patch: Optional[int] = None
    prerelease: Optional[str] = None
    date: Optional[str] = field(default=None, compare=False)
    signed: Optional[bool] = field(default=None, compare=False)
    advisories: list = field(default_factory=list, compare=False)

    @property
    def is_semantic(self) -> bool:
        return self.major is not None

    @property
    def is_prerelease(self) -> bool:
        return self.prerelease is not None

    @property
    def is_major_alias(self) -> bool:
        """True for floating tags like `v4` that track the latest v4.x.y.

        These move, so pinning one is exactly the practice this tool exists to
        replace — worth marking in the UI.
        """
        return self.is_semantic and self.minor is None and self.patch is None

    @property
    def sort_key(self) -> tuple:
        # Non-semantic tags sort last; a prerelease sorts below its release.
        if not self.is_semantic:
            return (0, 0, 0, 0, 0, self.tag)
        return (
            1,
            self.major or 0,
            self.minor if self.minor is not None else -1,
            self.patch if self.patch is not None else -1,
            0 if self.is_prerelease else 1,
            self.prerelease or "",
        )


def parse_tag(tag: Tag) -> Version:
    match = VERSION_PATTERN.match(tag.name.strip())
    if not match:
        return Version(tag=tag.name, commit_sha=tag.commit_sha)
    groups = match.groupdict()
    return Version(
        tag=tag.name,
        commit_sha=tag.commit_sha,
        major=int(groups["major"]),
        minor=int(groups["minor"]) if groups["minor"] is not None else None,
        patch=int(groups["patch"]) if groups["patch"] is not None else None,
        prerelease=groups["prerelease"],
    )


def _comparable(text: str) -> Optional[tuple]:
    """Numeric tuple for a version string, for range comparisons."""
    match = VERSION_PATTERN.match(text.strip())
    if not match:
        return None
    groups = match.groupdict()
    return (
        int(groups["major"]),
        int(groups["minor"] or 0),
        int(groups["patch"] or 0),
    )


def in_range(version: Version, vulnerable_range: str) -> bool:
    """Whether a version falls inside an advisory's vulnerable range.

    Ranges look like `>= 1.0.0, < 1.2.3` or `<= 2.0.0`. Anything we cannot
    parse is treated as a match, so an unparseable range warns rather than
    silently clearing a version.
    """
    if not version.is_semantic:
        return False
    subject = (version.major or 0, version.minor or 0, version.patch or 0)

    for clause in vulnerable_range.split(","):
        clause = clause.strip()
        if not clause:
            continue
        match = re.match(r"^(>=|<=|>|<|=)?\s*v?(.+)$", clause)
        if not match:
            return True
        operator, raw = match.group(1) or "=", match.group(2)
        bound = _comparable(raw)
        if bound is None:
            return True
        if operator == ">=" and not subject >= bound:
            return False
        if operator == ">" and not subject > bound:
            return False
        if operator == "<=" and not subject <= bound:
            return False
        if operator == "<" and not subject < bound:
            return False
        if operator == "=" and subject != bound:
            return False
    return True


def build_versions(
    tags: list[Tag],
    *,
    include_prereleases: bool = False,
    include_major_aliases: bool = False,
) -> list[Version]:
    """Parse and order tags newest-first, filtering by the given policy."""
    versions = [parse_tag(tag) for tag in tags]
    if not include_prereleases:
        versions = [version for version in versions if not version.is_prerelease]
    if not include_major_aliases:
        versions = [version for version in versions if not version.is_major_alias]
    versions.sort(key=lambda version: version.sort_key, reverse=True)
    return versions
