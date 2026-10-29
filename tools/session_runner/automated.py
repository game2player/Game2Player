"""One automated session loop shared by every agent harness."""

from __future__ import annotations

import asyncio
import hashlib
from collections import deque
from datetime import datetime
from typing import Any, Literal

from playwright.async_api import Error as PlaywrightError

from env.action_executor import ActionExecutor
from env.game_state_tracker import build_policy_state_bundle_tracker
from harnesses.base import AgentHarness, HarnessContext, HarnessObservation
from harnesses.policy.policies import is_terminal
from tools.trajectory_recorder.recorder import TrajectoryRecorder, RecordingConfig


InferenceMode = Literal["realtime", "paused"]


class AutomatedTrajectoryRecorder(TrajectoryRecorder):
    """Drive the normal recorder with a pluggable harness."""

    def __init__(
        self,
        config: RecordingConfig,
        *,
        harness: AgentHarness,
        inference_mode: InferenceMode | None,
    ):
        self.harness = harness
        config.player_type = str(
            harness.actor_metadata.get("agent")
            or harness.actor_metadata.get("kind")
            or "automated"
        )
        config.actor_metadata = dict(harness.actor_metadata)
        super().__init__(config)
        self.inference_mode = self._resolve_inference_mode(inference_mode)
        self.config.actor_metadata = {
            **dict(self.harness.actor_metadata),
            "inference_mode": self.inference_mode,
        }
        self._executor: ActionExecutor | None = None
        self._policy_state_bundle_tracker = (
            build_policy_state_bundle_tracker()
            if self.harness.actor_metadata.get("kind") == "policy"
            else None
        )
        self._step_index = 0
        self._previous_result: dict[str, Any] | None = None
        # (screen digest, task progress) of the latest observations, newest last: the
        # evidence attached to a report that the game is broken.
        self._recent: deque[tuple[str | None, Any]] = deque(maxlen=50)

    def _resolve_inference_mode(self, requested: InferenceMode | None) -> InferenceMode:
        if requested is not None:
            return requested
        if self.harness.actor_metadata.get("kind") == "vlm" and self.task is not None:
            return "paused" if self.task.pause_during_inference else "realtime"
        return "realtime"

    def _context(self) -> HarnessContext:
        return HarnessContext(
            game_id=self.game_id,
            game=self.game,
            task=self.task,
            action_contract=self.harness.action_contract,
            seed=self.config.random_seed,
        )

    def _build_episode_id(self) -> str:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        actor = str(self.config.player_type or "automated")
        if self.harness.actor_metadata.get("kind") == "vlm":
            model = str((self.config.actor_metadata or {}).get("model_id") or "model")
            actor = f"{actor}_{model}"
        label = self.task_id or "freeplay"
        return f"{self.game_id}_{label}_{actor}_{stamp}".replace("/", "-")

    def _report_evidence(self, reason: str, t: float) -> dict[str, Any]:
        """The report with what the recording itself says: for how many of the latest
        observations the screen and the task progress have not changed."""
        def unchanged(index: int) -> int:
            values = [entry[index] for entry in self._recent]
            if not values or values[-1] is None:
                return 0
            count = 0
            for value in reversed(values[:-1]):
                if value != values[-1]:
                    break
                count += 1
            return count

        return {
            "reason": reason,
            "step": self._step_index,
            "t": round(t, 6),
            "screen_unchanged_steps": unchanged(0),
            "progress_unchanged_steps": unchanged(1),
            "progress": self._recent[-1][1] if self._recent else None,
        }

    async def _capture_state(self, page) -> dict[str, Any] | None:
        state = await self.tracker.capture(page)
        return state if isinstance(state, dict) else None

    async def _observe(
        self, page
    ) -> tuple[HarnessObservation, dict[str, Any] | None]:
        trusted_state, policy_state = await self._capture_state_bundle(page)
        screenshot = None
        if self.harness.observation_mode == "screenshot":
            if self.manager is None:
                raise RuntimeError("Browser manager is unavailable.")
            screenshot = await self.manager.capture_screenshot(
                f"agent_step_{self._step_index:06d}.png"
            )
        self._recent.append((
            hashlib.sha256(screenshot.read_bytes()).hexdigest() if screenshot else None,
            self._eval_metrics.get("progress_current", self._eval_metrics.get("progress")),
        ))
        return (
            HarnessObservation(
                step_index=self._step_index,
                t=self._elapsed_t(),
                policy_state=policy_state,
                screenshot_path=screenshot,
                previous_result=self._previous_result,
            ),
            trusted_state,
        )

    async def _capture_state_bundle(
        self, page
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if self._policy_state_bundle_tracker is None:
            return (await self._capture_state(page), None)
        bundle = await self._policy_state_bundle_tracker.capture(page)
        if not isinstance(bundle, dict):
            return None, None
        state = bundle.get("state")
        policy_state = bundle.get("policy_state")
        return (
            state if isinstance(state, dict) else None,
            policy_state if isinstance(policy_state, dict) else None,
        )

    def _page_init_scripts(self) -> tuple[str, ...]:
        if self.harness.actor_metadata.get("kind") != "policy":
            return ()
        return tuple(self.harness.page_init_scripts)

    async def _infer(self, observation: HarnessObservation):
        paused = False
        if self.inference_mode == "paused":
            paused = await self._begin_inference_pause()
        try:
            return await self.harness.step(observation)
        finally:
            if paused:
                await self._end_inference_pause()

    async def _after_task_reset(self) -> None:
        if self._executor is not None:
            await self._executor.release_all_keys()
        self._previous_result = None
        await self.harness.reset(self._context())

    def _max_steps(self) -> int | None:
        if self.config.max_steps is not None:
            return self.config.max_steps
        if (
            self.task is not None
            and self.harness.actor_metadata.get("kind") == "vlm"
        ):
            return self.task.max_steps
        return None

    async def _wait_for_stop(self) -> None:
        page = self.manager.page if self.manager else None
        if page is None:
            raise RuntimeError("Browser page is unavailable.")

        self._executor = ActionExecutor(page, self.game.player.controls,
                                        on_input=self._write_executed_input)
        try:
            await self.harness.reset(self._context())
            max_steps = self._max_steps()

            while self._stop_event and not self._stop_event.is_set():
                if self._gameplay_ready is not None:
                    await self._gameplay_ready.wait()
                if self._stop_deadline is not None:
                    if self._elapsed_t() >= self._stop_deadline:
                        self._request_stop(self._outcome, self._stop_reason or "terminal")
                        break
                    await asyncio.sleep(0.05)
                    continue
                if (
                    self.config.duration_s is not None
                    and self._elapsed_t() >= self.config.duration_s
                ):
                    await self._stop_at_limit("duration")
                    break
                if max_steps is not None and self._step_index >= max_steps:
                    await self._stop_at_limit("max_steps")
                    break
                if self._wall_clock_spent():
                    await self._stop_at_limit("wall_clock")
                    break
                try:
                    observation, trusted_state = await self._observe(page)
                except PlaywrightError as exc:
                    if not await self._recover_navigation(exc):
                        raise
                    continue
                # A finished game waits for the recorder to stop or reset the task.
                # Failed resets now raise an interface report instead of resuming play.
                if is_terminal(trusted_state):
                    await self._executor.release_all_keys()
                    if self.task is not None:
                        await asyncio.sleep(0.05)
                        continue
                    terminal = (
                        trusted_state.get("terminal") if trusted_state else None
                    )
                    outcome = "fail"
                    reason = "terminal"
                    if isinstance(terminal, dict):
                        outcome = str(terminal.get("outcome") or "fail")
                        reason = str(terminal.get("reason") or "terminal")
                    if self.config.stop_on_terminal and self._stop_deadline is None:
                        self._outcome = outcome if outcome in {"success", "fail"} else "terminal"
                        self._stop_reason = reason
                        self._stop_deadline = self._elapsed_t() + max(
                            0.0,
                            self.config.post_terminal_s,
                        )
                    await asyncio.sleep(0.05)
                    continue

                decision = await self._infer(observation)
                if self._gameplay_ready is not None:
                    await self._gameplay_ready.wait()
                if self._stop_event.is_set() or self._stop_deadline is not None:
                    continue
                if decision.stop:
                    self._request_stop("stopped", "agent_stop")
                    break

                inspections = [self._executor.inspect_action(action) for action in decision.plan.actions]
                # Persist every CUA response, including direct actions and parse
                # failures, so thinking incidence can be measured without feeding
                # the thinking back into the next observation.
                if self.harness.actor_metadata.get("kind") == "vlm" or decision.thinking:
                    self._write_actor_decision(
                        step_index=self._step_index,
                        actions=list(decision.plan.actions),
                        thinking=decision.thinking,
                        note=decision.note,
                        model_output=decision.model_output,
                        observation_t=observation.t,
                        valid=[bool(item["is_valid"]) for item in inspections],
                        finish_reason=decision.finish_reason,
                        report=decision.report,
                        usage=decision.usage,
                    )
                if decision.report is not None:
                    self._report = self._report_evidence(decision.report, observation.t)
                    self._request_stop("env_reported", "agent_report")
                    break
                await self._executor.execute_actions(decision.plan.actions)
                if decision.counts_as_step:
                    self._step_index += 1
                    self._actor_steps = self._step_index
                self._previous_result = {
                    "actions": list(decision.plan.actions),
                    "errors": [
                        {"action": action, "reason": item["reason"]}
                        for action, item in zip(decision.plan.actions, inspections)
                        if not item["is_valid"]
                    ] if decision.plan.actions else [{"reason": "no_action_returned"}],
                }

                interval = max(0.0, float(self.harness.step_interval_s))
                if decision.plan.actions:
                    await asyncio.sleep(interval)
                elif interval > 0:
                    await asyncio.sleep(min(0.05, interval))
                else:
                    await asyncio.sleep(0)
        finally:
            if self._page_alive():
                await self._executor.release_all_keys()
            await self.harness.close()


__all__ = ["AutomatedTrajectoryRecorder", "InferenceMode"]
