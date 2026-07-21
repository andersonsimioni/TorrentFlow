from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def find_engine(module, module_name: str):
    preferred = getattr(module, module_name, None)
    if isinstance(preferred, type) and callable(getattr(preferred, "search", None)):
        return preferred
    for value in vars(module).values():
        if (
            isinstance(value, type)
            and value.__module__ == module.__name__
            and callable(getattr(value, "search", None))
        ):
            return value
    raise RuntimeError("no search engine class was found")


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: plugin_worker.py PLUGIN QUERY CATEGORY")

    plugin_path = Path(sys.argv[1]).resolve()
    query = sys.argv[2]
    category = sys.argv[3]
    runtime = Path(__file__).resolve().parent / "nova_runtime"
    sys.path.insert(0, str(runtime))
    sys.path.insert(0, str(plugin_path.parent))

    import helpers

    if not hasattr(helpers, "headers"):
        helpers.headers = dict(helpers._headers)

    module_name = plugin_path.stem
    spec = importlib.util.spec_from_file_location(module_name, plugin_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load plugin")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine_class = find_engine(module, module_name)
    engine = engine_class()

    supported = getattr(engine, "supported_categories", {"all": "all"})
    selected_category = category if category in supported else "all"
    engine.search(query, selected_category)


if __name__ == "__main__":
    main()
