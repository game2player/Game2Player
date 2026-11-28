/*
 * game_api.js -- Pigeon Ascent (148_pigeon-ascent) state adapter.
 *
 * The game is a Godot 4.2.1 web export; its state lives inside the wasm engine.
 * A small hook appended to the game's own autoload (res://scenes/global.gd)
 * calls window.__pigeonHook(state) ten times a second and whenever the scene,
 * level, battle or evolution history changes. Every hook value is a direct game
 * value (internal): global.level, global.player / global.enemy (PigeonClass),
 * the shop item flags global.hasWings/hasIce/hasSword, the battle scene's own
 * fighting/ended flags, and player.arrEvolutions, which the evolution panel
 * appends to when the pigeon actually evolves.
 *
 * This file keeps the task progress values. They are latched here on every hook
 * call (not on getState polls), because the end screen replaces global.player and
 * sets global.level back to 1 when it loads, and a lost run returns to the title
 * with a new pigeon:
 *   run_progress      fights won (global.level - 1) plus the share of the current
 *                     opponent's HP removed in an unfinished fight (capped at 0.99)
 *   knight_progress   min(run, 2) + Knight evolution
 *   fridgeon_progress min(run, 5) + Knight evolution + Ice owned + Fridgeon evolution
 *   wyrm_progress     min(run, 5) + Stronga evolution + Wing owned + Wyrm evolution
 * Each is the best value reached by any single run in this page load.
 *
 * Start/reset: `?start=run` makes the hook delete the save DB (Hall of Tries,
 * achievements) and call the title FIGHT button's own startGame(), so the page
 * opens on a fresh level-1 hub. Reset reloads the same URL. Opponents are rolled
 * with Godot's own RNG, which the page seed does not reach.
 *
 * Pointer enter: Godot's web runtime learns that the mouse is over the game only
 * from a `mouseover` event on the canvas, and until then Controls never emit
 * mouse_entered (shop items show no description or cost on hover). A pointer
 * that is already over the page when it loads, as with automated input, never
 * produces that event, so this file sends one `mouseover` to the canvas once the
 * engine is running, and again on the next mouse move after the pointer leaves.
 */
(() => {
  "use strict";

  const SCHEMA_VERSION = "1.0";
  const GAME_ID = "148_pigeon-ascent";
  const TOTAL_FIGHTS = 10;
  const HUD_ID = "gw-pigeon-hud";

  let hook = null;
  let hookCount = 0;
  let resetPending = false;
  let finished = false;
  let gameOver = false;
  let sawRun = false;
  const best = { run: 0, knight: 0, fridgeon: 0, wyrm: 0 };
  let hud = null;
  let hudText = "";

  const num = (value, fallback) =>
    typeof value === "number" && isFinite(value) ? value : fallback;
  const int = (value, fallback) => Math.trunc(num(value, fallback));
  const list = (value) => (Array.isArray(value) ? value.map(String) : []);
  const round3 = (value) => Math.round(value * 1000) / 1000;

  function fightFraction(raw) {
    // A won fight is already counted in global.level; a lost one keeps its damage.
    if (!raw.in_battle || (raw.battle_ended && num((raw.player || {}).hp, 0) > 0)) return 0;
    const enemy = raw.enemy || {};
    const maxHp = num(enemy.max_hp, 0);
    if (maxHp <= 0) return 0;
    const removed = 1 - num(enemy.hp, maxHp) / maxHp;
    return Math.min(0.99, Math.max(0, removed));
  }

  function onHook(raw) {
    if (!raw || typeof raw !== "object") return;
    hook = raw;
    hookCount += 1;
    if (raw.scene === "root") sawRun = true;
    if (!sawRun) return;

    const level = int(raw.level, 1);
    const evolutions = list(raw.evolutions);
    const inRun = raw.scene === "root";
    const wins = Math.max(0, Math.min(TOTAL_FIGHTS, level - 1));
    const run = inRun ? wins + fightFraction(raw) : 0;
    const latchedWins = Math.min(TOTAL_FIGHTS, int(raw.max_level, 1) - 1);
    const has = (name) => (evolutions.includes(name) ? 1 : 0);

    best.run = Math.max(best.run, run, latchedWins);
    if (inRun) {
      best.knight = Math.max(best.knight, Math.min(run, 2) + has("Knight"));
      best.fridgeon = Math.max(
        best.fridgeon,
        Math.min(run, 5) + has("Knight") + (raw.has_ice ? 1 : 0) + has("Fridgeon"),
      );
      best.wyrm = Math.max(
        best.wyrm,
        Math.min(run, 5) + has("Stronga") + (raw.has_wings ? 1 : 0) + has("Wyrm"),
      );
    }
    if (level >= TOTAL_FIGHTS + 1 || int(raw.max_level, 1) >= TOTAL_FIGHTS + 1 || raw.scene === "end") {
      finished = true;
    }
    if (!finished && (raw.battle_game_over || raw.scene === "intro")) gameOver = true;
    syncHud(raw);
  }

  let pointerEntered = false;
  function announcePointer() {
    const canvas = document.getElementById("canvas");
    if (!canvas || pointerEntered) return;
    pointerEntered = true;
    canvas.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
  }

  function watchPointer() {
    const canvas = document.getElementById("canvas");
    if (!canvas || canvas.__gwPointerWatch) return;
    canvas.__gwPointerWatch = true;
    canvas.addEventListener("mouseleave", () => {
      pointerEntered = false;
    });
    canvas.addEventListener("mousemove", announcePointer);
  }

  window.__pigeonHook = (raw) => {
    try {
      watchPointer();
      announcePointer();
      onHook(raw);
    } catch (_) {}
  };

  function statusOf(raw) {
    if (resetPending) return "resetting";
    if (!raw) return "loading";
    if (finished) return "finished";
    if (gameOver) return "game_over";
    if (raw.scene !== "root") return raw.start_run && !sawRun ? "loading" : raw.scene || "loading";
    if (raw.evolving) return "evolving";
    if (raw.in_battle) return raw.battle_ended ? "battle_over" : "battle";
    if (raw.opponents_open) return "choosing_opponent";
    return "hub";
  }

  // Page-level readout shown only on the hub screen, where the game itself never
  // displays how many fights have been won.
  function syncHud(raw) {
    try {
      if (!document.body) return;
      if (!hud) {
        hud = document.createElement("div");
        hud.id = HUD_ID;
        hud.style.cssText =
          "position:fixed;left:50%;top:100px;transform:translateX(-50%);padding:2px 10px;" +
          "font:bold 14px/1.3 monospace;color:#f4f1e8;background:rgba(20,18,30,0.75);" +
          "border-radius:4px;pointer-events:none;z-index:2147483647;white-space:pre;display:none;";
        document.body.appendChild(hud);
      }
      let text = "";
      if (statusOf(raw) === "hub") {
        const level = int(raw.level, 1);
        text = `FIGHTS WON ${Math.min(TOTAL_FIGHTS, level - 1)}/${TOTAL_FIGHTS}   LIVES ${int(raw.lives, 0)}`;
      }
      if (text !== hudText) {
        hud.textContent = text;
        hud.style.display = text ? "block" : "none";
        hudText = text;
      }
    } catch (_) {}
  }

  function pigeonView(p) {
    p = p || {};
    return {
      name: typeof p.name === "string" ? p.name : "",
      class: typeof p.class === "string" ? p.class : "",
      level: int(p.level, 0),
      hp: int(p.hp, 0),
      max_hp: int(p.max_hp, 0),
      food: int(p.food, 0),
      max_food: int(p.max_food, 0),
      attack: int(p.str, 0),
      defense: int(p.def, 0),
      speed: int(p.spd, 0),
      gold: int(p.gold, 0),
    };
  }

  function buildState() {
    const raw = hook;
    const ready =
      !!raw && !resetPending && (raw.start_run ? sawRun : ["intro", "root", "end"].includes(raw.scene));
    const player = pigeonView(raw && raw.player);
    const level = int(raw && raw.level, 1);
    const terminal = finished
      ? { isTerminal: true, outcome: "success", reason: "defeated the final boss Pigod" }
      : gameOver
        ? { isTerminal: true, outcome: "fail", reason: "the run ended: lost a fight with no lives left" }
        : { isTerminal: false, outcome: null, reason: null };

    return {
      schemaVersion: SCHEMA_VERSION,
      gameId: GAME_ID,
      timestampMs: Date.now(),
      gameTimeMs: int(raw && raw.ticks_ms, 0),
      status: statusOf(raw),
      is_actionable: ready,
      terminal,
      game_state: {
        run_progress: round3(best.run),
        scene: (raw && raw.scene) || "",
        level,
        fights_won: Math.max(0, Math.min(TOTAL_FIGHTS, level - 1)),
        best_fights_won: Math.floor(best.run),
        lives: int(raw && raw.lives, 0),
        stat_points: int(raw && raw.stat_points, 0),
        player,
        pigeon_class: player.class,
        evolutions: list(raw && raw.evolutions),
        bought_items: list(raw && raw.bought_items),
        has_wings: !!(raw && raw.has_wings),
        has_ice: !!(raw && raw.has_ice),
        has_sword: !!(raw && raw.has_sword),
        choosing_opponent: !!(raw && raw.opponents_open),
        opponents: raw && Array.isArray(raw.opponents) ? raw.opponents.map(pigeonView) : [],
        in_battle: !!(raw && raw.in_battle),
        battle_fighting: !!(raw && raw.battle_fighting),
        battle_ended: !!(raw && raw.battle_ended),
        battle_turn: int(raw && raw.battle_turn, 0),
        enemy: pigeonView(raw && raw.enemy),
        evolving: !!(raw && raw.evolving),
        runs_started: int(raw && raw.runs_started, 0),
      },
      metrics: {
        run_progress: round3(best.run),
        knight_progress: round3(best.knight),
        fridgeon_progress: round3(best.fridgeon),
        wyrm_progress: round3(best.wyrm),
        gold: player.gold,
        hook_updates: hookCount,
      },
    };
  }

  window.gameAPI = {
    version: "2.0",
    capabilities: {
      supports_seed: false,
      supports_level_select: false,
      supports_difficulty: false,
      supports_inplace_reset: false,
      supports_reload_reset: true,
      supports_pause_detection: false,
      supports_menu_detection: true,
      provides_actionable_flag: true,
    },
    async init() {
      return {
        ok: true,
        notes: ["state is projected from res://scenes/global.gd via window.__pigeonHook; ?start=run opens a fresh level-1 hub"],
      };
    },
    getState() {
      return buildState();
    },
    reset() {
      resetPending = true;
      try {
        if (typeof window.__resetRandomSeed === "function") window.__resetRandomSeed();
      } catch (_) {}
      window.setTimeout(() => {
        try {
          window.location.reload();
        } catch (_) {}
      }, 60);
      return { ok: true, method: "reload_reset" };
    },
  };
})();
