"""Report what the fleet build still carries on top of upstream, and what it can drop.

``python fleet/upstream.py`` reads ``fleet/carried.txt`` and asks GitHub (with the ``gh`` CLI) about each upstream
pull request: open, merged, or merged and released (a release published after the merge). It also checks that
``overrides.txt`` pins a commit a tag of our fork keeps, so a rebuild can always fetch it. Exits 1 if something
can be dropped or the pin isn't kept by a tag.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import List, Optional

UPSTREAM_OWNER = "alexdlaird"
FORK_OWNER = "benjamin-di"
HERE = os.path.dirname(os.path.abspath(__file__))


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def released_in(repo: str, merged_at: str) -> Optional[str]:
    """
    The first release of ``repo`` published after the pull request was merged. Upstream cuts releases from
    ``develop`` onto ``main`` as new commits, so a merge commit isn't an ancestor of the release tag; the timeline
    is the signal.
    """
    releases = json.loads(gh("api", f"repos/{UPSTREAM_OWNER}/{repo}/releases?per_page=20"))
    later = [r for r in releases if r["published_at"] and r["published_at"] > merged_at]
    return min(later, key=lambda r: r["published_at"])["tag_name"] if later else None


def carried() -> List[List[str]]:
    with open(os.path.join(HERE, "carried.txt"), encoding="utf-8") as f:
        return [line.strip().split(None, 3) for line in f if line.strip() and not line.startswith("#")]


def pinned_commit_tagged() -> Optional[bool]:
    with open(os.path.join(HERE, "overrides.txt"), encoding="utf-8") as f:
        match = re.search(rf"github\.com/{FORK_OWNER}/amazon-orders/archive/([0-9a-f]{{40}})", f.read())
    if not match:
        return None
    tags = json.loads(gh("api", f"repos/{FORK_OWNER}/amazon-orders/tags?per_page=100"))
    return any(tag["commit"]["sha"] == match.group(1) for tag in tags)


def main() -> int:
    actionable = False
    for repo, pull, branch, what in carried():
        if pull == "-":
            print(f"{repo} {branch}: no upstream pull request yet ({what})")
            continue
        pr = json.loads(gh("api", f"repos/{UPSTREAM_OWNER}/{repo}/pulls/{pull}"))
        if not pr["merged_at"]:
            print(f"{repo}#{pull} {branch}: {pr['state']}, not merged ({what})")
            continue
        release = released_in(repo, pr["merged_at"])
        if release:
            actionable = True
            print(f"{repo}#{pull} {branch}: released in {release} - drop it from the fleet ({what})")
        else:
            print(f"{repo}#{pull} {branch}: merged, not released yet ({what})")
    tagged = pinned_commit_tagged()
    if tagged is False:
        actionable = True
        print("overrides.txt pins a commit no tag of the fork keeps: tag it (fleet-core-<date>)")
    elif tagged:
        print("overrides.txt pins a tagged commit")
    return 1 if actionable else 0


if __name__ == "__main__":
    sys.exit(main())
