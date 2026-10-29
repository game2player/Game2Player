"""One recording interface for human, policy, and VLM game sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalog.tasks import load_task
from harnesses.registry import create_harness
from tools.trajectory_recorder import (
    FreeplayTrajectoryRecorder,
    TrajectoryRecorder,
    RecordingConfig,
)

from .automated import AutomatedTrajectoryRecorder, InferenceMode


@dataclass(slots=True)
class SessionConfig:
    game_id: str
    task_id: str | None = None
    actor: str = "human"
    model_id: str | None = None
    actor_config: dict[str, Any] | None = None
    policy_spec: str | None = None
    inference_mode: InferenceMode | None = None
    output_root: Path = Path("data/trajs/raw")
    episode_id: str | None = None
    port: int = 8101
    control_port: int = 8767
    suffix: str | None = None
    headless: bool = False
    duration_s: float | None = None
    max_steps: int | None = None
    wall_clock_s: float | None = None
    video_backend: str = "extension"
    video_fps: int | None = None
    video_bitrate: str = "8M"
    force_cfr: bool = True
    state_fps: float = 5.0
    input_poll_s: float = 0.1
    post_terminal_s: float = 1.0
    record_raw_mousemove: bool = True
    mousemove_min_interval_ms: float = 0.0
    random_seed: int | None = 42
    stop_on_terminal: bool = True
    continue_on_fail: bool | None = None
    run_plan: dict[str, Any] | None = None


@dataclass(slots=True)
class SessionResult:
    actor: str
    outcome: str
    stop_reason: str | None
    final_metrics: dict[str, object]
    steps: int
    episode_dir: Path | None
    summary_path: Path | None


class SessionRunner:
    """Select the actor without changing the episode artifact contract."""

    def __init__(self, config: SessionConfig):
        self.config = config

    def _validate(self) -> None:
        if self.config.actor == "vlm":
            if not self.config.model_id:
                raise ValueError(f"{self.config.actor} sessions require --model.")
            if self.config.policy_spec:
                raise ValueError(
                    f"--policy cannot be used with --actor {self.config.actor}."
                )
        elif self.config.actor_config:
            raise ValueError("--actor-config is only supported by the VLM player.")
        elif self.config.actor == "policy":
            if self.config.model_id:
                raise ValueError("--model cannot be used with --actor policy.")
        elif self.config.actor == "human":
            if self.config.model_id or self.config.policy_spec:
                raise ValueError("Human sessions do not accept --model or --policy.")
            if self.config.inference_mode is not None:
                raise ValueError("Human sessions do not accept --inference-mode.")
            if self.config.headless:
                raise ValueError("Human sessions require a headed browser.")
        else:
            supported = "human, policy, vlm"
            raise ValueError(
                f"Unsupported actor: {self.config.actor}. Supported: {supported}"
            )

        if self.config.inference_mode not in {None, "realtime", "paused"}:
            raise ValueError(f"Unsupported inference mode: {self.config.inference_mode}")
        if (
            self.config.actor != "human"
            and self.config.duration_s is None
            and self.config.max_steps is None
        ):
            catalog_max_steps = None
            if self.config.actor == "vlm" and self.config.task_id is not None:
                catalog_max_steps = load_task(
                    self.config.game_id, self.config.task_id
                ).max_steps
            if catalog_max_steps is None:
                raise ValueError(
                    "Automated sessions require --duration-s or --max-steps."
                )

    def _recording_config(self) -> RecordingConfig:
        video_fps = self.config.video_fps
        if video_fps is None:
            video_fps = 30 if self.config.video_backend == "playwright" else 60

        return RecordingConfig(
            game_id=self.config.game_id,
            task_id=self.config.task_id,
            output_root=self.config.output_root,
            port=self.config.port,
            control_port=self.config.control_port,
            suffix=self.config.suffix,
            headless=self.config.headless,
            video_backend=self.config.video_backend,
            video_fps=video_fps,
            video_bitrate=self.config.video_bitrate,
            force_cfr=self.config.force_cfr,
            state_fps=self.config.state_fps,
            input_poll_s=self.config.input_poll_s,
            duration_s=self.config.duration_s,
            post_terminal_s=self.config.post_terminal_s,
            record_raw_mousemove=self.config.record_raw_mousemove,
            mousemove_min_interval_ms=self.config.mousemove_min_interval_ms,
            random_seed=self.config.random_seed,
            stop_on_terminal=self.config.stop_on_terminal,
            continue_on_fail=self.config.continue_on_fail,
            episode_id=self.config.episode_id,
            player_type=self.config.actor,
            max_steps=self.config.max_steps,
            wall_clock_s=self.config.wall_clock_s,
            actor_metadata={"kind": self.config.actor},
            run_plan=self.config.run_plan,
        )

    def _build_session(self) -> TrajectoryRecorder:
        recording = self._recording_config()
        if self.config.actor == "human":
            if self.config.task_id is None:
                return FreeplayTrajectoryRecorder(recording)
            return TrajectoryRecorder(recording)
        if self.config.actor in {"policy", "vlm"}:
            harness = create_harness(
                self.config.actor,
                game_id=self.config.game_id,
                model_id=self.config.model_id,
                policy_spec=self.config.policy_spec,
                actor_config=self.config.actor_config,
            )
            return AutomatedTrajectoryRecorder(
                recording,
                harness=harness,
                inference_mode=self.config.inference_mode,
            )
        raise ValueError(f"Unsupported actor: {self.config.actor}")

    async def run(self) -> SessionResult:
        self._validate()
        session = self._build_session()
        episode_dir = await session.run()
        return SessionResult(
            actor=self.config.actor,
            outcome=session.outcome,
            stop_reason=session.stop_reason,
            final_metrics=session.final_metrics,
            steps=session.actor_steps,
            episode_dir=episode_dir,
            summary_path=episode_dir / "summary.json" if episode_dir else None,
        )


__all__ = ["SessionConfig", "SessionResult", "SessionRunner"]
