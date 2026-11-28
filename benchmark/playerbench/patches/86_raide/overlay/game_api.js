(function () {
  "use strict";

  var GAME_ID = "86_raide";
  if (window.gameAPI && window.gameAPI.gameIdMarker === GAME_ID) return;

  // The campaign is five towns of five named puzzles; the town map and the puzzle
  // name come straight off the bundle's level table (scr_GETLEVEL cases 10..34,
  // rooms 16..40 = rLEVEL_<town+1><index+1>).
  var PUZZLES_PER_TOWN = 5;
  var TOWNS = 5;
  var TOTAL_PUZZLES = PUZZLES_PER_TOWN * TOWNS;
  var PUZZLE_NAMES = [
    "BEGINNING", "OTHER SIDE", "FOREST", "CORNER", "MOUNTAINS",
    "DAMAGED", "SIMILAR", "JUNKYARD", "SEPARATED", "COPYCAT",
    "TURNTRACK", "NEIGHBOURS", "DISTANT", "CORNERSTONE", "UNDERGROUND",
    "HOLD THIS", "POSITIONS", "BROKEN TURN", "STAIRS", "HOUSE BELL",
    "FINAL DUET", "SIDEQUEST", "UNBALANCED", "TRICKY BELL", "THE FINAL TRACK"
  ];

  // ---------------------------------------------------------------------------
  // Task start. `?start=N` names the campaign puzzle a run must begin on. The
  // game's own boot code seeds gmlLEVELS from the array published here and then
  // jumps to the first uncleared puzzle, which is the route the in-game level
  // select takes, minus the menu clicking. This has to be published while this
  // script loads: it is deferred, so it runs after the document is parsed and
  // before GameMaker_Init boots the game on window.onload.
  // ---------------------------------------------------------------------------
  function requestedStartPuzzle() {
    try {
      var raw = new URLSearchParams(window.location.search).get("start");
      if (raw === null) return null;
      if (!/^[0-9]{1,2}$/.test(raw)) return null;
      var n = parseInt(raw, 10);
      if (n < 1 || n > TOTAL_PUZZLES) return null;
      return n;
    } catch (err) {
      return null;
    }
  }

  // gmlLEVELS[town] is the highest level index reached in that town (0..4), so
  // "puzzle P is the next one to play" means every earlier town is finished and
  // the puzzle's own town sits one index short.
  function levelsForPuzzle(puzzle) {
    var town = Math.floor((puzzle - 1) / PUZZLES_PER_TOWN);
    var index = (puzzle - 1) % PUZZLES_PER_TOWN;
    var out = [-1, -1, -1, -1, -1, -1, -1, -1, -1];
    for (var i = 0; i < TOWNS; i++) {
      if (i < town) out[i] = PUZZLES_PER_TOWN - 1;
    }
    out[town] = index - 1;
    return out;
  }

  var startPuzzle = requestedStartPuzzle();
  var startLevels = startPuzzle === null ? null : levelsForPuzzle(startPuzzle);
  var startCleared = 0;
  if (startLevels) {
    for (var s = 0; s < TOWNS; s++) startCleared += startLevels[s] + 1;
  }
  // The bundle's boot patch copies this. It has to be a copy: the GameMaker array
  // helper aliases its argument, so scr_LOAD_init would otherwise write empty
  // progress straight back through the array this file still holds.
  window.__startLevels = startLevels;
  // The native boot hook uses this name and copies the array before loading saves.
  window.__gwStartLevels = startLevels;

  var session = {
    episodeStartMs: Date.now(),
    playMs: 0,
    lastTickMs: null,
    lastPlaying: false,
    episodeCount: 0,
    lastResetMethod: null,
    terminal: { isTerminal: false, outcome: null, reason: null }
  };

  function numberOrZero(value) {
    if (typeof value === "number" && isFinite(value)) return value;
    if (value && typeof value.valueOf === "function") {
      var n = Number(value.valueOf());
      if (isFinite(n)) return n;
    }
    return 0;
  }

  function clearSaves() {
    try {
      var keys = [];
      for (var i = 0; i < localStorage.length; i++) {
        var key = localStorage.key(i);
        if (key && /raide|RAIDE_SAVE/i.test(key)) keys.push(key);
      }
      keys.forEach(function (k) { localStorage.removeItem(k); });
    } catch (err) {}
  }

  function readRuntime() {
    var empty = {
      started: 0,
      stage: 0,
      levels_cleared: 0,
      puzzle: 0,
      room: 0,
      in_puzzle: 0
    };
    try {
      if (typeof window.__raideRead === "function") {
        var packed = window.__raideRead();
        if (packed && typeof packed === "object") {
          return {
            started: packed.started ? 1 : 0,
            stage: numberOrZero(packed.stage),
            levels_cleared: numberOrZero(packed.levels_cleared),
            puzzle: numberOrZero(packed.puzzle),
            room: numberOrZero(packed.room),
            in_puzzle: packed.in_puzzle ? 1 : 0
          };
        }
      }
    } catch (err2) {}
    return empty;
  }

  // Only in-puzzle time counts: the clock stops while the game loads, while it
  // sits in an inter-level room, and once a puzzle has been solved. The whole
  // elapsed interval is credited to the previous observation, so the total does
  // not depend on how often the runner polls.
  function tickPlay(now, playing) {
    if (session.lastTickMs != null && session.lastPlaying === true) {
      session.playMs += Math.max(0, now - session.lastTickMs);
    }
    session.lastTickMs = now;
    session.lastPlaying = playing;
  }

  // The game records a finished puzzle as gmlLEVELS[town] = level index, so the
  // counter reaching 25 IS the campaign's own completion event (the fifth town's
  // last puzzle, THE FINAL TRACK). Latch it: the room then changes away from the
  // board and the counter must not be read as "not finished" again.
  function latchTerminal(cleared) {
    if (session.terminal.isTerminal) return session.terminal;
    if (cleared >= TOTAL_PUZZLES) {
      session.terminal = {
        isTerminal: true,
        outcome: "success",
        reason: "campaign_complete"
      };
    }
    return session.terminal;
  }

  function beginEpisode() {
    session.episodeStartMs = Date.now();
    session.playMs = 0;
    session.lastTickMs = null;
    session.lastPlaying = false;
    session.terminal = { isTerminal: false, outcome: null, reason: null };
    session.episodeCount += 1;
  }

  function clamp01(value) {
    return Math.max(0, Math.min(1, value));
  }

  clearSaves();

  window.gameAPI = {
    version: "2.1",
    gameIdMarker: GAME_ID,
    capabilities: {
      supports_seed: false,
      supports_level_select: startPuzzle !== null,
      supports_difficulty: false,
      supports_inplace_reset: false,
      supports_reload_reset: true,
      supports_pause_detection: true,
      supports_menu_detection: false,
      provides_actionable_flag: true
    },

    init: async function () {
      beginEpisode();
      clearSaves();
      return {
        ok: true,
        accepted: { seed: null, level: startPuzzle, difficulty: null },
        applied: { seed: null, level: startPuzzle, difficulty: null },
        notes: [
          "local_snapshot",
          "start_from_query_param_start",
          "starts_inside_named_campaign_puzzle"
        ]
      };
    },

    getState: function () {
      var now = Date.now();
      var rt = readRuntime();
      var paused = document.hidden === true;
      var inPuzzle = rt.in_puzzle > 0;
      var playing = inPuzzle && !paused;
      tickPlay(now, playing);

      var cleared = rt.levels_cleared;
      var town = rt.stage;
      var puzzle = rt.puzzle;
      var puzzleName = (puzzle >= 1 && puzzle <= PUZZLE_NAMES.length)
        ? PUZZLE_NAMES[puzzle - 1]
        : "";
      var terminal = latchTerminal(cleared);
      var won = cleared >= TOTAL_PUZZLES ? 1 : 0;

      var status = "loading";
      if (inPuzzle) status = paused ? "paused" : "playing";
      if (terminal.isTerminal) status = "finished";

      return {
        schemaVersion: "2.1",
        gameId: GAME_ID,
        seed: null,
        timestampMs: now,
        gameTimeMs: Math.round(session.playMs),
        status: status,
        is_actionable: playing && !terminal.isTerminal,
        terminal: {
          isTerminal: terminal.isTerminal,
          outcome: terminal.outcome,
          reason: terminal.reason
        },
        game_state: {
          score: cleared,
          level: puzzle,
          player: {
            stage: town,
            town: town + 1,
            puzzle: puzzle,
            puzzle_name: puzzleName
          },
          environment: {
            room: rt.room,
            in_puzzle: inPuzzle,
            paused: paused,
            survived_s: session.playMs / 1000,
            start_puzzle: startPuzzle === null ? 1 : startPuzzle
          },
          completion_progress: clamp01(cleared / TOTAL_PUZZLES)
        },
        metrics: {
          primary_score: cleared,
          levels_cleared: cleared,
          puzzle: puzzle,
          stage: town,
          room: rt.room,
          won: won,
          survived_s: session.playMs / 1000,
          puzzles_total: TOTAL_PUZZLES,
          start_puzzle: startPuzzle === null ? 1 : startPuzzle
        },
        debug: {
          engine: "gamemaker_html5",
          delivery: "local_snapshot",
          in_puzzle: inPuzzle,
          puzzle_name: puzzleName,
          start_requested: startPuzzle,
          start_observed: puzzle > 0 ? puzzle : null,
          start_verified: startPuzzle === null ? null : (puzzle === startPuzzle),
          start_cleared: startCleared,
          episode_count: session.episodeCount,
          last_reset_method: session.lastResetMethod
        }
      };
    },

    reset: async function () {
      beginEpisode();
      session.lastResetMethod = "reload";
      clearSaves();
      try {
        if (typeof window.__resetRandomSeed === "function") window.__resetRandomSeed();
      } catch (err) {}
      // The reload keeps the query string, so a task that started inside a puzzle
      // comes back to that same puzzle through the same boot patch.
      window.location.reload();
      return {
        ok: true,
        method: "reload",
        accepted: { seed: null, level: startPuzzle, difficulty: null },
        applied: { seed: null, level: startPuzzle, difficulty: null },
        notes: []
      };
    }
  };
})();
