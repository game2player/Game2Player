"""Run the coding-agent policy-authoring loop for one game."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.coding_session import load_agent

from .agent import PolicyAuthor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game", help="Exact generated game id, for example 1_example-game")
    parser.add_argument(
        "--agent",
        default="codex",
        help="coding agent: a catalog/agents name or a profile YAML path",
    )
    parser.add_argument("--max-revisions", type=int, default=50)
    parser.add_argument(
        "--experiment",
        default=None,
        help="cache experiment name; defaults to today's YYYY-MM-DD",
    )
    parser.add_argument(
        "--turn-timeout-minutes",
        type=float,
        default=25.0,
        help=(
            "how long one turn may produce no output at all before it is treated as "
            "hung; a turn that keeps streaming runs as long as it needs"
        ),
    )
    parser.add_argument(
        "--full-access",
        action="store_true",
        help="Allow the selected coding agent to edit without approval",
    )
    parser.add_argument(
        "--environment-handoff",
        type=Path,
        help="passed Builder run directory or state.json for a pending request",
    )
    restart = parser.add_mutually_exclusive_group()
    restart.add_argument(
        "--fresh",
        action="store_true",
        help="Discard this experiment's cached game run and start with an empty workspace",
    )
    restart.add_argument(
        "--repair",
        action="store_true",
        help="Discard this run and seed its workspace from the promoted policy",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        coding_agent = load_agent(args.agent)
        author = PolicyAuthor(
            args.game,
            agent=coding_agent,
            max_revisions=args.max_revisions,
            turn_timeout_s=max(1.0, args.turn_timeout_minutes * 60),
            full_access=bool(args.full_access),
            experiment=args.experiment,
            environment_handoff=args.environment_handoff,
            fresh=bool(args.fresh),
            repair=bool(args.repair),
        )
        state = author.run()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"policy-author: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "game": state.game_id,
                "experiment": author.experiment,
                "run_dir": str(author.run_dir),
                "workspace": str(author.workspace_dir),
                "status": state.status,
                "phase": state.phase,
                "revisions": state.revisions,
                "turns": state.turns,
                "state_requests": state.state_requests,
                "environment_request": (
                    str(author.environment_request_path)
                    if author.environment_request_path is not None
                    else None
                ),
                "thread_id": state.thread_id,
                "last_error": state.last_error,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if state.status in {"passed", "partial", "unsuitable"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
