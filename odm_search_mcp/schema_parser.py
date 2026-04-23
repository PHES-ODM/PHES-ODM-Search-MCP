"""
Parse the PHES-ODM LinkML schema into a structured index.

Each ODMPart has:
  - part_id, label, schema_type (class/slot/enum/enum_value), description
  - For slots: belongs_to_classes, slot_ranges, required_by_classes
  - For enum_values: belongs_to_enum, used_by_slots, used_by_classes
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# Class names that are schema-level wrappers with no domain meaning and should
# be excluded from the part index and from all slot/enum association lists.
_IGNORED_CLASSES: frozenset[str] = frozenset({"Container"})


@dataclass
class ODMPart:
    part_id: str
    label: str
    schema_type: str          # "class", "slot", "enum", or "enum_value"
    description: str
    belongs_to_classes: list[str] = field(default_factory=list)   # for slots
    slot_ranges: list[str] = field(default_factory=list)           # for slots
    required_by_classes: list[str] = field(default_factory=list)  # for slots: classes where required=true
    belongs_to_enum: Optional[str] = None                          # for enum_values
    used_by_slots: list[str] = field(default_factory=list)         # for enum_values
    used_by_classes: list[str] = field(default_factory=list)       # for enum_values

    def embed_text(self) -> str:
        """Text to embed: label + description."""
        parts = [self.label]
        if self.description:
            parts.append(self.description)
        return " ".join(parts)


def _build_slot_to_classes(schema: dict) -> dict[str, list[str]]:
    """Return {slot_name: [class_name, ...]} from all classes."""
    result: dict[str, list[str]] = {}
    for class_name, class_data in (schema.get("classes") or {}).items():
        if class_name in _IGNORED_CLASSES:
            continue
        for slot in class_data.get("slots") or []:
            result.setdefault(slot, []).append(class_name)
    return result


def _build_slot_to_ranges(schema: dict) -> dict[str, list[str]]:
    """Return {slot_name: [distinct_range, ...]} in schema-declaration order."""
    result: dict[str, dict[str, None]] = {}  # dict as ordered set
    for class_name, class_data in (schema.get("classes") or {}).items():
        if class_name in _IGNORED_CLASSES:
            continue
        for slot_name, usage in (class_data.get("slot_usage") or {}).items():
            seen = result.setdefault(slot_name, {})
            if usage.get("range"):
                seen[usage["range"]] = None
            for ao in (usage.get("any_of") or []):
                if ao.get("range"):
                    seen[ao["range"]] = None
    return {k: list(v) for k, v in result.items()}


def _build_slot_required_classes(schema: dict) -> dict[str, list[str]]:
    """Return {slot_name: [class_name, ...]} where slot_usage sets required=true."""
    result: dict[str, list[str]] = {}
    for class_name, class_data in (schema.get("classes") or {}).items():
        if class_name in _IGNORED_CLASSES:
            continue
        for slot_name, usage in (class_data.get("slot_usage") or {}).items():
            if usage.get("required") is True:
                result.setdefault(slot_name, []).append(class_name)
    return result


def _build_enum_to_slots_classes(schema: dict) -> dict[str, dict]:
    """
    Return {enum_name: {"slots": [(slot, class), ...], "classes": [class, ...]}}
    by inspecting slot_usage ranges in every class.
    """
    enum_names = set((schema.get("enums") or {}).keys())
    result: dict[str, dict] = {}

    for class_name, class_data in (schema.get("classes") or {}).items():
        if class_name in _IGNORED_CLASSES:
            continue
        for slot_name, usage in (class_data.get("slot_usage") or {}).items():
            ranges: list[str] = []
            if usage.get("range"):
                ranges.append(usage["range"])
            for ao in usage.get("any_of") or []:
                if ao.get("range"):
                    ranges.append(ao["range"])

            for r in ranges:
                if r in enum_names:
                    entry = result.setdefault(r, {"slots": [], "classes": []})
                    entry["slots"].append((slot_name, class_name))
                    if class_name not in entry["classes"]:
                        entry["classes"].append(class_name)

    return result


def _build_slot_labels(schema: dict) -> tuple[dict[str, str], dict[str, str]]:
    """
    Return (slot_titles, slot_descriptions) by scanning slot_usage across all classes.
    The first non-empty value found wins; ties broken by class iteration order.
    """
    titles: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    for class_name, class_data in (schema.get("classes") or {}).items():
        if class_name in _IGNORED_CLASSES:
            continue
        for slot_name, usage in (class_data.get("slot_usage") or {}).items():
            if slot_name not in titles and usage.get("title"):
                titles[slot_name] = usage["title"]
            if slot_name not in descriptions and usage.get("description"):
                descriptions[slot_name] = usage["description"]
    return titles, descriptions


def load_parts(schema_path: str | Path) -> list[ODMPart]:
    """Load all ODM parts from the LinkML schema YAML."""
    with open(schema_path, encoding="utf-8") as fh:
        schema = yaml.safe_load(fh)

    classes = schema.get("classes") or {}
    slots = schema.get("slots") or {}
    enums = schema.get("enums") or {}

    slot_to_classes = _build_slot_to_classes(schema)
    slot_to_ranges = _build_slot_to_ranges(schema)
    slot_required_classes = _build_slot_required_classes(schema)
    enum_to_slots_classes = _build_enum_to_slots_classes(schema)
    slot_titles, slot_descriptions = _build_slot_labels(schema)

    # Map each enum value to all enums that contain it; first entry is primary.
    val_to_enums: dict[str, list[str]] = {}
    for enum_name, enum_data in enums.items():
        for val in (enum_data.get("permissible_values") or {}).keys():
            val_to_enums.setdefault(val, []).append(enum_name)

    parts: list[ODMPart] = []
    seen_ids: set[str] = set()

    # 1. Classes
    for class_name, class_data in classes.items():
        if class_name in _IGNORED_CLASSES:
            continue
        if class_name in seen_ids:
            continue
        seen_ids.add(class_name)
        parts.append(ODMPart(
            part_id=class_name,
            label=class_data.get("title") or class_name,
            schema_type="class",
            description=class_data.get("description") or "",
        ))

    # 2. Slots
    for slot_name in slots:
        if slot_name in seen_ids:
            continue
        seen_ids.add(slot_name)
        parts.append(ODMPart(
            part_id=slot_name,
            label=slot_titles.get(slot_name) or slot_name,
            schema_type="slot",
            description=slot_descriptions.get(slot_name) or "",
            belongs_to_classes=slot_to_classes.get(slot_name, []),
            slot_ranges=slot_to_ranges.get(slot_name, []),
            required_by_classes=slot_required_classes.get(slot_name, []),
        ))

    # 3. Enums
    for enum_name, enum_data in enums.items():
        if enum_name in seen_ids:
            continue
        seen_ids.add(enum_name)
        parts.append(ODMPart(
            part_id=enum_name,
            label=enum_data.get("title") or enum_name,
            schema_type="enum",
            description=enum_data.get("description") or "",
        ))

    # 4. Enum values (deduplicated; values shared across enums get one entry)
    for enum_name, enum_data in enums.items():
        for val_name, val_data in (enum_data.get("permissible_values") or {}).items():
            if val_name in seen_ids:
                continue
            seen_ids.add(val_name)
            vd = val_data or {}

            # Aggregate used_by_slots / used_by_classes across all enums that share this value.
            all_enums_for_val = val_to_enums.get(val_name, [enum_name])
            used_slots_seen: set[str] = set()
            used_classes_seen: set[str] = set()
            used_by_slots: list[str] = []
            used_by_classes: list[str] = []
            for e in all_enums_for_val:
                rel = enum_to_slots_classes.get(e, {})
                for s, _ in rel.get("slots", []):
                    if s not in used_slots_seen:
                        used_slots_seen.add(s)
                        used_by_slots.append(s)
                for c in rel.get("classes", []):
                    if c not in used_classes_seen:
                        used_classes_seen.add(c)
                        used_by_classes.append(c)

            parts.append(ODMPart(
                part_id=val_name,
                label=vd.get("title") or val_name,
                schema_type="enum_value",
                description=vd.get("description") or "",
                belongs_to_enum=enum_name,
                used_by_slots=used_by_slots,
                used_by_classes=used_by_classes,
            ))

    return parts
