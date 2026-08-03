# ilmers_corner

[![](https://img.shields.io/pypi/v/actions_search.svg)](https://pypi.org/pypi/actions_search/)


A CLI for browsing and generating hashpins for GitHub Actions.

## Install


```bash
pip install ilmers_corner
```

## Usage

```bash
ilmers_corner checkout                # search by name
ilmers_corner actions/checkout        # exact repository, opens the picker
ilmers_corner actions/checkout@v4     # pin the latest v4.x.y, no prompt
ilmers_corner actions/checkout@v4.2.2 # pin that exact version
```


<img src="https://github.com/anderslatif/ilmers_corner/raw/main/documentation/images/actions_search.png" alt="cli actions search logo" />


<img src="https://github.com/anderslatif/ilmers_corner/raw/main/documentation/images/string_result.png" alt="cli string result logo" />

<img src="https://github.com/anderslatif/ilmers_corner/raw/main/documentation/images/signature_advisory.png" alt="cli signature advisory logo" />


### Options

| Flag | Effect |
| --- | --- |
| `--pre` | include prereleases (rc, beta, alpha) |
| `--major-tags` | include floating tags like `v4`, marked `moving tag` |
| `--limit N` | maximum tags to fetch (default 100) |
| `--token` | GitHub token (defaults to `$GITHUB_TOKEN`) |
| `--no-advisories` | skip the security advisory lookup (saves one request) |
| `--clear-cache` | delete cached API responses |

## Security signals

Versions are annotated with two facts, so you can see what you are pinning:

```
  v45.0.7  a284dc1814e3  2025-03-15  ⚠ high advisory
  v45.0.4  4edd678ac3f8  2024-11-05  ⚠ high advisory  unsigned
  v46.0.1  2f7c5bfce283  2025-03-16
```

- **Advisories** — versions covered by a published GitHub security advisory are
  flagged, and the selected version prints the GHSA id and the first patched
  version to stderr. One request covers every version.
- **Signatures** — commits without a verified signature are marked `unsigned`.
  This costs nothing extra; it rides along in the request that fetches the date.

**Neither of these proves a version is safe.** The advisory database lags real
compromises, often by weeks, so no warning means only that nothing has been
published yet. Unsigned commits are common in perfectly healthy repositories.
Treat both as prompts to look closer, never as a clearance.

The limits of advisory data are worth seeing concretely. During the March 2025
`tj-actions/changed-files` compromise the attacker repointed existing version
tags at a backdoored commit, which is why v45.0.5 through v45.0.9 all resolve to
the same SHA. The advisory covers `<= 45.0.7`, so v45.0.8 and v45.0.9 carry that
same poisoned commit without being flagged — advisories describe version
numbers, not the commits tags happen to point at. A pinned SHA is what makes
that visible and stops it from changing under you.


## Rate limits

Unauthenticated GitHub allows 60 requests/hour, and only **10/hour for search**.
Two things keep that workable:

- Passing an exact `owner/name` skips search entirely, so the scarce search
  quota is only spent on genuine name lookups.
- Responses are cached on disk. Repeat lookups cost zero requests.

For heavier use, set a token to raise the limit to 5000/hour:

```bash
export GITHUB_TOKEN=ghp_...
```

## Notes on correctness

Dates come from the **commit**, not the release. GitHub's `published_at` records
when the release entry was written, which can be rewritten in bulk — as of this
writing `actions/checkout` reports v7.0.1 and v3.7.0 as published six minutes
apart. The commit date is the one that reflects reality.

SHAs come from the `/tags` endpoint, which returns the dereferenced commit even
for annotated tags. Reading `git/ref/tags/...` instead would return the tag
object's own SHA, which GitHub Actions cannot resolve when pinned.

---

## Why does this exist?

Great tools like `pinact` exist that automates the process of hashpinning Actions in workflow files. This tool offers something in cases where:

- The developer wants to browse versions and release dates.
- The developer wants to check out security signals (verified signatures, advisories from GitHub Advisory Database).
- The hashpin is needed outside of workflow files.