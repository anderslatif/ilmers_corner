"""Tests for tag parsing, ordering, and advisory range matching.

These are the parts that decide which SHA a user ends up pinning, so they are
tested offline with no GitHub access.
"""

from __future__ import annotations

import pytest

from ilmers_corner.github import Tag
from ilmers_corner.versions import build_versions, in_range, parse_tag


def make_version(tag_name: str):
    return parse_tag(Tag(name=tag_name, commit_sha="a" * 40))


class TestParseTag:
    @pytest.mark.parametrize(
        "tag_name,major,minor,patch",
        [
            ("v1.2.3", 1, 2, 3),
            ("1.2.3", 1, 2, 3),
            ("v4", 4, None, None),
            ("v4.2", 4, 2, None),
            ("v10.0.0", 10, 0, 0),
        ],
    )
    def test_parses_numeric_components(self, tag_name, major, minor, patch):
        version = make_version(tag_name)
        assert (version.major, version.minor, version.patch) == (major, minor, patch)

    @pytest.mark.parametrize(
        "tag_name", ["v2.0.0-rc1", "v1.0.0-beta.2", "v3.1.0-alpha"]
    )
    def test_detects_prereleases(self, tag_name):
        assert make_version(tag_name).is_prerelease

    @pytest.mark.parametrize("tag_name", ["latest", "stable", "nightly-build"])
    def test_non_semantic_tags_are_flagged(self, tag_name):
        assert not make_version(tag_name).is_semantic

    def test_major_alias_detected(self):
        # `v4` moves as new v4.x.y ship; `v4.0.0` does not.
        assert make_version("v4").is_major_alias
        assert not make_version("v4.0.0").is_major_alias


class TestOrdering:
    def test_v10_sorts_above_v9(self):
        tags = [Tag("v9.0.0", "a" * 40), Tag("v10.0.0", "b" * 40)]
        assert [v.tag for v in build_versions(tags)] == ["v10.0.0", "v9.0.0"]

    def test_prereleases_excluded_by_default(self):
        tags = [Tag("v1.0.0", "a" * 40), Tag("v2.0.0-rc1", "b" * 40)]
        assert [v.tag for v in build_versions(tags)] == ["v1.0.0"]
        included = build_versions(tags, include_prereleases=True)
        assert "v2.0.0-rc1" in [v.tag for v in included]

    def test_prerelease_sorts_below_its_release(self):
        tags = [Tag("v2.0.0", "a" * 40), Tag("v2.0.0-rc1", "b" * 40)]
        ordered = build_versions(tags, include_prereleases=True)
        assert [v.tag for v in ordered] == ["v2.0.0", "v2.0.0-rc1"]

    def test_major_aliases_excluded_by_default(self):
        tags = [Tag("v4", "a" * 40), Tag("v4.1.0", "b" * 40)]
        assert [v.tag for v in build_versions(tags)] == ["v4.1.0"]
        included = build_versions(tags, include_major_aliases=True)
        assert "v4" in [v.tag for v in included]

    def test_non_semantic_tags_sort_last(self):
        tags = [Tag("nightly", "a" * 40), Tag("v1.0.0", "b" * 40)]
        assert [v.tag for v in build_versions(tags)][0] == "v1.0.0"


class TestAdvisoryRanges:
    @pytest.mark.parametrize(
        "tag_name,vulnerable_range,expected",
        [
            ("v1.5.0", ">= 1.0.0, < 1.6.0", True),
            ("v1.6.0", ">= 1.0.0, < 1.6.0", False),
            ("v0.9.0", ">= 1.0.0, < 1.6.0", False),
            ("v2.0.0", "<= 2.0.0", True),
            ("v2.0.1", "<= 2.0.0", False),
            ("v1.0.0", "= 1.0.0", True),
            ("v1.0.1", "= 1.0.0", False),
            ("v3.2.1", ">= 3.0.0", True),
        ],
    )
    def test_range_membership(self, tag_name, vulnerable_range, expected):
        assert in_range(make_version(tag_name), vulnerable_range) is expected

    def test_matches_real_tj_actions_advisory(self):
        """GHSA-mrrh-fwg8-r2c3 covered `<= 45.0.7`, patched in 46.0.1."""
        vulnerable_range = "<= 45.0.7"
        assert in_range(make_version("v45.0.7"), vulnerable_range)
        assert in_range(make_version("v45.0.0"), vulnerable_range)
        assert not in_range(make_version("v46.0.1"), vulnerable_range)

    def test_bare_major_bound(self):
        # GHSA-mcph-m25j-8j63 used the bound `< 41` with no minor or patch.
        assert in_range(make_version("v40.9.9"), "< 41")
        assert not in_range(make_version("v41.0.0"), "< 41")

    def test_unparseable_range_warns_rather_than_clearing(self):
        # Failing open would silently mark a vulnerable version as clean.
        assert in_range(make_version("v1.2.3"), "not-a-range")

    def test_non_semantic_version_never_matches(self):
        assert not in_range(make_version("nightly"), "<= 45.0.7")
