#!/usr/bin/env python3
"""Render assets/profile.svg: a dark "mission control" profile card.

Data (fetched fresh on every run):
  - GitHub GraphQL (needs GITHUB_TOKEN): repos, stars, languages, pinned
    repos, commit count and the contribution calendar.
  - Fallback without GraphQL: REST /users/<u>/repos + /repos/<r>/languages and
    the public contributions page.
  - Credly public badges.json for the certification count (falls back to the
    number of badges in the README's credly section).
Text, tech pills and featured repos live in .github/profile/config.json.

Usage:
  build_profile_svg.py                       fetch + render assets/profile.svg
  build_profile_svg.py --dump data.json      also save the fetched data
  build_profile_svg.py --from-json data.json render from saved data (offline)
"""
import argparse
import datetime
import html
import json
import os
import random
import re
import sys
import urllib.error
import urllib.request

CONFIG = ".github/profile/config.json"
OUT = "assets/profile.svg"
README = "README.md"

W = 880          # canvas width
PAD = 40         # inner horizontal padding
INNER = W - 2 * PAD
FONT = "'Segoe UI', Ubuntu, 'Helvetica Neue', Arial, sans-serif"

C = {
    "bg": "#0a0b14", "panel": "#10121f", "line": "#ffffff14", "line2": "#ffffff22",
    "text": "#f8fafc", "muted": "#94a3b8", "dim": "#475569",
    "cyan": "#67e8f9", "pink": "#f472b6", "gold": "#fbbf24", "blue": "#60a5fa",
    "violet": "#7c3aed", "magenta": "#be185d", "teal": "#0f766e",
}
HEAT = ["#141a2e", "#1e3a8a", "#2563eb", "#22d3ee", "#ffffff"]


# ----------------------------------------------------------------- fetching
def http_json(url, token=None, data=None, accept="application/vnd.github+json"):
    headers = {"Accept": accept, "User-Agent": "profile-svg-builder"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(data).encode() if data is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


GQL = """
query($login: String!) {
  user(login: $login) {
    repositories(ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC, first: 100,
                 orderBy: {field: STARGAZERS, direction: DESC}) {
      totalCount
      nodes {
        name description stargazerCount forkCount isArchived url
        primaryLanguage { name color }
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
    pinnedItems(first: 6, types: REPOSITORY) { nodes { ... on Repository { name } } }
    contributionsCollection {
      totalCommitContributions restrictedContributionsCount
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch_graphql(user, token):
    res = http_json("https://api.github.com/graphql", token, {"query": GQL, "variables": {"login": user}})
    if res.get("errors"):
        raise RuntimeError(res["errors"])
    u = res["data"]["user"]
    repos = []
    for r in u["repositories"]["nodes"]:
        repos.append({
            "name": r["name"], "description": r["description"] or "",
            "stars": r["stargazerCount"], "forks": r["forkCount"], "archived": r["isArchived"],
            "language": (r["primaryLanguage"] or {}).get("name"),
            "language_color": (r["primaryLanguage"] or {}).get("color"),
            "languages": {e["node"]["name"]: [e["size"], e["node"]["color"]] for e in r["languages"]["edges"]},
        })
    cc = u["contributionsCollection"]
    days = [{"date": d["date"], "count": d["contributionCount"]}
            for w in cc["contributionCalendar"]["weeks"] for d in w["contributionDays"]]
    return {
        "repo_count": u["repositories"]["totalCount"],
        "repos": repos,
        "pinned": [n["name"] for n in u["pinnedItems"]["nodes"] if n],
        "commits": cc["totalCommitContributions"] + cc["restrictedContributionsCount"],
        "contributions": cc["contributionCalendar"]["totalContributions"],
        "days": days,
    }


def fetch_rest(user, token):
    raw = http_json(f"https://api.github.com/users/{user}/repos?per_page=100&type=owner&sort=pushed", token)
    repos = []
    for r in raw:
        if r.get("fork") or r.get("private"):
            continue
        try:
            langs = http_json(r["languages_url"], token)
        except urllib.error.URLError:
            langs = {}
        repos.append({
            "name": r["name"], "description": r.get("description") or "",
            "stars": r["stargazers_count"], "forks": r["forks_count"], "archived": r.get("archived", False),
            "language": r.get("language"), "language_color": None,
            "languages": {k: [v, None] for k, v in langs.items()},
        })
    days = fetch_calendar_html(user)
    return {"repo_count": len(repos), "repos": repos, "pinned": [],
            "commits": None, "contributions": sum(d["count"] for d in days), "days": days}


def fetch_calendar_html(user):
    req = urllib.request.Request(f"https://github.com/users/{user}/contributions",
                                 headers={"User-Agent": "Mozilla/5.0 profile-svg-builder"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        page = resp.read().decode("utf-8", "replace")
    tips = {m.group(1): m.group(2) for m in re.finditer(r'<tool-tip[^>]*for="([^"]+)"[^>]*>([^<]*)</tool-tip>', page)}
    days = []
    for m in re.finditer(r'<td[^>]*data-date="(\d{4}-\d{2}-\d{2})"[^>]*>', page):
        tag = m.group(0)
        ident = re.search(r'id="([^"]+)"', tag)
        tip = tips.get(ident.group(1), "") if ident else ""
        num = re.match(r"\s*(\d+)\s+contribution", tip)
        if num:
            count = int(num.group(1))
        else:
            level = re.search(r'data-level="(\d)"', tag)
            count = int(level.group(1)) if level and not tip.lower().startswith("no ") else 0
        days.append({"date": m.group(1), "count": count})
    days.sort(key=lambda d: d["date"])
    return days


def fetch_credly_count(user):
    try:
        data = http_json(f"https://www.credly.com/users/{user}/badges.json?page_size=100",
                         accept="application/json")
        today = datetime.date.today().isoformat()
        return sum(1 for b in data.get("data", [])
                   if b.get("state") in (None, "accepted")
                   and not ((b.get("expires_at_date") or "9999") < today))
    except Exception:  # Credly blocked or down: count what the README shows
        try:
            section = open(README, encoding="utf-8").read().split("START_SECTION:credly")[1].split("END_SECTION:credly")[0]
            return section.count("<img")
        except (OSError, IndexError):
            return None


def fetch_all(cfg):
    user, token = cfg["username"], os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    data = None
    if token:
        try:
            data = fetch_graphql(user, token)
        except Exception as exc:
            print(f"::warning::GraphQL failed ({exc}); using REST fallback", file=sys.stderr)
    if data is None:
        data = fetch_rest(user, token)
    data["credly"] = fetch_credly_count(cfg.get("credly_user") or user)
    data["generated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return data


# ---------------------------------------------------------------- helpers
def esc(text):
    return html.escape(str(text), quote=True)


def text_width(text, size, spacing=0.0, bold=False):
    """Rough width estimate for layout (no font metrics available in SVG)."""
    w = 0.0
    for ch in str(text):
        if ch in "il.,:;|!'·":
            f = 0.30
        elif ch in "mwMW@":
            f = 0.85
        elif ch.isupper() or ch.isdigit():
            f = 0.66
        elif ch == " ":
            f = 0.30
        else:
            f = 0.54
        w += f * size * (1.07 if bold else 1.0) + spacing
    return w


def wrap(text, size, max_w, max_lines=2):
    words, lines, cur = str(text).split(), [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if text_width(trial, size) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = word
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and " ".join(lines) != " ".join(words):
        while lines[-1] and text_width(lines[-1] + "…", size) > max_w:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "…"
    return lines


def readable(color, fallback):
    """Lighten GitHub language colours that vanish on the dark background."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", color or "")
    if not m:
        return fallback
    r, g, b = (int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if lum >= 95:
        return "#" + m.group(1)
    k = 0.55  # mix towards white
    return "#%02x%02x%02x" % tuple(int(c + (255 - c) * k) for c in (r, g, b))


def fmt(n):
    if n is None:
        return "—"
    return f"{n/1000:.1f}k" if n >= 10000 else f"{n:,}"


def label(x, y, text, color, size=11, anchor="start", spacing=4, opacity=1.0):
    return (f'<text x="{x:.1f}" y="{y:.1f}" fill="{color}" font-size="{size}" font-weight="700" '
            f'letter-spacing="{spacing}" text-anchor="{anchor}" opacity="{opacity}">{esc(text)}</text>')


# ---------------------------------------------------------------- sections
def languages(data, cfg):
    totals, colors = {}, {}
    skip = set(cfg.get("exclude_languages", []))
    for r in data["repos"]:
        if r["archived"] or r["name"] in cfg.get("exclude_repos", []):
            continue
        for name, (size, color) in r["languages"].items():
            if name in skip:
                continue
            totals[name] = totals.get(name, 0) + size
            if color:
                colors[name] = color
    total = sum(totals.values())
    if not total:
        return []
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    palette = [C["pink"], C["cyan"], C["blue"], C["gold"], "#a78bfa", "#34d399", "#fb923c", "#e879f9", "#94a3b8"]
    out = []
    for i, (name, size) in enumerate(ranked[:9]):
        out.append((name, size / total, readable(colors.get(name), palette[i % len(palette)])))
    rest = sum(size for _, size in ranked[9:])
    if rest:
        out.append(("Other", rest / total, "#334155"))
    return out


def projects(data, cfg):
    by_name = {r["name"]: r for r in data["repos"]}
    order = []
    for name in cfg.get("featured", []) + data.get("pinned", []):
        if name in by_name and name not in order:
            order.append(name)
    for r in sorted(data["repos"], key=lambda r: (-r["stars"], r["name"])):
        if r["name"] not in order and not r["archived"]:
            order.append(r["name"])
    order = [n for n in order if n not in cfg.get("exclude_repos", [])]
    return [by_name[n] for n in order[:3]]


def build_svg(cfg, data):
    rnd = random.Random(cfg["username"])  # stable starfield between runs
    parts, y = [], 0

    # ---- hero
    hero_h = 300
    star_list = []
    while len(star_list) < 46:
        sx, sy = rnd.uniform(20, W - 20), rnd.uniform(20, hero_h)
        if 150 < sx < W - 150 and 70 < sy < 280:   # keep the text block clean
            continue
        star_list.append(
            f'<circle class="tw" style="animation-delay:{rnd.uniform(0, 6):.1f}s" cx="{sx:.0f}" cy="{sy:.0f}" '
            f'r="{rnd.choice([0.8, 1, 1.2, 1.6])}" fill="#fff" opacity="{rnd.uniform(.25, .8):.2f}"/>')
    stars = "".join(star_list)
    parts.append(stars)
    cx = W / 2
    name_size = 64 if len(cfg["name"]) <= 9 else 48
    parts.append(f'<text x="{cx}" y="128" text-anchor="middle" fill="{C["text"]}" font-size="{name_size}" '
                 f'font-weight="800" letter-spacing="{name_size * 0.28:.0f}" filter="url(#soft)">{esc(cfg["name"])}</text>')
    parts.append(label(cx, 170, cfg["title"], C["cyan"], 14, "middle", 6))
    parts.append(label(cx, 212, cfg["subtitle"], C["muted"], 12, "middle", 4))
    parts.append(label(cx, 238, cfg["focus"], C["dim"], 10.5, "middle", 3))
    parts.append(label(cx, 266, cfg["url"], "#334155", 10, "middle", 2))
    b, m = 14, 26  # corner brackets
    for x0, y0, dx, dy in ((m, m, 1, 1), (W - m, m, -1, 1), (m, hero_h - 6, 1, -1), (W - m, hero_h - 6, -1, -1)):
        parts.append(f'<path d="M{x0} {y0 + dy*b} V{y0} H{x0 + dx*b}" stroke="{C["pink"] if dx > 0 else C["cyan"]}" '
                     f'stroke-width="1.6" fill="none" opacity=".8"/>')
    y = hero_h + 18
    parts.append(f'<line x1="{PAD}" y1="{y}" x2="{W-PAD}" y2="{y}" stroke="{C["line"]}"/>')

    # ---- pills (wrap to several centred rows)
    y += 34
    sized = [(p, text_width(p["label"], 12, 0.6, True) + 30) for p in cfg.get("pills", [])]
    full = sum(w for _, w in sized) + 10 * max(len(sized) - 1, 0)
    n_rows = max(1, -(-int(full) // int(INNER)))
    per_row = -(-len(sized) // n_rows) if sized else 0
    rows = []
    for i in range(0, len(sized), per_row or 1):
        chunk = sized[i:i + per_row]
        rows.append((chunk, sum(w for _, w in chunk) + 10 * (len(chunk) - 1)))
    for row, row_w in rows:
        x = (W - row_w) / 2
        for p, w in row:
            color = C.get(p.get("tone", "cyan"), C["cyan"])
            parts.append(f'<rect x="{x:.1f}" y="{y - 17}" width="{w:.1f}" height="28" rx="14" fill="#ffffff06" stroke="{color}" stroke-opacity=".35"/>')
            parts.append(f'<text x="{x + w/2:.1f}" y="{y + 1}" text-anchor="middle" fill="{color}" font-size="12" font-weight="700" letter-spacing=".6">{esc(p["label"])}</text>')
            x += w + 10
        y += 40
    y += 4
    parts.append(f'<line x1="{PAD}" y1="{y}" x2="{W-PAD}" y2="{y}" stroke="{C["line"]}"/>')

    # ---- stats
    y += 62
    stars_total = sum(r["stars"] for r in data["repos"]) if data["repos"] else None
    fourth = ("BADGES", data.get("credly"), C["pink"]) if data.get("credly") else \
             ("CONTRIBUTIONS", data.get("contributions"), C["pink"])
    stats = [("REPOS", data.get("repo_count"), C["blue"]), ("STARS", stars_total, C["gold"]),
             ("COMMITS · 1Y", data.get("commits") if data.get("commits") is not None else data.get("contributions"), C["cyan"]),
             fourth]
    col = INNER / 4
    for i, (name, value, color) in enumerate(stats):
        x = PAD + col * i + col / 2
        parts.append(f'<text x="{x:.1f}" y="{y}" text-anchor="middle" fill="{C["text"]}" font-size="38" font-weight="800">{esc(fmt(value))}</text>')
        parts.append(label(x, y + 24, name, color, 10, "middle", 3.5))
    parts.append(f'<rect x="{PAD + 60}" y="{y + 40}" width="{INNER - 120}" height="1.5" fill="url(#beam)" opacity=".7"/>')
    y += 62
    parts.append(f'<line x1="{PAD}" y1="{y}" x2="{W-PAD}" y2="{y}" stroke="{C["line"]}"/>')

    # ---- stack analytics
    langs = languages(data, cfg)
    if langs:
        y += 40
        parts.append(label(PAD, y, "STACK ANALYTICS", C["cyan"], 11, spacing=5))
        y += 22
        parts.append(f'<rect x="{PAD}" y="{y}" width="{INNER}" height="10" rx="5" fill="#ffffff0d"/>')
        parts.append(f'<clipPath id="bar"><rect x="{PAD}" y="{y}" width="{INNER}" height="10" rx="5"/></clipPath><g clip-path="url(#bar)">')
        x = PAD
        for name, share, color in langs:
            w = max(share * INNER, 2)
            parts.append(f'<rect x="{x:.1f}" y="{y}" width="{max(w - 2, 1):.1f}" height="10" fill="{color}"/>')
            x += w
        parts.append("</g>")
        y += 40
        cw = INNER / 5
        for i, (name, share, color) in enumerate(langs):
            lx, ly = PAD + (i % 5) * cw, y + (i // 5) * 28
            parts.append(f'<circle cx="{lx + 5}" cy="{ly - 4}" r="4.5" fill="{color}"/>')
            parts.append(f'<text x="{lx + 16}" y="{ly}" fill="{C["text"]}" font-size="12.5" font-weight="700">{esc(name)}'
                         f'<tspan fill="{C["dim"]}" font-weight="600" font-size="11"> {"<1" if share < 0.005 else f"{share*100:.0f}"}%</tspan></text>')
        y += 28 * ((len(langs) - 1) // 5) + 26
        parts.append(f'<line x1="{PAD}" y1="{y}" x2="{W-PAD}" y2="{y}" stroke="{C["line"]}"/>')

    # ---- activity pulse
    days = data.get("days") or []
    if days:
        y += 40
        parts.append(label(PAD, y, "ACTIVITY PULSE", C["cyan"], 11, spacing=5))
        total = data.get("contributions") or sum(d["count"] for d in days)
        parts.append(f'<text x="{W-PAD}" y="{y}" text-anchor="end" fill="{C["muted"]}" font-size="11.5" font-weight="700">'
                     f'{total:,} contributions · last year</text>')
        y += 18
        days = days[-371:]
        first = datetime.date.fromisoformat(days[0]["date"])
        offset = (first.weekday() + 1) % 7  # GitHub weeks start on Sunday
        counts = sorted(d["count"] for d in days if d["count"] > 0)
        def level(n):
            if n <= 0 or not counts:
                return 0
            q = [counts[int(len(counts) * f)] for f in (0.25, 0.5, 0.8)]
            return 1 + sum(n > t for t in q)
        weeks = (len(days) + offset + 6) // 7
        cell = min(13, (INNER - (weeks - 1) * 2.4) / weeks)
        gap = 2.4
        gx = PAD + (INNER - (weeks * cell + (weeks - 1) * gap)) / 2
        for i, d in enumerate(days):
            k = i + offset
            wx, wy = gx + (k // 7) * (cell + gap), y + (k % 7) * (cell + gap)
            lv = level(d["count"])
            extra = ' class="hot"' if lv == 4 else ""
            parts.append(f'<rect{extra} x="{wx:.1f}" y="{wy:.1f}" width="{cell:.1f}" height="{cell:.1f}" rx="2" fill="{HEAT[lv]}">'
                         f'<title>{esc(d["date"])}: {d["count"]}</title></rect>')
        y += 7 * (cell + gap) + 22
        parts.append(f'<line x1="{PAD}" y1="{y}" x2="{W-PAD}" y2="{y}" stroke="{C["line"]}"/>')

    # ---- primary deployments
    cards = projects(data, cfg)
    if cards:
        y += 40
        parts.append(label(PAD, y, "PRIMARY DEPLOYMENTS", C["cyan"], 11, spacing=5))
        y += 20
        gap, n = 18, len(cards)
        cw = (INNER - gap * 2) / 3
        x0 = PAD + (INNER - (n * cw + (n - 1) * gap)) / 2
        ch = 150
        for i, r in enumerate(cards):
            x = x0 + i * (cw + gap)
            parts.append(f'<rect x="{x:.1f}" y="{y}" width="{cw:.1f}" height="{ch}" rx="14" fill="url(#card)" stroke="{C["line2"]}"/>')
            title = r["name"] if text_width(r["name"], 17, 0, True) < cw - 36 else r["name"][:18] + "…"
            parts.append(f'<text x="{x + 18:.1f}" y="{y + 36}" fill="{C["text"]}" font-size="17" font-weight="800">{esc(title)}</text>')
            for j, line in enumerate(wrap(r["description"] or "No description yet.", 12, cw - 36, 3)):
                parts.append(f'<text x="{x + 18:.1f}" y="{y + 62 + j * 17}" fill="{C["muted"]}" font-size="12">{esc(line)}</text>')
            parts.append(f'<line x1="{x + 18:.1f}" y1="{y + ch - 40}" x2="{x + cw - 18:.1f}" y2="{y + ch - 40}" stroke="{C["line"]}"/>')
            lang = r.get("language") or "—"
            dot = readable(r.get("language_color"), C["cyan"])
            parts.append(f'<circle cx="{x + 23:.1f}" cy="{y + ch - 19}" r="4.5" fill="{dot}"/>')
            parts.append(f'<text x="{x + 34:.1f}" y="{y + ch - 15}" fill="{C["text"]}" font-size="12" font-weight="700">{esc(lang)}</text>')
            parts.append(f'<text x="{x + cw - 18:.1f}" y="{y + ch - 15}" text-anchor="end" fill="{C["dim"]}" font-size="12" font-weight="700">'
                         f'★ {r["stars"]}  ⑂ {r["forks"]}</text>')
        y += ch + 30

    # ---- footer
    y += 20
    parts.append(label(W / 2, y, cfg.get("footer", ""), "#334155", 9.5, "middle", 4))
    parts.append(f'<text x="{W/2}" y="{y + 18}" text-anchor="middle" fill="#1e293b" font-size="9" font-weight="600" letter-spacing="2">'
                 f'UPDATED {esc(data.get("generated", ""))}</text>')
    H = int(y + 44)

    defs = f'''<defs>
  <radialGradient id="g1"><stop offset="0" stop-color="{C["violet"]}" stop-opacity=".55"/><stop offset="1" stop-color="{C["violet"]}" stop-opacity="0"/></radialGradient>
  <radialGradient id="g2"><stop offset="0" stop-color="{C["magenta"]}" stop-opacity=".45"/><stop offset="1" stop-color="{C["magenta"]}" stop-opacity="0"/></radialGradient>
  <radialGradient id="g3"><stop offset="0" stop-color="{C["teal"]}" stop-opacity=".5"/><stop offset="1" stop-color="{C["teal"]}" stop-opacity="0"/></radialGradient>
  <linearGradient id="beam" x1="0" x2="1"><stop offset="0" stop-color="{C["violet"]}" stop-opacity="0"/><stop offset=".5" stop-color="{C["pink"]}"/><stop offset="1" stop-color="{C["cyan"]}" stop-opacity="0"/></linearGradient>
  <linearGradient id="card" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#151830"/><stop offset="1" stop-color="#0c0e1a"/></linearGradient>
  <filter id="soft" x="-20%" y="-50%" width="140%" height="200%"><feGaussianBlur stdDeviation="6" result="b"/><feColorMatrix in="b" values="0 0 0 0 0.75  0 0 0 0 0.6  0 0 0 0 1  0 0 0 .55 0"/><feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <clipPath id="frame"><rect x="0" y="0" width="{W}" height="{H}" rx="20"/></clipPath>
</defs>'''
    style = f'''<style>
  text {{ font-family: {FONT}; }}
  .glow {{ animation: drift 14s ease-in-out infinite alternate; transform-origin: center; transform-box: fill-box; }}
  .tw {{ animation: tw 6s ease-in-out infinite; }}
  .hot {{ animation: hot 3.2s ease-in-out infinite; }}
  @keyframes drift {{ from {{ opacity: .75; transform: scale(1); }} to {{ opacity: 1; transform: scale(1.12); }} }}
  @keyframes tw {{ 0%,100% {{ opacity: .15; }} 50% {{ opacity: .9; }} }}
  @keyframes hot {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: .55; }} }}
  @media (prefers-reduced-motion: reduce) {{ .glow, .tw, .hot {{ animation: none; }} }}
</style>'''
    background = f'''<g clip-path="url(#frame)">
  <rect width="{W}" height="{H}" fill="{C["bg"]}"/>
  <ellipse class="glow" cx="190" cy="120" rx="300" ry="190" fill="url(#g1)"/>
  <ellipse class="glow" style="animation-delay:-5s" cx="470" cy="70" rx="260" ry="170" fill="url(#g2)"/>
  <ellipse class="glow" style="animation-delay:-9s" cx="720" cy="190" rx="280" ry="190" fill="url(#g3)"/>
  <ellipse class="glow" style="animation-delay:-3s" cx="440" cy="{max(H*0.45, 600):.0f}" rx="380" ry="160" fill="url(#g1)" opacity=".5"/>
  <ellipse class="glow" style="animation-delay:-7s" cx="760" cy="{max(H*0.62, 800):.0f}" rx="260" ry="160" fill="url(#g2)" opacity=".45"/>'''
    title = esc(f'{cfg["name"]} - {cfg["title"]}')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="t">\n'
            f'<title id="t">{title}</title>\n{defs}\n{style}\n{background}\n'
            + "\n".join(parts) +
            f'\n</g>\n<rect x=".5" y=".5" width="{W-1}" height="{H-1}" rx="20" fill="none" stroke="{C["line2"]}"/>\n</svg>\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-json")
    ap.add_argument("--dump")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    with open(CONFIG, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if args.from_json:
        with open(args.from_json, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = fetch_all(cfg)
    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
    svg = build_svg(cfg, data)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"wrote {args.out}: {len(data['repos'])} repos, {len(data.get('days') or [])} days, credly={data.get('credly')}")


if __name__ == "__main__":
    main()
