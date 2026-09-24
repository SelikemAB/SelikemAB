#!/usr/bin/env python3
"""Replace the credly section of README.md with the user's public Credly badges.

Credly username: $CREDLY_USER (defaults to "selikemab").
Source: https://www.credly.com/users/<user>/badges.json (public, no auth), all pages.
Revoked/pending/expired badges are skipped; newest first.
If Credly cannot be reached, the existing section is left untouched and the
run ends with a warning instead of wiping your badges.
"""
import datetime
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request

README = "README.md"
START, END = "<!--START_SECTION:credly-->", "<!--END_SECTION:credly-->"
DEFAULT_USER = "selikemab"
SIZE = 110       # px
LIMIT = 30
PAGE_SIZE = 48


def fetch(user):
    badges, page = [], 1
    while page <= 10:
        url = f"https://www.credly.com/users/{user}/badges.json?page={page}&page_size={PAGE_SIZE}&sort=-state_updated_at"
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; credly-readme-sync; +https://github.com/SelikemAB)"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
        data = payload.get("data", [])
        badges += data
        meta = payload.get("metadata") or {}
        if not data or not meta.get("total_pages") or page >= int(meta["total_pages"]):
            break
        page += 1
    return badges


def expired(badge, today):
    if badge.get("expires_at_date_expired"):
        return True
    raw = badge.get("expires_at_date") or (badge.get("expires_at") or "")[:10]
    try:
        return bool(raw) and datetime.date.fromisoformat(raw) < today
    except ValueError:
        return False


def render(badges, user, today=None):
    today = today or datetime.date.today()
    cells, seen = [], set()
    for b in badges:
        if b.get("state") not in (None, "accepted") or expired(b, today):
            continue
        tpl = b.get("badge_template") or {}
        name = html.escape(tpl.get("name") or "Credly badge", quote=True)
        image = tpl.get("image_url") or b.get("image_url")
        if not image or not b.get("id") or tpl.get("id", name) in seen:
            continue
        seen.add(tpl.get("id", name))
        cells.append(
            f'<a href="https://www.credly.com/badges/{b["id"]}/public_url" title="{name}">'
            f'<img src="{html.escape(image, quote=True)}" width="{SIZE}" alt="{name}" /></a>')
    profile = f'<a href="https://www.credly.com/users/{user}/badges">View all on Credly</a>'
    if not cells:
        return f"<p><em>No public Credly badges yet.</em> {profile}</p>"
    return '<p align="left">\n  ' + "\n  ".join(cells[:LIMIT]) + f"\n</p>\n<p>{profile}</p>"


def main():
    user = (os.environ.get("CREDLY_USER") or DEFAULT_USER).strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", user):
        sys.exit(f"CREDLY_USER looks invalid: {user!r}")
    with open(README, encoding="utf-8") as fh:
        text = fh.read()
    if START not in text or END not in text:
        sys.exit("credly markers not found in README.md")
    try:
        badges = fetch(user)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"::warning::Could not fetch Credly badges for {user}: {exc}. README left unchanged.")
        return
    block = render(badges, user)
    new = re.sub(re.escape(START) + r".*?" + re.escape(END),
                 lambda _: f"{START}\n{block}\n{END}", text, flags=re.S)
    with open(README, "w", encoding="utf-8") as fh:
        fh.write(new)
    print(f"Rendered {block.count('<img')} badge(s) for {user}.")


if __name__ == "__main__":
    main()
