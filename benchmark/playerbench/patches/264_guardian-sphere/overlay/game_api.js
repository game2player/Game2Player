// Guardian Sphere (Godot 3.2.2) state adapter.
//
// The game's own scripts (scene/main.gd, patched) read the live GDScript objects every
// few frames and call window.gwSnapshot({...}) through JavaScript.eval. This file turns
// that raw snapshot into the canonical gameAPI state. Nothing here is pixel inference.
//
// Task start variants are a query on the page URL, honoured by the patched main.gd:
//   ?world=1..5  &difficulty=easy|medium|hard  &char=0..8
// With no query the game boots to its normal logo/language/title flow.
(function () {
  "use strict";

  const GAME_ID = "264_guardian-sphere";
  const pageStart = performance.now();
  const WORLD_NAMES = ["cactus canyon", "union city", "peace garden", "zenith building", "alien ship"];
  const CHAR_NAMES = ["sphere", "pyramid", "cube", "diamond", "capsule", "moon", "aaa", "random", "pixelboy"];
  // Screens where the game is waiting for, or accepting, player input.
  const NOT_READY = new Set(["loading", "logo", "stage_intro"]);
  const QUICK_NOT_READY = new Set(["title", "language", "cinematic", "character_select"]);

  const round = (v, n) => Math.round(v * n) / n;
  const num = (v, d) => (typeof v === "number" && isFinite(v) ? v : d);

  const query = new URLSearchParams(window.location.search);
  const requested = query.has("world") || query.has("difficulty")
    ? {
        world: Math.min(5, Math.max(1, parseInt(query.get("world") || "1", 10) || 1)),
        difficulty: query.get("difficulty") || "medium",
        character: Math.min(8, Math.max(0, parseInt(query.get("char") || "0", 10) || 0)),
      }
    : null;

  const PROVENANCE = {
    status: "internal",
    is_actionable: "internal",
    gameTimeMs: "internal",
    "terminal.isTerminal": "internal",
    "terminal.outcome": "internal",
  };
  [
    "screen", "world", "world_name", "difficulty", "character", "sphere_hp", "sphere_max", "credit",
    "enemies_destroyed", "deaths", "shots_fired", "shots_hit", "upgrades_bought", "stage_progress", "scroll_fraction",
    "boss_loaded", "boss_damage_fraction", "stage_cleared", "worlds_cleared", "campaign_progress",
    "run_time_s",
  ].forEach((k) => { PROVENANCE["game_state." + k] = "internal"; });
  ["enemies_destroyed", "stage_progress", "upgrades_bought", "campaign_progress", "worlds_cleared",
    "sphere_hp", "world"].forEach((k) => { PROVENANCE["metrics." + k] = "internal"; });

  function blank() {
    return {
      schemaVersion: "1.0",
      gameId: GAME_ID,
      timestampMs: Date.now(),
      gameTimeMs: 0,
      status: "loading",
      is_actionable: false,
      terminal: { isTerminal: false, outcome: null },
      game_state: {
        screen: "loading",
        world: 0,
        world_name: "",
        difficulty: "",
        character: 0,
        character_name: "",
        sphere_hp: 0,
        sphere_max: 0,
        credit: 0,
        enemies_destroyed: 0,
        deaths: 0,
        shots_fired: 0,
        shots_hit: 0,
        upgrades_bought: 0,
        stage_progress: 0,
        scroll_fraction: 0,
        boss_loaded: false,
        boss_damage_fraction: 0,
        stage_cleared: false,
        worlds_cleared: 0,
        campaign_progress: 0,
        run_time_s: 0,
        paused: false,
        requested_start: requested,
      },
      metrics: {
        enemies_destroyed: 0,
        stage_progress: 0,
        upgrades_bought: 0,
        campaign_progress: 0,
        worlds_cleared: 0,
        sphere_hp: 0,
        world: 0,
      },
      provenance: PROVENANCE,
    };
  }

  let state = blank();

  function fromSnapshot(s) {
    const next = blank();
    const p = (s.players && s.players[0]) || null;
    const world = num(s.world, 0);
    const cleared = Array.isArray(s.worlds_cleared_list) ? s.worlds_cleared_list.map(Number) : [];
    const stageCleared = cleared.includes(world);
    const scroll = num(s.scroll_fraction, 0);
    const bossDmg = num(s.boss_damage_fraction, 0);
    // Stage position (70%) and boss damage (30%); 1.0 only once the game's own stage-clear
    // event (game._onEndLevel) has fired for this world, so killing the boss alone is < 1.
    const raw = 0.7 * scroll + 0.3 * bossDmg;
    const stageProgress = stageCleared ? 1 : Math.min(0.99, raw);
    const campaign = cleared.length + (stageCleared ? 0 : stageProgress);
    const screen = String(s.screen || "loading");

    const finalCleared = cleared.includes(5);
    const gameOver = screen === "game_over" && num(s.credit, 0) === 0 && !!s.paused;

    next.game_state = Object.assign(next.game_state, {
      screen,
      world,
      world_name: WORLD_NAMES[world - 1] || "",
      difficulty: String(s.difficulty || ""),
      character: p ? num(p.character, 0) : 0,
      character_name: p ? CHAR_NAMES[num(p.character, 0)] || "" : "",
      sphere_hp: p ? num(p.life, 0) : 0,
      sphere_max: p ? num(p.sphere_max, 0) : 0,
      credit: num(s.credit, 0),
      enemies_destroyed: num(s.enemies_destroyed, 0),
      deaths: p ? num(p.deaths, 0) : 0,
      shots_fired: p ? num(p.shots, 0) : 0,
      shots_hit: p ? num(p.hits, 0) : 0,
      upgrades_bought: num(s.upgrades_bought, 0),
      stage_progress: round(stageProgress, 1000),
      scroll_fraction: round(scroll, 1000),
      boss_loaded: !!s.boss_present,
      boss_damage_fraction: round(bossDmg, 1000),
      stage_cleared: stageCleared,
      worlds_cleared: cleared.length,
      campaign_progress: round(campaign, 1000),
      run_time_s: round(num(s.run_time_s, 0), 100),
      paused: !!s.paused,
      requested_start: requested,
    });
    const g = next.game_state;
    next.metrics = {
      enemies_destroyed: g.enemies_destroyed,
      stage_progress: g.stage_progress,
      upgrades_bought: g.upgrades_bought,
      campaign_progress: g.campaign_progress,
      worlds_cleared: g.worlds_cleared,
      sphere_hp: g.sphere_hp,
      world: g.world,
    };

    let ready = !NOT_READY.has(screen);
    if (s.quick_pending && QUICK_NOT_READY.has(screen)) ready = false;
    if (s.quick_pending && screen === "playing") ready = false;
    next.is_actionable = ready && !gameOver;
    next.status = gameOver ? "game_over" : finalCleared ? "won" : ready ? "ready" : "loading";
    next.terminal = gameOver
      ? { isTerminal: true, outcome: "fail" }
      : finalCleared
        ? { isTerminal: true, outcome: "success" }
        : { isTerminal: false, outcome: null };
    next.gameTimeMs = Math.round(num(s.run_time_s, 0) * 1000);
    next.timestampMs = Date.now();
    return next;
  }

  // Policy observation: on-screen geometry for the probe, kept out of gameAPI. The patched
  // main.gd (gwObservation) sends it in the same snapshot, in the 640x360 screen frame
  // (origin top-left, y down); vx/vy are screen px per second, including stage scroll. A field is null
  // until the game has produced it (title/menu screens, before the first live frame).
  const OBS_FIELDS = ["player", "enemies", "enemy_projectiles", "pickups", "lab_door"];
  let observation = null;
  window.__policyObservation = {
    getState() {
      const out = {};
      OBS_FIELDS.forEach((k) => {
        out[k] = observation && observation[k] !== undefined ? observation[k] : null;
      });
      return JSON.parse(JSON.stringify(out));
    },
  };

  // Called by the patched scene/main.gd (JavaScript.eval) every ~3 frames.
  let focused = false;
  window.gwSnapshot = function (snap) {
    if (!snap || typeof snap !== "object") return;
    observation = snap.obs && typeof snap.obs === "object" ? snap.obs : null;
    state = fromSnapshot(snap);
    // Godot 3 reads the keyboard from the canvas; make sure it has focus once input is live.
    if (!focused && state.is_actionable) {
      const canvas = document.getElementById("canvas");
      if (canvas) { canvas.focus(); focused = true; }
    }
  };

  window.gameAPI = {
    version: "2.0",
    capabilities: {
      supports_seed: false,
      supports_level_select: true,
      supports_difficulty: true,
      supports_inplace_reset: false,
      supports_reload_reset: true,
      supports_pause_detection: true,
      supports_menu_detection: true,
      provides_actionable_flag: true,
    },
    async init() {
      return { ok: true };
    },
    getState() {
      const out = JSON.parse(JSON.stringify(state));
      out.timestampMs = Date.now();
      if (!out.gameTimeMs) out.gameTimeMs = Math.round(performance.now() - pageStart);
      return out;
    },
    async reset() {
      window.location.reload();
      return { ok: true, method: "reload" };
    },
  };
})();
