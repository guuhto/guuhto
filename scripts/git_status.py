#!/usr/bin/env python3
"""Generate a fake-but-real `git status` block from GitHub activity."""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

USER = os.environ.get("GH_USER", "guuhto")
README = os.environ.get("README_PATH", "README.md")
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "30"))
MAX_REPOS = int(os.environ.get("MAX_REPOS", "5"))
MSG_WIDTH = 46

START = "<!--START_SECTION:status-->"
END = "<!--END_SECTION:status-->"


def api(path):
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{USER}-readme-status",
        },
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def collect():
    """Return (modified, untracked, commit_count) from public events.

    The public events payload no longer carries `commits` / `distinct_size`,
    so we only take `head` and `before` here and resolve the range later
    with one compare call per repo.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    events = []
    for page in (1, 2, 3):
        batch = api(f"/users/{USER}/events/public?per_page=100&page={page}")
        if not batch:
            break
        events.extend(batch)

    ranges = {}   # full_name -> {"head": newest sha, "before": oldest sha}
    created = []

    for ev in events:
        when = datetime.strptime(ev["created_at"], "%Y-%m-%dT%H:%M:%SZ")
        when = when.replace(tzinfo=timezone.utc)
        if when < cutoff:
            continue
        full = ev["repo"]["name"]

        if ev["type"] == "PushEvent":
            payload = ev.get("payload", {})
            if payload.get("ref", "").startswith("refs/tags/"):
                continue
            head, before = payload.get("head"), payload.get("before")
            if not head or not before:
                continue
            # events come newest-first: first head wins, last before wins
            entry = ranges.setdefault(full, {"head": head, "before": before})
            entry["before"] = before
        elif ev["type"] == "CreateEvent" and ev["payload"].get("ref_type") == "repository":
            if full not in created:
                created.append(full)

    modified = []
    total = 0
    for full, r in list(ranges.items())[:MAX_REPOS]:
        count, msg = compare(full, r["before"], r["head"])
        total += count
        modified.append((full.split("/", 1)[-1], count, msg))

    untracked = [c.split("/", 1)[-1] for c in created if c not in ranges][:3]
    return modified, untracked, total


def compare(full_name, before, head):
    """Return (commit_count, latest_commit_message) for a sha range."""
    try:
        data = api(f"/repos/{full_name}/compare/{before}...{head}")
    except urllib.error.HTTPError:
        # force-push, deleted branch, or repo gone private
        return 0, ""
    commits = data.get("commits", [])
    count = data.get("total_commits", len(commits))
    msg = ""
    for c in reversed(commits):
        first = c["commit"]["message"].split("\n")[0].strip()
        if first.lower().startswith("merge "):
            continue
        msg = first
        break
    return count, msg


def shorten(msg):
    msg = re.sub(r"\s+", " ", msg).strip()
    return msg if len(msg) <= MSG_WIDTH else msg[: MSG_WIDTH - 1] + "…"


def render(modified, untracked, commits):
    lines = [
        "```console",
        "$ git status",
        "On branch main",
    ]
    if commits:
        plural = "commit" if commits == 1 else "commits"
        lines.append(f"Your branch is ahead of 'origin/main' by {commits} {plural}.")
    else:
        lines.append("Your branch is up to date with 'origin/main'.")
    lines.append("")

    if modified:
        pad = max(len(r) for r, _, _ in modified) + 2
        lines.append("Changes not staged for commit:")
        for repo, count, msg in modified:
            note = shorten(msg) if msg else f"{count} commits"
            lines.append(f"  modified:   {(repo + '/').ljust(pad)}  # {note}")
        lines.append("")

    if untracked:
        lines.append("Untracked files:")
        lines.append('  (use "git add <idea>" to include in what will be committed)')
        for repo in untracked:
            lines.append(f"        {repo}/")
        lines.append("")

    if not modified and not untracked:
        lines.append("nothing to commit, working tree clean")
        lines.append("")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"# last {WINDOW_DAYS} days · refreshed {stamp}")
    lines.append("```")
    return "\n".join(lines)


def main():
    modified, untracked, commits = collect()
    block = render(modified, untracked, commits)

    with open(README, encoding="utf-8") as fh:
        content = fh.read()

    if START not in content or END not in content:
        sys.exit(f"markers {START} / {END} not found in {README}")

    new = re.sub(
        re.escape(START) + r".*?" + re.escape(END),
        f"{START}\n{block}\n{END}",
        content,
        flags=re.DOTALL,
    )

    if new == content:
        print("no changes")
        return

    with open(README, "w", encoding="utf-8") as fh:
        fh.write(new)
    print("README updated")


if __name__ == "__main__":
    main()
