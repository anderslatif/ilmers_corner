# ilmers_corner

[![](https://img.shields.io/pypi/v/ilmers_corner.svg)](https://pypi.org/project/ilmers_corner/)


A CLI for browsing and generating hashpins for GitHub Actions.

---

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

Browsing:

<img src="https://github.com/anderslatif/ilmers_corner/raw/main/documentation/images/actions_search.png" alt="cli actions search logo" />


Result:

<img src="https://github.com/anderslatif/ilmers_corner/raw/main/documentation/images/string_result.png" alt="cli string result logo" />


---

### Options

| Flag | Effect |
| --- | --- |
| `--pre` | include prereleases (rc, beta, alpha) |
| `--major-tags` | include floating tags like `v4`, marked `moving tag` |
| `--limit N` | maximum tags to fetch (default 100) |
| `--token` | GitHub token (defaults to `$GITHUB_TOKEN`) |
| `--no-advisories` | skip the security advisory lookup (saves one request) |
| `--clear-cache` | delete cached API responses |

---

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

- The hashpin is needed outside of workflow files.
- The developer wants to browse versions and release dates.
- The developer wants to check out security signals (verified signatures, advisories from GitHub Advisory Database).

Example of the last use case with the famous supply chain compromised `tj-actions/changed-files` in March 2025:

<img src="https://github.com/anderslatif/ilmers_corner/raw/main/documentation/images/signature_advisory.png" alt="cli signature advisory logo" />
