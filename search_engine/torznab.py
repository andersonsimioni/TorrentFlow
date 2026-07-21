from __future__ import annotations

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path


TORZNAB_NAMESPACE = "http://torznab.com/schemas/2015/feed"


@dataclass(frozen=True)
class SearchSource:
    name: str
    url: str
    api_key: str = ""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    size: int
    seeders: int
    leechers: int
    source: str
    info_hash: str = ""


def load_sources(config_path: Path) -> list[SearchSource]:
    if not config_path.is_file():
        raise RuntimeError(
            f"search configuration was not found: {config_path}\n"
            "Copy search_engine/search_config.example.json to "
            "search_config.json and add your Torznab connection details."
        )

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"invalid search configuration: {error}") from error

    sources = []
    for item in data.get("sources", []):
        if not item.get("enabled", True):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if not name or not url.startswith(("http://", "https://")):
            raise RuntimeError("each enabled search source needs a name and URL")
        sources.append(SearchSource(name, url, str(item.get("api_key", "")).strip()))

    if not sources:
        raise RuntimeError("no enabled Torznab sources were found in the configuration")
    return sources


def _attribute(item: ET.Element, name: str, default: str = "") -> str:
    for attribute in item.findall(f"{{{TORZNAB_NAMESPACE}}}attr"):
        if attribute.get("name", "").lower() == name.lower():
            return attribute.get("value", default)
    return default


def _integer(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _result_url(item: ET.Element) -> str:
    magnet = _attribute(item, "magneturl")
    download = _attribute(item, "downloadurl")
    if magnet or download:
        return magnet or download
    enclosure = item.find("enclosure")
    if enclosure is not None and enclosure.get("url"):
        return enclosure.get("url", "")
    return (item.findtext("link") or "").strip()


def search_source(source: SearchSource, query: str, timeout: int = 30) -> list[SearchResult]:
    parsed = urllib.parse.urlsplit(source.url)
    parameters = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    parameters.update({"t": "search", "q": query, "extended": "1"})
    if source.api_key:
        parameters["apikey"] = source.api_key
    url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(parameters), parsed.fragment)
    )
    request = urllib.request.Request(url, headers={"User-Agent": "LiveTorrentClient/1.0"})

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            root = ET.fromstring(response.read())
    except Exception as error:
        raise RuntimeError(f"{source.name}: {error}") from error

    results = []
    for item in root.findall("./channel/item"):
        result_url = _result_url(item)
        title = (item.findtext("title") or "").strip()
        if not title or not result_url:
            continue
        size = _integer(item.findtext("size") or _attribute(item, "size"))
        seeders = _integer(_attribute(item, "seeders"))
        peers = _integer(_attribute(item, "peers"))
        leechers = _integer(_attribute(item, "leechers"))
        if not leechers and peers >= seeders:
            leechers = peers - seeders
        results.append(
            SearchResult(
                title=title,
                url=result_url,
                size=size,
                seeders=seeders,
                leechers=leechers,
                source=_attribute(item, "indexer", source.name),
                info_hash=_attribute(item, "infohash").lower(),
            )
        )
    return results


def search_all(
    sources: list[SearchSource], query: str, timeout: int = 30
) -> tuple[list[SearchResult], list[str]]:
    results: list[SearchResult] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(len(sources), 8)) as executor:
        jobs = {
            executor.submit(search_source, source, query, timeout): source
            for source in sources
        }
        for job in as_completed(jobs):
            try:
                results.extend(job.result())
            except RuntimeError as error:
                errors.append(str(error))

    return merge_results(results), errors


def merge_results(results: list[SearchResult]) -> list[SearchResult]:
    unique: dict[str, SearchResult] = {}
    for result in results:
        key = f"{result.title.casefold()}|{result.size}"
        existing = unique.get(key)
        if existing is None or (
            result.seeders,
            result.url.startswith("magnet:?"),
        ) > (
            existing.seeders,
            existing.url.startswith("magnet:?"),
        ):
            unique[key] = result
    return sorted(unique.values(), key=lambda item: (-item.seeders, item.title.casefold()))
