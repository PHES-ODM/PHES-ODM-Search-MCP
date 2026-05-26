"""
PHES-ODM Embedding Search — MCP Server

Provides tools: search_odm_parts, get_enum_values, get_class_slots, list_part_types, get_version

Usage
-----
  python -m odm_search_mcp.server [--schema SCHEMA] [--store STORE]
                                    [--model MODEL] [--rebuild]

  When --rebuild is given the server builds (or rebuilds) the embeddings index,
  prints a completion message, and exits immediately — it does NOT start serving.
  This is the recommended way to pre-build the index during initial setup or
  after a schema update.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import logging
import os
from pathlib import Path
from typing import Any, Optional

from fastmcp import FastMCP

from .embedder import DEFAULT_MODEL, DEFAULT_STORE_DIR, ODMEmbedder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults (can be overridden via CLI args or env vars)
# ---------------------------------------------------------------------------
_PKG = Path(__file__).parent  # odm_search_mcp/

DEFAULT_SCHEMA = str(_PKG / "data" / "schemas" / "odm_v3.yaml")
DEFAULT_STORE  = str(DEFAULT_STORE_DIR)

# ---------------------------------------------------------------------------
# Global embedder (initialised in main before serving)
# ---------------------------------------------------------------------------
_embedder: Optional[ODMEmbedder] = None

_VALID_PART_TYPES: frozenset[str] = frozenset({"class", "slot", "enum", "enum_value"})

# ---------------------------------------------------------------------------
# MCP app
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="phes-odm-search",
    instructions=(
        "Search PHES-ODM parts (classes, slots, enumerations) "
        "using natural language queries backed by LLM embeddings."
    ),
)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
def search_odm_parts(
    query: str,
    top_n: int = 10,
    part_types: Optional[list[str]] = None,
    include_score: bool = True,
    include_id: bool = True,
    include_label: bool = True,
    include_type: bool = True,
    include_description: bool = True,
    include_class_info: bool = True,
    include_enum_info: bool = True,
    include_range: bool = True,
    include_required: bool = True,
    include_min_max: bool = True,
) -> list[dict[str, Any]]:
    """Search PHES-ODM parts using a natural language query.

    Parameters
    ----------
    query:
        Natural language description or title of the term to find.
    top_n:
        Maximum number of results to return (default 10).
    part_types:
        Restrict search to specific schema types.  Accepted values:
        "class", "slot", "enum", "enum_value".
        Pass null / omit to search all types.
    include_score:
        Include the cosine similarity score in each result.
    include_id:
        Include the part ID in each result.
    include_label:
        Include the human-readable label in each result.
    include_type:
        Include the schema type (class / slot / enum / enum_value) in each result.
    include_description:
        Include the part description in each result.
    include_class_info:
        For slot matches: include the list of classes the slot belongs to.
    include_enum_info:
        For enum_value matches: include the parent enumeration, the slots
        and classes that accept this enumeration value.
    include_range:
        For slot matches: include the list of allowed range types (e.g. "string",
        an enumeration name, a class name).  Multiple values appear when the slot
        accepts more than one type via any_of.
    include_required:
        For slot matches: include the list of classes in which this slot is marked
        required (required: true in slot_usage).  An empty list means the slot is
        not required in any class.
    include_min_max:
        For slot matches: include minimum_value and maximum_value when specified
        in the schema.  Values are omitted (null) when the slot has no constraint.

    Returns
    -------
    A list of match dicts ordered by descending similarity score.
    """
    if _embedder is None:
        raise RuntimeError("Embedder not initialised.")
    if top_n <= 0:
        raise ValueError(f"top_n must be a positive integer, got {top_n!r}.")
    if part_types:
        unknown = set(part_types) - _VALID_PART_TYPES
        if unknown:
            raise ValueError(
                f"Unknown part_types: {sorted(unknown)!r}. "
                f"Valid values: {sorted(_VALID_PART_TYPES)!r}."
            )

    results = _embedder.search(query=query, top_n=top_n, part_types=part_types)

    output: list[dict[str, Any]] = []
    for score, part in results:
        item: dict[str, Any] = {}

        if include_score:
            item["score"] = round(score, 4)
        if include_id:
            item["id"] = part.part_id
        if include_label:
            item["label"] = part.label
        if include_type:
            item["type"] = part.schema_type
        if include_description:
            item["description"] = part.description

        if include_class_info and part.schema_type == "slot":
            item["belongs_to_classes"] = part.belongs_to_classes

        if include_range and part.schema_type == "slot":
            item["slot_ranges"] = part.slot_ranges

        if include_required and part.schema_type == "slot":
            item["required_by_classes"] = part.required_by_classes

        if include_min_max and part.schema_type == "slot":
            item["minimum_value"] = part.minimum_value
            item["maximum_value"] = part.maximum_value

        if include_enum_info and part.schema_type == "enum_value":
            item["belongs_to_enum"] = part.belongs_to_enum
            item["used_by_slots"] = part.used_by_slots
            item["used_by_classes"] = part.used_by_classes

        output.append(item)

    return output


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
def get_enum_values(enum_name: str) -> list[dict[str, str]]:
    """Return all permissible values for a named enumeration.

    Parameters
    ----------
    enum_name:
        The identifier of the enumeration as it appears in the schema
        (e.g. "dataRepoSet", "absHumidUnitSet").

    Returns
    -------
    A list of dicts ordered as they appear in the schema.  Each dict has:
      - value:       the short identifier / code for this permissible value
      - title:       the human-readable name
      - description: a prose explanation of the value
    Raises an error if *enum_name* is not found in the schema.
    """
    if _embedder is None:
        raise RuntimeError("Embedder not initialised.")
    return _embedder.get_enum_values(enum_name)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
def get_class_slots(class_name: str) -> list[dict[str, Any]]:
    """Return all slots for a named class with their schema-level details.

    Parameters
    ----------
    class_name:
        The identifier of the class as it appears in the schema
        (e.g. "wideNames", "Sample", "Organization").

    Returns
    -------
    A list of dicts ordered as the slots appear in the class definition.
    Each dict has:
      - part_id:       the slot identifier (partID)
      - title:         human-readable name from slot_usage
      - description:   prose description of the slot in this class context
      - range:         list of allowed types (e.g. ["string"], ["dateSet"],
                       ["string", "genMissingnessSet"] when any_of is used)
      - pattern:       regex pattern constraint, or empty string if none
      - identifier:    true if this slot is the primary key / identifier in this class
      - required:      true if this slot is required in this class
      - minimum_value: numeric lower bound for the slot value, or null if unspecified
      - maximum_value: numeric upper bound for the slot value, or null if unspecified
    Raises an error if *class_name* is not found in the schema.
    """
    if _embedder is None:
        raise RuntimeError("Embedder not initialised.")
    return _embedder.get_class_slots(class_name)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
def get_version() -> dict[str, Any]:
    """Return version information for this MCP server.

    Returns
    -------
    A dict with:
      - server_version: the installed package version (e.g. "0.1.0")
      - schema_name:    the name field from the loaded ODM schema
      - model:          the sentence-transformer model used for embeddings
      - parts_indexed:  number of ODM parts in the search index
    """
    try:
        server_version = importlib.metadata.version("odm-search-mcp")
    except importlib.metadata.PackageNotFoundError:
        server_version = "unknown"

    if _embedder is None:
        return {"server_version": server_version, "schema_name": None, "model": None, "parts_indexed": 0}

    import yaml
    with open(_embedder.schema_path, encoding="utf-8") as fh:
        schema_name = yaml.safe_load(fh).get("name", "unknown")

    return {
        "server_version": server_version,
        "schema_name": schema_name,
        "model": _embedder.model_name,
        "parts_indexed": len(_embedder.parts),
    }


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
def list_part_types() -> list[str]:
    """Return all distinct schema_type values present in the loaded index."""
    if _embedder is None:
        raise RuntimeError("Embedder not initialised.")
    return sorted({p.schema_type for p in _embedder.parts})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PHES-ODM Embedding Search MCP Server")
    p.add_argument("--schema",  default=os.environ.get("ODM_SCHEMA",  DEFAULT_SCHEMA))
    p.add_argument("--store",   default=os.environ.get("ODM_STORE",   DEFAULT_STORE))
    p.add_argument("--model",   default=os.environ.get("ODM_MODEL",   DEFAULT_MODEL))
    p.add_argument("--rebuild", action="store_true",
                   help="Rebuild the embeddings index and exit (does not start the server)")
    p.add_argument("--transport", default=os.environ.get("ODM_TRANSPORT", "stdio"),
                   choices=["stdio", "http", "sse"],
                   help="MCP transport (default: stdio; env: ODM_TRANSPORT)")
    p.add_argument("--host", default=os.environ.get("ODM_HOST", "127.0.0.1"),
                   help="Host address for HTTP server (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=int(os.environ.get("ODM_PORT", "8000")),
                   help="Port for HTTP server (default: 8000)")
    return p.parse_args()


def main() -> None:
    global _embedder

    args = _parse_args()

    _embedder = ODMEmbedder(
        schema_path=args.schema,
        store_dir=args.store,
        model_name=args.model,
    )

    if args.rebuild:
        _embedder.rebuild()
        logger.info(
            "Rebuild complete — %d parts indexed, model=%s",
            len(_embedder.parts),
            args.model,
        )
        return
    _embedder.load_or_build()

    logger.info(
        "Server ready — %d parts indexed, model=%s",
        len(_embedder.parts),
        args.model,
    )

    if args.transport in ("http", "sse"):
        mcp.run(transport=args.transport, host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
