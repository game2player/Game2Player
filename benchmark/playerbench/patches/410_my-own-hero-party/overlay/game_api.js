/*
 * My Own Hero Party browser adapter.
 * MainScene publishes source-backed Godot state through JavaScriptBridge.
 */
(function () {
  "use strict";

  const gameId = "410_my-own-hero-party";
  const taskId = new URL(window.location.href).searchParams.get("task") || "";
  let resetPending = false;
  let gameState = null;

  const initialState = {
    schemaVersion: "1.0.0",
    gameId,
    timestampMs: Date.now(),
    gameTimeMs: 0,
    status: "loading",
    is_actionable: false,
    terminal: { isTerminal: false, outcome: null },
    game_state: {
      task_id: taskId,
      task_start_ready: false,
      task_start_matches: false,
      dungeon_id: "",
      room: 0,
      best_room: 0,
      run_goblin_kills: 0,
      gold: 0,
      run_gold: 0,
      best_run_gold: 0,
      upgrades: { start_stamina: 0 }
    },
    metrics: {
      run_goblin_kills: 0,
      best_room: 0,
      best_run_gold: 0,
      start_stamina_level: 0
    },
    provenance: {}
  };

  window.__heroPartySetState = function (state) {
    if (state && typeof state === "object") gameState = state;
  };

  let policyObservation = {
    scene_phase: null,
    visible_gold_pickups: null,
    visible_action_targets: null
  };

  window.__heroPartySetPolicyObservation = function (state) {
    if (!state || typeof state !== "object") return;
    policyObservation = {
      scene_phase: typeof state.scene_phase === "string" ? state.scene_phase : null,
      visible_gold_pickups: Array.isArray(state.visible_gold_pickups)
        ? state.visible_gold_pickups
        : null,
      visible_action_targets: Array.isArray(state.visible_action_targets)
        ? state.visible_action_targets
        : null
    };
  };

  window.__policyObservation = {
    getState() {
      return JSON.parse(JSON.stringify(policyObservation));
    }
  };

  function getState() {
    const state = JSON.parse(JSON.stringify(gameState || initialState));
    state.schemaVersion = "1.0.0";
    state.gameId = gameId;
    state.timestampMs = Date.now();
    state.gameTimeMs = Number(state.gameTimeMs) || 0;
    if (resetPending) state.is_actionable = false;
    return state;
  }

  window.gameAPI = {
    getState,
    async init() {
      return true;
    },
    reset() {
      resetPending = true;
      if (typeof window.__resetRandomSeed === "function") {
        window.__resetRandomSeed();
      }
      window.setTimeout(function () {
        window.location.reload();
      }, 0);
      return true;
    }
  };
})();
