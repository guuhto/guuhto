#!/usr/bin/env python3
"""Generate a fake-but-real `git status` block from GitHub activity."""

import json
import os
import re
import sys
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
    """Return (modified, untracked, commit_count) from public events."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    events = []
    for page in (1, 2, 3):
        batch = api(f"/users/{USER}/events/public?per_page=100&page={page}")
        if not batch:
            break
        events.extend(batch)

    modified = {}          # repo -> last commit message
    created = []           # brand new repos
    commits = 0

    for ev in events:
        when = datetime.strptime(ev["created_at"], "%Y-%m-%dT%H:%M:%SZ")
        when = when.replace(tzinfo=timezone.utc)
        if when < cutoff:
            continue
        repo = ev["repo"]["name"].split("/", 1)[-1]

        if ev["type"] == "PushEvent":
            payload = ev.get("payload", {})
            commits += payload.get("distinct_size", 0)
            for c in reversed(payload.get("commits", [])):
                msg = c["message"].split("\n")[0].strip()
                if msg.lower().startswith("merge "):
                    continue
                modified.setdefault(repo, msg)
                break
        elif ev["type"] == "CreateEvent" and ev["payload"].get("ref_type") == "repository":
            if repo not in created:
                created.append(repo)

    # a repo that was just created and already pushed to counts as modified only
    untracked = [r for r in created if r not in modified][:3]
    top = list(modified.items())[:MAX_REPOS]
    return top, untracked, commits


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
        pad = max(len(r) for r, _ in modified) + 1
        lines.append("Changes not staged for commit:")
        for repo, msg in modified:
            lines.append(f"  modified:   {(repo + '/').ljust(pad + 1)}  # {shorten(msg)}")
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
