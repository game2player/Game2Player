(function () {
  "use strict";

  var GAME_ID = "64_kingdom-rush";
  if (window.gameAPI && window.gameAPI.gameIdMarker === GAME_ID) return;

  // Level state enum published by the SWF's Level class (Level.as):
  // 0 NORMAL, 1 PAUSED, 2 WIN, 3 OVER, 4 STARTING, 5 PRE_WIN.
  var LEVEL_PAUSED = 1;
  var LEVEL_STARTING = 4;

  var session = {
    seed: 42,
    episodeStartMs: Date.now(),
    gameTimeMs: 0,
    lastClockMs: null,
    episodeCount: 0,
    lastResetMethod: null,
    lastPullMs: 0
  };

  var capabilities = {
    supports_seed: false,
    supports_level_select: false,
    supports_difficulty: false,
    supports_inplace_reset: false,
    supports_reload_reset: true,
    supports_pause_detection: true,
    supports_menu_detection: false,
    provides_actionable_flag: true
  };

  var BLANK_FLASH_STATE = {
    cash: 0,
    lives: 0,
    kills: 0,
    towers: 0,
    wave: 0,
    waves: 0,
    won: 0,
    status: 0,
    failed: 0,
    started: 0
  };

  function toNumber(value) {
    if (typeof value === "boolean") return value ? 1 : 0;
    if (typeof value === "number" && isFinite(value)) return value;
    if (typeof value === "string" && value !== "") {
      var parsed = Number(value);
      if (isFinite(parsed)) return parsed;
    }
    return 0;
  }

  function readField(obj, key) {
    if (!obj) return 0;
    if (typeof obj.get === "function") {
      try {
        var mapped = obj.get(key);
        if (mapped !== undefined && mapped !== null) return toNumber(mapped);
      } catch (err) {}
    }
    if (Object.prototype.hasOwnProperty.call(obj, key) || (obj[key] !== undefined && obj[key] !== null)) {
      return toNumber(obj[key]);
    }
    return 0;
  }

  function normalizeFlashState(raw) {
    if (!raw || typeof raw !== "object") return null;
    return {
      cash: readField(raw, "cash"),
      lives: readField(raw, "lives"),
      kills: readField(raw, "kills"),
      towers: readField(raw, "towers"),
      wave: readField(raw, "wave"),
      waves: readField(raw, "waves"),
      won: readField(raw, "won"),
      status: readField(raw, "status"),
      failed: readField(raw, "failed"),
      started: readField(raw, "started")
    };
  }

  window.__gwSetFlashState = function (cash, lives, kills, towers, wave, status, failed, started, waves, won) {
    if (arguments.length === 1 && cash && typeof cash === "object") {
      var packed = normalizeFlashState(cash);
      if (packed) window.__gwFlashState = packed;
      return;
    }
    window.__gwFlashState = {
      cash: toNumber(cash),
      lives: toNumber(lives),
      kills: toNumber(kills),
      towers: toNumber(towers),
      wave: toNumber(wave),
      waves: toNumber(waves),
      won: toNumber(won),
      status: toNumber(status),
      failed: toNumber(failed),
      started: toNumber(started)
    };
  };

  function ruffleApi() {
    var player = document.getElementById("game-player");
    if (!player) return null;
    try {
      if (typeof player.ruffle === "function") return player.ruffle();
    } catch (err) {}
    return player;
  }

  function pullFromRuffle() {
    var now = Date.now();
    if (now - session.lastPullMs < 80) return;
    session.lastPullMs = now;
    var api = ruffleApi();
    if (!api) return;
    var result;
    try {
      if (typeof api.callExternalInterface === "function") {
        result = api.callExternalInterface("gwGetState");
      } else if (typeof api.call === "function") {
        result = api.call("gwGetState");
      } else if (typeof api.gwGetState === "function") {
        result = api.gwGetState();
      }
    } catch (err2) {
      result = null;
    }
    var packed = normalizeFlashState(result);
    if (packed) window.__gwFlashState = packed;
  }

  function currentFlash() {
    pullFromRuffle();
    return window.__gwFlashState || BLANK_FLASH_STATE;
  }

  // Mirrors Level.getStars(): 3 stars at >= 18 lives, 2 at 6..17, 1 at <= 5.
  // The SWF does not export the award, so it is derived from the game's own
  // lives counter and is only meaningful once the level has been won.
  function starsFrom(won, lives) {
    if (won <= 0) return 0;
    if (lives >= 18) return 3;
    if (lives >= 6) return 2;
    if (lives >= 1) return 1;
    return 0;
  }

  function statusName(status) {
    switch (status) {
      case 0: return "normal";
      case 1: return "paused";
      case 2: return "win";
      case 3: return "over";
      case 4: return "starting";
      case 5: return "pre_win";
      default: return "unknown";
    }
  }

  // Advance the play clock only while the level is actually being simulated:
  // not while loading, starting, paused, defeated, or on the victory screen.
  function tickClock(now, inPlay) {
    if (session.lastClockMs != null && inPlay) {
      session.gameTimeMs += Math.max(0, now - session.lastClockMs);
    }
    session.lastClockMs = now;
    return session.gameTimeMs;
  }

  function beginEpisode() {
    session.episodeStartMs = Date.now();
    session.gameTimeMs = 0;
    session.lastClockMs = null;
    session.episodeCount += 1;
    window.__gwFlashState = null;
  }

  window.gameAPI = {
    version: "2.1",
    gameIdMarker: GAME_ID,
    capabilities: capabilities,

    init: async function (options) {
      if (options && typeof options.seed === "number" && isFinite(options.seed)) {
        session.seed = options.seed;
      }
      beginEpisode();
      return {
        ok: true,
        accepted: { seed: null, level: null, difficulty: null },
        applied: { seed: null, level: null, difficulty: null },
        notes: ["local_snapshot", "auto_starts_southport_level1_campaign_normal"]
      };
    },

    getState: function () {
      var now = Date.now();
      var flash = currentFlash();
      var cash = flash.cash;
      var lives = flash.lives;
      var kills = flash.kills;
      var towers = flash.towers;
      var wave = flash.wave;
      var waves = flash.waves;
      var won = flash.won > 0;
      var stars = starsFrom(won ? 1 : 0, lives);
      var failed = flash.failed > 0;
      var hidden = document.hidden;
      var starting = flash.status === LEVEL_STARTING;
      var paused = flash.status === LEVEL_PAUSED || hidden;
      var started = flash.started > 0;
      var inPlay = started && !failed && !won && !paused && !starting;
      var gameTimeMs = tickClock(now, inPlay);
      var survivedS = gameTimeMs / 1000;

      var status = "playing";
      if (!started) status = "loading";
      else if (failed) status = "terminal";
      else if (won) status = "won";
      else if (starting) status = "starting";
      else if (paused) status = "paused";

      var terminal = { isTerminal: false, outcome: null, reason: null };
      if (failed) {
        terminal = { isTerminal: true, outcome: "fail", reason: "lives_depleted" };
      } else if (won) {
        terminal = { isTerminal: true, outcome: "success", reason: "level_won" };
      }

      return {
        schemaVersion: "2.0",
        gameId: GAME_ID,
        seed: session.seed,
        timestampMs: now,
        gameTimeMs: gameTimeMs,
        status: status,
        is_actionable: started && !paused && !failed && !won && !starting,
        terminal: terminal,
        game_state: {
          score: waves,
          level: 1,
          player: {
            cash: cash,
            gold: cash,
            lives: lives,
            towers: towers
          },
          environment: {
            kills: kills,
            wave: wave,
            waves: waves,
            won: won ? 1 : 0,
            stars: stars,
            survived_s: survivedS,
            paused: paused
          },
          completion_progress: Math.max(0, Math.min(1, waves / 7))
        },
        metrics: {
          primary_score: waves,
          cash: cash,
          gold: cash,
          lives: lives,
          kills: kills,
          towers: towers,
          wave: wave,
          waves: waves,
          won: won ? 1 : 0,
          stars: stars,
          survived_s: survivedS
        },
        debug: {
          started: started,
          in_play: inPlay,
          starting: starting,
          flash_status: flash.status,
          flash_status_name: statusName(flash.status),
          max_waves: 7,
          episode_count: session.episodeCount,
          last_reset_method: session.lastResetMethod,
          stars_source: "derived_from_lives_mirrors_Level_getStars",
          delivery: "local_snapshot"
        }
      };
    },

    reset: async function () {
      beginEpisode();
      session.lastResetMethod = "reload";
      window.location.reload();
      return {
        ok: true,
        method: "reload",
        accepted: { seed: null, level: null, difficulty: null },
        applied: { seed: null, level: null, difficulty: null },
        notes: []
      };
    }
  };
})();
