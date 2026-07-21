from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_CATALOG = ROOT / "catalogs/community_plugins.json"
DEFAULT_DESTINATION = ROOT / "plugins/community/public"
MAX_PLUGIN_SIZE = 2 * 1024 * 1024


def safe_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized or "plugin"


def plugin_filename(url: str) -> str:
    name = Path(urllib.parse.urlsplit(url).path).name
    if not name.lower().endswith(".py"):
        return "plugin.py"
    return safe_name(Path(name).stem).replace("-", "_") + ".py"


def download(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "TorrentFlow-PluginFetcher/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        length = int(response.headers.get("Content-Length", 0))
        if length > MAX_PLUGIN_SIZE:
            raise RuntimeError(f"file is larger than {MAX_PLUGIN_SIZE} bytes")
        content = response.read(MAX_PLUGIN_SIZE + 1)
    if len(content) > MAX_PLUGIN_SIZE:
        raise RuntimeError(f"file is larger than {MAX_PLUGIN_SIZE} bytes")
    return content


def validate_python(content: bytes) -> None:
    if b"\x00" in content:
        raise RuntimeError("download is not a text file")
    text = content.decode("utf-8-sig")
    if "class " not in text or "def search(" not in text:
        raise RuntimeError("download does not look like a qBittorrent search plugin")
    compile(text, "<downloaded-plugin>", "exec")


def fetch_plugins(catalog_path: Path, destination: Path, timeout: int) -> dict:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    public_plugins = [item for item in catalog["plugins"] if item["section"] == "public"]
    destination.mkdir(parents=True, exist_ok=True)
    try:
        catalog_reference = str(catalog_path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        catalog_reference = str(catalog_path)
    manifest = {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "source_catalog": catalog_reference,
        "plugins": [],
    }

    used_directories: dict[str, int] = {}
    for position, item in enumerate(public_plugins, start=1):
        base_slug = safe_name(item["name"])
        used_directories[base_slug] = used_directories.get(base_slug, 0) + 1
        suffix = used_directories[base_slug]
        directory_name = base_slug if suffix == 1 else f"{base_slug}-{suffix}"
        plugin_directory = destination / directory_name
        target = plugin_directory / plugin_filename(item["url"])
        record = {
            "name": item["name"],
            "url": item["url"],
            "path": str(target.relative_to(ROOT)).replace("\\", "/"),
            "status": "failed",
        }
        print(f"[{position:>2}/{len(public_plugins)}] {item['name']}...", end=" ", flush=True)
        try:
            content = download(item["url"], timeout)
            validate_python(content)
            plugin_directory.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            record.update(
                {
                    "status": "downloaded",
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
            print("OK")
        except (OSError, UnicodeError, SyntaxError, RuntimeError, urllib.error.URLError) as error:
            record["error"] = str(error)
            print(f"FAILED: {error}")
        manifest["plugins"].append(record)

    manifest["downloaded"] = sum(item["status"] == "downloaded" for item in manifest["plugins"])
    manifest["failed"] = len(manifest["plugins"]) - manifest["downloaded"]
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Download cataloged public search plugins.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    manifest = fetch_plugins(args.catalog.resolve(), args.destination.resolve(), args.timeout)
    manifest_path = args.destination.resolve() / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nDownloaded: {manifest['downloaded']}")
    print(f"Failed: {manifest['failed']}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
