"""Render PRIVACY.md and TERMS.md into hosted pages under docs/ for GitHub Pages.

Platform developer portals (TikTok, Meta) require a *hosted* privacy policy and terms URL, and a
GitHub blob page is a code view rather than a published document. This renders the same markdown
that lives at the repo root, so there is one source of truth and the pages cannot drift from it.

Run after editing either policy, then commit the generated HTML:

    python scripts/build_policy_pages.py

Requires the `markdown` package (already present in the project venv).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SITE = "https://scottvaaron-ctrl.github.io/AI-MEDIA-ZERO"
REPO = "https://github.com/scottvaaron-ctrl/AI-MEDIA-ZERO"

PAGES = [
    ("PRIVACY.md", "privacy", "Privacy Policy"),
    ("TERMS.md", "terms", "Terms of Service"),
]

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - AI Media Zero</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font: 16px/1.65 system-ui, -apple-system, "Segoe UI", sans-serif;
    max-width: 48rem; margin: 0 auto; padding: 2.5rem 1.25rem 4rem;
  }}
  nav {{ font-size: .9rem; margin-bottom: 2rem; opacity: .8; }}
  nav a {{ margin-right: 1rem; }}
  h1 {{ font-size: 1.6rem; line-height: 1.25; }}
  h2 {{ font-size: 1.15rem; margin-top: 2.2rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; display: block; overflow-x: auto; }}
  th, td {{ border: 1px solid rgba(128,128,128,.35); padding: .5rem .7rem; text-align: left; vertical-align: top; }}
  th {{ background: rgba(128,128,128,.1); }}
  code {{
    font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size: .87em;
    background: rgba(128,128,128,.14); padding: .1rem .35rem; border-radius: 4px;
  }}
  li {{ margin: .35rem 0; }}
  footer {{ margin-top: 3rem; padding-top: 1.25rem; border-top: 1px solid rgba(128,128,128,.3);
            font-size: .85rem; opacity: .7; }}
</style>
</head>
<body>
<nav>
  <a href="{site}/">AI Media Zero</a>
  <a href="{site}/privacy/">Privacy</a>
  <a href="{site}/terms/">Terms</a>
  <a href="{repo}">Source</a>
</nav>
{body}
<footer>
  AI Media Zero is free, open-source software (MIT). Generated from
  <a href="{repo}/blob/main/{source}">{source}</a> on {built}.
</footer>
</body>
</html>
"""

INDEX = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Media Zero</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font: 16px/1.65 system-ui, -apple-system, "Segoe UI", sans-serif;
    max-width: 44rem; margin: 0 auto; padding: 3rem 1.25rem 4rem;
  }}
  h1 {{ font-size: 1.7rem; margin-bottom: .3rem; }}
  p.sub {{ opacity: .75; margin-top: 0; }}
  ul {{ line-height: 2; }}
  footer {{ margin-top: 3rem; font-size: .85rem; opacity: .65; }}
</style>
</head>
<body>
<h1>AI Media Zero</h1>
<p class="sub">An autonomous, zero-budget AI media channel. Open source, MIT licensed.</p>

<p>It researches, writes, fact-checks, narrates and renders short explainer videos on one
computer, publishes them to its owner's own YouTube, TikTok and Bluesky accounts through those
platforms' official APIs, then learns from the performance data. It costs nothing to run.</p>

<ul>
  <li><a href="{site}/privacy/">Privacy Policy</a></li>
  <li><a href="{site}/terms/">Terms of Service</a></li>
  <li><a href="{repo}">Source code on GitHub</a></li>
</ul>

<footer>Built {built}.</footer>
</body>
</html>
"""


def main() -> None:
    built = dt.date.today().isoformat()
    for source, slug, title in PAGES:
        text = (ROOT / source).read_text(encoding="utf-8")
        body = markdown.markdown(text, extensions=["tables", "sane_lists"])
        out = DOCS / slug / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            TEMPLATE.format(
                title=title, body=body, site=SITE, repo=REPO, source=source, built=built
            ),
            encoding="utf-8",
            newline="\n",
        )
        print(f"wrote {out.relative_to(ROOT)}  ({len(body)} bytes of rendered markdown)")

    index = DOCS / "index.html"
    index.write_text(INDEX.format(site=SITE, repo=REPO, built=built), encoding="utf-8", newline="\n")
    print(f"wrote {index.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
