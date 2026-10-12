#!/usr/bin/env python3
"""Safely prune abandoned GitHub branches after their exact PR head was merged.

Dry run by default. A squash merge changes commit ancestry, so a simple
"git branch --merged" is NOT an appropriate test for this repository.

A branch qualifies only when its current head equals the head SHA of a
merged PR into the default branch. Unmerged, modified, protected, and
unassociated branches are never deleted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def api(path: str, *, method: str = "GET", token: str = ""):
    url = "https://api.github.com" + path
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "vinted-dashboard-safe-branch-cleanup",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(url, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            data = response.read()
            return json.loads(data) if data else {}
    except HTTPError as exc:
        # Do not echo tokens, request headers, or response bodies.
        raise RuntimeError(f"GitHub returned HTTP {exc.code} for {method} {path}") from exc


def pages(repository: str, collection: str, token: str):
    results = []
    page = 1
    while True:
        batch = api(
            f"/repos/{repository}/{collection}?per_page=100&page={page}",
            token=token,
        )
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected GitHub response for {collection}")
        results.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return results


def candidates(branches, pull_requests, *, default_branch: str, repository: str):
    merged_heads = {
        (str(pr.get("head", {}).get("ref") or ""), str(pr.get("head", {}).get("sha") or ""))
        for pr in pull_requests
        if pr.get("merged_at")
        and pr.get("base", {}).get("ref") == default_branch
        and pr.get("base", {}).get("repo", {}).get("full_name") == repository
        and pr.get("head", {}).get("repo", {}).get("full_name") == repository
    }
    return sorted([
        branch
        for branch in branches
        if branch.get("name") != default_branch
        and not branch.get("protected")
        and (branch.get("name"), branch.get("commit", {}).get("sha")) in merged_heads
    ], key=lambda branch: branch["name"])


def run(*, repository: str, execute: bool, max_delete: int, token: str) -> int:
    info = api(f"/repos/{repository}", token=token)
    default_branch = str(info["default_branch"])
    branches = pages(repository, "branches", token)
    # Handle GitHub PR query filters explicitly instead of building an unsafe URL.
    prs = []
    page = 1
    while True:
        batch = api(
            f"/repos/{repository}/pulls?state=closed&per_page=100&page={page}",
            token=token,
        )
        if not isinstance(batch, list):
            raise RuntimeError("Unexpected GitHub pull request response")
        prs.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    safe = candidates(branches, prs, default_branch=default_branch, repository=repository)
    retained = len(branches) - 1 - len(safe)
    print(f"Audited {len(branches)-1} non-default branches: {len(safe)} exact merged heads, {retained} retained.")
    for branch in safe:
        print(("DELETE" if execute else "DRY RUN") + ": " + branch["name"])
    if len(safe) > max_delete:
        raise RuntimeError(f"Safety limit exceeded ({len(safe)} > {max_delete}); no branches deleted")
    if not execute:
        print("Dry run only. Use --execute for deletion.")
        return 0
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required for --execute")

    deleted = 0
    for branch in safe:
        name = branch["name"]
        path_name = quote(name, safe="")
        # Verify the exact SHA and protection flag again immediately before deletion.
        current = api(f"/repos/{repository}/branches/{path_name}", token=token)
        if current.get("commit", {}).get("sha") != branch["commit"]["sha"] or current.get("protected"):
            print(f"SKIP changed/protected branch: {name}")
            continue
        api(f"/repos/{repository}/git/refs/heads/{path_name}", method="DELETE", token=token)
        deleted += 1
        print(f"DELETED: {name}")
    print(f"Deleted {deleted} verified branches; {retained} other branches retained.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", "tpepels/vinted-dashboard"))
    parser.add_argument("--execute", action="store_true", help="Delete exact matched merged branches")
    parser.add_argument("--max-delete", type=int, default=115, help="Abort if candidate count exceeds audit cap")
    args = parser.parse_args()
    if not args.repository or "/" not in args.repository:
        parser.error("Expected owner/repository")
    try:
        return run(
            repository=args.repository, execute=args.execute,
            max_delete=args.max_delete, token=os.getenv("GITHUB_TOKEN", ""),
        )
    except RuntimeError as exc:
        print(f"Cleanup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
