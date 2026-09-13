#!/usr/bin/env python3
"""Print the object tree of a Morrowind NIF as JSON.

Usage:
    python3 cmd/dump_nif_tree/dump_nif_tree.py INPUT_NIF
    python3 cmd/dump_nif_tree/dump_nif_tree.py INPUT_NIF > tree.json

The command uses the addon's standalone ``es3`` NIF reader, so Blender is
not required.  NiAVObject children are represented as nested ``children``
arrays, preserving the NIF hierarchy.  Other NIF object references (such as
properties, geometry data, skins, controllers, etc.) are expanded the first
time they are encountered and represented by ``{"$ref": ...}`` when a block
has already been emitted or a reference would create a cycle.
"""

from __future__ import annotations

import argparse
import json
import sys
from enum import Enum
from pathlib import Path
from typing import Any



def find_es3_dir(start: Path) -> Path:
    """Find the repository's ``io_scene_mw/es3`` directory."""
    for ancestor in (start, *start.parents):
        candidate = ancestor / "io_scene_mw" / "es3"
        if (candidate / "nif").is_dir():
            return candidate
    raise RuntimeError(
        "Could not locate io_scene_mw/es3. Run this command from the "
        "repository or keep the command under cmd/."
    )


ES3_DIR = find_es3_dir(Path(__file__).resolve().parent)
sys.path.insert(0, str(ES3_DIR.parent))

from es3 import nif  # noqa: E402


def json_value(value: Any, serializer: "NifJsonSerializer") -> Any:
    """Convert NIF/Python values into JSON-compatible values."""
    if isinstance(value, nif.NiObject):
        return serializer.object_value(value)

    if isinstance(value, Enum):
        # Keep both the symbolic name and numeric value when possible.
        try:
            return {"name": value.name, "value": value.value}
        except AttributeError:
            return value.name

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(key): json_value(item, serializer)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [json_value(item, serializer) for item in value]

    # numpy scalars/arrays are deliberately handled without importing numpy
    # directly.  The NIF library already depends on it.
    if hasattr(value, "tolist"):
        return json_value(value.tolist(), serializer)

    # LinkedList is iterable but is not itself a list.  This also handles any
    # other iterable collection used by the NIF implementation.
    try:
        if not isinstance(value, (bytes, bytearray)) and hasattr(value, "__iter__"):
            return [json_value(item, serializer) for item in value]
    except TypeError:
        pass

    if isinstance(value, (bytes, bytearray)):
        return {"$bytes": bytes(value).hex()}

    # Last resort for an unusual NIF value.  Keep the output valid JSON rather
    # than failing an otherwise useful diagnostic dump.
    return repr(value)


class NifJsonSerializer:
    """Serialize NIF blocks while preserving object identity and hierarchy."""

    def __init__(self) -> None:
        self.ids: dict[int, str] = {}
        self.emitted: set[int] = set()
        self.active: set[int] = set()
        self.next_id = 0

    def object_id(self, obj: nif.NiObject) -> str:
        key = id(obj)
        if key not in self.ids:
            self.ids[key] = f"block_{self.next_id}"
            self.next_id += 1
        return self.ids[key]

    def object_value(self, obj: nif.NiObject) -> Any:
        """Emit a block, or a reference if it has already been emitted."""
        key = id(obj)
        ref = self.object_id(obj)

        if key in self.active or key in self.emitted:
            # Include the referenced block's type and name so the reference
            # is self-describing (e.g. skin-instance bone lists that were
            # already emitted as part of the scene graph).
            return {
                "$ref": ref,
                "type": obj.type,
                "name": getattr(obj, "name", ""),
            }

        self.active.add(key)
        try:
            result: dict[str, Any] = {
                "id": ref,
                "type": obj.type,
            }

            # ``attributes()`` includes inherited annotated fields and is the
            # canonical list of serializable NIF fields in this library.
            fields: dict[str, Any] = {}
            for attr in sorted(obj.attributes()):
                # Children are represented separately below so the hierarchy
                # is explicit rather than buried inside the generic fields.
                if attr == "children":
                    continue
                value = getattr(obj, attr)
                fields[attr] = json_value(value, self)

            result["fields"] = fields

            # Keep the NIF scene graph as an actual JSON tree.  References
            # elsewhere in the NIF still use $ref when an object is shared.
            children = getattr(obj, "children", None)
            if children is not None:
                result["children"] = [
                    json_value(child, self)
                    for child in children
                    if child is not None
                ]

            self.emitted.add(key)
            return result
        finally:
            self.active.discard(key)


def dump_nif(filepath: Path) -> dict[str, Any]:
    stream = nif.NiStream()
    stream.load(filepath)

    serializer = NifJsonSerializer()
    return {
        "roots": [serializer.object_value(root) for root in stream.roots],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print a Morrowind NIF's node hierarchy and all NIF fields as JSON."
    )
    parser.add_argument("input_nif", type=Path, help="Path to the input .nif file")
    args = parser.parse_args()

    if not args.input_nif.is_file():
        parser.error(f"NIF file does not exist: {args.input_nif}")

    try:
        document = dump_nif(args.input_nif)
        json.dump(document, sys.stdout, indent=2, ensure_ascii=False, allow_nan=False)
        sys.stdout.write("\n")
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
