#!/usr/bin/env python3
"""Generate reliable GitHub profile cards from the public REST API."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


API_ROOT = "https://api.github.com"
GRAPHQL_URL = "https://api.github.com/graphql"
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


def graphql_json(query: str, variables: dict[str, object]) -> dict[str, object]:
    """Run an authenticated GitHub GraphQL query with bounded retries."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required to fetch contribution activity")

    request = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "LiuMengxuan04-readme-card-generator",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise RuntimeError("GitHub GraphQL response was not an object")
            errors = result.get("errors")
            if errors:
                raise RuntimeError(f"GitHub GraphQL query failed: {errors}")
            return result
        except urllib.error.HTTPError as error:
            retryable = error.code in {403, 408, 429, 500, 502, 503, 504}
            if not retryable or attempt == 2:
                details = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"GitHub GraphQL request failed ({error.code}): {details}"
                ) from error
        except urllib.error.URLError as error:
            if attempt == 2:
                raise RuntimeError(f"GitHub GraphQL request failed: {error}") from error

        time.sleep(2**attempt)

    raise RuntimeError("GitHub GraphQL request failed")


def fetch_contribution_weeks(username: str) -> list[tuple[str, int]]:
    """Return the most recent 52 contribution weeks and their totals."""
    result = graphql_json(
        """
        query ContributionActivity($login: String!) {
          user(login: $login) {
            contributionsCollection {
              contributionCalendar {
                weeks {
                  contributionDays {
                    contributionCount
                    date
                  }
                }
              }
            }
          }
        }
        """,
        {"login": username},
    )

    try:
        weeks = result["data"]["user"]["contributionsCollection"][
            "contributionCalendar"
        ]["weeks"]
    except (KeyError, TypeError) as error:
        raise RuntimeError("GitHub contribution response had an unexpected shape") from error

    if not isinstance(weeks, list) or not weeks:
        raise RuntimeError("GitHub returned no contribution weeks")

    weekly_totals: list[tuple[str, int]] = []
    for week in weeks[-52:]:
        if not isinstance(week, dict):
            raise RuntimeError("GitHub contribution week was invalid")
        days = week.get("contributionDays")
        if not isinstance(days, list) or not days:
            raise RuntimeError("GitHub contribution week contained no days")
        first_day = days[0]
        if not isinstance(first_day, dict) or not isinstance(first_day.get("date"), str):
            raise RuntimeError("GitHub contribution day was invalid")
        total = sum(
            int(day.get("contributionCount", 0))
            for day in days
            if isinstance(day, dict)
        )
        weekly_totals.append((str(first_day["date"]), total))

    return weekly_totals


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


def nice_axis_max(value: int) -> int:
    """Round a chart maximum up to a readable tick value."""
    value = max(value, 5)
    magnitude = 10 ** math.floor(math.log10(value))
    normalized = value / magnitude
    for candidate in (1, 2, 5, 10):
        if normalized <= candidate:
            return int(candidate * magnitude)
    return int(10 * magnitude)


def render_activity_card(
    username: str,
    contribution_weeks: list[tuple[str, int]],
    updated_at: str,
) -> str:
    if len(contribution_weeks) < 2:
        raise RuntimeError("At least two contribution weeks are required")

    width = 900
    height = 250
    plot_left = 58
    plot_right = 875
    plot_top = 65
    plot_bottom = 196
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top
    counts = [count for _, count in contribution_weeks]
    axis_max = nice_axis_max(max(counts))
    total = sum(counts)

    points: list[tuple[float, float]] = []
    point_elements: list[str] = []
    for index, (week_start, count) in enumerate(contribution_weeks):
        x = plot_left + plot_width * index / (len(contribution_weeks) - 1)
        y = plot_bottom - plot_height * count / axis_max
        points.append((x, y))
        point_elements.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" class="point">'
            f'<title>Week of {html.escape(week_start)}: {count} contributions</title>'
            "</circle>"
        )

    line_path = " ".join(
        f"{'M' if index == 0 else 'L'} {x:.2f} {y:.2f}"
        for index, (x, y) in enumerate(points)
    )
    area_path = (
        f"M {points[0][0]:.2f} {plot_bottom} "
        + " ".join(f"L {x:.2f} {y:.2f}" for x, y in points)
        + f" L {points[-1][0]:.2f} {plot_bottom} Z"
    )

    grid_elements: list[str] = []
    for fraction in (0.0, 0.5, 1.0):
        value = round(axis_max * fraction)
        y = plot_bottom - plot_height * fraction
        grid_elements.append(
            f'<line x1="{plot_left}" y1="{y:.2f}" x2="{plot_right}" '
            f'y2="{y:.2f}" class="grid"/>'
            f'<text x="{plot_left - 10}" y="{y + 4:.2f}" '
            f'text-anchor="end" class="axis">{value}</text>'
        )

    month_elements: list[str] = []
    previous_month = ""
    for index, (week_start, _) in enumerate(contribution_weeks):
        week_date = datetime.strptime(week_start, "%Y-%m-%d")
        month_key = week_date.strftime("%Y-%m")
        if month_key == previous_month:
            continue
        previous_month = month_key
        x = plot_left + plot_width * index / (len(contribution_weeks) - 1)
        label = week_date.strftime("%b")
        if week_date.month == 1:
            label = week_date.strftime("%b %Y")
        month_elements.append(
            f'<text x="{x:.2f}" y="220" text-anchor="middle" '
            f'class="axis">{label}</text>'
        )

    safe_username = html.escape(username)
    description = html.escape(
        f"{total} contributions by {username} across the most recent 52 weeks"
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">{safe_username}'s Contribution Signal</title>
  <desc id="desc">{description}</desc>
  <style>
    .header {{ font: 700 18px Inter, "Segoe UI", sans-serif; fill: #0891b2; }}
    .summary {{ font: 500 12px Inter, "Segoe UI", sans-serif; fill: #64748b; }}
    .axis {{ font: 400 10px Inter, "Segoe UI", sans-serif; fill: #64748b; }}
    .updated {{ font: 400 10px Inter, "Segoe UI", sans-serif; fill: #94a3b8; }}
    .border {{ fill: #ffffff00; stroke: #e2e8f0; }}
    .grid {{ stroke: #e2e8f0; stroke-width: 1; }}
    .area {{ fill: #ccfbf1; fill-opacity: .68; }}
    .line {{ fill: none; stroke: #0891b2; stroke-width: 2.5; stroke-linejoin: round; stroke-linecap: round; }}
    .point {{ fill: #14b8a6; stroke: #ffffff; stroke-width: 1.5; }}
    @media (prefers-color-scheme: dark) {{
      .summary, .axis {{ fill: #94a3b8; }}
      .border, .grid {{ stroke: #334155; }}
      .area {{ fill: #134e4a; fill-opacity: .58; }}
      .point {{ stroke: #0d1117; }}
    }}
  </style>
  <rect x=".5" y=".5" width="{width - 1}" height="{height - 1}" rx="10" class="border"/>
  <text x="24" y="30" class="header">Contribution Signal</text>
  <text x="24" y="49" class="summary">{total:,} contributions in the last 52 weeks</text>
  <text x="876" y="30" text-anchor="end" class="updated">{html.escape(updated_at)}</text>
  {''.join(grid_elements)}
  <path d="{area_path}" class="area"/>
  <path d="{line_path}" class="line"/>
  {''.join(point_elements)}
  {''.join(month_elements)}
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
    contribution_weeks = fetch_contribution_weeks(args.username)
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
    activity_svg = render_activity_card(
        args.username,
        contribution_weeks,
        updated_at,
    )

    write_atomic(args.output_dir / "stats.svg", stats_svg)
    write_atomic(args.output_dir / "top-langs.svg", languages_svg)
    write_atomic(args.output_dir / "activity.svg", activity_svg)

    print(
        json.dumps(
            {
                "username": args.username,
                "stars": stars,
                "public_repositories": int(profile.get("public_repos", len(repositories))),
                "followers": int(profile.get("followers", 0)),
                "following": int(profile.get("following", 0)),
                "languages": language_totals.most_common(6),
                "contributions_last_52_weeks": sum(
                    count for _, count in contribution_weeks
                ),
                "updated_at": updated_at,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
