"""Load and validate compact per-game Unity runtime profiles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import yaml


PROFILE_SCHEMA_VERSION = 1
DEFAULT_PROFILE_DIR = Path(__file__).resolve().parents[2] / "catalog" / "unity"
_READ_TYPES = {"u8", "i8", "u16", "i16", "u32", "i32", "f32", "f64", "ptr", "bool", "string"}
_SOURCE_KINDS = {"memory", "method", "constant", "fs_json"}
_ADDRESS_KINDS = {"absolute", "static_fields", "method", "chain"}
_PREDICATE_OPERATORS = {
    "equals",
    "not_equals",
    "lt",
    "lte",
    "gt",
    "gte",
    "in",
    "not_in",
    "is_null",
    "truthy",
}
_DANGEROUS_PATH_PARTS = {"__proto__", "prototype", "constructor"}
_RESERVED_TOP_LEVEL_FIELDS = {
    "schemaVersion",
    "gameId",
    "timestampMs",
    "gameTimeMs",
    "status",
    "is_actionable",
    "terminal",
    "raw",
}


class ProfileError(ValueError):
    """Raised when a Unity profile is unsafe or structurally invalid."""


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProfileError(f"{path} must be a mapping")
    return value


def _integer(value: object, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProfileError(f"{path} must be an integer >= {minimum}")
    return value


def _validate_method(
    value: Mapping[str, Any],
    path: str,
    *,
    allow_method_calls: bool,
    address_result: bool = False,
    depth: int = 0,
) -> None:
    if not allow_method_calls:
        raise ProfileError(f"{path} uses a WASM method but runtime.allow_method_calls is false")
    _integer(value.get("table_index"), f"{path}.table_index")
    if "this" in value:
        _validate_address(
            value["this"],
            f"{path}.this",
            allow_method_calls=allow_method_calls,
            depth=depth + 1,
        )
    args = value.get("args", [])
    if not isinstance(args, list) or any(not isinstance(arg, (int, float, bool)) for arg in args):
        raise ProfileError(f"{path}.args must contain only primitive numeric values")
    if "append_method_info" in value and not isinstance(value["append_method_info"], bool):
        raise ProfileError(f"{path}.append_method_info must be a boolean")
    return_type = value.get("return", "ptr" if address_result else "i32")
    allowed_returns = {"ptr", "u32", "i32"} if address_result else _READ_TYPES - {"string"}
    if return_type not in allowed_returns:
        suffix = " for an address root" if address_result else ""
        raise ProfileError(f"{path}.return is unsupported{suffix}")


def _validate_address(
    value: object,
    path: str,
    *,
    allow_method_calls: bool,
    depth: int = 0,
) -> None:
    if depth > 12:
        raise ProfileError(f"{path} exceeds the maximum address nesting depth")
    address = _mapping(value, path)
    kind = address.get("kind")
    if kind not in _ADDRESS_KINDS:
        raise ProfileError(f"{path}.kind must be one of {sorted(_ADDRESS_KINDS)}")
    if kind == "absolute":
        _integer(address.get("address"), f"{path}.address", minimum=1)
        return
    if kind == "static_fields":
        _integer(address.get("type_info"), f"{path}.type_info", minimum=1)
        if "class_static_fields_offset" in address:
            _integer(address["class_static_fields_offset"], f"{path}.class_static_fields_offset")
        return
    if kind == "method":
        _validate_method(
            address,
            path,
            allow_method_calls=allow_method_calls,
            address_result=True,
            depth=depth,
        )
        if "expected_type_info" in address:
            _integer(address["expected_type_info"], f"{path}.expected_type_info", minimum=1)
        return
    _validate_address(
        address.get("base"),
        f"{path}.base",
        allow_method_calls=allow_method_calls,
        depth=depth + 1,
    )
    steps = address.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ProfileError(f"{path}.steps must be a non-empty list")
    for index, raw_step in enumerate(steps):
        step_path = f"{path}.steps[{index}]"
        step = _mapping(raw_step, step_path)
        _integer(step.get("offset", 0), f"{step_path}.offset")
        if "dereference" in step and not isinstance(step["dereference"], bool):
            raise ProfileError(f"{step_path}.dereference must be a boolean")
        if "expected_type_info" in step:
            if step.get("dereference") is not True:
                raise ProfileError(f"{step_path}.expected_type_info requires dereference: true")
            _integer(step["expected_type_info"], f"{step_path}.expected_type_info", minimum=1)


def _validate_source(value: object, path: str, *, allow_method_calls: bool) -> None:
    source = _mapping(value, path)
    kind = source.get("kind")
    if kind not in _SOURCE_KINDS:
        raise ProfileError(f"{path}.kind must be one of {sorted(_SOURCE_KINDS)}")
    if kind == "constant":
        if "value" not in source:
            raise ProfileError(f"{path}.value is required")
        return
    if kind == "memory":
        read_type = source.get("type")
        if read_type not in _READ_TYPES:
            raise ProfileError(f"{path}.type must be one of {sorted(_READ_TYPES)}")
        _validate_address(
            source.get("address"),
            f"{path}.address",
            allow_method_calls=allow_method_calls,
        )
        if read_type == "string" and "max_length" in source:
            _integer(source["max_length"], f"{path}.max_length", minimum=1)
        return
    if kind == "method":
        _validate_method(source, path, allow_method_calls=allow_method_calls)
        return
    fs_path = source.get("path")
    if not isinstance(fs_path, str) or not fs_path.startswith("/") or ".." in Path(fs_path).parts:
        raise ProfileError(f"{path}.path must be an absolute, traversal-free virtual FS path")
    json_path = source.get("json_path", "")
    if not isinstance(json_path, str):
        raise ProfileError(f"{path}.json_path must be a dotted string")


def _validate_scalar(value: object, path: str) -> None:
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise ProfileError(f"{path} must be a JSON scalar")


def _validate_predicate(
    value: object,
    path: str,
    *,
    fields: set[str],
    depth: int = 0,
) -> None:
    if depth > 8:
        raise ProfileError(f"{path} exceeds the maximum predicate nesting depth")
    predicate = _mapping(value, path)
    logical = [key for key in ("all", "any", "not") if key in predicate]
    if logical:
        if len(logical) != 1 or len(predicate) != 1:
            raise ProfileError(f"{path} must contain exactly one logical operator")
        operator = logical[0]
        if operator == "not":
            _validate_predicate(predicate[operator], f"{path}.not", fields=fields, depth=depth + 1)
            return
        children = predicate[operator]
        if not isinstance(children, list) or not children:
            raise ProfileError(f"{path}.{operator} must be a non-empty list")
        for index, child in enumerate(children):
            _validate_predicate(
                child,
                f"{path}.{operator}[{index}]",
                fields=fields,
                depth=depth + 1,
            )
        return

    field = predicate.get("field")
    if field not in fields:
        raise ProfileError(f"{path}.field must name a configured or generated state field")
    operators = [name for name in _PREDICATE_OPERATORS if name in predicate]
    if len(operators) != 1 or len(predicate) != 2:
        raise ProfileError(f"{path} must contain field and exactly one comparison operator")
    operator = operators[0]
    operand = predicate[operator]
    if operator in {"in", "not_in"}:
        if not isinstance(operand, list) or not operand:
            raise ProfileError(f"{path}.{operator} must be a non-empty list")
        for index, item in enumerate(operand):
            _validate_scalar(item, f"{path}.{operator}[{index}]")
    elif operator in {"is_null", "truthy"}:
        if not isinstance(operand, bool):
            raise ProfileError(f"{path}.{operator} must be a boolean")
    elif operator in {"lt", "lte", "gt", "gte"}:
        if isinstance(operand, bool) or not isinstance(operand, (int, float)):
            raise ProfileError(f"{path}.{operator} must be numeric")
    else:
        _validate_scalar(operand, f"{path}.{operator}")


def _validate_action(
    value: object,
    path: str,
    *,
    allow_method_calls: bool,
    depth: int = 0,
) -> None:
    if depth > 8:
        raise ProfileError(f"{path} exceeds the maximum action nesting depth")
    action = _mapping(value, path)
    kind = action.get("kind")
    if kind == "reload":
        return
    if kind == "send_message":
        for key in ("target", "method"):
            if not isinstance(action.get(key), str) or not action[key]:
                raise ProfileError(f"{path}.{key} must be a non-empty string")
        if "value" in action and not isinstance(action["value"], (str, int, float, bool)):
            raise ProfileError(f"{path}.value must be a primitive value")
        return
    if kind == "method":
        _validate_source(action, path, allow_method_calls=allow_method_calls)
        return
    if kind == "sequence":
        actions = action.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ProfileError(f"{path}.actions must be a non-empty list")
        for index, child in enumerate(actions):
            _validate_action(
                child,
                f"{path}.actions[{index}]",
                allow_method_calls=allow_method_calls,
                depth=depth + 1,
            )
        return
    raise ProfileError(f"{path}.kind must be reload, send_message, method, or sequence")


def validate_profile(profile: object) -> dict[str, Any]:
    """Return a plain validated mapping suitable for browser serialization."""
    root = dict(_mapping(profile, "profile"))
    if root.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ProfileError(f"profile.schema_version must equal {PROFILE_SCHEMA_VERSION}")
    game_id = root.get("game_id")
    if not isinstance(game_id, str) or not game_id.strip():
        raise ProfileError("profile.game_id must be a non-empty string")

    runtime = dict(_mapping(root.get("runtime", {}), "profile.runtime"))
    pointer_size = runtime.get("pointer_size", 4)
    if pointer_size != 4:
        raise ProfileError("profile.runtime.pointer_size must be 4 for Unity WebGL")
    if "class_static_fields_offset" in runtime:
        _integer(runtime["class_static_fields_offset"], "profile.runtime.class_static_fields_offset")
    allow_method_calls = runtime.get("allow_method_calls", False)
    if not isinstance(allow_method_calls, bool):
        raise ProfileError("profile.runtime.allow_method_calls must be a boolean")

    state = dict(_mapping(root.get("state"), "profile.state"))
    fields = _mapping(state.get("fields"), "profile.state.fields")
    if not fields:
        raise ProfileError("profile.state.fields must not be empty")
    for field_path, source in fields.items():
        parts = field_path.split(".") if isinstance(field_path, str) else []
        if not parts or any(not part for part in parts):
            raise ProfileError("profile.state.fields keys must be non-empty dotted paths")
        if any(part in _DANGEROUS_PATH_PARTS for part in parts):
            raise ProfileError(f"unsafe state field path: {field_path}")
        if parts[0] in _RESERVED_TOP_LEVEL_FIELDS:
            raise ProfileError(f"reserved state field path: {field_path}")
        _validate_source(source, f"profile.state.fields.{field_path}", allow_method_calls=allow_method_calls)

    status = _mapping(state.get("status", {"default": "unknown"}), "profile.state.status")
    if "field" in status and status["field"] not in fields:
        raise ProfileError("profile.state.status.field must name a configured field")
    mapping = status.get("mapping", {})
    if not isinstance(mapping, Mapping) or any(not isinstance(value, str) for value in mapping.values()):
        raise ProfileError("profile.state.status.mapping values must be strings")
    actionable = state.get("actionable_statuses", [])
    if not isinstance(actionable, list) or any(not isinstance(item, str) for item in actionable):
        raise ProfileError("profile.state.actionable_statuses must be a list of strings")

    terminal = state.get("terminal")
    if terminal is not None:
        terminal_map = _mapping(terminal, "profile.state.terminal")
        allowed_predicate_fields = set(fields) | {"status", "is_actionable"}
        if set(terminal_map) - {"rules"}:
            raise ProfileError("profile.state.terminal supports only rules")
        rules = terminal_map.get("rules")
        if not isinstance(rules, list) or not rules:
            raise ProfileError("profile.state.terminal.rules must be a non-empty list")
        for index, raw_rule in enumerate(rules):
            rule_path = f"profile.state.terminal.rules[{index}]"
            rule = _mapping(raw_rule, rule_path)
            if set(rule) - {"when", "outcome", "reason"}:
                raise ProfileError(f"{rule_path} contains unsupported keys")
            _validate_predicate(
                rule.get("when"),
                f"{rule_path}.when",
                fields=allowed_predicate_fields,
            )
            if any(
                rule.get(name) is not None and not isinstance(rule.get(name), str)
                for name in ("outcome", "reason")
            ):
                raise ProfileError("terminal outcome and reason must be strings or null")

    actions = root.get("actions", {})
    actions_map = _mapping(actions, "profile.actions")
    for name, action in actions_map.items():
        if not isinstance(name, str) or not name:
            raise ProfileError("profile.actions keys must be non-empty strings")
        _validate_action(
            action,
            f"profile.actions.{name}",
            allow_method_calls=allow_method_calls,
        )

    capabilities = root.get("capabilities", {})
    if not isinstance(capabilities, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, bool)
        for key, value in capabilities.items()
    ):
        raise ProfileError("profile.capabilities must map string keys to booleans")

    root["runtime"] = runtime
    root["state"] = state
    return json.loads(json.dumps(root))


def profile_path(game_id: str, directory: Path | None = None) -> Path:
    return (directory or DEFAULT_PROFILE_DIR) / f"{game_id}.yaml"


def load_profile(path: Path | str) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ProfileError(f"Unable to load Unity profile {source}: {exc}") from exc
    return validate_profile(payload)


def load_profile_for_game(game_id: str, directory: Path | None = None) -> dict[str, Any] | None:
    path = profile_path(game_id, directory)
    return load_profile(path) if path.is_file() else None


__all__ = [
    "DEFAULT_PROFILE_DIR",
    "PROFILE_SCHEMA_VERSION",
    "ProfileError",
    "load_profile",
    "load_profile_for_game",
    "profile_path",
    "validate_profile",
]
