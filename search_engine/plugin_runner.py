from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .torznab import SearchResult, merge_results


ROOT = Path(__file__).resolve().parent
WORKER = ROOT / "plugin_worker.py"
OFFICIAL_DIRECTORY = ROOT / "plugins/official"
COMMUNITY_MANIFEST = ROOT / "plugins/community/public/manifest.json"


def qbittorrent_plugin_directories() -> list[Path]:
    directories = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        directories.append(Path(local_app_data) / "qBittorrent/nova3/engines")
    directories.extend(
        [
            Path.home() / ".local/share/qBittorrent/nova3/engines",
            Path.home() / "Library/Application Support/qBittorrent/nova3/engines",
        ]
    )
    return directories


def available_plugins() -> list[tuple[str, Path]]:
    plugins: list[tuple[str, Path]] = []
    installed_modules: set[str] = set()
    for directory in qbittorrent_plugin_directories():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.stem == "__init__":
                continue
            plugins.append((f"qbittorrent:{path.stem}", path))
            installed_modules.add(path.stem)

    for path in sorted(OFFICIAL_DIRECTORY.glob("*.py")):
        if path.stem not in installed_modules:
            plugins.append((f"official:{path.stem}", path))
            installed_modules.add(path.stem)

    community_root = (ROOT / "plugins/community/public").resolve()
    if COMMUNITY_MANIFEST.is_file():
        manifest = json.loads(COMMUNITY_MANIFEST.read_text(encoding="utf-8"))
        for item in manifest.get("plugins", []):
            if item.get("status") != "downloaded":
                continue
            path = (ROOT / item["path"]).resolve()
            if (
                path.is_file()
                and path.is_relative_to(community_root)
                and path.stem not in installed_modules
            ):
                plugins.append((f"community:{item['name']}", path))
                installed_modules.add(path.stem)
    return plugins


def parse_output(output: str, source: str) -> list[SearchResult]:
    results = []
    for line in output.splitlines():
        parts = line.strip().split("|", 7)
        if len(parts) < 6 or not parts[0].startswith(("magnet:?", "http://", "https://")):
            continue
        try:
            size = int(parts[2])
            seeders = int(parts[3])
            leechers = int(parts[4])
        except ValueError:
            continue
        info_hash = ""
        if parts[0].startswith("magnet:?"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(parts[0]).query)
            xt = query.get("xt", [""])[0]
            if xt.lower().startswith("urn:btih:"):
                info_hash = xt.rsplit(":", 1)[-1].lower()
        results.append(
            SearchResult(parts[1], parts[0], size, seeders, leechers, source, info_hash)
        )
    return results


def run_plugin(name: str, path: Path, query: str, timeout: int) -> list[SearchResult]:
    encoded_query = urllib.parse.quote(query)
    command = [sys.executable, str(WORKER), str(path), encoded_query, "all"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{name}: timed out after {timeout}s") from error
    if completed.returncode != 0:
        message = completed.stderr.strip().splitlines()
        detail = message[-1] if message else f"exit code {completed.returncode}"
        raise RuntimeError(f"{name}: {detail}")
    return parse_output(completed.stdout, name)


def search_local_plugins(
    query: str, timeout: int = 30, max_workers: int = 8
) -> tuple[list[SearchResult], list[str]]:
    plugins = available_plugins()
    results: list[SearchResult] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(plugins) or 1)) as executor:
        jobs = {
            executor.submit(run_plugin, name, path, query, timeout): name
            for name, path in plugins
        }
        for job in as_completed(jobs):
            try:
                results.extend(job.result())
            except RuntimeError as error:
                errors.append(str(error))
    return merge_results(results), sorted(errors)
