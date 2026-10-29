"""Typed game catalog records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from harnesses.actions import DEFAULT_ACTION_DURATION

from .._yaml import (
    as_bool,
    as_float,
    as_int,
    as_mapping,
    as_mapping_list,
    as_optional_float,
    as_optional_text,
    as_string_set,
    as_text,
)


@dataclass(slots=True)
class RoleControls:
    """Keyboard and mouse constraints for one player role."""

    allowed_keys: set[str] = field(default_factory=set)
    hold_duration: float = DEFAULT_ACTION_DURATION
    allow_clicks: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> RoleControls:
        """Parse control limits from a YAML mapping."""
        raw = as_mapping(data)
        return cls(
            allowed_keys=as_string_set(raw.get("allowed_keys")),
            hold_duration=as_float(raw.get("hold_duration"), default=DEFAULT_ACTION_DURATION),
            allow_clicks=as_bool(raw.get("allow_clicks"), default=True),
        )

    def copy(self) -> RoleControls:
        """Return an independent copy for runtime use."""
        return RoleControls(
            allowed_keys=set(self.allowed_keys),
            hold_duration=self.hold_duration,
            allow_clicks=self.allow_clicks,
        )


@dataclass(slots=True)
class RoleContext:
    """Game facts attached to one controllable role."""

    role_description: str = ""
    control_notes: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> RoleContext:
        """Parse model-independent role context from YAML."""
        raw = as_mapping(data)
        return cls(
            role_description=as_text(raw.get("role_description")),
            control_notes=as_text(raw.get("control_notes")),
        )


@dataclass(slots=True)
class PlayerRole:
    """Definition of one controllable role in a game."""

    name: str
    controls: RoleControls = field(default_factory=RoleControls)
    context: RoleContext = field(default_factory=RoleContext)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> PlayerRole:
        """Parse one player role from YAML."""
        raw = as_mapping(data)
        return cls(
            name=as_optional_text(raw.get("name")) or "player",
            controls=RoleControls.from_mapping(raw.get("controls")),
            context=RoleContext.from_mapping(raw.get("context")),
        )


@dataclass(slots=True)
class GameDefinition:
    """Shared game definition loaded from catalog YAML."""

    game_name: str
    game_rules: str = ""
    game_roles: list[PlayerRole] = field(default_factory=list)
    speed_multiplier: float = 1.0
    width: int = 1280
    height: int = 720
    url: str | None = None
    readiness_timeout_s: float | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> GameDefinition:
        """Parse a game definition from a YAML mapping."""
        raw = as_mapping(data)
        game_name = as_optional_text(raw.get("game_name"))
        if not game_name:
            raise ValueError("Game definition missing required field: game_name")

        return cls(
            game_name=game_name,
            game_rules=as_text(raw.get("game_rules")),
            game_roles=[
                PlayerRole.from_mapping(item)
                for item in as_mapping_list(raw.get("game_roles"))
            ],
            speed_multiplier=as_float(raw.get("speed_multiplier"), default=1.0),
            width=as_int(raw.get("width"), default=1280),
            height=as_int(raw.get("height"), default=720),
            url=as_optional_text(raw.get("url")),
            readiness_timeout_s=as_optional_float(raw.get("readiness_timeout_s")),
        )

    @property
    def player(self) -> PlayerRole:
        """Return the game's single player definition."""
        if len(self.game_roles) != 1:
            raise ValueError(
                f"Game '{self.game_name}' must define exactly one player; "
                f"found {len(self.game_roles)}."
            )
        return self.game_roles[0]
