"""
Manage embeddings for PHES-ODM parts.

Embeddings are stored to disk as:
  <store_dir>/embeddings.npy   — float32 array of shape (N, D)
  <store_dir>/metadata.json    — list of serialised ODMPart dicts

All part data is derived from the LinkML schema YAML; no CSV file is required.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import numpy as np
import yaml

from .schema_parser import ODMPart, _IGNORED_CLASSES, load_parts

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "all-MiniLM-L6-v2"
DEFAULT_STORE_DIR = Path(__file__).parent.parent / "embeddings"


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

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    # Fields that must be present in every cached ODMPart dict.
    # Add new ODMPart fields here so stale caches are detected and rebuilt.
    _REQUIRED_META_FIELDS = frozenset({
        "part_id", "label", "schema_type", "description",
        "belongs_to_classes", "slot_ranges",
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
        """Return False if the cached metadata is missing any required fields."""
        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            if not meta:
                return False
            # csv_part_type presence means cache was built from the old CSV pipeline
            if "csv_part_type" in meta[0]:
                return False
            return self._REQUIRED_META_FIELDS.issubset(meta[0].keys())
        except Exception:
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

        from sentence_transformers import SentenceTransformer  # lazy import

        model = self._get_model()
        query_vec = model.encode([query], show_progress_bar=False)[0].astype(np.float32)

        if part_types:
            pt_set = set(part_types)
            indices = [i for i, p in enumerate(self.parts) if p.schema_type in pt_set]
            if not indices:
                return []
            idx_arr = np.array(indices)
            sub_matrix = self.embeddings[idx_arr]
            scores = _cosine_similarity(query_vec, sub_matrix)
            local_top = min(top_n, len(indices))
            top_local = np.argsort(-scores)[:local_top]
            return [(float(scores[i]), self.parts[idx_arr[i]]) for i in top_local]
        else:
            scores = _cosine_similarity(query_vec, self.embeddings)
            top_k = min(top_n, len(self.parts))
            top_idx = np.argsort(-scores)[:top_k]
            return [(float(scores[i]), self.parts[i]) for i in top_idx]

    def get_class_slots(self, class_name: str) -> list[dict]:
        """Return all slots for *class_name* with their schema-level details.

        Each returned dict contains the part_id, title, description, range list,
        pattern, identifier flag, and required flag as defined in slot_usage for
        this class.
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

            result.append({
                "part_id": slot_name,
                "title": usage.get("title") or (part.label if part else ""),
                "description": usage.get("description") or (part.description if part else ""),
                "range": ranges,
                "pattern": usage.get("pattern") or "",
                "identifier": bool(usage.get("identifier")),
                "required": bool(usage.get("required")),
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
        # Cache the loaded model on the instance so we don't reload on every search
        if not hasattr(self, "_model") or self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading sentence-transformer model: %s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def _build(self) -> None:
        """Parse parts and encode them."""
        self.parts = load_parts(self.schema_path)
        texts = [p.embed_text() for p in self.parts]
        model = self._get_model()
        logger.info("Encoding %d parts…", len(texts))
        vecs = model.encode(texts, show_progress_bar=True, batch_size=64)
        self.embeddings = vecs.astype(np.float32)

    def _save(self) -> None:
        """Persist embeddings and metadata to disk."""
        self.store_dir.mkdir(parents=True, exist_ok=True)
        np.save(self.store_dir / "embeddings.npy", self.embeddings)
        meta = [asdict(p) for p in self.parts]
        with open(self.store_dir / "metadata.json", "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        logger.info("Saved %d embeddings to %s", len(self.parts), self.store_dir)

    def _load(self) -> None:
        """Load embeddings and metadata from disk."""
        self.embeddings = np.load(self.store_dir / "embeddings.npy")
        with open(self.store_dir / "metadata.json", encoding="utf-8") as fh:
            meta = json.load(fh)
        valid_fields = {f.name for f in ODMPart.__dataclass_fields__.values()}
        self.parts = [ODMPart(**{k: v for k, v in m.items() if k in valid_fields}) for m in meta]
        logger.info("Loaded %d embeddings from %s", len(self.parts), self.store_dir)
