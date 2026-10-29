"""Read serialized Unity assets with UnityPy, independently of IL2CPP analysis."""

from __future__ import annotations

import base64
from pathlib import Path


def load_assets(source: Path):
    try:
        import UnityPy
    except ImportError as exc:
        raise RuntimeError("UnityPy is not installed. Use project Python: python -m pip install 'UnityPy==1.25.4'") from exc
    if not source.is_file():
        raise FileNotFoundError(source)
    return UnityPy.load(str(source.resolve()))


def list_assets(environment, *, type_name: str | None, name: str | None, limit: int) -> dict:
    rows = []
    for obj in environment.objects:
        if type_name and obj.type.name != type_name:
            continue
        row = {"file": obj.assets_file.name, "path_id": obj.path_id, "type": obj.type.name}
        try:
            row["name"] = obj.peek_name()
        except Exception as exc:
            row["name"] = None
            row["name_error"] = str(exc)
        if name and name.casefold() not in (row["name"] or "").casefold():
            continue
        if len(rows) == limit:
            return {"objects": rows, "truncated": True}
        rows.append(row)
    return {"objects": rows, "truncated": False}


def show_asset(environment, *, path_id: int, file_name: str | None, output: Path | None):
    matches = [obj for obj in environment.objects
               if obj.path_id == path_id and (file_name is None or obj.assets_file.name == file_name)]
    if len(matches) != 1:
        raise ValueError(f"Expected one object, found {len(matches)} for path_id={path_id}; use --file from assets list.")
    obj = matches[0]
    if output is not None:
        if obj.type.name != "TextAsset":
            raise ValueError("--output exports TextAsset bytes only; omit it to inspect a serialized object.")
        data = obj.parse_as_object().m_Script
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data.encode("utf-8", "surrogateescape") if isinstance(data, str) else data)
        return {"file": obj.assets_file.name, "path_id": obj.path_id, "output": str(output)}
    return {"file": obj.assets_file.name, "path_id": obj.path_id, "type": obj.type.name,
            "data": obj.parse_as_dict()}


def json_default(value):
    if isinstance(value, (bytes, bytearray)):
        return {"base64": base64.b64encode(value).decode("ascii")}
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")
