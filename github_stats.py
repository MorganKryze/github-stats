#!/usr/bin/python3
# github_stats.py

import asyncio
import os
import random
import time
from typing import Any, Dict, List, Optional, Set, Tuple, cast

import aiohttp
import requests


###############################################################################
# Main Classes
###############################################################################


class Queries(object):
    """
    Class with functions to query the GitHub GraphQL (v4) API and the REST (v3)
    API. Also includes functions to dynamically generate GraphQL queries.
    """

    def __init__(
        self,
        username: str,
        access_token: str,
        session: aiohttp.ClientSession,
        max_connections: int = 10,
        rest_max_retries: int = 5,
        rest_base_delay_seconds: float = 1.0,
        rest_max_delay_seconds: float = 20.0,
    ):
        self.username = username
        self.access_token = access_token
        self.session = session
        self.semaphore = asyncio.Semaphore(max_connections)
        self.rest_max_retries = rest_max_retries
        self.rest_base_delay_seconds = rest_base_delay_seconds
        self.rest_max_delay_seconds = rest_max_delay_seconds

    async def query(self, generated_query: str) -> Dict:
        """
        Make a request to the GraphQL API using the authentication token from
        the environment.
        """
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            async with self.semaphore:
                r_async = await self.session.post(
                    "https://api.github.com/graphql",
                    headers=headers,
                    json={"query": generated_query},
                )
            result = await r_async.json(content_type=None)
            if result is not None:
                return result
        except Exception:
            print("aiohttp failed for GraphQL query")
            async with self.semaphore:
                r_requests = await asyncio.to_thread(
                    requests.post,
                    "https://api.github.com/graphql",
                    headers=headers,
                    json={"query": generated_query},
                    timeout=30,
                )
                result = r_requests.json()
                if result is not None:
                    return result
        return {}

    def _retry_delay(self, status: int, headers: Dict[str, str], attempt: int) -> float:
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), self.rest_max_delay_seconds)
            except ValueError:
                pass

        if status == 403:
            reset = headers.get("X-RateLimit-Reset")
            if reset:
                try:
                    wait = max(0.0, float(reset) - time.time()) + 0.25
                    return min(wait, self.rest_max_delay_seconds)
                except ValueError:
                    pass

        exp = self.rest_base_delay_seconds * (2**attempt)
        jitter = random.uniform(0.0, 0.5)
        return min(exp + jitter, self.rest_max_delay_seconds)

    async def query_rest(self, path: str, params: Optional[Dict] = None) -> Any:
        """
        Make a request to the REST API.
        """
        request_path = path[1:] if path.startswith("/") else path
        request_params = tuple((params or {}).items())

        headers = {"Authorization": f"token {self.access_token}"}

        for attempt in range(self.rest_max_retries):
            try:
                async with self.semaphore:
                    r_async = await self.session.get(
                        f"https://api.github.com/{request_path}",
                        headers=headers,
                        params=request_params,
                    )

                if r_async.status in (202, 429, 502, 503, 504):
                    delay = self._retry_delay(r_async.status, dict(r_async.headers), attempt)
                    print(
                        f"{request_path} returned {r_async.status}. "
                        f"Retry {attempt + 1}/{self.rest_max_retries} in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                    continue

                if r_async.status >= 400:
                    text = await r_async.text()
                    print(
                        f"{request_path} failed with status {r_async.status}. "
                        f"Response: {text[:200]}"
                    )
                    return {}

                result = await r_async.json(content_type=None)
                if result is not None:
                    return result
            except Exception as exc:
                print(f"aiohttp failed for REST query ({request_path}): {exc}")
                async with self.semaphore:
                    r_requests = await asyncio.to_thread(
                        requests.get,
                        f"https://api.github.com/{request_path}",
                        headers=headers,
                        params=request_params,
                        timeout=30,
                    )

                if r_requests.status_code in (202, 429, 502, 503, 504):
                    delay = self._retry_delay(
                        r_requests.status_code, dict(r_requests.headers), attempt
                    )
                    print(
                        f"{request_path} returned {r_requests.status_code} (requests fallback). "
                        f"Retry {attempt + 1}/{self.rest_max_retries} in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                    continue

                if r_requests.status_code == 200:
                    return r_requests.json()

                print(
                    f"{request_path} failed with status {r_requests.status_code} "
                    "(requests fallback)."
                )
                return {}

        print(f"Too many retries for {request_path}. Data for this endpoint will be incomplete.")
        return {}

    @staticmethod
    def repos_overview(
        contrib_cursor: Optional[str] = None, owned_cursor: Optional[str] = None
    ) -> str:
        return f"""{{
  viewer {{
    login,
    name,
    repositories(
        first: 100,
        orderBy: {{
            field: UPDATED_AT,
            direction: DESC
        }},
        isFork: false,
        after: {"null" if owned_cursor is None else '"'+ owned_cursor +'"'}
    ) {{
      pageInfo {{
        hasNextPage
        endCursor
      }}
      nodes {{
        nameWithOwner
        stargazers {{
          totalCount
        }}
        forkCount
        languages(first: 10, orderBy: {{field: SIZE, direction: DESC}}) {{
          edges {{
            size
            node {{
              name
              color
            }}
          }}
        }}
      }}
    }}
    repositoriesContributedTo(
        first: 100,
        includeUserRepositories: false,
        orderBy: {{
            field: UPDATED_AT,
            direction: DESC
        }},
        contributionTypes: [
            COMMIT,
            PULL_REQUEST,
            REPOSITORY,
            PULL_REQUEST_REVIEW
        ]
        after: {"null" if contrib_cursor is None else '"'+ contrib_cursor +'"'}
    ) {{
      pageInfo {{
        hasNextPage
        endCursor
      }}
      nodes {{
        nameWithOwner
        stargazers {{
          totalCount
        }}
        forkCount
        languages(first: 10, orderBy: {{field: SIZE, direction: DESC}}) {{
          edges {{
            size
            node {{
              name
              color
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""

    @staticmethod
    def contrib_years() -> str:
        return """
query {
  viewer {
    contributionsCollection {
      contributionYears
    }
  }
}
"""

    @staticmethod
    def contribs_by_year(year: str) -> str:
        return f"""
    year{year}: contributionsCollection(
        from: "{year}-01-01T00:00:00Z",
        to: "{int(year) + 1}-01-01T00:00:00Z"
    ) {{
      contributionCalendar {{
        totalContributions
      }}
    }}
"""

    @classmethod
    def all_contribs(cls, years: List[str]) -> str:
        by_years = "\n".join(map(cls.contribs_by_year, years))
        return f"""
query {{
  viewer {{
    {by_years}
  }}
}}
"""


class Stats(object):
    """
    Retrieve and store statistics about GitHub usage.
    """

    def __init__(
        self,
        username: str,
        access_token: str,
        session: aiohttp.ClientSession,
        exclude_repos: Optional[Set] = None,
        exclude_langs: Optional[Set] = None,
        ignore_forked_repos: bool = False,
    ):
        self.username = username
        self._ignore_forked_repos = ignore_forked_repos
        self._exclude_repos = set() if exclude_repos is None else exclude_repos
        self._exclude_langs = set() if exclude_langs is None else exclude_langs
        self.queries = Queries(username, access_token, session)

        self._name: Optional[str] = None
        self._stargazers: Optional[int] = None
        self._forks: Optional[int] = None
        self._total_contributions: Optional[int] = None
        self._languages: Optional[Dict[str, Any]] = None
        self._repos: Optional[Set[str]] = None
        self._lines_changed: Optional[Tuple[int, int]] = None
        self._views: Optional[int] = None

    async def to_str(self) -> str:
        languages = await self.languages_proportional
        formatted_languages = "\n  - ".join(
            [f"{k}: {v:0.4f}%" for k, v in languages.items()]
        )
        lines_changed = await self.lines_changed
        return f"""Name: {await self.name}
Stargazers: {await self.stargazers:,}
Forks: {await self.forks:,}
All-time contributions: {await self.total_contributions:,}
Repositories with contributions: {len(await self.repos)}
Lines of code added: {lines_changed[0]:,}
Lines of code deleted: {lines_changed[1]:,}
Lines of code changed: {lines_changed[0] + lines_changed[1]:,}
Project page views: {await self.views:,}
Languages:
  - {formatted_languages}"""

    async def get_stats(self) -> None:
        self._stargazers = 0
        self._forks = 0
        self._languages = {}
        self._repos = set()

        exclude_langs_lower = {x.lower() for x in self._exclude_langs}

        next_owned = None
        next_contrib = None
        while True:
            raw_results = await self.queries.query(
                Queries.repos_overview(
                    owned_cursor=next_owned, contrib_cursor=next_contrib
                )
            )
            raw_results = raw_results if raw_results is not None else {}

            self._name = raw_results.get("data", {}).get("viewer", {}).get("name", None)
            if self._name is None:
                self._name = (
                    raw_results.get("data", {}).get("viewer", {}).get("login", "No Name")
                )

            contrib_repos = (
                raw_results.get("data", {})
                .get("viewer", {})
                .get("repositoriesContributedTo", {})
            )
            owned_repos = (
                raw_results.get("data", {}).get("viewer", {}).get("repositories", {})
            )

            repos = owned_repos.get("nodes", [])
            if not self._ignore_forked_repos:
                repos += contrib_repos.get("nodes", [])

            for repo in repos:
                if repo is None:
                    continue
                name = repo.get("nameWithOwner")
                if name in self._repos or name in self._exclude_repos:
                    continue

                self._repos.add(name)
                self._stargazers += repo.get("stargazers", {}).get("totalCount", 0)
                self._forks += repo.get("forkCount", 0)

                for lang in repo.get("languages", {}).get("edges", []):
                    lang_name = lang.get("node", {}).get("name", "Other")
                    languages = await self.languages
                    if lang_name.lower() in exclude_langs_lower:
                        continue
                    if lang_name in languages:
                        languages[lang_name]["size"] += lang.get("size", 0)
                        languages[lang_name]["occurrences"] += 1
                    else:
                        languages[lang_name] = {
                            "size": lang.get("size", 0),
                            "occurrences": 1,
                            "color": lang.get("node", {}).get("color"),
                        }

            if owned_repos.get("pageInfo", {}).get(
                "hasNextPage", False
            ) or contrib_repos.get("pageInfo", {}).get("hasNextPage", False):
                next_owned = owned_repos.get("pageInfo", {}).get("endCursor", next_owned)
                next_contrib = contrib_repos.get("pageInfo", {}).get(
                    "endCursor", next_contrib
                )
            else:
                break

        langs_total = sum([v.get("size", 0) for v in self._languages.values()])
        if langs_total > 0:
            for _, v in self._languages.items():
                v["prop"] = 100 * (v.get("size", 0) / langs_total)
        else:
            for _, v in self._languages.items():
                v["prop"] = 0.0

    @property
    async def name(self) -> str:
        if self._name is not None:
            return self._name
        await self.get_stats()
        assert self._name is not None
        return self._name

    @property
    async def stargazers(self) -> int:
        if self._stargazers is not None:
            return self._stargazers
        await self.get_stats()
        assert self._stargazers is not None
        return self._stargazers

    @property
    async def forks(self) -> int:
        if self._forks is not None:
            return self._forks
        await self.get_stats()
        assert self._forks is not None
        return self._forks

    @property
    async def languages(self) -> Dict:
        if self._languages is not None:
            return self._languages
        await self.get_stats()
        assert self._languages is not None
        return self._languages

    @property
    async def languages_proportional(self) -> Dict:
        if self._languages is None:
            await self.get_stats()
            assert self._languages is not None
        return {k: v.get("prop", 0) for (k, v) in self._languages.items()}

    @property
    async def repos(self) -> Set[str]:
        if self._repos is not None:
            return self._repos
        await self.get_stats()
        assert self._repos is not None
        return self._repos

    @property
    async def total_contributions(self) -> int:
        if self._total_contributions is not None:
            return self._total_contributions

        self._total_contributions = 0
        years = (
            (await self.queries.query(Queries.contrib_years()))
            .get("data", {})
            .get("viewer", {})
            .get("contributionsCollection", {})
            .get("contributionYears", [])
        )
        by_year = (
            (await self.queries.query(Queries.all_contribs(years)))
            .get("data", {})
            .get("viewer", {})
            .values()
        )
        for year in by_year:
            self._total_contributions += year.get("contributionCalendar", {}).get(
                "totalContributions", 0
            )
        return cast(int, self._total_contributions)

    @property
    async def lines_changed(self) -> Tuple[int, int]:
        if self._lines_changed is not None:
            return self._lines_changed

        async def fetch(repo: str) -> Tuple[int, int]:
            r = await self.queries.query_rest(f"/repos/{repo}/stats/contributors")
            if not isinstance(r, list):
                return 0, 0
            a = d = 0
            for author_obj in r:
                if not isinstance(author_obj, dict):
                    continue
                author = author_obj.get("author") or {}
                if not isinstance(author, dict) or author.get("login") != self.username:
                    continue
                for week in author_obj.get("weeks", []):
                    a += week.get("a", 0)
                    d += week.get("d", 0)
            return a, d

        repos = await self.repos
        results = await asyncio.gather(*(fetch(r) for r in repos))
        additions = sum(x[0] for x in results)
        deletions = sum(x[1] for x in results)

        self._lines_changed = (additions, deletions)
        return self._lines_changed

    @property
    async def views(self) -> int:
        if self._views is not None:
            return self._views

        async def fetch(repo: str) -> int:
            r = await self.queries.query_rest(f"/repos/{repo}/traffic/views")
            if not isinstance(r, dict):
                return 0
            return sum(view.get("count", 0) for view in r.get("views", []))

        repos = await self.repos
        counts = await asyncio.gather(*(fetch(r) for r in repos))
        total = sum(counts)

        self._views = total
        return total


###############################################################################
# Main Function
###############################################################################


async def main() -> None:
    access_token = os.getenv("ACCESS_TOKEN")
    user = os.getenv("GITHUB_ACTOR")
    if access_token is None or user is None:
        raise RuntimeError(
            "ACCESS_TOKEN and GITHUB_ACTOR environment variables cannot be None!"
        )
    async with aiohttp.ClientSession() as session:
        s = Stats(user, access_token, session)
        print(await s.to_str())


if __name__ == "__main__":
    asyncio.run(main())
