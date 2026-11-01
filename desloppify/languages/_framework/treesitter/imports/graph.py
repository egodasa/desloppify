"""Shared import graph construction utilities for tree-sitter backends."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .cache import get_or_parse_tree
from ..analysis.extractors import _get_parser, _make_query, _run_query, _unwrap_node

if TYPE_CHECKING:
    from desloppify.languages._framework.treesitter import TreeSitterLangSpec


def ts_build_dep_graph(
    path: Path,
    spec: TreeSitterLangSpec,
    file_list: list[str],
) -> dict[str, dict[str, Any]]:
    """Build a dependency graph by parsing imports with tree-sitter.

    Returns the same shape as Python/TS dep graphs:
    {file: {"imports": set[str], "importers": set[str], "import_count": int, "importer_count": int}}
    """
    if not spec.import_query or not spec.resolve_import:
        return {}

    parser, language = _get_parser(spec.grammar)
    query = _make_query(language, spec.import_query)

    scan_path = str(path.resolve())
    graph: dict[str, dict[str, Any]] = {}

    # File lists are usually relative to the scan root (e.g. "app/Services/Foo.php")
    # while resolved imports are normalised to absolute paths below. Without a
    # common form every edge is dropped and every file looks orphaned, so map
    # absolute path -> original graph key and match on that instead.
    abs_by_key: dict[str, str] = {}
    for f in file_list:
        abs_f = f if os.path.isabs(f) else os.path.join(scan_path, f)
        abs_by_key[f] = os.path.normpath(os.path.abspath(abs_f))

    key_by_abs: dict[str, str] = {abs_path: key for key, abs_path in abs_by_key.items()}

    # Initialize all files in the graph.
    for f in file_list:
        graph[f] = {"imports": set(), "importers": set()}

    for filepath in file_list:
        # Parse the absolute path: relative entries would otherwise be read
        # relative to the process CWD instead of the scan root.
        cached = get_or_parse_tree(abs_by_key[filepath], parser, spec.grammar)
        if cached is None:
            continue
        _source, tree = cached
        matches = _run_query(query, tree.root_node)

        for _pattern_idx, captures in matches:
            path_node = _unwrap_node(captures.get("path"))
            if not path_node:
                continue

            raw_text = path_node.text
            import_text = (
                raw_text.decode("utf-8", errors="replace")
                if isinstance(raw_text, bytes)
                else str(raw_text)
            )

            # Strip surrounding quotes if present.
            import_text = import_text.strip("\"'`")

            # Prepend group-use prefix when present (PHP ``use A\B\{C, D}``).
            prefix_node = _unwrap_node(captures.get("prefix"))
            if prefix_node is not None:
                prefix_raw = prefix_node.text
                prefix_text = (
                    prefix_raw.decode("utf-8", errors="replace")
                    if isinstance(prefix_raw, bytes)
                    else str(prefix_raw)
                ).strip("\"'`")
                import_text = f"{prefix_text}\\{import_text}"

            resolved = spec.resolve_import(import_text, filepath, scan_path)
            if resolved is None:
                continue

            # Normalize to absolute path.
            if not os.path.isabs(resolved):
                resolved = os.path.normpath(os.path.join(scan_path, resolved))
            else:
                resolved = os.path.normpath(resolved)

            # Only track edges within the scanned file set.
            target = key_by_abs.get(resolved)
            if target is None:
                continue

            graph[filepath]["imports"].add(target)
            if target in graph:
                graph[target]["importers"].add(filepath)

    # Finalize: add counts.
    for data in graph.values():
        data["import_count"] = len(data["imports"])
        data["importer_count"] = len(data["importers"])

    return graph


def make_ts_dep_builder(
    spec: TreeSitterLangSpec,
    file_finder: Callable[[Path], list[str]],
) -> Callable[[Path], dict[str, dict[str, Any]]]:
    """Create a dep graph builder bound to a TreeSitterLangSpec + file finder.

    Returns a callable with signature (path: Path) -> dict,
    matching the contract expected by LangConfig.build_dep_graph.
    """

    def build(path: Path) -> dict[str, dict[str, Any]]:
        file_list = file_finder(path)
        return ts_build_dep_graph(path, spec, file_list)

    return build


__all__ = ["make_ts_dep_builder", "ts_build_dep_graph"]
