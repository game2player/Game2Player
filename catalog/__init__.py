"""Typed game, task, model, and prompt catalogs."""

from .games import get_game_definition_path, load_game, list_games, resolve_game_id
from .tasks import load_task, list_tasks
from .models import get_model_definition_path, load_model, list_models
from .prompt_templates import (
    list_prompt_templates,
    load_prompt_template,
)

__all__ = [
    "get_game_definition_path",
    "load_game",
    "list_games",
    "resolve_game_id",
    "get_model_definition_path",
    "load_task",
    "list_tasks",
    "load_model",
    "list_models",
    "list_prompt_templates",
    "load_prompt_template",
]
