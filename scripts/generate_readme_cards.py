#!/usr/bin/env python3
"""Generate reliable GitHub profile cards from the public REST API."""

from __future__ import annotations

import argparse
import html
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


API_ROOT = "https://api.github.com"
LANGUAGE_COLORS = {
    "C": "#555555",
    "C#": "#178600",
    "C++": "#f34b7d",
    "CSS": "#563d7c",
    "Go": "#00ADD8",
    "HTML": "#e34c26",
    "Java": "#b07219",
    "JavaScript": "#f1e05a",
    "Jupyter Notebook": "#DA5B0B",
    "Kotlin": "#A97BFF",
    "Python": "#3572A5",
    "Rust": "#dea584",
    "Shell": "#89e051",
    "Swift": "#F05138",
    "TypeScript": "#3178c6",
}
FALLBACK_COLORS = (
    "#0891b2",
    "#14b8a6",
    "#8b5cf6",
    "#f59e0b",
    "#ef4444",
    "#64748b",
)


def api_json(path_or_url: str) -> object:
    """Fetch one public GitHub API response with bounded retries."""
    url = (
        path_or_url
        if path_or_url.startswith("https://")
        else f"{API_ROOT}{path_or_url}"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "LiuMengxuan04-readme-card-generator",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)

    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            retryable = error.code in {403, 408, 429, 500, 502, 503, 504}
            if not retryable or attempt == 2:
                details = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"GitHub API request failed ({error.code}) for {url}: {details}"
                ) from error
        except urllib.error.URLError as error:
            if attempt == 2:
                raise RuntimeError(f"GitHub API request failed for {url}: {error}") from error

        time.sleep(2**attempt)

    raise RuntimeError(f"GitHub API request failed for {url}")


def fetch_repositories(username: str) -> list[dict[str, object]]:
    """Fetch every public repository owned by the user."""
    repositories: list[dict[str, object]] = []
    encoded_username = urllib.parse.quote(username, safe="")

    for page in range(1, 11):
        result = api_json(
            f"/users/{encoded_username}/repos"
            f"?type=owner&sort=updated&per_page=100&page={page}"
        )
        if not isinstance(result, list):
            raise RuntimeError("GitHub repositories response was not a list")
        repositories.extend(result)
        if len(result) < 100:
            break

    return repositories


def fetch_language_totals(repositories: list[dict[str, object]]) -> Counter[str]:
    """Aggregate language bytes for non-fork public repositories."""
    totals: Counter[str] = Counter()

    for repository in repositories:
        if repository.get("fork") or repository.get("archived"):
            continue
        languages_url = repository.get("languages_url")
        if not isinstance(languages_url, str):
            continue
        languages = api_json(languages_url)
        if not isinstance(languages, dict):
            raise RuntimeError(f"Languages response was invalid for {repository.get('name')}")
        for language, byte_count in languages.items():
            if isinstance(language, str) and isinstance(byte_count, int):
                totals[language] += byte_count

    return totals


def compact_number(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m".replace(".0m", "m")
    if value >= 1_000:
        return f"{value / 1_000:.1f}k".replace(".0k", "k")
    return str(value)


def render_stats_card(
    display_name: str,
    stars: int,
    public_repos: int,
    followers: int,
    following: int,
    updated_at: str,
) -> str:
    safe_name = html.escape(display_name)
    description = html.escape(
        f"Total stars: {stars}; public repositories: {public_repos}; "
        f"followers: {followers}; following: {following}"
    )
    metrics = (
        ("Total Stars", f"{stars:,}"),
        ("Public Repos", f"{public_repos:,}"),
        ("Followers", f"{followers:,}"),
        ("Following", f"{following:,}"),
    )

    metric_svg = []
    for index, (label, value) in enumerate(metrics):
        column = index % 2
        row = index // 2
        x = 25 + column * 220
        y = 80 + row * 48
        metric_svg.append(
            f"""
    <g transform="translate({x} {y})">
      <circle cx="7" cy="-5" r="5" class="dot"/>
      <text x="22" y="0" class="label">{html.escape(label)}</text>
      <text x="22" y="22" class="value">{html.escape(value)}</text>
    </g>"""
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="467" height="170" viewBox="0 0 467 170" role="img" aria-labelledby="title desc">
  <title id="title">{safe_name}'s GitHub Stats</title>
  <desc id="desc">{description}</desc>
  <style>
    .header {{ font: 700 18px Inter, "Segoe UI", sans-serif; fill: #0891b2; }}
    .label {{ font: 500 12px Inter, "Segoe UI", sans-serif; fill: #64748b; }}
    .value {{ font: 700 17px Inter, "Segoe UI", sans-serif; fill: #0f172a; }}
    .updated {{ font: 400 10px Inter, "Segoe UI", sans-serif; fill: #94a3b8; }}
    .dot {{ fill: #14b8a6; }}
    .border {{ fill: #ffffff00; stroke: #e2e8f0; }}
    @media (prefers-color-scheme: dark) {{
      .value {{ fill: #e2e8f0; }}
      .label {{ fill: #94a3b8; }}
      .border {{ stroke: #334155; }}
    }}
  </style>
  <rect x=".5" y=".5" width="466" height="169" rx="10" class="border"/>
  <text x="25" y="34" class="header">{safe_name}'s GitHub Stats</text>
  <text x="442" y="33" text-anchor="end" class="updated">{html.escape(updated_at)}</text>
  <path d="M25 49H442" stroke="#e2e8f0"/>{''.join(metric_svg)}
</svg>
"""


def language_color(language: str, index: int) -> str:
    return LANGUAGE_COLORS.get(language, FALLBACK_COLORS[index % len(FALLBACK_COLORS)])


def render_languages_card(language_totals: Counter[str], updated_at: str) -> str:
    top_languages = language_totals.most_common(6)
    total_bytes = sum(language_totals.values())
    if not top_languages or total_bytes <= 0:
        raise RuntimeError("No language data was returned by GitHub")

    bar_segments = []
    legend_entries = []
    cursor = 20.0
    bar_width = 280.0

    for index, (language, byte_count) in enumerate(top_languages):
        percentage = byte_count / total_bytes * 100
        width = bar_width * percentage / 100
        color = language_color(language, index)
        bar_segments.append(
            f'<rect x="{cursor:.2f}" y="54" width="{width:.2f}" height="8" fill="{color}"/>'
        )
        cursor += width

        column = index % 2
        row = index // 2
        x = 20 + column * 150
        y = 91 + row * 24
        legend_entries.append(
            f"""
    <g transform="translate({x} {y})">
      <circle cx="5" cy="-4" r="5" fill="{color}"/>
      <text x="16" y="0" class="language">{html.escape(language)}</text>
      <text x="135" y="0" text-anchor="end" class="percent">{percentage:.1f}%</text>
    </g>"""
        )

    if cursor < 300:
        bar_segments.append(
            f'<rect x="{cursor:.2f}" y="54" width="{300 - cursor:.2f}" height="8" fill="#e2e8f0"/>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="320" height="170" viewBox="0 0 320 170" role="img" aria-labelledby="title desc">
  <title id="title">Most Used Languages</title>
  <desc id="desc">Top languages by bytes across non-fork public repositories.</desc>
  <style>
    .header {{ font: 700 18px Inter, "Segoe UI", sans-serif; fill: #0891b2; }}
    .language {{ font: 600 11px Inter, "Segoe UI", sans-serif; fill: #334155; }}
    .percent {{ font: 400 10px Inter, "Segoe UI", sans-serif; fill: #64748b; }}
    .updated {{ font: 400 9px Inter, "Segoe UI", sans-serif; fill: #94a3b8; }}
    .border {{ fill: #ffffff00; stroke: #e2e8f0; }}
    @media (prefers-color-scheme: dark) {{
      .language {{ fill: #cbd5e1; }}
      .percent {{ fill: #94a3b8; }}
      .border {{ stroke: #334155; }}
    }}
  </style>
  <rect x=".5" y=".5" width="319" height="169" rx="10" class="border"/>
  <text x="20" y="28" class="header">Most Used Languages</text>
  <text x="300" y="44" text-anchor="end" class="updated">{html.escape(updated_at)}</text>
  <clipPath id="bar-clip"><rect x="20" y="54" width="280" height="8" rx="4"/></clipPath>
  <g clip-path="url(#bar-clip)">{''.join(bar_segments)}</g>{''.join(legend_entries)}
</svg>
"""


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    encoded_username = urllib.parse.quote(args.username, safe="")
    profile = api_json(f"/users/{encoded_username}")
    if not isinstance(profile, dict):
        raise RuntimeError("GitHub profile response was not an object")

    repositories = fetch_repositories(args.username)
    language_totals = fetch_language_totals(repositories)
    stars = sum(
        int(repository.get("stargazers_count", 0))
        for repository in repositories
    )
    updated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    stats_svg = render_stats_card(
        display_name=str(profile.get("name") or args.username),
        stars=stars,
        public_repos=int(profile.get("public_repos", len(repositories))),
        followers=int(profile.get("followers", 0)),
        following=int(profile.get("following", 0)),
        updated_at=updated_at,
    )
    languages_svg = render_languages_card(language_totals, updated_at)

    write_atomic(args.output_dir / "stats.svg", stats_svg)
    write_atomic(args.output_dir / "top-langs.svg", languages_svg)

    print(
        json.dumps(
            {
                "username": args.username,
                "stars": stars,
                "public_repositories": int(profile.get("public_repos", len(repositories))),
                "followers": int(profile.get("followers", 0)),
                "following": int(profile.get("following", 0)),
                "languages": language_totals.most_common(6),
                "updated_at": updated_at,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
