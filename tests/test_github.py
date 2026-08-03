"""Tests for response parsing, caching, and the `uses:` output format.

No network access: `_request` is stubbed so the parsing logic can be checked
without spending rate limit.
"""

from __future__ import annotations

import time

import pytest

from ilmers_corner.cache import ResponseCache
from ilmers_corner.cli import _format_uses
from ilmers_corner.github import GitHubClient, Repository
from ilmers_corner.versions import parse_tag
from ilmers_corner.github import Tag


class StubClient(GitHubClient):
    """A client whose responses come from a dict instead of GitHub."""

    def __init__(self, responses):
        super().__init__(token=None, cache=None)
        self.responses = responses
        self.requested = []

    def _request(self, path, *, allow_missing=False, immutable=False):
        self.requested.append(path)
        for fragment, payload in self.responses.items():
            if fragment in path:
                return payload
        return None


class TestTagParsing:
    def test_uses_dereferenced_commit_sha(self):
        """`/tags` returns the commit even for annotated tags.

        Reading the tag object's own SHA instead would produce a ref that
        GitHub Actions cannot resolve.
        """
        client = StubClient(
            {"/tags": [{"name": "v1.0.0", "commit": {"sha": "c" * 40}}]}
        )
        tags = client.tags("owner", "repo")
        assert tags[0].commit_sha == "c" * 40


class TestCommitDetails:
    def test_extracts_date_and_signature(self):
        client = StubClient(
            {
                "/commits/": {
                    "commit": {
                        "committer": {"date": "2024-10-23T12:00:00Z"},
                        "verification": {"verified": True, "reason": "valid"},
                    }
                }
            }
        )
        details = client.commit_details("owner", "repo", "d" * 40)
        assert details.date == "2024-10-23"
        assert details.signed is True

    def test_unsigned_commit(self):
        client = StubClient(
            {
                "/commits/": {
                    "commit": {
                        "committer": {"date": "2024-01-01T00:00:00Z"},
                        "verification": {"verified": False, "reason": "unsigned"},
                    }
                }
            }
        )
        details = client.commit_details("owner", "repo", "d" * 40)
        assert details.signed is False

    def test_missing_commit_returns_empty_details(self):
        client = StubClient({})
        details = client.commit_details("owner", "repo", "d" * 40)
        assert details.date is None and details.signed is None


class TestAdvisories:
    def test_keeps_only_ranges_naming_this_action(self):
        """An advisory can list several packages; other packages must not leak."""
        client = StubClient(
            {
                "/advisories": [
                    {
                        "ghsa_id": "GHSA-test",
                        "severity": "high",
                        "summary": "example",
                        "html_url": "https://example.test",
                        "vulnerabilities": [
                            {
                                "package": {"name": "owner/repo"},
                                "vulnerable_version_range": "<= 1.0.0",
                                "first_patched_version": "1.0.1",
                            },
                            {
                                "package": {"name": "other/thing"},
                                "vulnerable_version_range": "<= 9.9.9",
                                "first_patched_version": "10.0.0",
                            },
                        ],
                    }
                ]
            }
        )
        advisories = client.advisories("owner", "repo")
        assert len(advisories) == 1
        assert advisories[0].ranges == [("<= 1.0.0", "1.0.1")]

    def test_no_advisories_returns_empty(self):
        assert StubClient({"/advisories": []}).advisories("owner", "repo") == []


class TestCache:
    def test_fresh_entry_is_reused(self, tmp_path):
        cache = ResponseCache(directory=tmp_path)
        cache.set("https://example.test/a", "etag-1", {"value": 1})
        entry = cache.get("https://example.test/a")
        assert entry.payload == {"value": 1} and entry.is_fresh

    def test_stale_entry_keeps_etag_but_is_not_fresh(self, tmp_path):
        """Past the freshness window an entry still revalidates via its ETag."""
        import json

        from ilmers_corner import cache as cache_module

        cache = ResponseCache(directory=tmp_path)
        url = "https://example.test/b"
        cache.set(url, "etag-2", {"value": 2})

        # Backdate the record beyond FRESH_SECONDS but within MAX_AGE_SECONDS.
        path = cache._path_for(url)
        record = json.loads(path.read_text())
        record["stored_at"] = time.time() - (cache_module.FRESH_SECONDS + 60)
        path.write_text(json.dumps(record))

        entry = cache.get(url)
        assert entry is not None
        assert entry.etag == "etag-2"
        assert not entry.is_fresh

    def test_entry_past_retention_is_dropped(self, tmp_path):
        import json

        from ilmers_corner import cache as cache_module

        cache = ResponseCache(directory=tmp_path)
        url = "https://example.test/old"
        cache.set(url, "etag-old", {"value": 0})

        path = cache._path_for(url)
        record = json.loads(path.read_text())
        record["stored_at"] = time.time() - (cache_module.MAX_AGE_SECONDS + 60)
        path.write_text(json.dumps(record))

        assert cache.get(url) is None

    def test_immutable_entry_stays_fresh(self, tmp_path):
        cache = ResponseCache(directory=tmp_path)
        cache.set("https://example.test/c", "etag-3", {"value": 3}, immutable=True)
        assert cache.get("https://example.test/c").is_fresh

    def test_missing_entry(self, tmp_path):
        assert ResponseCache(directory=tmp_path).get("https://example.test/x") is None

    def test_corrupt_entry_is_ignored(self, tmp_path):
        cache = ResponseCache(directory=tmp_path)
        url = "https://example.test/d"
        cache.set(url, "etag-4", {"value": 4})
        cache._path_for(url).write_text("{ not json")
        assert cache.get(url) is None

    def test_clear_removes_entries(self, tmp_path):
        cache = ResponseCache(directory=tmp_path)
        cache.set("https://example.test/e", "etag-5", {"value": 5})
        assert cache.clear() == 1


class TestOutputFormat:
    def test_uses_line_shape(self):
        repository = Repository(owner="actions", name="checkout", description="", stars=0)
        version = parse_tag(Tag(name="v4.2.2", commit_sha="1" * 40))
        version.date = "2024-10-23"
        assert _format_uses(repository, version) == (
            f"- uses: actions/checkout@{'1' * 40}  # v4.2.2  2024-10-23"
        )

    def test_full_sha_is_used_not_truncated(self):
        """A short SHA is not a valid pin, so the full 40 chars must survive."""
        repository = Repository(owner="o", name="r", description="", stars=0)
        version = parse_tag(Tag(name="v1.0.0", commit_sha="2" * 40))
        version.date = "2024-01-01"
        assert "2" * 40 in _format_uses(repository, version)
