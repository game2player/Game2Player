#!/usr/bin/env python3
"""Run and inspect recorded game sessions."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from logging_setup import setup_logging

DEFAULT_PORT = 8101


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser(
        "run",
        help="Play and record one game session.",
    )
    run.add_argument("--game", required=True, help="Exact catalog game id.")
    run.add_argument("--task", default=None, help="Omit for freeplay.")
    run.add_argument(
        "--actor",
        choices=("human", "policy", "vlm"),
        default="human",
        help=(
            "Player: human, policy, or vlm. "
            "Defaults to human."
        ),
    )
    run.add_argument(
        "--model",
        default=None,
        help="Model profile for VLM play: a catalog id, or the path of a profile YAML.",
    )
    run.add_argument(
        "--actor-config",
        type=Path,
        default=None,
        help="Optional YAML configuration for the selected automated actor.",
    )
    run.add_argument(
        "--plan",
        type=Path,
        default=None,
        help="Run plan YAML this episode belongs to; recorded in the summary.",
    )
    run.add_argument("--policy", default=None, help="Optional policy module:object.")
    run.add_argument(
        "--inference-mode",
        choices=("realtime", "paused"),
        default=None,
        help="Override task inference behavior for automated actors.",
    )
    run.add_argument("--port", type=int, default=DEFAULT_PORT)
    run.add_argument("--control-port", type=int, default=8767)
    run.add_argument("--suffix", default=None)
    run.add_argument("--headless", action="store_true")
    run.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/trajs/raw"),
    )
    run.add_argument("--episode-id", default=None)
    run.add_argument("--duration-s", type=float, default=None)
    run.add_argument(
        "--wall-clock-s",
        type=float,
        default=None,
        help="Real seconds the episode may run, model waits included; it then ends as a timeout.",
    )
    run.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help=(
            "Explicit automated-actor step limit. Without this option, task YAML "
            "limits apply only to the VLM player."
        ),
    )
    run.add_argument(
        "--video-backend",
        choices=("extension", "playwright"),
        default=None,
        help="Defaults to extension when headed and Playwright when headless.",
    )
    run.add_argument(
        "--video-fps",
        type=int,
        default=None,
        help="Defaults to 30 with Playwright and 60 with the extension.",
    )
    run.add_argument(
        "--video-bitrate",
        default=None,
        help="Defaults to 8M headed and 4M headless.",
    )
    run.add_argument("--no-force-cfr", dest="force_cfr", action="store_false")
    run.set_defaults(force_cfr=True)
    run.add_argument("--state-fps", type=float, default=5.0)
    run.add_argument("--input-poll-ms", type=float, default=100.0)
    run.add_argument("--post-terminal-s", type=float, default=1.0)
    run.add_argument(
        "--no-raw-mousemove",
        dest="record_raw_mousemove",
        action="store_false",
    )
    run.set_defaults(record_raw_mousemove=True)
    run.add_argument("--mousemove-min-interval-ms", type=float, default=0.0)
    run.add_argument("--random-seed", type=int, default=42)
    run.add_argument(
        "--no-stop-on-terminal",
        dest="stop_on_terminal",
        action="store_false",
    )
    run.set_defaults(stop_on_terminal=True)
    run.add_argument(
        "--no-continue-on-fail",
        dest="continue_on_fail",
        action="store_false",
        help=(
            "End the episode on a failed attempt instead of resetting and playing on. "
            "Without this option the task's own continue_on_fail applies, and "
            "--duration-s is what ends the recording."
        ),
    )
    run.set_defaults(continue_on_fail=None)

    stream = commands.add_parser(
        "stream-state",
        help="Print live game state for integration debugging.",
    )
    stream.add_argument("--game", required=True)
    stream.add_argument("--port", type=int, default=DEFAULT_PORT)
    stream.add_argument("--suffix", default=None)
    stream.add_argument("--headless", action="store_true")

    return parser


def _video_backend(args: argparse.Namespace) -> str:
    return args.video_backend or ("playwright" if args.headless else "extension")


def _video_bitrate(args: argparse.Namespace) -> str:
    return args.video_bitrate or ("4M" if args.headless else "8M")


async def _run_session(args: argparse.Namespace) -> int:
    from catalog._yaml import load_yaml_mapping
    from tools.session_runner import SessionConfig, SessionRunner

    try:
        actor_config = (
            load_yaml_mapping(args.actor_config) if args.actor_config is not None else None
        )
        run_plan = load_yaml_mapping(args.plan) if args.plan is not None else None
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    config = SessionConfig(
        game_id=args.game,
        task_id=args.task,
        actor=args.actor,
        model_id=args.model,
        actor_config=actor_config,
        policy_spec=args.policy,
        inference_mode=args.inference_mode,
        output_root=args.output_root,
        episode_id=args.episode_id,
        port=args.port,
        control_port=args.control_port,
        suffix=args.suffix,
        headless=bool(args.headless),
        duration_s=args.duration_s,
        max_steps=args.max_steps,
        wall_clock_s=args.wall_clock_s,
        video_backend=_video_backend(args),
        video_fps=args.video_fps,
        video_bitrate=_video_bitrate(args),
        force_cfr=bool(args.force_cfr),
        state_fps=args.state_fps,
        input_poll_s=max(0.01, args.input_poll_ms / 1000.0),
        post_terminal_s=args.post_terminal_s,
        record_raw_mousemove=bool(args.record_raw_mousemove),
        mousemove_min_interval_ms=args.mousemove_min_interval_ms,
        random_seed=args.random_seed,
        stop_on_terminal=bool(args.stop_on_terminal),
        continue_on_fail=args.continue_on_fail,
        run_plan=run_plan,
    )
    try:
        result = await SessionRunner(config).run()
    except (FileExistsError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "actor": result.actor,
                "outcome": result.outcome,
                "stop_reason": result.stop_reason,
                "steps": result.steps,
                "episode_dir": str(result.episode_dir) if result.episode_dir else None,
                "summary": str(result.summary_path) if result.summary_path else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


async def _stream_state(args: argparse.Namespace) -> int:
    from catalog.games import load_game, resolve_game_id
    from env.browser_manager import BrowserConfig, BrowserGameManager
    from env.game_launcher import GameLauncher, append_url_suffix
    from env.game_state_tracker import build_game_state_tracker
    from tools.unity_webgl.profile import load_profile_for_game

    game_id = resolve_game_id(args.game)
    game = load_game(game_id)
    launcher = GameLauncher(game_name=game.game_name, port=args.port)
    tracker = build_game_state_tracker()
    try:
        manager = BrowserGameManager(
            BrowserConfig(
                game_url=append_url_suffix(launcher.start(), args.suffix),
                width=game.width,
                height=game.height,
                headless=bool(args.headless),
                speed_multiplier=game.speed_multiplier,
                readiness_timeout_s=game.readiness_timeout_s,
                unity_profile=load_profile_for_game(game_id),
            )
        )
        async with manager:
            while True:
                snapshot = await tracker.snapshot(manager.page)
                print(snapshot.summary)
                await asyncio.sleep(1.0)
    finally:
        launcher.stop()


async def _main(args: argparse.Namespace) -> int:
    if args.command == "run":
        return await _run_session(args)
    if args.command == "stream-state":
        return await _stream_state(args)
    raise ValueError(f"Unsupported command: {args.command}")


def main() -> int:
    setup_logging()
    return asyncio.run(_main(_build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
