#!/usr/bin/env python3
"""Replace the credly section of README.md with the user's public Credly badges.

Reads CREDLY_USER from the environment and fetches
https://www.credly.com/users/<user>/badges.json (public, no auth).
Badges that are expired or revoked are skipped. Newest first.
"""
import html
import json
import os
import re
import sys
import urllib.request

README = "README.md"
START, END = "<!--START_SECTION:credly-->", "<!--END_SECTION:credly-->"
SIZE = 110       # px
LIMIT = 24


def fetch(user):
    url = f"https://www.credly.com/users/{user}/badges.json?page=1&page_size=100"
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "credly-readme-sync"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp).get("data", [])


def render(badges):
    cells = []
    for b in badges:
        if b.get("state") not in (None, "accepted") or b.get("expires_at_date_expired"):
            continue
        tpl = b.get("badge_template") or {}
        name = html.escape(tpl.get("name", "Credly badge"))
        image = tpl.get("image_url") or b.get("image_url")
        if not image or not b.get("id"):
            continue
        cells.append(
            f'<a href="https://www.credly.com/badges/{b["id"]}/public_url" title="{name}">'
            f'<img src="{image}" width="{SIZE}" alt="{name}" /></a>')
    if not cells:
        return "<p><em>No public Credly badges found yet.</em></p>"
    return '<p align="left">\n  ' + "\n  ".join(cells[:LIMIT]) + "\n</p>"


def main():
    user = os.environ.get("CREDLY_USER", "").strip()
    if not user:
        sys.exit("CREDLY_USER repository variable is not set (Settings > Secrets and variables > Actions > Variables).")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", user):
        sys.exit(f"CREDLY_USER looks invalid: {user!r}")
    block = render(fetch(user))
    with open(README, encoding="utf-8") as fh:
        text = fh.read()
    if START not in text or END not in text:
        sys.exit("credly markers not found in README.md")
    new = re.sub(re.escape(START) + r".*?" + re.escape(END),
                 lambda _: f"{START}\n{block}\n{END}", text, flags=re.S)
    with open(README, "w", encoding="utf-8") as fh:
        fh.write(new)
    print(f"Rendered {block.count('<img')} badge(s) for {user}.")


if __name__ == "__main__":
    main()
