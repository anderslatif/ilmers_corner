"""Minimal GitHub REST API client built on the standard library.

Deliberately dependency-free: `urllib` is enough for the handful of endpoints
this tool needs, and it keeps installation from ever failing on dependency
resolution.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from . import __version__
from .cache import ResponseCache

API_ROOT = "https://api.github.com"
USER_AGENT = f"ilmers_corner/{__version__}"

# Files that mark a repository as a GitHub Action, in the order GitHub itself
# resolves them.
ACTION_MANIFESTS = ("action.yml", "action.yaml")


class GitHubError(Exception):
    """A GitHub request failed in a way the user needs to know about."""


class RateLimitError(GitHubError):
    """The rate limit is exhausted; carries the reset time so we can advise."""

    def __init__(self, message: str, reset_at: Optional[int], resource: str) -> None:
        super().__init__(message)
        self.reset_at = reset_at
        self.resource = resource

    def advice(self) -> str:
        parts = []
        if self.reset_at:
            seconds_remaining = max(0, self.reset_at - int(time.time()))
            minutes = seconds_remaining // 60 + 1
            parts.append(f"The {self.resource} limit resets in about {minutes} min.")
        if not os.environ.get("GITHUB_TOKEN"):
            parts.append(
                "Set GITHUB_TOKEN to raise the limit from 60 to 5000 requests/hour."
            )
        return " ".join(parts)


@dataclass
class Repository:
    owner: str
    name: str
    description: str
    stars: int

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass
class Tag:
    name: str
    commit_sha: str


@dataclass
class Advisory:
    ghsa_id: str
    severity: str
    summary: str
    url: str
    # (vulnerable_version_range, first_patched_version) pairs for this action.
    ranges: list = field(default_factory=list)


@dataclass
class CommitDetails:
    """What a single commit lookup tells us about a tagged version."""

    date: Optional[str] = None
    signed: Optional[bool] = None
    signature_reason: Optional[str] = None


class GitHubClient:
    def __init__(self, token: Optional[str] = None, cache: Optional[ResponseCache] = None):
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.cache = cache if cache is not None else ResponseCache()

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    def _request(
        self, path: str, *, allow_missing: bool = False, immutable: bool = False
    ) -> Optional[Any]:
        """GET a path, returning decoded JSON.

        A still-fresh cache entry is returned without touching the network,
        since an unauthenticated 304 would otherwise consume rate-limit quota
        just like a 200.
        """
        url = path if path.startswith("http") else f"{API_ROOT}{path}"

        cached = self.cache.get(url)
        if cached is not None and cached.is_fresh:
            return cached.payload

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if cached is not None and cached.etag:
            headers["If-None-Match"] = cached.etag

        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read()
                etag = response.headers.get("ETag")
                payload = json.loads(body) if body else None
                if etag:
                    self.cache.set(url, etag, payload, immutable=immutable)
                return payload
        except urllib.error.HTTPError as error:
            if error.code == 304 and cached is not None:
                # Restamp so the entry counts as fresh again and the next run
                # can skip the network entirely.
                self.cache.set(url, cached.etag, cached.payload, immutable=immutable)
                return cached.payload
            # 422 comes back for a malformed ref, which for our purposes is the
            # same as "no such commit".
            if error.code in (404, 422):
                if allow_missing:
                    return None
                raise GitHubError(f"Not found: {url}") from error
            if error.code in (403, 429):
                remaining = error.headers.get("x-ratelimit-remaining")
                if remaining == "0":
                    reset = error.headers.get("x-ratelimit-reset")
                    raise RateLimitError(
                        "GitHub API rate limit exceeded.",
                        int(reset) if reset and reset.isdigit() else None,
                        error.headers.get("x-ratelimit-resource", "core"),
                    ) from error
                raise GitHubError(f"GitHub refused the request ({error.code}).") from error
            raise GitHubError(f"GitHub request failed ({error.code}): {url}") from error
        except urllib.error.URLError as error:
            raise GitHubError(f"Could not reach GitHub: {error.reason}") from error

    # ------------------------------------------------------------------
    # endpoints
    # ------------------------------------------------------------------

    def repository_exists(self, owner: str, name: str) -> bool:
        return self._request(f"/repos/{owner}/{name}", allow_missing=True) is not None

    def is_action(self, owner: str, name: str) -> bool:
        """True when the repo root holds an action manifest.

        This is what separates `actions/checkout` from the many unrelated repos
        that merely have "checkout" in the name.
        """
        for manifest in ACTION_MANIFESTS:
            found = self._request(
                f"/repos/{owner}/{name}/contents/{manifest}", allow_missing=True
            )
            if found is not None:
                return True
        return False

    def search_actions(self, query: str, limit: int = 10) -> list[Repository]:
        """Search repositories by name, most-starred first.

        Search has its own, much stricter quota (10/hour unauthenticated), so
        this is only ever called when an exact `owner/name` was not given.
        """
        encoded = urllib.parse.quote(f"{query} in:name")
        payload = self._request(
            f"/search/repositories?q={encoded}&sort=stars&order=desc&per_page={limit}"
        )
        items = (payload or {}).get("items", [])
        return [
            Repository(
                owner=item["owner"]["login"],
                name=item["name"],
                description=item.get("description") or "",
                stars=item.get("stargazers_count", 0),
            )
            for item in items
        ]

    def tags(self, owner: str, name: str, limit: int = 100) -> list[Tag]:
        """List tags newest-first.

        Note this endpoint returns the *dereferenced* commit SHA even for
        annotated tags. Reading `git/ref/tags/...` instead would hand back the
        tag-object SHA, which GitHub Actions cannot resolve when pinned.
        """
        payload = self._request(f"/repos/{owner}/{name}/tags?per_page={limit}")
        return [
            Tag(name=item["name"], commit_sha=item["commit"]["sha"])
            for item in (payload or [])
        ]

    def advisories(self, owner: str, name: str) -> list[Advisory]:
        """Published advisories affecting this action.

        Absence of advisories is not evidence of safety — the database lags
        real compromises, often by weeks. Presence, however, is decisive.
        """
        payload = self._request(
            f"/advisories?ecosystem=actions&affects={owner}/{name}&per_page=20",
            allow_missing=True,
        )
        if not isinstance(payload, list):
            return []

        results = []
        for item in payload:
            ranges = []
            for vulnerability in item.get("vulnerabilities") or []:
                package = vulnerability.get("package") or {}
                # An advisory can cover several packages; keep only the ranges
                # that name this action.
                if (package.get("name") or "").lower() != f"{owner}/{name}".lower():
                    continue
                ranges.append(
                    (
                        vulnerability.get("vulnerable_version_range") or "",
                        (vulnerability.get("first_patched_version") or ""),
                    )
                )
            results.append(
                Advisory(
                    ghsa_id=item.get("ghsa_id") or "",
                    severity=item.get("severity") or "unknown",
                    summary=item.get("summary") or "",
                    url=item.get("html_url") or "",
                    ranges=ranges,
                )
            )
        return results

    def commit_details(self, owner: str, name: str, sha: str) -> CommitDetails:
        """Date and signature status for a SHA, from a single request.

        The date is preferred over release `published_at`, which reflects when
        the release entry was written and can be reshuffled in bulk. Signature
        status rides along in the same payload, so it costs nothing extra.
        """
        payload = self._request(
            f"/repos/{owner}/{name}/commits/{sha}", allow_missing=True, immutable=True
        )
        if not payload:
            return CommitDetails()
        commit = payload.get("commit", {})
        stamp = commit.get("committer", {}).get("date") or commit.get("author", {}).get("date")
        verification = commit.get("verification") or {}
        return CommitDetails(
            date=stamp[:10] if stamp else None,
            signed=verification.get("verified"),
            signature_reason=verification.get("reason"),
        )
