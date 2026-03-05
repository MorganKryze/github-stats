#!/usr/bin/python3
# generate_images.py

import asyncio
import os
import re

import aiohttp

from github_stats import Stats

# Safety caps so CI cannot hang forever.
HTTP_CONNECT_TIMEOUT_SECONDS = int(os.getenv("HTTP_CONNECT_TIMEOUT_SECONDS", "15"))
HTTP_READ_TIMEOUT_SECONDS = int(os.getenv("HTTP_READ_TIMEOUT_SECONDS", "60"))
TOTAL_RUN_TIMEOUT_SECONDS = int(os.getenv("TOTAL_RUN_TIMEOUT_SECONDS", "900"))  # 15m


def generate_output_folder() -> None:
    """Create the output folder if it does not already exist."""
    if not os.path.isdir("generated"):
        os.mkdir("generated")


async def generate_overview(s: Stats) -> None:
    """Generate an SVG badge with summary statistics."""
    with open("templates/overview.svg", "r") as f:
        output = f.read()

    output = re.sub("{{ name }}", await s.name, output)
    output = re.sub("{{ stars }}", f"{await s.stargazers:,}", output)
    output = re.sub("{{ forks }}", f"{await s.forks:,}", output)
    output = re.sub("{{ contributions }}", f"{await s.total_contributions:,}", output)
    changed = (await s.lines_changed)[0] + (await s.lines_changed)[1]
    output = re.sub("{{ lines_changed }}", f"{changed:,}", output)
    output = re.sub("{{ views }}", f"{await s.views:,}", output)
    output = re.sub("{{ repos }}", f"{len(await s.repos):,}", output)

    generate_output_folder()
    with open("generated/overview.svg", "w") as f:
        f.write(output)


async def generate_languages(s: Stats) -> None:
    """Generate an SVG badge with summary languages used."""
    with open("templates/languages.svg", "r") as f:
        output = f.read()

    progress = ""
    lang_list = ""
    sorted_languages = sorted(
        (await s.languages).items(), reverse=True, key=lambda t: t[1].get("size")
    )
    delay_between = 150
    for i, (lang, data) in enumerate(sorted_languages):
        color = data.get("color") or "#000000"
        progress += (
            f'<span style="background-color: {color};'
            f'width: {data.get("prop", 0):0.3f}%;" '
            f'class="progress-item"></span>'
        )
        lang_list += f"""
<li style="animation-delay: {i * delay_between}ms;">
<svg xmlns="http://www.w3.org/2000/svg" class="octicon" style="fill:{color};"
viewBox="0 0 16 16" version="1.1" width="16" height="16"><path
fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8z"></path></svg>
<span class="lang">{lang}</span>
<span class="percent">{data.get("prop", 0):0.2f}%</span>
</li>

"""

    output = re.sub(r"{{ progress }}", progress, output)
    output = re.sub(r"{{ lang_list }}", lang_list, output)

    generate_output_folder()
    with open("generated/languages.svg", "w") as f:
        f.write(output)


async def run_generation() -> None:
    """Generate all badges with bounded network behavior."""
    access_token = os.getenv("ACCESS_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not access_token:
        raise RuntimeError(
            "A personal access token is required. Set ACCESS_TOKEN or GITHUB_TOKEN."
        )

    user = os.getenv("GITHUB_ACTOR")
    if not user:
        raise RuntimeError("Environment variable GITHUB_ACTOR must be set.")

    exclude_repos = os.getenv("EXCLUDED")
    excluded_repos = (
        {x.strip() for x in exclude_repos.split(",")} if exclude_repos else None
    )

    exclude_langs = os.getenv("EXCLUDED_LANGS")
    excluded_langs = (
        {x.strip() for x in exclude_langs.split(",")} if exclude_langs else None
    )

    raw_ignore_forked_repos = os.getenv("EXCLUDE_FORKED_REPOS")
    ignore_forked_repos = (
        bool(raw_ignore_forked_repos)
        and raw_ignore_forked_repos.strip().lower() != "false"
    )

    timeout = aiohttp.ClientTimeout(
        total=HTTP_READ_TIMEOUT_SECONDS,
        connect=HTTP_CONNECT_TIMEOUT_SECONDS,
        sock_connect=HTTP_CONNECT_TIMEOUT_SECONDS,
        sock_read=HTTP_READ_TIMEOUT_SECONDS,
    )

    async with aiohttp.ClientSession(timeout=timeout) as session:
        s = Stats(
            user,
            access_token,
            session,
            exclude_repos=excluded_repos,
            exclude_langs=excluded_langs,
            ignore_forked_repos=ignore_forked_repos,
        )
        await asyncio.gather(generate_languages(s), generate_overview(s))


async def main() -> None:
    try:
        await asyncio.wait_for(run_generation(), timeout=TOTAL_RUN_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"Generation timed out after {TOTAL_RUN_TIMEOUT_SECONDS}s. "
            "Likely stuck retrying GitHub 202 responses."
        ) from exc


if __name__ == "__main__":
    asyncio.run(main())
