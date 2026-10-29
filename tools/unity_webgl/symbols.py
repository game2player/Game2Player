"""Low-memory SQLite index for Il2CppDumper output."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO


_TYPE_RE = re.compile(
    r"^(?P<declaration>.*?\b(?:class|struct|enum|interface)\s+(?P<name>[^\s:]+).*?)"
    r"\s*//\s*TypeDefIndex:\s*(?P<index>\d+)\s*$"
)
_FIELD_RE = re.compile(r"^\s*(?P<declaration>.+?;)\s*//\s*(?P<offset>0x[0-9A-Fa-f]+|-1)\s*$")
_RVA_RE = re.compile(r"//\s*RVA:\s*(?P<rva>0x[0-9A-Fa-f]+|-1)")
_METHOD_NAME_RE = re.compile(r"(?P<name>(?:[\w<>.$]+\.)?[\w<>.$]+)\s*\(")
_ENUM_VALUE_RE = re.compile(
    r"^\s*public\s+const\s+.+?\s+(?P<name>[\w.$<>]+)\s*=\s*"
    r"(?P<value>-?(?:0x[0-9A-Fa-f]+|\d+))\s*;\s*$"
)


class SymbolIndexError(RuntimeError):
    """Raised when reverse-engineering output is incomplete or malformed."""


def _seek_json_array(handle: TextIO, key: str, *, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    needle = json.dumps(key)
    buffer = ""
    while True:
        chunk = handle.read(chunk_size)
        if not chunk:
            raise SymbolIndexError(f"JSON array {key!r} was not found")
        buffer += chunk
        position = buffer.find(needle)
        if position >= 0:
            bracket = buffer.find("[", position + len(needle))
            while bracket < 0:
                chunk = handle.read(chunk_size)
                if not chunk:
                    raise SymbolIndexError(f"JSON array {key!r} is truncated")
                buffer += chunk
                bracket = buffer.find("[", position + len(needle))
            return buffer, bracket + 1
        buffer = buffer[-(len(needle) + 16) :]


def iter_json_array(path: Path, key: str) -> Iterator[dict[str, Any]]:
    """Stream one named top-level array without loading a large script.json."""
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        buffer, position = _seek_json_array(handle, key)
        while True:
            while True:
                while position < len(buffer) and buffer[position] in " \t\r\n,":
                    position += 1
                if position < len(buffer):
                    break
                buffer = handle.read(1024 * 1024)
                position = 0
                if not buffer:
                    raise SymbolIndexError(f"JSON array {key!r} is truncated")
            if buffer[position] == "]":
                return
            while True:
                try:
                    value, end = decoder.raw_decode(buffer, position)
                    break
                except json.JSONDecodeError:
                    tail = buffer[position:]
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        raise SymbolIndexError(f"Invalid item in JSON array {key!r}")
                    buffer = tail + chunk
                    position = 0
            if isinstance(value, dict):
                yield value
            position = end
            if position > 2 * 1024 * 1024:
                buffer = buffer[position:]
                position = 0


def _database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA foreign_keys = ON;
        CREATE TABLE metadata (
            address INTEGER NOT NULL,
            name TEXT NOT NULL,
            signature TEXT NOT NULL
        );
        CREATE INDEX metadata_name ON metadata(name);
        CREATE TABLE method_info (
            address INTEGER NOT NULL,
            name TEXT NOT NULL,
            method_address INTEGER NOT NULL
        );
        CREATE INDEX method_info_name ON method_info(name);
        CREATE TABLE wasm_methods (
            table_index INTEGER NOT NULL,
            name TEXT NOT NULL,
            signature TEXT NOT NULL,
            type_signature TEXT NOT NULL
        );
        CREATE INDEX wasm_methods_name ON wasm_methods(name);
        CREATE INDEX wasm_methods_table_index ON wasm_methods(table_index);
        CREATE TABLE types (
            type_def_index INTEGER PRIMARY KEY,
            namespace TEXT NOT NULL,
            name TEXT NOT NULL,
            declaration TEXT NOT NULL,
            source_line INTEGER NOT NULL
        );
        CREATE INDEX types_name ON types(name);
        CREATE TABLE fields (
            type_def_index INTEGER NOT NULL REFERENCES types(type_def_index),
            name TEXT NOT NULL,
            declaration TEXT NOT NULL,
            offset INTEGER NOT NULL,
            is_static INTEGER NOT NULL,
            source_line INTEGER NOT NULL
        );
        CREATE INDEX fields_name ON fields(name);
        CREATE INDEX fields_type ON fields(type_def_index);
        CREATE TABLE enum_values (
            type_def_index INTEGER NOT NULL REFERENCES types(type_def_index),
            name TEXT NOT NULL,
            value_text TEXT NOT NULL,
            value_integer INTEGER,
            source_line INTEGER NOT NULL
        );
        CREATE INDEX enum_values_name ON enum_values(name);
        CREATE INDEX enum_values_type ON enum_values(type_def_index);
        CREATE TABLE dump_methods (
            type_def_index INTEGER NOT NULL REFERENCES types(type_def_index),
            name TEXT NOT NULL,
            signature TEXT NOT NULL,
            rva INTEGER NOT NULL,
            is_static INTEGER NOT NULL,
            source_line INTEGER NOT NULL
        );
        CREATE INDEX dump_methods_name ON dump_methods(name);
        CREATE INDEX dump_methods_type ON dump_methods(type_def_index);
        """
    )


def _field_name(declaration: str) -> str:
    clean = declaration[:-1].strip()
    clean = clean.split("=", 1)[0].strip()
    return clean.rsplit(" ", 1)[-1]


def _method_name(signature: str) -> str:
    prefix = signature.split("(", 1)[0]
    return prefix.rsplit(" ", 1)[-1]


def _index_script(connection: sqlite3.Connection, script_json: Path) -> tuple[int, int, int]:
    metadata_count = 0
    method_count = 0
    rows: list[tuple[object, ...]] = []
    for item in iter_json_array(script_json, "ScriptMetadata"):
        try:
            rows.append((int(item["Address"]), str(item["Name"]), str(item.get("Signature", ""))))
        except (KeyError, TypeError, ValueError):
            continue
        if len(rows) >= 5000:
            connection.executemany("INSERT INTO metadata VALUES (?, ?, ?)", rows)
            metadata_count += len(rows)
            rows.clear()
    if rows:
        connection.executemany("INSERT INTO metadata VALUES (?, ?, ?)", rows)
        metadata_count += len(rows)

    rows = []
    for item in iter_json_array(script_json, "ScriptMethod"):
        try:
            rows.append(
                (
                    int(item["Address"]),
                    str(item["Name"]),
                    str(item.get("Signature", "")),
                    str(item.get("TypeSignature", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
        if len(rows) >= 5000:
            connection.executemany("INSERT INTO wasm_methods VALUES (?, ?, ?, ?)", rows)
            method_count += len(rows)
            rows.clear()
    if rows:
        connection.executemany("INSERT INTO wasm_methods VALUES (?, ?, ?, ?)", rows)
        method_count += len(rows)

    method_info_count = 0
    rows = []
    try:
        for item in iter_json_array(script_json, "ScriptMetadataMethod"):
            try:
                rows.append(
                    (
                        int(item["Address"]),
                        str(item["Name"]),
                        int(item.get("MethodAddress", 0)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
            if len(rows) >= 5000:
                connection.executemany("INSERT INTO method_info VALUES (?, ?, ?)", rows)
                method_info_count += len(rows)
                rows.clear()
    except SymbolIndexError as exc:
        if "was not found" not in str(exc):
            raise
    if rows:
        connection.executemany("INSERT INTO method_info VALUES (?, ?, ?)", rows)
        method_info_count += len(rows)
    return metadata_count, method_count, method_info_count


def _index_dump(connection: sqlite3.Connection, dump_cs: Path) -> tuple[int, int, int, int]:
    namespace = ""
    current_type: int | None = None
    section = ""
    pending_rva: int | None = None
    type_count = field_count = enum_count = method_count = 0
    with dump_cs.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if stripped.startswith("// Namespace:"):
                namespace = stripped.partition(":")[2].strip()
                current_type = None
                section = ""
                pending_rva = None
                continue
            type_match = _TYPE_RE.match(stripped)
            if type_match:
                current_type = int(type_match.group("index"))
                connection.execute(
                    "INSERT OR REPLACE INTO types VALUES (?, ?, ?, ?, ?)",
                    (
                        current_type,
                        namespace,
                        type_match.group("name"),
                        type_match.group("declaration"),
                        line_number,
                    ),
                )
                type_count += 1
                section = ""
                pending_rva = None
                continue
            if current_type is None:
                continue
            if stripped in {"// Fields", "// Properties", "// Methods"}:
                section = stripped[3:].lower()
                pending_rva = None
                continue
            if section == "fields":
                enum_match = _ENUM_VALUE_RE.match(line.rstrip("\n"))
                if enum_match:
                    enum_value = int(enum_match.group("value"), 0)
                    sqlite_value = enum_value if -(2**63) <= enum_value < 2**63 else None
                    connection.execute(
                        "INSERT INTO enum_values VALUES (?, ?, ?, ?, ?)",
                        (
                            current_type,
                            enum_match.group("name"),
                            str(enum_value),
                            sqlite_value,
                            line_number,
                        ),
                    )
                    enum_count += 1
                    continue
                field_match = _FIELD_RE.match(line.rstrip("\n"))
                if field_match:
                    declaration = field_match.group("declaration").strip()
                    raw_offset = field_match.group("offset")
                    offset = -1 if raw_offset == "-1" else int(raw_offset, 16)
                    connection.execute(
                        "INSERT INTO fields VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            current_type,
                            _field_name(declaration),
                            declaration,
                            offset,
                            int(" static " in f" {declaration} "),
                            line_number,
                        ),
                    )
                    field_count += 1
                continue
            if section != "methods":
                continue
            rva_match = _RVA_RE.search(stripped)
            if rva_match:
                raw_rva = rva_match.group("rva")
                pending_rva = -1 if raw_rva == "-1" else int(raw_rva, 16)
                continue
            if pending_rva is None or not stripped or stripped.startswith("[") or "(" not in stripped:
                continue
            signature = stripped
            connection.execute(
                "INSERT INTO dump_methods VALUES (?, ?, ?, ?, ?, ?)",
                (
                    current_type,
                    _method_name(signature),
                    signature,
                    pending_rva,
                    int(" static " in f" {signature} "),
                    line_number,
                ),
            )
            method_count += 1
            pending_rva = None
    return type_count, field_count, enum_count, method_count


def build_symbol_index(output_dir: Path | str, destination: Path | None = None) -> Path:
    root = Path(output_dir).resolve()
    script_json = root / "script.json"
    dump_cs = root / "dump.cs"
    if not script_json.is_file() or not dump_cs.is_file():
        raise SymbolIndexError(f"Il2CppDumper output is incomplete: {root}")
    target = (destination or root / "symbols.sqlite3").resolve()
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(temporary)
        try:
            _database(connection)
            metadata_count, wasm_method_count, method_info_count = _index_script(
                connection,
                script_json,
            )
            type_count, field_count, enum_count, dump_method_count = _index_dump(connection, dump_cs)
            connection.execute("CREATE TABLE info (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.executemany(
                "INSERT INTO info VALUES (?, ?)",
                [
                    ("metadata_count", str(metadata_count)),
                    ("wasm_method_count", str(wasm_method_count)),
                    ("method_info_count", str(method_info_count)),
                    ("type_count", str(type_count)),
                    ("field_count", str(field_count)),
                    ("enum_count", str(enum_count)),
                    ("dump_method_count", str(dump_method_count)),
                ],
            )
            connection.commit()
        finally:
            connection.close()
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def search_symbols(
    database: Path | str,
    query: str,
    *,
    limit: int = 100,
    kind: str = "all",
) -> list[dict[str, Any]]:
    if not query.strip():
        raise ValueError("Symbol query must not be empty")
    pattern = f"%{query}%"
    results: list[dict[str, Any]] = []
    filter_kind = kind
    allowed_kinds = {
        "all",
        "type",
        "field",
        "enum",
        "method",
        "method_info",
        "metadata",
    }
    if filter_kind not in allowed_kinds:
        raise ValueError(f"Symbol kind must be one of {sorted(allowed_kinds)}")
    connection = sqlite3.connect(
        f"file:{Path(database).resolve()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        has_enum_values = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'enum_values'"
        ).fetchone() is not None
        has_method_info = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'method_info'"
        ).fetchone() is not None
        searches = [
            (
                "type",
                "SELECT name, namespace, declaration, type_def_index AS address, source_line "
                "FROM types WHERE name LIKE ? OR declaration LIKE ? ORDER BY name LIMIT ?",
            ),
            (
                "field",
                "SELECT f.name, t.name AS owner, f.declaration, f.offset AS address, f.source_line "
                "FROM fields f JOIN types t USING(type_def_index) "
                "WHERE f.name LIKE ? OR f.declaration LIKE ? ORDER BY f.name LIMIT ?",
            ),
            (
                "enum",
                "SELECT e.name, t.name AS owner, (e.name || ' = ' || e.value_text) AS declaration, "
                "COALESCE(e.value_integer, e.value_text) AS address, e.source_line "
                "FROM enum_values e JOIN types t USING(type_def_index) "
                "WHERE e.name LIKE ? OR t.name LIKE ? "
                "ORDER BY t.name, e.value_integer IS NULL, e.value_integer, e.value_text LIMIT ?",
            ),
            (
                "method",
                "SELECT name, '' AS owner, signature AS declaration, table_index AS address, 0 AS source_line "
                "FROM wasm_methods WHERE name LIKE ? OR signature LIKE ? ORDER BY name LIMIT ?",
            ),
            (
                "method_info",
                "SELECT name, '' AS owner, ('metadata-method locator; method_address=' || method_address) "
                "AS declaration, address, 0 AS source_line FROM method_info "
                "WHERE name LIKE ? OR CAST(method_address AS TEXT) LIKE ? ORDER BY name LIMIT ?",
            ),
            (
                "metadata",
                "SELECT name, '' AS owner, signature AS declaration, address, 0 AS source_line "
                "FROM metadata WHERE name LIKE ? OR signature LIKE ? ORDER BY name LIMIT ?",
            ),
        ]
        for row_kind, sql in searches:
            if row_kind == "enum" and not has_enum_values:
                continue
            if row_kind == "method_info" and not has_method_info:
                continue
            if filter_kind != "all" and row_kind != filter_kind:
                continue
            remaining = limit - len(results)
            if remaining <= 0:
                break
            for row in connection.execute(sql, (pattern, pattern, remaining)):
                item = dict(row)
                item["kind"] = row_kind
                results.append(item)
    finally:
        connection.close()
    return results


def describe_type(database: Path | str, query: str, *, limit: int = 200) -> dict[str, Any]:
    """Return one type with its fields, methods, table indices, and TypeInfo candidates."""
    if not query.strip():
        raise ValueError("Type query must not be empty")
    connection = sqlite3.connect(
        f"file:{Path(database).resolve()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        exact = connection.execute(
            "SELECT * FROM types WHERE name = ? OR (namespace || '.' || name) = ? "
            "ORDER BY CASE WHEN name = ? THEN 0 ELSE 1 END, type_def_index LIMIT 2",
            (query, query, query),
        ).fetchall()
        if not exact:
            matches = connection.execute(
                "SELECT * FROM types WHERE name LIKE ? OR declaration LIKE ? ORDER BY name LIMIT 6",
                (f"%{query}%", f"%{query}%"),
            ).fetchall()
            return {"query": query, "ambiguous": True, "matches": [dict(row) for row in matches]}
        if len(exact) > 1 and all(row["name"] == query for row in exact):
            return {"query": query, "ambiguous": True, "matches": [dict(row) for row in exact]}
        selected = dict(exact[0])
        type_index = selected["type_def_index"]
        fields = [
            dict(row)
            for row in connection.execute(
                "SELECT name, declaration, offset, is_static, source_line FROM fields "
                "WHERE type_def_index = ? ORDER BY offset, source_line LIMIT ?",
                (type_index, limit),
            )
        ]
        has_enum_values = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'enum_values'"
        ).fetchone() is not None
        enum_values = (
            [
                dict(row)
                for row in connection.execute(
                    "SELECT name, COALESCE(value_integer, value_text) AS value, "
                    "source_line FROM enum_values "
                    "WHERE type_def_index = ? ORDER BY value_integer IS NULL, "
                    "value_integer, value_text, source_line",
                    (type_index,),
                )
            ]
            if has_enum_values
            else []
        )
        method_rows = connection.execute(
            "SELECT name, signature, rva, is_static, source_line FROM dump_methods "
            "WHERE type_def_index = ? ORDER BY source_line LIMIT ?",
            (type_index, limit),
        ).fetchall()
        owners = []
        if selected["namespace"]:
            owners.append(f"{selected['namespace']}.{selected['name']}")
        owners.append(selected["name"])
        methods = []
        for method_row in method_rows:
            item = dict(method_row)
            aliases = connection.execute(
                "SELECT table_index, name, type_signature FROM wasm_methods "
                "WHERE table_index = ? ORDER BY name",
                (item["rva"],),
            ).fetchall()
            expected_names = [f"{owner}$${item['name']}" for owner in owners]
            matching = [row for expected in expected_names for row in aliases if row["name"] == expected]
            if not matching:
                matching = [
                    row
                    for expected in expected_names
                    for row in aliases
                    if row["name"].endswith(expected)
                ]
            selected_alias = matching[0] if matching else None
            item.update(
                {
                    "table_index": item["rva"] if item["rva"] >= 0 else None,
                    "wasm_name": selected_alias["name"] if selected_alias else None,
                    "type_signature": selected_alias["type_signature"] if selected_alias else None,
                    "shared_alias_count": len(aliases),
                    "alias_matched": selected_alias is not None,
                }
            )
            methods.append(item)
        type_names = [selected["name"]]
        if selected["namespace"]:
            type_names.insert(0, f"{selected['namespace']}.{selected['name']}")
        metadata_names = []
        for type_name in type_names:
            for variant in (type_name, type_name.replace("+", "."), type_name.replace(".", "_")):
                candidate = f"{variant}_TypeInfo"
                if candidate not in metadata_names:
                    metadata_names.append(candidate)
        metadata = []
        seen_metadata: set[tuple[int, str]] = set()
        for candidate in metadata_names:
            for row in connection.execute(
                "SELECT address, name, signature FROM metadata WHERE name = ? LIMIT 12",
                (candidate,),
            ):
                key = (row["address"], row["name"])
                if key not in seen_metadata:
                    metadata.append(dict(row))
                    seen_metadata.add(key)
        if len(metadata) < 12:
            for row in connection.execute(
                "SELECT address, name, signature FROM metadata WHERE name LIKE ? ORDER BY name LIMIT 24",
                (f"%{selected['name']}%TypeInfo",),
            ):
                key = (row["address"], row["name"])
                if key not in seen_metadata:
                    metadata.append(dict(row))
                    seen_metadata.add(key)
                    if len(metadata) >= 12:
                        break
        exact_metadata_names = set(metadata_names)
        for item in metadata:
            item["exact_match"] = item["name"] in exact_metadata_names
        has_method_info = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'method_info'"
        ).fetchone() is not None
        method_info_candidates = []
        if has_method_info:
            type_name = str(selected["name"])
            generic_base = type_name.split("<", 1)[0]
            patterns = [f"%<{type_name}>%", f"Method${type_name}.%"]
            if "<" in type_name:
                patterns.insert(0, f"Method${generic_base}<%")
            seen_method_info: set[tuple[int, str]] = set()
            for pattern in patterns:
                for row in connection.execute(
                    "SELECT address, name, method_address FROM method_info "
                    "WHERE name LIKE ? ORDER BY name LIMIT 50",
                    (pattern,),
                ):
                    key = (row["address"], row["name"])
                    if key not in seen_method_info:
                        method_info_candidates.append(dict(row))
                        seen_method_info.add(key)
                        if len(method_info_candidates) >= 50:
                            break
                if len(method_info_candidates) >= 50:
                    break
        return {
            "query": query,
            "ambiguous": False,
            "type": selected,
            "type_info_candidates": metadata,
            "method_info_candidates": method_info_candidates,
            "fields": fields,
            "enum_values": enum_values,
            "methods": methods,
        }
    finally:
        connection.close()


__all__ = [
    "SymbolIndexError",
    "build_symbol_index",
    "describe_type",
    "iter_json_array",
    "search_symbols",
]
