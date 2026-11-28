(() => {
  "use strict";

  const GAME_ID = "226_dominoir";
  const params = new URLSearchParams(window.location.search);
  const taskId = params.get("task") || null;
  let snapshot = null;

  // Called ~10x/second by the patched res://_Core/RunManager.gd (autoload) through
  // JavaScriptBridge with a JSON string built from the game's own variables:
  // Game.round_score/round_goal/turns_remaining/discards_remaining/is_game_active,
  // Game.hand.held_dominoes, RunManager.current_rank/current_money, Shop slots and the
  // purchase counter bumped in Shop._on_buy_requested. All fields are provenance `internal`.
  // With ?task=<id> the same patch starts a fresh seeded run in the first Game round
  // (optionally ?seed=N); without it the game opens at its normal title screen.
  window.__gwPublish = (json) => {
    let data;
    try { data = JSON.parse(json); } catch (e) { return; }
    if (!data || typeof data !== "object") return;
    snapshot = {
      schemaVersion: "1.0",
      gameId: GAME_ID,
      timestampMs: Date.now(),
      gameTimeMs: data.gameTimeMs,
      status: data.status,
      is_actionable: data.is_actionable === true,
      terminal: data.terminal,
      game_state: Object.assign({ task_id: taskId }, data.game_state),
      metrics: data.metrics
    };
  };

  window.gameAPI = {
    version: "1.0",
    capabilities: {
      supports_seed: true,
      supports_inplace_reset: false,
      supports_reload_reset: true,
      provides_actionable_flag: true
    },
    async init() {
      return { ok: true, method: "godot_source_hook" };
    },
    getState() {
      if (!snapshot) {
        return {
          schemaVersion: "1.0", gameId: GAME_ID, timestampMs: Date.now(),
          gameTimeMs: 0, status: "loading", is_actionable: false,
          terminal: { isTerminal: false, outcome: null },
          game_state: {
            task_id: taskId, scene: "loading", rank: 25, money: 0, round_goal: 100,
            round_score: 0, turns_remaining: 0, discards_remaining: 0,
            rounds_cleared: 0, ladder_progress: 0, shop_progress: 0, purchases: 0
          },
          metrics: { ladder_progress: 0, shop_progress: 0, rounds_cleared: 0, round_score: 0, money: 0 }
        };
      }
      return JSON.parse(JSON.stringify(snapshot));
    },
    async reset() {
      // The patched RunManager deletes the save and starts a new seeded run on every load of a ?task= URL.
      window.location.reload();
      return { ok: true, method: "reload_fresh_run" };
    }
  };
})();
