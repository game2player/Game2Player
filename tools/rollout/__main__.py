"""Run generated games with a model or policy for validation and collection.

    python -m tools.rollout run PLAN --model MODEL [--weights DIR] [--gpus 1,2 | --endpoints URL,URL] [--dry-run]
    python -m tools.rollout env-reports ROOT [--min-episodes 2] [--write]

MODEL is a catalog model id, the path of a model profile YAML, or `policy` for the
built-in scripted policies. A model whose profile has `serve` is started with vLLM on
each GPU in --gpus (only idle GPUs; --wait-idle waits for them), or played on servers
already running at --endpoints; either way the workers take the servers in turn.
--weights runs other weights of the same model (a merged fine-tune). Episodes go to
data/trajs/<mode>/<plan>/<model> unless --out says otherwise; re-running the
same command resumes. Run state lives in <out>/_scheduler/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from catalog.models import load_model  # noqa: E402
from catalog.models._base import thinking_level  # noqa: E402

from tools.paths import temporary_dir

from .env_reports import builder_requests  # noqa: E402
from .plan import load_plan  # noqa: E402
from .scheduler import (  # noqa: E402
    Scheduler,
    Worker,
    bootstrap_skip_ledger,
    build_collect_jobs,
    build_eval_jobs,
    exhausted_slot_counts,
    gives_up,
    load_skip_counts,
    scan_existing,
)
from .serve import gpu_states, log, port_free, require_idle_gpus, start_server  # noqa: E402

MAX_POLICY_WORKERS = 32
DEFAULT_POLICY_WORKERS = 8


def _tag(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@-]+", "-", text).strip("-")


def _policy_games() -> list[str]:
    from catalog import list_games
    from harnesses.policy.policies import builtin_policy_path
    return [game for game in list_games() if builtin_policy_path(game).is_file()]


def _git_commit() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()


def _interrupt(signum, frame) -> None:
    raise KeyboardInterrupt(f"signal {signum}")


def cmd_run(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    if args.thinking is not None:
        plan = replace(plan, thinking=thinking_level(args.thinking))
    if plan.continue_on_fail not in {None, False}:
        raise SystemExit("plan continue_on_fail may only be false (or absent)")
    kind = "policy" if args.model == "policy" else "vlm"
    profile = None
    gpus = [int(item) for item in args.gpus.split(",") if item.strip()] if args.gpus else []
    endpoints = [item.strip() for item in args.endpoints.split(",") if item.strip()] if args.endpoints else []
    if kind == "policy":
        if args.weights or gpus or endpoints:
            raise SystemExit("--weights, --gpus and --endpoints are for model runs")
        tag = args.tag or "policy"
    else:
        profile = load_model(args.model)
        plan = replace(plan, thinking=profile.resolve_thinking(plan.thinking))
        if profile.serve is None and (args.weights or gpus or endpoints):
            raise SystemExit(f"{profile.model_name} is not served locally: no --weights, --gpus or --endpoints")
        if profile.serve is not None and bool(gpus) == bool(endpoints):
            raise SystemExit(f"{profile.model_name} is served locally: pass --gpus to start it, "
                             "or --endpoints of servers already running")
        if endpoints and args.weights:
            raise SystemExit("--weights chooses what --gpus starts; running servers hold their own weights")
        if args.weights and not Path(args.weights).expanduser().exists():
            raise SystemExit(f"--weights not found: {args.weights}")
        tag = args.tag or (f"{profile.model_name}@{Path(args.weights).name}" if args.weights else profile.model_name)
        if args.tag is None and args.thinking is not None and plan.thinking != load_plan(args.plan).thinking:
            tag += f"-thinking-{plan.thinking}"
    tag = _tag(tag)
    if args.workers is not None and not 1 <= args.workers <= (MAX_POLICY_WORKERS if kind == "policy" else 10**6):
        raise SystemExit(f"--workers must be at least 1{' and at most ' + str(MAX_POLICY_WORKERS) if kind == 'policy' else ''}")

    # Games: the plan's, optionally narrowed; a policy run takes the games a policy exists for.
    wanted = [game.strip() for game in args.games.split(",") if game.strip()] if args.games else None
    if kind == "policy":
        policy_games = _policy_games()
        if wanted:
            unknown = sorted(set(wanted) - set(policy_games))
            if unknown:
                raise SystemExit(f"no built-in policy or tasks for: {', '.join(unknown)}")
        games = [game for game in policy_games if wanted is None or game in wanted]
    else:
        games = wanted
    cases, skipped_tasks = plan.cases(games=games)
    keys = list(dict.fromkeys((case.game_id, case.task_id) for case in cases))
    if args.tasks:
        wanted_tasks = {task.strip() for task in args.tasks.split(",") if task.strip()}
        unknown = sorted(wanted_tasks - {task for _, task in keys})
        if unknown:
            raise SystemExit(f"tasks not in the plan's selection: {', '.join(unknown)}")
        keys = [key for key in keys if key[1] in wanted_tasks]
    # A policy's list of the tasks it completes spares collection the attempts it would
    # spend giving the others up.
    not_completed = []
    if kind == "policy":
        from harnesses.policy.policies import policy_completed_tasks

        lists = {game: policy_completed_tasks(game) for game in {game for game, _ in keys}}
        not_completed = [f"{game}/{task}" for game, task in keys
                         if lists[game] is not None and task not in lists[game]]
        keys = [(game, task) for game, task in keys if lists[game] is None or task in lists[game]]
    if not keys:
        raise SystemExit("the plan selects no tasks")
    priority_games = (
        set(args.priority_games_file.read_text(encoding="utf-8").split())
        if args.priority_games_file else set()
    )

    if args.out is None and kind == "policy" and plan.mode == "collect":
        raise SystemExit("a policy collection needs --out (its name is the default seed namespace)")
    output_root = (args.out or REPO / "data" / "trajs" / plan.mode / plan.name / tag).resolve()
    control = output_root / "_scheduler"
    seed_namespace = args.seed_namespace or plan.seed_namespace or output_root.name
    # One output folder holds one model configuration: its episodes are reused as done.
    identity = {"model": args.model, "weights": str(Path(args.weights).expanduser().resolve()) if args.weights else None,
                "thinking": plan.thinking if kind == "vlm" else None}
    run_path = control / "run.json"
    if run_path.exists():
        previous = json.loads(run_path.read_text(encoding="utf-8"))
        before = {"model": previous.get("model"), "thinking": previous.get("thinking"),
                  "weights": str(Path(previous["weights"]).expanduser().resolve()) if previous.get("weights") else None}
        if before != identity:
            raise SystemExit(f"{output_root} holds episodes of {before}, not {identity}; use another --out or --tag")

    reuse_root = (plan.reuse_root or output_root).resolve() if plan.mode == "collect" else output_root
    # An evaluation keeps an episode the model ended with a report as its result.
    reported_ok = plan.mode == "eval"
    counts, durations, success_durations, invalid, by_seed, successes = scan_existing(
        reuse_root, set(keys), kind, reported_ok=reported_ok)
    if not output_root.is_relative_to(reuse_root):
        # This run's own episodes count too, or a resume would record them again.
        own = scan_existing(output_root, set(keys), kind, reported_ok=reported_ok)
        counts.update(own[0])
        for key, values in own[1].items():
            durations[key].extend(values)
        for key, values in own[2].items():
            success_durations[key].extend(values)
        invalid += own[3]
        by_seed.update(own[4])
        successes.update(own[5])
    attempts: Counter = Counter()
    given_up: list[str] = []
    if plan.mode == "collect":
        ledger = control / "skipped_jobs.jsonl"
        if args.dry_run:  # plan on a copy: a dry run leaves the run's ledger as it was
            scratch = tempfile.TemporaryDirectory(dir=temporary_dir("rollout"))
            copy = Path(scratch.name) / ledger.name
            if ledger.is_file():
                shutil.copyfile(ledger, copy)
            ledger = copy
        bootstrapped = bootstrap_skip_ledger(output_root, ledger)
        skip_counts, _ = load_skip_counts(ledger)
        exhausted = exhausted_slot_counts(ledger)
        jobs = build_collect_jobs(keys, counts, durations, success_durations, skip_counts,
                                  plan, kind, seed_namespace, successes, exhausted, priority_games,
                                  task_order=args.task_order, reverse_games=args.reverse_games)
        # Attempts toward giving a task up: what the game did, not crashes or outages.
        attempts = Counter({key: counts[key] + skip_counts[key] - exhausted[key] for key in keys})
        given_up = [f"{game}/{task}" for game, task in keys
                    if gives_up(attempts[(game, task)], successes[(game, task)], plan.give_up_after)]
        reused = sum(min(plan.target_per_task, counts[key]) for key in keys)
        skipped_at_start = sum(
            min(plan.target_per_task, counts[key] + skip_counts[key]) - min(plan.target_per_task, counts[key])
            for key in keys
        )
        target = plan.target_per_task
    else:
        bootstrapped = 0
        jobs = build_eval_jobs(keys, plan, kind, by_seed, durations)
        reused = sum(1 for key in keys for seed in plan.seeds if (*key, seed) in by_seed)
        skipped_at_start = 0
        target = len(plan.seeds)

    if kind == "policy":
        worker_count = min(args.workers or DEFAULT_POLICY_WORKERS, MAX_POLICY_WORKERS)
    elif profile is not None and profile.serve is not None:
        worker_count = args.workers or len(gpus or endpoints) * args.per_gpu
    else:
        worker_count = args.workers or 2
    summary = {
        "plan": plan.name,
        "mode": plan.mode,
        "model": tag,
        "kind": kind,
        "thinking": plan.thinking if kind == "vlm" else None,
        "tasks": len(keys),
        "missing_tasks": [f"{game}:{suffix}" for game, suffix in skipped_tasks],
        "target_per_task": target,
        "target_total": len(keys) * target,
        "reused_valid": reused,
        "skipped_slots": skipped_at_start,
        "bootstrapped_skips": bootstrapped,
        "scheduled": len(jobs),
        "priority_games": len(priority_games & {game for game, _ in keys}),
        "task_order": args.task_order if plan.mode == "collect" else None,
        "reverse_games": args.reverse_games if plan.mode == "collect" else None,
        "invalid_existing_ignored": invalid,
        "given_up_tasks": given_up,
        "not_completed_by_policy": not_completed,
        "workers": worker_count,
        "gpus": gpus,
        "endpoints": endpoints,
        "output_root": str(output_root),
        "seed_namespace": seed_namespace if plan.mode == "collect" else None,
        "first_jobs": [asdict(job) for job in jobs[:8]],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run or not jobs:
        return 0

    control.mkdir(parents=True, exist_ok=True)
    plan_copy = control / "plan.yaml"
    plan_text = Path(plan.path).read_text(encoding="utf-8")
    if plan_copy.exists() and plan_copy.read_text(encoding="utf-8") != plan_text:
        raise SystemExit(f"{output_root} was started with a different plan; use another --out")
    plan_copy.write_text(plan_text, encoding="utf-8")
    harness_path = None
    if kind == "vlm":
        harness_path = control / "harness.yaml"
        harness_path.write_text(yaml.safe_dump({
            **plan.harness,
            "thinking": plan.thinking,
            "sampling": plan.sampling,
            "report_tool": plan.report_tool,
        }, sort_keys=False), encoding="utf-8")
    (control / "run.json").write_text(json.dumps({
        "command": sys.argv,
        "git_commit": _git_commit(),
        "model": args.model,
        "weights": identity["weights"],
        "thinking": identity["thinking"],
        "gpus": gpus,
    }, indent=2) + "\n", encoding="utf-8")

    ports = [(args.port_base + index, args.control_port_base + index) for index in range(worker_count)]
    busy = [port for pair in ports for port in pair if not port_free(port)]
    if busy:
        raise SystemExit(f"ports in use: {busy}; pass --port-base / --control-port-base")

    servers = []
    previous_sigterm = signal.signal(signal.SIGTERM, _interrupt)
    try:
        workers: list[Worker] = []
        if kind == "policy":
            workers = [Worker(index, *ports[index]) for index in range(worker_count)]
        elif profile is not None and profile.serve is not None:
            models_dir = control / "models"
            models_dir.mkdir(parents=True, exist_ok=True)
            served_paths = []
            if gpus:
                require_idle_gpus(gpus, wait=args.wait_idle)
                vllm = args.vllm or os.environ.get("GAMEENV_VLLM") or "vllm"
                if shutil.which(vllm) is None:
                    raise SystemExit(f"vLLM executable not found: {vllm} (pass --vllm or set GAMEENV_VLLM)")
                states = gpu_states()
                for gpu in gpus:
                    server = start_server(profile, gpu=gpu, device=states[gpu]["uuid"],
                                          port=args.server_port_base + gpu, vllm=vllm,
                                          weights=args.weights, thinking=plan.thinking != "off",
                                          log_dir=control)
                    servers.append(server)
                    served = profile.served_at(server.endpoint).to_mapping()
                    if args.weights:
                        served["serve"]["weights"] = args.weights
                        served["serve"].pop("revision", None)
                    served_paths.append((models_dir / f"gpu{gpu}.yaml", served))
            else:
                served_paths = [(models_dir / f"server{number}.yaml", profile.served_at(endpoint).to_mapping())
                                for number, endpoint in enumerate(endpoints)]
            for path, served in served_paths:
                path.write_text(yaml.safe_dump(served, sort_keys=False), encoding="utf-8")
            # Workers take the servers in turn, so every server carries the same load.
            workers = [Worker(index, *ports[index], profile_path=served_paths[index % len(served_paths)][0])
                       for index in range(worker_count)]
        else:
            profile_path = control / "model.yaml"
            profile_path.write_text(yaml.safe_dump(profile.to_mapping(), sort_keys=False), encoding="utf-8")
            workers = [Worker(index, *ports[index], profile_path=profile_path) for index in range(worker_count)]

        if plan.mode == "collect":
            prefix = "policy" if kind == "policy" else tag
        else:
            prefix = f"{plan.name}_{tag}"
        scheduler = Scheduler(
            plan=plan,
            kind=kind,
            output_root=output_root,
            jobs=jobs,
            workers=workers,
            episode_prefix=_tag(prefix),
            plan_copy=plan_copy,
            harness_config=harness_path,
            job_timeout_s=args.job_timeout_s or (1800.0 if kind == "policy" else 86400.0),
            keep_going=getattr(args, "keep_going", False),
            reused=reused,
            skipped_at_start=skipped_at_start,
            task_count=len(keys),
            invalid_existing=invalid,
            run_info={"model": tag, "kind": kind,
                      "seed_namespace": seed_namespace if plan.mode == "collect" else None},
            task_attempts=attempts,
            task_successes=successes,
            given_up_at_start=given_up,
        )
        return asyncio.run(scheduler.run())
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        for server in servers:
            log(f"GPU {server.gpu}: stopping the server")
            server.stop()


def cmd_env_reports(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    requests = builder_requests(root, min_episodes=args.min_episodes)
    if args.write:
        target = root / "_scheduler" / "builder_requests"
        target.mkdir(parents=True, exist_ok=True)
        for request in requests:
            path = target / f"{request['game_id']}.json"
            path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"wrote {path}")
    else:
        print(json.dumps(requests, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="python -m tools.rollout", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a plan with a model")
    run.add_argument("plan", type=Path)
    run.add_argument("--model", required=True, help="catalog id, profile YAML path, or policy")
    run.add_argument("--weights", help="other weights of a locally served model (a merged fine-tune)")
    run.add_argument("--tag", help="name of this model in output paths (default: the profile name)")
    run.add_argument("--thinking", help="override the plan's thinking level (names differ per model)")
    run.add_argument("--gpus", help="comma-separated GPU indices for a locally served model")
    run.add_argument("--endpoints", help="comma-separated URLs of running servers of a locally served model")
    run.add_argument("--per-gpu", type=int, default=2, help="episodes at once per server")
    run.add_argument("--workers", type=int, help="episodes at once (API models: 2; local: --per-gpu per server; "
                                                  "policy: 8, the maximum)")
    run.add_argument("--wait-idle", action="store_true", help="wait until the GPUs are free")
    run.add_argument("--out", type=Path, help="output root (default data/trajs/<mode>/<plan>/<model>)")
    run.add_argument("--games", help="comma-separated subset of the plan's games")
    run.add_argument("--priority-games-file", type=Path,
                     help="collect: whitespace-separated game ids to run before the other selected games")
    run.add_argument("--task-order", choices=("longest-first", "round-robin"), default="longest-first",
                     help="collect: round-robin schedules each game's first task before second tasks, then third tasks")
    run.add_argument("--reverse-games", action="store_true",
                     help="collect: reverse game-id order, placing the priority group last")
    run.add_argument("--tasks", help="comma-separated subset of the selected task ids")
    run.add_argument("--seed-namespace", help="collect: seed prefix (default: the plan's, else the output folder name)")
    run.add_argument("--job-timeout-s", type=float, help="per-episode wall clock limit (policy 1800, model 86400)")
    run.add_argument("--keep-going", action="store_true",
                     help="leave failed tasks pending and continue other jobs")
    run.add_argument("--port-base", type=int, default=21100)
    run.add_argument("--control-port-base", type=int, default=22100)
    run.add_argument("--server-port-base", type=int, default=8600, help="vLLM port = this + GPU index")
    run.add_argument("--vllm", help="vLLM executable (default: $GAMEENV_VLLM, else vllm on PATH)")
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=cmd_run)
    env = commands.add_parser("env-reports", help="builder requests from reports that a game is broken")
    env.add_argument("root")
    env.add_argument("--min-episodes", type=int, default=2)
    env.add_argument("--write", action="store_true", help="write <root>/_scheduler/builder_requests/<game>.json")
    env.set_defaults(func=cmd_env_reports)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if getattr(args, "per_gpu", 1) < 1:
        raise SystemExit("--per-gpu must be >= 1")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
