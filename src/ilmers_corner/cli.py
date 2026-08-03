"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from . import __version__
from .cache import ResponseCache
from .github import GitHubClient, GitHubError, RateLimitError, Repository
from .picker import select
from .terminal import dim, style
from .versions import Version, build_versions, in_range

# Ranking for picking the worst advisory affecting a version.
SEVERITY_ORDER = {"low": 1, "medium": 2, "moderate": 2, "high": 3, "critical": 4}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)

    if arguments.clear_cache:
        removed = ResponseCache().clear()
        print(f"Cleared {removed} cached response(s).")
        return 0

    if not arguments.action:
        parser.print_help()
        return 2

    client = GitHubClient(token=arguments.token)
    try:
        return _run(client, arguments)
    except RateLimitError as error:
        print(style(f"\n{error}", "red"), file=sys.stderr)
        advice = error.advice()
        if advice:
            print(dim(advice), file=sys.stderr)
        return 1
    except GitHubError as error:
        print(style(f"\n{error}", "red"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ilmers_corner",
        description="Browse GitHub Action versions and generate a hash-pinned `uses:` line.",
        epilog=(
            "examples:\n"
            "  ilmers_corner actions/checkout      exact repository\n"
            "  ilmers_corner checkout              search by name\n"
            "  ilmers_corner actions/checkout@v4   pin that version directly\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "action",
        nargs="?",
        help="action name, `owner/name`, or `owner/name@version`",
    )
    parser.add_argument(
        "--pre", action="store_true", help="include prereleases (rc, beta, alpha)"
    )
    parser.add_argument(
        "--major-tags",
        action="store_true",
        help="include floating major tags such as v4 (these move; pinning them is unsafe)",
    )
    parser.add_argument(
        "--limit", type=int, default=100, help="maximum tags to fetch (default: 100)"
    )
    parser.add_argument(
        "--no-advisories",
        action="store_true",
        help="skip the security advisory lookup (saves one request)",
    )
    parser.add_argument("--token", help="GitHub token (defaults to $GITHUB_TOKEN)")
    parser.add_argument(
        "--clear-cache", action="store_true", help="delete cached API responses and exit"
    )
    parser.add_argument("--version", action="version", version=f"ilmers_corner {__version__}")
    return parser


def _run(client: GitHubClient, arguments: argparse.Namespace) -> int:
    query = arguments.action
    requested_version: Optional[str] = None
    if "@" in query:
        query, _, requested_version = query.partition("@")

    repository = _resolve_repository(client, query)
    if repository is None:
        return 1

    tags = client.tags(repository.owner, repository.name, limit=arguments.limit)
    if not tags:
        print(
            style(f"{repository.full_name} has no tags to pin.", "yellow"), file=sys.stderr
        )
        return 1

    versions = build_versions(
        tags,
        include_prereleases=arguments.pre,
        include_major_aliases=arguments.major_tags,
    )
    if not versions:
        print(
            style("No stable versions found.", "yellow")
            + dim(" Try --pre or --major-tags."),
            file=sys.stderr,
        )
        return 1

    if not arguments.no_advisories:
        _attach_advisories(client, repository, versions)

    if requested_version:
        chosen = _match_version(versions, requested_version)
        if chosen is None:
            available = ", ".join(version.tag for version in versions[:5])
            print(
                style(f"No tag matching '{requested_version}'.", "red")
                + dim(f" Available: {available}"),
                file=sys.stderr,
            )
            return 1
    else:
        chosen = _choose_version(client, repository, versions)
        if chosen is None:
            print(dim("Cancelled."), file=sys.stderr)
            return 130

    if chosen.date is None:
        details = client.commit_details(
            repository.owner, repository.name, chosen.commit_sha
        )
        chosen.date = details.date
        chosen.signed = details.signed

    _warn_about(chosen)
    print(_format_uses(repository, chosen))
    return 0


def _warn_about(version: Version) -> None:
    """Print advisory warnings to stderr, keeping stdout pipeable."""
    for advisory in version.advisories:
        patched = next(
            (patch for _, patch in advisory.ranges if patch), ""
        )
        headline = style(
            f"⚠  {advisory.severity.upper()}: {advisory.summary}", "red", bold=True
        )
        print(f"\n{headline}", file=sys.stderr)
        print(dim(f"   {advisory.ghsa_id}  {advisory.url}"), file=sys.stderr)
        if patched:
            print(dim(f"   Fixed in {patched}."), file=sys.stderr)
        print(file=sys.stderr)


def _attach_advisories(
    client: GitHubClient, repository: Repository, versions: list[Version]
) -> None:
    """Mark versions covered by a published advisory.

    One request covers every version, so this is cheap regardless of tag count.
    """
    try:
        advisories = client.advisories(repository.owner, repository.name)
    except GitHubError:
        # Advisory data is advisory. Losing it must not block pinning.
        return

    for advisory in advisories:
        for version in versions:
            if any(
                in_range(version, vulnerable_range)
                for vulnerable_range, _ in advisory.ranges
            ):
                version.advisories.append(advisory)


def _resolve_repository(client: GitHubClient, query: str) -> Optional[Repository]:
    """Turn user input into a concrete repository.

    An explicit `owner/name` is verified directly, which avoids spending any of
    the very limited search quota.
    """
    if "/" in query:
        owner, _, name = query.partition("/")
        if client.repository_exists(owner, name):
            return Repository(owner=owner, name=name, description="", stars=0)
        print(style(f"No repository at {query}.", "red"), file=sys.stderr)
        return None

    print(dim(f"Searching for actions named '{query}'…"), file=sys.stderr)
    candidates = client.search_actions(query)
    if not candidates:
        print(style(f"Nothing found for '{query}'.", "yellow"), file=sys.stderr)
        return None

    exact = [
        candidate
        for candidate in candidates
        if candidate.name.lower() == query.lower()
        and client.is_action(candidate.owner, candidate.name)
    ]
    if len(exact) == 1:
        match = exact[0]
        print(dim(f"Matched {match.full_name}"), file=sys.stderr)
        return match

    return _choose_repository(candidates)


def _choose_repository(candidates: list[Repository]) -> Optional[Repository]:
    width = max(len(candidate.full_name) for candidate in candidates)

    def render(candidate: Repository, selected: bool) -> str:
        name = candidate.full_name.ljust(width)
        name = style(name, "cyan", bold=True) if selected else name
        stars = dim(f"★ {candidate.stars:,}".rjust(9))
        description = candidate.description[:48]
        return f"{name}  {stars}  {dim(description)}"

    return select(
        candidates,
        render,
        title=style("Select a repository", bold=True),
        footer="↑/↓ move · enter select · q cancel",
    )


def _choose_version(
    client: GitHubClient, repository: Repository, versions: list[Version]
) -> Optional[Version]:
    width = max(len(version.tag) for version in versions)

    def fetch_dates(visible: Sequence[Version]) -> None:
        # Only the rows on screen are resolved, keeping a browse session to a
        # handful of API calls rather than one per tag.
        for version in visible:
            if version.date is not None:
                continue
            try:
                details = client.commit_details(
                    repository.owner, repository.name, version.commit_sha
                )
                version.date = details.date or "unknown"
                version.signed = details.signed
            except RateLimitError:
                # Dates are a nicety; running out of quota mid-scroll must not
                # destroy the session, since the SHA is what actually matters.
                version.date = "rate-limited"
            except GitHubError:
                version.date = "unknown"

    def render(version: Version, selected: bool) -> str:
        tag = version.tag.ljust(width)
        tag = style(tag, "cyan", bold=True) if selected else tag
        date = version.date or "…"

        markers = []
        if version.advisories:
            worst = max(
                (advisory.severity for advisory in version.advisories),
                key=lambda severity: SEVERITY_ORDER.get(severity, 0),
            )
            markers.append(style(f"⚠ {worst} advisory", "red", bold=True))
        if version.signed:
            markers.append(style("✔ signed", "blue"))
        elif version.signed is False:
            markers.append(style("unsigned", "yellow"))
        if version.is_major_alias:
            markers.append(dim("moving tag"))

        suffix = ("  " + "  ".join(markers)) if markers else ""
        return f"{tag}  {dim(version.commit_sha[:12])}  {dim(date)}{suffix}"

    return select(
        versions,
        render,
        title=style(f"Select a version of {repository.full_name}", bold=True),
        on_page_change=fetch_dates,
    )


def _match_version(versions: list[Version], requested: str) -> Optional[Version]:
    wanted = requested.lstrip("v").lower()
    for version in versions:
        if version.tag.lstrip("v").lower() == wanted:
            return version
    # Fall back to the newest version under a partial prefix, so `@v4` picks the
    # latest v4.x.y rather than failing.
    for version in versions:
        if version.tag.lstrip("v").lower().startswith(f"{wanted}."):
            return version
    return None


def _format_uses(repository: Repository, version: Version) -> str:
    date = version.date or "unknown"
    return (
        f"- uses: {repository.full_name}@{version.commit_sha}"
        f"  # {version.tag}  {date}"
    )
