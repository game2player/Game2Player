(function () {
  "use strict";

  var GAME_ID = "125_shift-2";
  if (window.gameAPI && window.gameAPI.gameIdMarker === GAME_ID) return;

  // Instrumentation for the patched AS2 SWF. The SWF owns the state: its
  // appended scripts register `gwGetState` (a `|`-joined string) and
  // `gwStartLevel`, and enter the chamber named by `level=` in the movie's own
  // URL. This file only bridges that state into the canonical gameAPI shape.
  //
  // gwGetState fields, in order:
  //   frame|level|playTime|dead|eol|x|y|onGround|inversion|live|paused|levelId
  // provenance: internal (direct values read from the movie).
  //
  // `level` is _root.LevelNo, the campaign index 0..18. It is the game's own
  // counter, but chamber 16 contains a joke hole that force-sets LevelNo to 17
  // and then 18 on the way to the secret snake room (levelId 29), so campaign
  // progress is latched from the level actually loaded (`levelId` in
  // LEVEL_ARRAY) instead; that follows the same meaning, +1 per chamber whose
  // exit door is used, without crediting two chambers nobody cleared.
  var FIELD_COUNT = 12;
  var GAME_FRAME = 20;      // root frame 20 is GAME; 21 is the BLANK router
  var ENDING_FRAME = 22;    // 22 ENDMOVIE, 23 END_BAD, 24 END_GOOD
  var FPS = 25;             // movie frame rate; playTime counts these frames
  var LEVEL_ARRAY = [8, 9, 16, 18, 1, 14, 17, 19, 3, 10, 4, 5, 6, 7, 20, 23, 24, 25, 29];
  var CAMPAIGN_LENGTH = 18;
  var FINAL_LEVEL_ID = 25;
  var SNAKE_LEVEL_ID = 29;

  var requestedLevel = (function () {
    var match = /[?&]level=(\d+)/.exec(window.location.search || "");
    return match ? Number(match[1]) : null;
  })();

  // Progress repair: source-backed room-local coordinates from loadLevel() /
  // arrangeTiles(). MAN is measured at its feet (settled y=-21 in room 1,
  // y=219 in room 18). Display rotation never changes this coordinate frame.
  // Provenance: internal; derived geometric approach, NOT keys collected or
  // switches activated. Only the existing chamber-clear verifier awards 100.
  var APPROACH_ROOMS = {
    0: { levelId: 8, origin: [-220, -21], exit: [220, -21] },
    8: { levelId: 3, origin: [-220, -220], exit: [140, 220] },
    14: { levelId: 20, origin: [-220, -220], exit: [220, 220] },
    17: { levelId: 25, origin: [-220, 219], exit: [220, 220] }
  };
  var FINAL_WAYPOINTS = [
    [220, 180], // grey switch region; weight 15%
    [100, -220], [20, -220], [-60, -220], [-140, -220], [-220, -220]
    // five key regions; weight 14% each, independent of visiting order
  ];
  var approach = { best: 0, life: null, wasDead: false };

  function newApproachLife() {
    return {
      best: [0, 0, 0, 0, 0, 0],
      visited: [false, false, false, false, false, false],
      returnBest: 0
    };
  }

  function manhattan(a, b) {
    return Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);
  }

  function proximity(position, origin, goal) {
    var initialDistance = manhattan(origin, goal);
    if (initialDistance <= 0) return 0;
    return Math.max(0, Math.min(1,
      1 - manhattan(position, goal) / initialDistance));
  }

  function trackApproach() {
    var room = APPROACH_ROOMS[requestedLevel];
    if (!room || !session.startConfirmed) return;
    if (session.campaignCleared > requestedLevel) {
      approach.best = 100;
      return;
    }
    if (!inGameNow() || swf.levelId !== room.levelId) return;
    if (swf.dead === 1) {
      // Never combine visits from separate lives into a fictitious key route.
      // Episode best is retained, while this life's waypoint history is lost.
      if (!approach.wasDead) approach.life = newApproachLife();
      approach.wasDead = true;
      return;
    }
    approach.wasDead = false;
    if (swf.paused === 1 || swf.eol === 1 ||
        Math.abs(swf.x) > 240 || Math.abs(swf.y) > 260) return;
    var position = [swf.x, swf.y];
    var fraction;
    if (requestedLevel !== 17) {
      fraction = proximity(position, room.origin, room.exit);
    } else {
      var life = approach.life || (approach.life = newApproachLife());
      for (var i = 0; i < FINAL_WAYPOINTS.length; i++) {
        var waypoint = FINAL_WAYPOINTS[i];
        // Small regions account for a 40px tile and the avatar's foot anchor.
        // These are geometric visits, never assertions of item acquisition.
        if (Math.abs(position[0] - waypoint[0]) <= 25 &&
            Math.abs(position[1] - waypoint[1]) <= 30) life.visited[i] = true;
        var value = life.visited[i] ? 1 : proximity(position, room.origin, waypoint);
        life.best[i] = Math.max(life.best[i], value);
      }
      var allKeyRegionsVisited = life.visited.slice(1).every(function (v) { return v; });
      if (allKeyRegionsVisited) {
        life.returnBest = Math.max(life.returnBest,
          proximity(position, room.origin, room.exit));
      }
      fraction = 0.15 * life.best[0] + 0.15 * life.returnBest;
      for (var j = 1; j < life.best.length; j++) fraction += 0.14 * life.best[j];
    }
    approach.best = Math.max(approach.best, Math.min(95, 95 * fraction));
  }

  var session = {
    episodeStartMs: Date.now(),
    episodeCount: 0,
    lastResetMethod: null,
    deaths: 0,
    lastDeadSeen: 0,
    lastPullMs: 0,
    startConfirmed: false,
    appliedVia: null,
    campaignCleared: requestedLevel === null ? 0 : requestedLevel,
    lastLevelId: -1
  };

  var swf = {
    raw: "",
    frame: -1,
    level: -1,
    playTime: -1,
    dead: 0,
    eol: 0,
    x: -1,
    y: -1,
    onGround: 0,
    inverted: 0,
    live: 0,
    paused: 0,
    levelId: -1
  };

  var capabilities = {
    supports_seed: false,
    supports_level_select: true,
    supports_difficulty: false,
    supports_inplace_reset: true,
    supports_reload_reset: true,
    supports_pause_detection: true,
    supports_menu_detection: true,
    provides_actionable_flag: true
  };

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function ruffleApi() {
    var player = document.getElementById("game-player");
    if (!player) return null;
    try {
      if (typeof player.ruffle === "function") return player.ruffle();
    } catch (err) {}
    return player;
  }

  function invoke(name, arg) {
    var api = ruffleApi();
    if (!api) return undefined;
    var args = arg === undefined ? [] : [String(arg)];
    try {
      if (typeof api.callExternalInterface === "function") {
        return api.callExternalInterface(name, args);
      }
    } catch (err) {}
    try {
      if (typeof api.call === "function") return api.call(name, args);
    } catch (err2) {}
    return undefined;
  }

  function parseRaw(raw) {
    if (typeof raw !== "string" || raw.indexOf("|") < 0) return null;
    var parts = raw.split("|");
    if (parts.length < FIELD_COUNT) return null;
    var values = [];
    for (var i = 0; i < FIELD_COUNT; i++) {
      var n = Number(parts[i]);
      if (!isFinite(n)) return null;
      values.push(n);
    }
    return {
      raw: raw,
      frame: values[0],
      level: values[1],
      playTime: values[2],
      dead: values[3],
      eol: values[4],
      x: values[5],
      y: values[6],
      onGround: values[7],
      inverted: values[8],
      live: values[9],
      paused: values[10],
      levelId: values[11]
    };
  }

  function refresh(force) {
    var now = Date.now();
    if (!force && now - session.lastPullMs < 60) return swf;
    session.lastPullMs = now;
    var parsed = parseRaw(invoke("gwGetState"));
    if (parsed) swf = parsed;
    return swf;
  }

  function inGameNow() {
    return swf.frame === GAME_FRAME && swf.live === 1;
  }

  function trackPlay() {
    if (inGameNow()) {
      if (swf.dead === 1 && session.lastDeadSeen === 0) session.deaths += 1;
      session.lastDeadSeen = swf.dead;
      if (swf.levelId >= 0) {
        session.lastLevelId = swf.levelId;
        var index = LEVEL_ARRAY.indexOf(swf.levelId);
        if (index > session.campaignCleared && swf.levelId !== SNAKE_LEVEL_ID) {
          session.campaignCleared = index;
        }
      }
    }
    if (swf.frame >= ENDING_FRAME && session.lastLevelId !== SNAKE_LEVEL_ID &&
        session.lastLevelId >= 0 && session.campaignCleared === CAMPAIGN_LENGTH - 1) {
      // The final chamber's exit routes the movie to the ending; no further
      // GAME frame plays, so complete the campaign count here.
      session.campaignCleared = CAMPAIGN_LENGTH;
    }
  }

  function resetSessionCounters() {
    approach.best = 0;
    approach.life = newApproachLife();
    approach.wasDead = false;
    session.episodeStartMs = Date.now();
    session.episodeCount += 1;
    session.deaths = 0;
    session.lastDeadSeen = 0;
    session.startConfirmed = false;
    session.appliedVia = null;
    session.campaignCleared = requestedLevel === null ? 0 : requestedLevel;
    session.lastLevelId = -1;
  }

  function isAtRequestedStart() {
    if (!inGameNow()) return false;
    if (requestedLevel === null) return true;
    return swf.level === requestedLevel;
  }

  async function awaitStart(timeoutMs) {
    // The SWF applies `level=` itself at its title frame; wait for the chamber
    // to be live, then fall back to the same level-select callback the movie
    // exposes if the URL route has not happened.
    var deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      refresh(true);
      trackPlay();
      if (isAtRequestedStart()) {
        session.startConfirmed = true;
        session.appliedVia = session.appliedVia || "url_level";
        return true;
      }
      await sleep(100);
    }
    if (requestedLevel === null) return false;
    var applied = invoke("gwStartLevel", requestedLevel);
    if (applied === undefined || applied === null) return false;
    deadline = Date.now() + 5000;
    while (Date.now() < deadline) {
      refresh(true);
      trackPlay();
      if (isAtRequestedStart()) {
        session.startConfirmed = true;
        session.appliedVia = "gwStartLevel";
        return true;
      }
      await sleep(100);
    }
    return false;
  }

  window.gameAPI = {
    version: "2.0",
    gameIdMarker: GAME_ID,
    capabilities: capabilities,

    init: async function () {
      resetSessionCounters();
      await awaitStart(8000);
      return {
        ok: true,
        accepted: { seed: null, level: requestedLevel, difficulty: null },
        applied: { seed: null, level: requestedLevel, difficulty: null },
        notes: ["local_swf", requestedLevel === null
          ? "no_level_query_title_menu"
          : "auto_start_from_url_level"]
      };
    },

    getState: function () {
      var now = Date.now();
      refresh(false);
      trackPlay();
      trackApproach();

      var frame = swf.frame;
      var started = frame >= 0;
      var inGame = inGameNow();
      var ending = frame >= ENDING_FRAME;
      var paused = swf.paused === 1 || (inGame && document.hidden);
      var playTimeS = swf.playTime >= 0 ? swf.playTime / FPS : 0;
      var chambersCleared = session.campaignCleared;
      var startOk = requestedLevel === null || session.startConfirmed;
      var badEnding = ending && session.lastLevelId === SNAKE_LEVEL_ID;
      var finishedCampaign = ending && chambersCleared >= CAMPAIGN_LENGTH;

      var status = "loading";
      if (ending) status = "terminal";
      else if (inGame && paused) status = "paused";
      else if (inGame) status = "playing";
      else if (started) status = "ready";

      var terminal = { isTerminal: false, outcome: null, reason: null };
      if (finishedCampaign) {
        terminal = { isTerminal: true, outcome: "success", reason: "campaign_ending" };
      } else if (badEnding) {
        terminal = { isTerminal: true, outcome: "fail", reason: "bad_ending" };
      } else if (ending) {
        terminal = { isTerminal: true, outcome: "fail", reason: "ended_without_full_campaign" };
      }

      return {
        schemaVersion: "2.0",
        gameId: GAME_ID,
        seed: null,
        timestampMs: now,
        gameTimeMs: Math.max(0, now - session.episodeStartMs),
        status: status,
        is_actionable: inGame && startOk,
        terminal: terminal,
        game_state: {
          score: chambersCleared,
          level: chambersCleared,
          player: {
            x: swf.x,
            y: swf.y,
            on_ground: swf.onGround,
            inverted: swf.inverted
          },
          environment: {
            frame: frame,
            dead: swf.dead,
            eol: swf.eol,
            deaths: session.deaths,
            play_time_s: Math.round(playTimeS * 10) / 10,
            paused: paused ? 1 : 0,
            raw_level: swf.level,
            level_id: swf.levelId,
            delivery: "local_swf"
          }
        },
        metrics: {
          chambers_cleared: chambersCleared,
          chamber_approach_progress: approach.best,
          play_time_s: Math.round(playTimeS * 10) / 10,
          deaths: session.deaths,
          dead: swf.dead,
          on_ground: swf.onGround,
          inverted: swf.inverted,
          expected_level: requestedLevel === null ? -1 : requestedLevel
        },
        debug: {
          swf: swf.raw,
          frame: frame,
          raw_level: swf.level,
          level_id: swf.levelId,
          last_level_id: session.lastLevelId,
          campaign_cleared: session.campaignCleared,
          live: swf.live,
          requested_level: requestedLevel,
          start_confirmed: session.startConfirmed,
          applied_via: session.appliedVia,
          episode_count: session.episodeCount,
          last_reset_method: session.lastResetMethod
        }
      };
    },

    reset: async function () {
      resetSessionCounters();
      if (requestedLevel === null) {
        // No task chamber: a fresh load returns to the title menu.
        session.lastResetMethod = "reload";
        window.location.reload();
        return { ok: true, method: "reload", applied: { level: null } };
      }
      var applied = invoke("gwStartLevel", requestedLevel);
      if (applied !== undefined && applied !== null) {
        session.lastResetMethod = "inplace_gwStartLevel";
        await awaitStart(5000);
        return {
          ok: session.startConfirmed,
          method: "inplace_gwStartLevel",
          applied: { level: requestedLevel }
        };
      }
      session.lastResetMethod = "reload";
      window.location.reload();
      return { ok: true, method: "reload", applied: { level: requestedLevel } };
    }
  };

  // Sample independently of getState(): a policy can move toward a waypoint and
  // back during one held action. 25Hz matches the SWF's native frame rate and
  // catches the native death flag throughout its two-second restart animation.
  // Start confirmation prevents an in-place reset from sampling the old room.
  window.setInterval(function () {
    if (!session.startConfirmed || !APPROACH_ROOMS[requestedLevel]) return;
    refresh(true);
    trackApproach();
  }, 40);
})();
