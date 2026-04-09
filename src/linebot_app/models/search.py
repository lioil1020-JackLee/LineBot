from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def format_search_results(results: list[SearchResult]) -> str:
    if not results:
        return "No search results were found."

    lines: list[str] = []
    for index, item in enumerate(results, start=1):
        lines.append(f"{index}. {item.title or '(untitled)'}")
        if item.url:
            lines.append(f"   URL: {item.url}")
        if item.snippet:
            lines.append(f"   {item.snippet}")
    return "\n".join(lines)

