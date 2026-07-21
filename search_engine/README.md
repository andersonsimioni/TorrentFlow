# Search Engine

This package contains every search-related part of Live Torrent Client.

## Structure

```text
search_engine/
├── torznab.py                 # Torznab client, normalization, and deduplication
├── search_config.example.json # Safe local configuration template
├── catalogs/
│   ├── trusted_sources.json   # Verified qBittorrent and Torznab sources
│   └── community_plugins.json # Community plugin download catalog
└── plugins/
    ├── official/              # Plugins from qbittorrent/search-plugins
    └── community/public/      # Downloaded public community plugins
```

Plugins are data sources, not trusted application code. Never execute a newly
downloaded community plugin before reviewing it. The client uses Torznab by
default because it provides broad coverage without loading arbitrary Python into
the main process.

The user's real `search_config.json` remains in the project root and is ignored
by Git because it may contain API keys.

Refresh the public plugin snapshot with:

```powershell
python search_engine\download_plugins.py
```

The downloader validates that each response is syntactically valid Python and
resembles a qBittorrent search plugin, then records its SHA-256 hash. Validation
does not mean that a plugin is safe or currently functional against its site.
