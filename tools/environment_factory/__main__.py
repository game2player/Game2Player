"""Run the Scout or Builder coding agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.coding_session import load_agent

from .builder import ENGINES, Builder
from .scout import Scout


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent",
        default="codex",
        help="coding agent: a catalog/agents name or a profile YAML path",
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
    parser.add_argument("--fresh", action="store_true", help="discard the cached thread")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "repository root the agent works in; point it at a checkout under the "
            "run cache to keep an unfinished build out of the live catalog"
        ),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scout = commands.add_parser("scout", help="research a batch without editing the repo")
    scout.add_argument("campaign")
    scout.add_argument("--brief", required=True)
    _common(scout)
    builder = commands.add_parser("build", help="build and validate exactly one game")
    builder.add_argument("game")
    builder.add_argument("--source-url", default="")
    builder.add_argument("--title", default="")
    build_input = builder.add_mutually_exclusive_group()
    build_input.add_argument(
        "--brief",
        default="",
        help="narrow instructions for an existing or partially built environment",
    )
    build_input.add_argument(
        "--request",
        type=Path,
        help="Policy Author or player-report request JSON",
    )
    builder.add_argument("--max-revisions", type=int, default=3)
    builder.add_argument(
        "--redesign-tasks",
        action="store_true",
        help=(
            "audit an existing environment with its task set unfrozen; DISCOVER may "
            "replace tasks, and every dropped task must be named in evidence"
        ),
    )
    builder.add_argument(
        "--run-id",
        default=None,
        help="optional readable cache run name for an independent build",
    )
    builder.add_argument(
        "--engine",
        choices=ENGINES,
        default="unknown",
        help="optional trusted engine hint; lets DISCOVER load the right skill immediately",
    )
    _common(builder)
    return parser


def main() -> int:
    args = _parser().parse_args()
    timeout = max(1.0, args.turn_timeout_minutes * 60)
    try:
        coding_agent = load_agent(args.agent)
        if args.command == "scout":
            agent = Scout(
                args.campaign,
                root=args.root,
                agent=coding_agent,
                turn_timeout_s=timeout,
                full_access=bool(args.full_access),
                fresh=bool(args.fresh),
            )
            result = agent.run(args.brief)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        brief = args.brief
        run_id = args.run_id
        player_reports = None
        if args.request is not None:
            brief, suggested_run_id = _load_build_request(args.request, args.game)
            run_id = run_id or suggested_run_id
            player_reports = json.loads(args.request.expanduser().read_text()).get("reports")
        agent = Builder(
            args.game,
            source_url=args.source_url,
            title=args.title,
            brief=brief,
            player_reports=player_reports,
            engine=args.engine,
            redesign_tasks=bool(args.redesign_tasks),
            root=args.root,
            agent=coding_agent,
            max_revisions=args.max_revisions,
            turn_timeout_s=timeout,
            full_access=bool(args.full_access),
            fresh=bool(args.fresh),
            run_id=run_id,
        )
        state = agent.run()
        print(
            json.dumps(
                {
                    "game": state.game_id,
                    "status": state.status,
                    "phase": state.phase,
                    "engine": state.engine,
                    "revisions": state.revisions,
                    "turns": state.turns,
                    "thread_id": state.thread_id,
                    "last_error": state.last_error,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if state.status in {"passed", "unsuitable"} else 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"environment-factory: {exc}", file=sys.stderr)
        return 2


def _load_build_request(path: Path, game_id: str) -> tuple[str, str | None]:
    try:
        request = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Policy Author request: {path}") from exc
    if not isinstance(request, dict) or request.get("game_id") != game_id:
        raise ValueError("Policy Author request belongs to another game")
    brief = request.get("builder_brief")
    if not isinstance(brief, str) or not brief.strip():
        raise ValueError("Policy Author request has no Builder brief")
    run_id = request.get("suggested_builder_run_id")
    if run_id is not None and not isinstance(run_id, str):
        raise ValueError("Policy Author request has an invalid Builder run id")
    return brief, run_id


if __name__ == "__main__":
    raise SystemExit(main())
