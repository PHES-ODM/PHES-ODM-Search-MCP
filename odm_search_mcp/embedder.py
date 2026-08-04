"""
Manage embeddings for PHES-ODM parts.

Embeddings are stored to disk as:
  <store_dir>/embeddings.npy   — float32 array of shape (N, D)
  <store_dir>/metadata.json    — {"schema_mtime": float, "parts": [ODMPart dicts]}

All part data is derived from the LinkML schema YAML; no CSV file is required.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import numpy as np
import yaml

from .schema_parser import ODMPart, _IGNORED_CLASSES, load_parts

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "all-MiniLM-L6-v2"
DEFAULT_STORE_DIR = Path(__file__).parent.parent / "embeddings"

# Number of parts encoded per forward pass when building the index. Lower this
# (e.g. ODM_BATCH_SIZE=8) to reduce peak memory on constrained hosts, at the cost
# of a slower rebuild. Falls back to 64 if the env var is unset or invalid.
try:
    DEFAULT_BATCH_SIZE = max(1, int(os.environ.get("ODM_BATCH_SIZE", "64")))
except ValueError:
    DEFAULT_BATCH_SIZE = 64


def _cosine_similarity(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Return cosine similarity between a 1-D query and each row of matrix."""
    query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10
    normed = matrix / norms
    return normed @ query_norm


class ODMEmbedder:
    """
    Loads or builds embeddings for all PHES-ODM parts.

    Parameters
    ----------
    schema_path:
        Path to the LinkML YAML schema file.
    store_dir:
        Directory where embeddings.npy and metadata.json are saved.
    model_name:
        Sentence-transformers model name (default: all-MiniLM-L6-v2).
    """

    def __init__(
        self,
        schema_path: str | Path,
        store_dir: str | Path = DEFAULT_STORE_DIR,
        model_name: str = DEFAULT_MODEL,
    ) -> None:
        self.schema_path = Path(schema_path)
        self.store_dir = Path(store_dir)
        self.model_name = model_name

        self.parts: list[ODMPart] = []
        self.embeddings: Optional[np.ndarray] = None  # (N, D)
        self._schema: Optional[dict] = None
        self._model = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    # Fields that must be present in every cached ODMPart dict.
    # Add new ODMPart fields here so stale caches are detected and rebuilt.
    _REQUIRED_META_FIELDS = frozenset({
        "part_id", "label", "schema_type", "description",
        "belongs_to_classes", "slot_ranges", "slot_ranges_by_class",
        "required_by_classes", "minimum_value", "maximum_value",
        "belongs_to_enum", "used_by_slots", "used_by_classes",
    })

    def load_or_build(self) -> None:
        """Load embeddings from disk if available, otherwise build and save."""
        emb_path = self.store_dir / "embeddings.npy"
        meta_path = self.store_dir / "metadata.json"

        if emb_path.exists() and meta_path.exists():
            if not self._cache_is_fresh(meta_path):
                logger.info("Cached metadata is stale — rebuilding embeddings…")
                self._build()
                self._save()
            else:
                logger.info("Loading embeddings from %s", self.store_dir)
                self._load()
        else:
            logger.info("Building embeddings (this may take a while)…")
            self._build()
            self._save()

    def _cache_is_fresh(self, meta_path: Path) -> bool:
        """Return False if cached metadata is stale, missing required fields, or schema changed."""
        try:
            with open(meta_path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return False  # old list format
            parts = data.get("parts") or []
            if not parts:
                return False
            if "csv_part_type" in parts[0]:
                return False
            if not self._REQUIRED_META_FIELDS.issubset(parts[0].keys()):
                return False
            if data.get("schema_mtime") != self.schema_path.stat().st_mtime:
                return False
            return True
        except (json.JSONDecodeError, OSError, KeyError, TypeError) as exc:
            logger.warning("Cache freshness check failed (%s) — will rebuild.", exc)
            return False

    def rebuild(self) -> None:
        """Force rebuild of embeddings and overwrite stored files."""
        logger.info("Rebuilding embeddings…")
        self._build()
        self._save()

    def search(
        self,
        query: str,
        top_n: int = 10,
        part_types: Optional[list[str]] = None,
    ) -> list[tuple[float, ODMPart]]:
        """
        Return up to *top_n* (score, part) tuples sorted by descending cosine similarity.

        Parameters
        ----------
        query:
            Natural language search string.
        top_n:
            Maximum number of results.
        part_types:
            Optional list of schema_type values to restrict results to,
            e.g. ["class", "slot", "enum_value"].  None means all types.
        """
        if self.embeddings is None or not self.parts:
            raise RuntimeError("Embeddings not loaded. Call load_or_build() first.")

        pt_set = set(part_types) if part_types else None

        # Exact-match short-circuit: a query that is a literal part_id or label is
        # a lookup, not a fuzzy search.  Semantic embeddings cluster short codes
        # (e.g. covN1, covN2) too tightly to rank an exact code first, so surface
        # any exact matches (case-insensitive) at the top with score 1.0.
        q_norm = query.strip().casefold()
        exact_parts = [
            p for p in self.parts
            if (pt_set is None or p.schema_type in pt_set)
            and (p.part_id.casefold() == q_norm or p.label.casefold() == q_norm)
        ]
        exact_results = [(1.0, p) for p in exact_parts]
        exact_ids = {p.part_id for p in exact_parts}
        if len(exact_results) >= top_n:
            return exact_results[:top_n]

        model = self._get_model()
        query_vec = model.encode([query], show_progress_bar=False)[0].astype(np.float32)

        if pt_set:
            indices = [i for i, p in enumerate(self.parts) if p.schema_type in pt_set]
            if not indices:
                return exact_results[:top_n]
            idx_arr = np.array(indices)
            sub_matrix = self.embeddings[idx_arr]
            scores = _cosine_similarity(query_vec, sub_matrix)
            order = np.argsort(-scores)
            semantic = [(float(scores[i]), self.parts[idx_arr[i]]) for i in order]
        else:
            scores = _cosine_similarity(query_vec, self.embeddings)
            order = np.argsort(-scores)
            semantic = [(float(scores[i]), self.parts[i]) for i in order]

        # Prepend exact matches, drop their duplicates from the semantic list,
        # then truncate to top_n.
        merged = exact_results + [(s, p) for s, p in semantic if p.part_id not in exact_ids]
        return merged[:top_n]

    def get_class_slots(self, class_name: str) -> list[dict]:
        """Return all slots for *class_name* with their schema-level details.

        Each returned dict contains the part_id, title, description, range list,
        pattern, identifier flag, required flag, and minimum/maximum values as
        defined in slot_usage for this class.
        """
        schema = self._get_schema()
        classes = schema.get("classes") or {}

        if class_name not in classes:
            raise ValueError(f"Class '{class_name}' not found in schema.")
        if class_name in _IGNORED_CLASSES:
            raise ValueError(f"Class '{class_name}' is not a valid ODM class.")

        class_data = classes[class_name]
        slot_list = class_data.get("slots") or []
        slot_usage = class_data.get("slot_usage") or {}

        part_lookup: dict[str, ODMPart] = {p.part_id: p for p in self.parts} if self.parts else {}

        result = []
        for slot_name in slot_list:
            usage = slot_usage.get(slot_name) or {}
            part = part_lookup.get(slot_name)

            ranges: list[str] = []
            if usage.get("range"):
                ranges.append(usage["range"])
            for ao in (usage.get("any_of") or []):
                if ao.get("range"):
                    ranges.append(ao["range"])

            min_val = usage.get("minimum_value")
            max_val = usage.get("maximum_value")
            result.append({
                "part_id": slot_name,
                "title": usage.get("title") or (part.label if part else ""),
                "description": usage.get("description") or (part.description if part else ""),
                "range": ranges,
                "pattern": usage.get("pattern") or "",
                "identifier": bool(usage.get("identifier")),
                "required": bool(usage.get("required")),
                "minimum_value": float(min_val) if min_val is not None else None,
                "maximum_value": float(max_val) if max_val is not None else None,
            })

        return result

    def get_enum_values(self, enum_name: str) -> list[dict[str, str]]:
        """Return all permissible values for *enum_name* with value, title, description."""
        enums = (self._get_schema().get("enums") or {})
        if enum_name not in enums:
            raise ValueError(f"Enumeration '{enum_name}' not found in schema.")
        result = []
        for val, val_data in (enums[enum_name].get("permissible_values") or {}).items():
            vd = val_data or {}
            result.append({
                "value": val,
                "title": vd.get("title") or "",
                "description": vd.get("description") or "",
            })
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_schema(self) -> dict:
        if self._schema is None:
            with open(self.schema_path, encoding="utf-8") as fh:
                self._schema = yaml.safe_load(fh)
        return self._schema

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading sentence-transformer model: %s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def _build(self) -> None:
        """Parse parts and encode them."""
        self.parts = load_parts(self._get_schema())
        texts = [p.embed_text() for p in self.parts]
        model = self._get_model()
        logger.info("Encoding %d parts (batch_size=%d)…", len(texts), DEFAULT_BATCH_SIZE)
        vecs = model.encode(texts, show_progress_bar=True, batch_size=DEFAULT_BATCH_SIZE)
        self.embeddings = vecs.astype(np.float32)

    def _save(self) -> None:
        """Persist embeddings and metadata to disk."""
        self.store_dir.mkdir(parents=True, exist_ok=True)
        np.save(self.store_dir / "embeddings.npy", self.embeddings)
        payload = {
            "schema_mtime": self.schema_path.stat().st_mtime,
            "parts": [asdict(p) for p in self.parts],
        }
        with open(self.store_dir / "metadata.json", "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        logger.info("Saved %d embeddings to %s", len(self.parts), self.store_dir)

    def _load(self) -> None:
        """Load embeddings and metadata from disk."""
        self.embeddings = np.load(self.store_dir / "embeddings.npy")
        with open(self.store_dir / "metadata.json", encoding="utf-8") as fh:
            data = json.load(fh)
        meta = data["parts"]
        valid_fields = {f.name for f in ODMPart.__dataclass_fields__.values()}
        self.parts = [ODMPart(**{k: v for k, v in m.items() if k in valid_fields}) for m in meta]
        logger.info("Loaded %d embeddings from %s", len(self.parts), self.store_dir)
