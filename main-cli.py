from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from search_engine import (
    SearchResult,
    available_plugins,
    load_sources,
    merge_results,
    search_all,
    search_local_plugins,
)


VIDEO_EXTENSIONS = {
    ".avi",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ts",
    ".webm",
    ".wmv",
}

STREAM_AHEAD_BYTES = 32 * 1024 * 1024
STREAM_TAIL_BYTES = 10 * 1024 * 1024
STREAM_PRIORITY_STEP = 8 * 1024 * 1024
STREAM_MIN_BUFFER_BYTES = 64 * 1024 * 1024
STREAM_MAX_BUFFER_BYTES = 256 * 1024 * 1024


def format_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def find_aria2() -> str:
    executable = shutil.which("aria2c")
    if executable:
        return executable

    winget_packages = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    matches = list(winget_packages.glob("aria2.aria2_*/**/aria2c.exe"))
    if matches:
        return str(matches[0])

    raise RuntimeError(
        "aria2c was not found. Install it with: "
        "winget install --id aria2.aria2 --exact"
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Aria2:
    def __init__(self, download_dir: Path) -> None:
        executable = find_aria2()
        self.port = free_port()
        self.secret = secrets.token_hex(16)
        self.url = f"http://127.0.0.1:{self.port}/jsonrpc"
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            [
                executable,
                "--enable-rpc=true",
                "--rpc-listen-all=false",
                f"--rpc-listen-port={self.port}",
                f"--rpc-secret={self.secret}",
                "--enable-dht=true",
                "--enable-peer-exchange=true",
                "--seed-time=0",
                "--summary-interval=0",
                "--console-log-level=warn",
                f"--dir={download_dir}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        self._wait_until_ready()

    def _wait_until_ready(self) -> None:
        for _ in range(50):
            if self.process.poll() is not None:
                raise RuntimeError("aria2c stopped during startup")
            try:
                self.call("aria2.getVersion")
                return
            except (ConnectionError, urllib.error.URLError):
                time.sleep(0.1)
        raise RuntimeError("could not connect to aria2c")

    def call(self, method: str, *params):
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "live-torrent-client",
                "method": method,
                "params": [f"token:{self.secret}", *params],
            }
        ).encode()
        request = urllib.request.Request(
            self.url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read())
        if "error" in result:
            raise RuntimeError(result["error"]["message"])
        return result["result"]

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.call("aria2.shutdown")
                self.process.wait(timeout=5)
            except Exception:
                self.process.terminate()


def wait_for_metadata(
    aria2: Aria2,
    magnet: str,
    timeout: int,
    progress_callback: Callable[[dict], None] | None = None,
) -> tuple[str, dict]:
    gid = aria2.call(
        "aria2.addUri",
        [magnet],
        {"pause-metadata": "true", "bt-save-metadata": "true"},
    )
    if progress_callback:
        progress_callback({"event": "started", "elapsed": 0})
    else:
        print("Fetching torrent metadata...", flush=True)
    started_at = time.monotonic()
    last_update = -5

    while True:
        status = aria2.call("aria2.tellStatus", gid)
        followed_by = status.get("followedBy", [])
        if followed_by:
            torrent_gid = followed_by[0]
            torrent = aria2.call("aria2.tellStatus", torrent_gid)
            if progress_callback:
                progress_callback({"event": "complete", "status": torrent})
            else:
                print("Metadata received.")
            return torrent_gid, torrent
        if status["status"] in {"error", "removed"}:
            message = status.get("errorMessage", "failed to fetch metadata")
            raise RuntimeError(message)

        elapsed = int(time.monotonic() - started_at)
        update = {
            "event": "waiting",
            "elapsed": elapsed,
            "seeders": int(status.get("numSeeders", 0)),
            "connections": int(status.get("connections", 0)),
        }
        if progress_callback:
            progress_callback(update)
        if elapsed - last_update >= 5:
            seeders = status.get("numSeeders", "0")
            connections = status.get("connections", "0")
            if not progress_callback:
                print(
                    f"  waiting for {elapsed}s | "
                    f"seeders {seeders} | connections {connections}",
                    flush=True,
                )
            last_update = elapsed
        if timeout > 0 and elapsed >= timeout:
            raise RuntimeError(
                "metadata request timed out; check the magnet link, firewall, "
                "VPN, and seeder availability"
            )
        time.sleep(1)


def renew_finished_torrent(
    aria2: Aria2,
    gid: str,
    source_uri: str,
    timeout: int,
    progress_callback: Callable[[dict], None] | None = None,
) -> tuple[str, dict]:
    """Create a fresh aria2 task when a previous file selection has finished."""
    status = aria2.call("aria2.tellStatus", gid)
    if status.get("status") not in {"complete", "error", "removed"}:
        return gid, status

    if status.get("status") != "removed":
        try:
            aria2.call("aria2.removeDownloadResult", gid)
        except RuntimeError:
            pass
    return wait_for_metadata(aria2, source_uri, timeout, progress_callback)


def show_files(status: dict) -> list[dict]:
    files = status.get("files", [])
    torrent_name = status.get("bittorrent", {}).get("info", {}).get("name")
    torrent_name = torrent_name or "Unnamed torrent"

    print(f"\nTorrent: {torrent_name}")
    print(f"Current seeders: {status.get('numSeeders', '0')}")
    print(f"Files: {len(files)}\n")

    for index, item in enumerate(files, start=1):
        path = item.get("path", "").replace("/", "\\")
        print(f"[{index:>3}] {path} ({format_size(int(item['length']))})")
    return files


def parse_selection(value: str, file_count: int) -> set[int]:
    value = value.strip().lower()
    if value in {"all", "a"}:
        return set(range(1, file_count + 1))

    selected: set[int] = set()
    for part in value.replace(",", " ").split():
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                start, end = end, start
            numbers = range(start, end + 1)
        else:
            numbers = (int(part),)

        for number in numbers:
            if not 1 <= number <= file_count:
                raise ValueError(f"file {number} does not exist")
            selected.add(number)

    if not selected:
        raise ValueError("no files selected")
    return selected


def choose_files(file_count: int) -> set[int]:
    print("\nEnter file numbers separated by spaces.")
    print("Examples: 1 3 5 | 2-6 | all")
    while True:
        try:
            return parse_selection(input("Select: "), file_count)
        except (ValueError, TypeError) as error:
            print(f"Invalid selection: {error}")


def show_search_results(results: list[SearchResult], limit: int) -> list[SearchResult]:
    visible = results[:limit]
    print(f"\nFound {len(results)} unique results. Showing {len(visible)}:\n")
    for index, result in enumerate(visible, start=1):
        title = result.title if len(result.title) <= 70 else result.title[:67] + "..."
        print(f"[{index:>3}] {title}")
        print(
            f"      {format_size(result.size):>10} | "
            f"seeders {result.seeders:>5} | leechers {result.leechers:>5} | "
            f"{result.source}"
        )
    return visible


def choose_search_result(results: list[SearchResult]) -> SearchResult:
    while True:
        try:
            number = int(input("\nSelect a search result: ").strip())
            if not 1 <= number <= len(results):
                raise ValueError("result does not exist")
            return results[number - 1]
        except ValueError as error:
            print(f"Invalid selection: {error}")


def run_search(
    query: str,
    config: Path,
    timeout: int,
    plugin_timeout: int,
    limit: int,
) -> str:
    results: list[SearchResult] = []
    errors: list[str] = []

    if config.is_file():
        sources = load_sources(config)
        print(f"Searching {len(sources)} Torznab source(s) for: {query}")
        torznab_results, torznab_errors = search_all(sources, query, timeout)
        results.extend(torznab_results)
        errors.extend(torznab_errors)
    else:
        print("Torznab configuration not found; using local plugins only.")

    plugins = available_plugins()
    print(f"Searching {len(plugins)} local plugin(s) for: {query}")
    plugin_results, plugin_errors = search_local_plugins(query, plugin_timeout)
    results.extend(plugin_results)
    errors.extend(plugin_errors)
    results = merge_results(results)

    if errors:
        print(f"\n{len(errors)} search source(s) reported errors.", file=sys.stderr)
        for error in errors[:10]:
            print(f"  - {error}", file=sys.stderr)
        if len(errors) > 10:
            print(f"  - and {len(errors) - 10} more", file=sys.stderr)
    if not results:
        raise RuntimeError("the search returned no results")
    selected = choose_search_result(show_search_results(results, limit))
    print(f"\nSelected: {selected.title}")
    return selected.url


def interactive_source(args) -> tuple[str | None, str | None]:
    if args.magnet or args.search:
        return args.magnet, args.search

    print("What would you like to do?")
    print("[1] Open a magnet link")
    print("[2] Search for torrents")
    while True:
        choice = input("Select: ").strip()
        if choice == "1":
            return input("Paste the magnet link: ").strip(), None
        if choice == "2":
            return None, input("Search for: ").strip()
        print("Invalid selection. Enter 1 or 2.")


def is_video(item: dict) -> bool:
    return Path(item.get("path", "")).suffix.lower() in VIDEO_EXTENSIONS


def wants_to_stream(selected: set[int], files: list[dict]) -> bool:
    if len(selected) != 1:
        return False
    item = files[next(iter(selected)) - 1]
    if not is_video(item):
        return False

    while True:
        answer = input("Watch while downloading? [y/N]: ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False
        print("Please answer y or n.")


def find_vlc() -> str | None:
    executable = shutil.which("vlc")
    if executable:
        return executable
    for environment_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(environment_name)
        if base:
            candidate = Path(base) / "VideoLAN/VLC/vlc.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def open_video(path: Path) -> None:
    vlc = find_vlc()
    if not vlc:
        raise RuntimeError(
            "VLC was not found. It is required to watch a video while it is "
            "downloading. Install it from: "
            "https://www.videolan.org/vlc/"
        )
    subprocess.Popen(
        [vlc, str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def piece_is_complete(bitfield: str, piece_index: int) -> bool:
    byte_index, bit_index = divmod(piece_index, 8)
    try:
        value = bytes.fromhex(bitfield)[byte_index]
    except (ValueError, IndexError):
        return False
    return bool(value & (0x80 >> bit_index))


def contiguous_file_bytes(status: dict, item: dict) -> int:
    piece_length = int(status.get("pieceLength", 0))
    bitfield = status.get("bitfield", "")
    file_offset = int(item.get("offset", 0))
    file_length = int(item.get("length", 0))
    if piece_length <= 0 or not bitfield or file_length <= 0:
        return 0

    first_piece = file_offset // piece_length
    last_piece = (file_offset + file_length - 1) // piece_length
    completed_end = file_offset
    for piece_index in range(first_piece, last_piece + 1):
        if not piece_is_complete(bitfield, piece_index):
            break
        completed_end = min((piece_index + 1) * piece_length, file_offset + file_length)
    return max(0, completed_end - file_offset)


def stream_priority(contiguous: int, file_length: int) -> str:
    head_bytes = min(file_length, contiguous + STREAM_AHEAD_BYTES)
    head_mib = max(1, (head_bytes + 1024 * 1024 - 1) // (1024 * 1024))
    tail_bytes = min(file_length, STREAM_TAIL_BYTES)
    tail_mib = max(1, (tail_bytes + 1024 * 1024 - 1) // (1024 * 1024))
    return f"head={head_mib}M,tail={tail_mib}M"


def stream_buffer_size(file_length: int) -> int:
    """Require a useful playback buffer instead of opening VLC after a few MB."""
    if file_length <= 0:
        return STREAM_MIN_BUFFER_BYTES
    return min(
        file_length,
        STREAM_MAX_BUFFER_BYTES,
        max(STREAM_MIN_BUFFER_BYTES, file_length // 20),
    )


def download(
    aria2: Aria2,
    gid: str,
    selected: set[int],
    stream_item: dict | None = None,
    progress_callback: Callable[[dict], None] | None = None,
    event_callback: Callable[[str, dict], None] | None = None,
    stop_callback: Callable[[], bool] | None = None,
) -> None:
    selection = ",".join(str(index) for index in sorted(selected))
    options = {"select-file": selection}
    stream_file = Path(stream_item["path"]) if stream_item else None
    stream_length = int(stream_item["length"]) if stream_item else 0
    if stream_item:
        options["bt-prioritize-piece"] = stream_priority(0, stream_length)
    aria2.call("aria2.changeOption", gid, options)
    aria2.call("aria2.unpause", gid)
    if event_callback:
        event_callback("started", {"streaming": bool(stream_file)})
    else:
        print("\nDownload started. Press Ctrl+C to pause and exit.")
        if stream_file:
            print("Buffering video for playback...")

    player_opened = False
    last_priority_at = 0

    while True:
        if stop_callback and stop_callback():
            aria2.call("aria2.pause", gid)
            if event_callback:
                event_callback("paused", {})
            return
        status = aria2.call("aria2.tellStatus", gid)
        total = int(status.get("totalLength", 0))
        completed = int(status.get("completedLength", 0))
        progress = completed / total * 100 if total else 0
        speed = format_size(int(status.get("downloadSpeed", 0))) + "/s"
        seeders = status.get("numSeeders", "0")
        update = {
            "progress": progress,
            "speed": speed,
            "seeders": int(seeders),
            "completed": completed,
            "total": total,
            "status": status["status"],
        }
        if progress_callback:
            progress_callback(update)
        else:
            print(
                f"\r{progress:6.2f}% | speed {speed:>12} | seeders {seeders:>4}",
                end="",
                flush=True,
            )
        if stream_item:
            contiguous = contiguous_file_bytes(status, stream_item)
            if contiguous - last_priority_at >= STREAM_PRIORITY_STEP:
                aria2.call(
                    "aria2.changeOption",
                    gid,
                    {"bt-prioritize-piece": stream_priority(contiguous, stream_length)},
                )
                last_priority_at = contiguous
        if stream_file and not player_opened:
            buffer_size = stream_buffer_size(stream_length)
            contiguous = contiguous_file_bytes(status, stream_item)
            if event_callback:
                event_callback(
                    "buffering",
                    {
                        "buffered": contiguous,
                        "required": buffer_size,
                        "progress": min(contiguous / buffer_size * 100, 100),
                    },
                )
            if contiguous >= buffer_size and stream_file.exists():
                if event_callback:
                    event_callback("opening_vlc", {"path": str(stream_file)})
                else:
                    print(f"\nOpening in VLC: {stream_file.name}")
                open_video(stream_file)
                player_opened = True
        if status["status"] == "complete":
            if event_callback:
                event_callback("complete", {})
            else:
                print("\nDownload complete.")
            return
        if status["status"] == "error":
            raise RuntimeError(status.get("errorMessage", "download failed"))
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explore a magnet link and choose which files to download."
    )
    parser.add_argument("magnet", nargs="?", help="torrent magnet link")
    parser.add_argument(
        "--search",
        metavar="QUERY",
        help="search local plugins and configured Torznab sources",
    )
    parser.add_argument(
        "--search-config",
        default="search_config.json",
        help="Torznab source configuration (default: search_config.json)",
    )
    parser.add_argument(
        "--search-timeout",
        type=int,
        default=30,
        help="timeout for each search source in seconds (default: 30)",
    )
    parser.add_argument(
        "--plugin-timeout",
        type=int,
        default=30,
        help="timeout for each local plugin in seconds (default: 30)",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=50,
        help="maximum search results to display (default: 50)",
    )
    parser.add_argument("-o", "--output", default="downloads", help="output directory")
    parser.add_argument("--list-only", action="store_true", help="only list files")
    parser.add_argument(
        "--metadata-timeout",
        type=int,
        default=120,
        help="metadata timeout in seconds; use 0 to disable (default: 120)",
    )
    args = parser.parse_args()

    if args.magnet and args.search:
        parser.error("provide either a magnet link or --search, not both")
    if args.max_results < 1:
        parser.error("--max-results must be greater than zero")

    source_uri, query = interactive_source(args)
    try:
        if query is not None:
            if not query.strip():
                raise RuntimeError("search query cannot be empty")
            source_uri = run_search(
                query.strip(),
                Path(args.search_config).resolve(),
                args.search_timeout,
                args.plugin_timeout,
                args.max_results,
            )
    except (RuntimeError, urllib.error.URLError) as error:
        print(f"\nError: {error}", file=sys.stderr)
        raise SystemExit(1)

    if not source_uri or not source_uri.startswith(("magnet:?", "http://", "https://")):
        parser.error("provide a valid magnet link or torrent URL")

    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    aria2 = Aria2(output)
    torrent_gid = None

    try:
        torrent_gid, status = wait_for_metadata(aria2, source_uri, args.metadata_timeout)
        files = show_files(status)
        if not args.list_only:
            selected = choose_files(len(files))
            stream_item = None
            if wants_to_stream(selected, files):
                if not find_vlc():
                    raise RuntimeError(
                        "VLC was not found. It is required to watch a video "
                        "while it is downloading. Install it from: "
                        "https://www.videolan.org/vlc/"
                    )
                stream_item = files[next(iter(selected)) - 1]
            download(aria2, torrent_gid, selected, stream_item)
    except KeyboardInterrupt:
        if torrent_gid:
            try:
                aria2.call("aria2.pause", torrent_gid)
            except Exception:
                pass
        print("\nDownload paused.")
    except (RuntimeError, urllib.error.URLError) as error:
        print(f"\nError: {error}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        aria2.close()


if __name__ == "__main__":
    main()
