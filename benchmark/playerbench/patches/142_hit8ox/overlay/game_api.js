/* ---------------------------------------------------------------------------
 * gameAPI for 142_hit8ox (HIT8OX, PICO-8 0.2.5G export)
 *
 * The cartridge (hit8ox_minified.p8 inside hit8ox.js) is patched to publish its
 * own fight state on the GPIO bank every _update (GPIO pin n = memory 0x5f80+n):
 *   0  magic 72 once the cart runs     1  P1 health x2 (0..200)
 *   2  CPU health x2 (0..200)          3  selected AI difficulty k (1 easy..3 hard)
 *   4  selected stage H (1 city, 2 subway station, 3 parking lot)
 *   5  active stage M (0 outside a fight)
 *   6  fight over flag en (the "victory for fighter N" banner, ~7 s)
 *   7  screen f: 1 title, 2 COLORS editor, 3 fight menu, 4 solo fight, 5 versus
 *   8  P1 stamina x100 (capped 255)    9  CPU stamina x100
 *   10 strikes P1 blocked this fight   11 fights started (mod 256)
 *   12 winner of the latest fight (0 none yet, 1 P1, 2 CPU)
 *   13 difficulty, 14 stage, 15 mode (4 solo, 5 versus) latched at START FIGHT
 *   20 menu cursor row h
 * Policy observation only (never part of gameAPI.getState()):
 *   32-39 int16 little-endian x10: P1 x, P1 depth, CPU x, CPU depth (e[n][1], e[n][3])
 *   40-42 P1 action code d[1] (ACTIONS below), animation frame t[1], animation id F[1]
 *   43-45 the same for the CPU (d[2], t[2], F[2])
 * Start request (written here, consumed by the cart): pin 16 = 1, pin 17 =
 * difficulty 1..3, pin 18 = stage 1..3. The cart then sets k/H/mode solo and
 * calls its own START FIGHT handler n6().
 *
 * URL: ?difficulty=easy|normal|hard&stage=city|subway|parking&goal=win|blocks|decisive|tour
 * ------------------------------------------------------------------------- */
(function () {
  "use strict";

  var GAME_ID = "142_hit8ox";
  if (window.gameAPI && window.gameAPI.gameIdMarker === GAME_ID) return;

  var MAGIC = 72;
  var PIN = {
    magic: 0, p1hp: 1, cpuhp: 2, diff: 3, stageSel: 4, stageActive: 5, fightOver: 6,
    screen: 7, p1stam: 8, cpustam: 9, blocks: 10, serial: 11, winner: 12,
    fightDiff: 13, fightStage: 14, fightMode: 15, cmd: 16, cmdDiff: 17, cmdStage: 18,
    cursor: 20
  };
  var DIFFS = { easy: 1, normal: 2, hard: 3 };
  var DIFF_NAMES = [null, "easy", "normal", "hard"];
  var STAGES = { city: 1, subway: 2, parking: 3 };
  var STAGE_NAMES = [null, "city", "subway station", "parking lot"];
  var SCREENS = [null, "title", "colors", "fight_menu", "solo_fight", "versus_fight"];
  var BLOCK_GOAL = 20;
  var DECISIVE_HP = 90;

  function query(name, fallback) {
    var m = new RegExp("[?&]" + name + "=([^&#]*)").exec(window.location.search);
    return m ? decodeURIComponent(m[1]).toLowerCase() : fallback;
  }

  var config = {
    difficulty: DIFFS[query("difficulty", "easy")] || 1,
    stage: STAGES[query("stage", "city")] || 1,
    goal: query("goal", "win")
  };
  if (["win", "blocks", "decisive", "tour"].indexOf(config.goal) < 0) config.goal = "win";

  var session;
  var frameWatch = { frame: -1, changedAt: 0 };

  function newSession() {
    return {
      startMs: Date.now(),
      phase: "starting",          // starting -> playing
      requestedAt: 0,
      baseSerial: -1,
      startSerial: -1,
      fightSerial: -1,
      fightsStarted: 0,
      fightsWon: 0,
      fightsLost: 0,
      fightProgress: 0,
      decisiveBest: 0,
      decisiveFrozen: false,
      tourBest: 0,
      hardStagesWon: {},
      lastWinnerSeen: 0,
      terminal: null
    };
  }
  session = newSession();

  function ports() { return window.pico8_gpio; }
  function pin(i) {
    var g = ports();
    var v = g ? g[i] : 0;
    return typeof v === "number" && isFinite(v) ? v : 0;
  }
  function cartLive() { return pin(PIN.magic) === MAGIC; }

  function framesAdvancing() {
    var st = window.pico8_state;
    if (!st || typeof st.frame_number !== "number") return false;
    var now = Date.now();
    if (st.frame_number !== frameWatch.frame) {
      frameWatch.frame = st.frame_number;
      frameWatch.changedAt = now;
      return true;
    }
    return now - frameWatch.changedAt < 1500;
  }

  function autostart() {
    if (window.p8_is_running) return;
    try {
      if (typeof p8_create_audio_context === "function") p8_create_audio_context();
      if (typeof p8_run_cart === "function") p8_run_cart();
    } catch (err) {}
  }

  function requestStart() {
    var g = ports();
    if (!g || !cartLive()) return false;
    session.baseSerial = pin(PIN.serial);
    g[PIN.cmdDiff] = config.difficulty;
    g[PIN.cmdStage] = config.stage;
    g[PIN.cmd] = 1;
    session.requestedAt = Date.now();
    return true;
  }

  function readCart() {
    return {
      p1hp: pin(PIN.p1hp) / 2,
      cpuhp: pin(PIN.cpuhp) / 2,
      diff: pin(PIN.diff),
      stageSel: pin(PIN.stageSel),
      stageActive: pin(PIN.stageActive),
      fightOver: pin(PIN.fightOver) === 1,
      screen: pin(PIN.screen),
      p1stam: pin(PIN.p1stam) / 100,
      cpustam: pin(PIN.cpustam) / 100,
      blocks: pin(PIN.blocks),
      serial: pin(PIN.serial),
      winner: pin(PIN.winner),
      fightDiff: pin(PIN.fightDiff),
      fightStage: pin(PIN.fightStage),
      fightMode: pin(PIN.fightMode),
      cursor: pin(PIN.cursor)
    };
  }

  function hardStagesCount() {
    return Object.keys(session.hardStagesWon).length;
  }

  // Advance the episode bookkeeping from one cart snapshot.
  function update() {
    if (!cartLive()) { autostart(); return null; }
    var c = readCart();
    var now = Date.now();

    if (session.phase === "starting") {
      if (!session.requestedAt) { requestStart(); return c; }
      var started = c.serial !== session.baseSerial && c.screen === 4 &&
        c.fightMode === 4 && c.fightDiff === config.difficulty &&
        c.fightStage === config.stage && c.diff === config.difficulty &&
        c.stageActive === config.stage && c.p1hp === 100 && c.cpuhp === 100;
      if (started) {
        session.phase = "playing";
        session.startSerial = c.serial;
      } else if (now - session.requestedAt > 3000) {
        requestStart();
      }
      return c;
    }

    // a new fight began (tour episodes go through the game's own menus)
    if (c.serial !== session.fightSerial) {
      session.fightSerial = c.serial;
      session.fightsStarted += 1;
      session.fightProgress = 0;
      session.decisiveFrozen = false;
      session.lastWinnerSeen = 0;
    }
    var soloFight = c.fightMode === 4;
    var damage = Math.max(0, 100 - c.cpuhp);

    if (c.winner && !session.lastWinnerSeen) {
      session.lastWinnerSeen = c.winner;
      if (c.winner === 1) session.fightsWon += 1; else session.fightsLost += 1;
      if (c.winner === 1 && soloFight && c.fightDiff === 3 && c.fightStage >= 1) {
        session.hardStagesWon[c.fightStage] = true;
      }
    }
    var p1Won = session.lastWinnerSeen === 1;
    var firstFight = c.serial === session.startSerial;

    // damage dealt this fight; 100 only on the victory event
    if (soloFight && (config.goal === "tour" || firstFight)) {
      var fp = p1Won ? 100 : Math.min(99, damage);
      if (fp > session.fightProgress) session.fightProgress = fp;
    }

    if (firstFight && soloFight) {
      if (c.p1hp < DECISIVE_HP) session.decisiveFrozen = true;
      if (!session.decisiveFrozen) {
        var dp = p1Won ? 100 : Math.min(99, damage);
        if (dp > session.decisiveBest) session.decisiveBest = dp;
      }
    }

    var tour = hardStagesCount() * 100;
    if (soloFight && c.fightDiff === 3 && !session.lastWinnerSeen &&
        c.screen === 4 && !session.hardStagesWon[c.fightStage]) {
      tour += Math.min(99, damage);
    }
    if (tour > session.tourBest) session.tourBest = tour;

    if (!session.terminal) {
      var ended = firstFight && session.lastWinnerSeen;
      if (config.goal === "win" && ended) {
        session.terminal = { isTerminal: true, outcome: p1Won ? "success" : "fail", reason: p1Won ? "p1_victory" : "p1_knocked_out" };
      } else if (config.goal === "blocks") {
        if (firstFight && c.blocks >= BLOCK_GOAL) session.terminal = { isTerminal: true, outcome: "success", reason: "blocks_reached" };
        else if (ended) session.terminal = { isTerminal: true, outcome: "fail", reason: "fight_ended" };
      } else if (config.goal === "decisive" && ended) {
        var ok = p1Won && session.decisiveBest >= 100;
        session.terminal = { isTerminal: true, outcome: ok ? "success" : "fail", reason: ok ? "decisive_victory" : (p1Won ? "victory_below_90_health" : "p1_knocked_out") };
      } else if (config.goal === "tour" && hardStagesCount() >= 3) {
        session.terminal = { isTerminal: true, outcome: "success", reason: "all_stages_won_on_hard" };
      }
    }
    return c;
  }

  setInterval(update, 100);

  function blankCart() {
    return { p1hp: null, cpuhp: null, diff: 0, stageSel: 0, stageActive: 0, fightOver: false,
      screen: 0, p1stam: null, cpustam: null, blocks: 0, serial: 0, winner: 0,
      fightDiff: 0, fightStage: 0, fightMode: 0, cursor: 0 };
  }

  function buildState() {
    var c = update() || blankCart();
    var now = Date.now();
    var live = cartLive();
    var advancing = framesAdvancing();
    var paused = !!(window.pico8_state && window.pico8_state.is_paused);
    var playing = session.phase === "playing";
    var firstFight = playing && c.serial === session.startSerial;
    var blocks = firstFight ? c.blocks : 0;

    var status = "loading";
    if (session.terminal) status = "ended";
    else if (live && !playing) status = "starting";
    else if (playing && paused) status = "paused";
    else if (playing && c.fightOver) status = "fight_over";
    else if (playing && c.screen >= 4) status = "fighting";
    else if (playing) status = "menu";

    var actionable = live && advancing && playing && !paused && !session.terminal;
    var stagesWon = Object.keys(session.hardStagesWon).map(Number).sort();

    return {
      schemaVersion: "1.0",
      gameId: GAME_ID,
      timestampMs: now,
      gameTimeMs: Math.max(0, now - session.startMs),
      status: status,
      is_actionable: actionable,
      terminal: session.terminal || { isTerminal: false, outcome: null, reason: null },
      game_state: {
        screen: SCREENS[c.screen] || null,
        requested: { difficulty: DIFF_NAMES[config.difficulty], stage: STAGE_NAMES[config.stage], goal: config.goal },
        selected_difficulty: DIFF_NAMES[c.diff] || null,
        selected_stage: STAGE_NAMES[c.stageSel] || null,
        active_stage: STAGE_NAMES[c.stageActive] || null,
        fight: {
          difficulty: DIFF_NAMES[c.fightDiff] || null,
          stage: STAGE_NAMES[c.fightStage] || null,
          mode: c.fightMode === 4 ? "solo" : (c.fightMode === 5 ? "versus" : null),
          over: c.fightOver,
          winner: c.winner === 1 ? "p1" : (c.winner === 2 ? "cpu" : null)
        },
        p1: { health: c.p1hp, stamina: c.p1stam },
        cpu: { health: c.cpuhp, stamina: c.cpustam },
        menu_cursor_row: c.cursor
      },
      metrics: {
        fight_progress: session.fightProgress,
        blocked_attacks: blocks,
        decisive_win_progress: session.decisiveBest,
        stage_tour_progress: session.tourBest,
        hard_stages_won: stagesWon.length,
        p1_health: c.p1hp === null ? 100 : c.p1hp,
        cpu_health: c.cpuhp === null ? 100 : c.cpuhp,
        fights_started: session.fightsStarted,
        fights_won: session.fightsWon,
        fights_lost: session.fightsLost
      },
      debug: {
        cart_live: live,
        frames_advancing: advancing,
        phase: session.phase,
        fight_serial: c.serial,
        start_serial: session.startSerial,
        hard_stages_won_ids: stagesWon,
        provenance: "engine_hook: cart-published GPIO pins 0-20"
      }
    };
  }

  function waitForStart(timeoutMs) {
    return new Promise(function (resolve) {
      var t0 = Date.now();
      (function poll() {
        update();
        if (session.phase === "playing") return resolve(true);
        if (Date.now() - t0 > timeoutMs) return resolve(false);
        setTimeout(poll, 100);
      })();
    });
  }

  window.gameAPI = {
    version: "1.0",
    gameIdMarker: GAME_ID,
    capabilities: {
      supports_seed: false,
      supports_level_select: true,
      supports_difficulty: true,
      supports_inplace_reset: true,
      supports_reload_reset: true,
      provides_actionable_flag: true
    },

    init: async function () {
      autostart();
      var ok = await waitForStart(45000);
      return {
        ok: ok,
        accepted: { level: STAGE_NAMES[config.stage], difficulty: DIFF_NAMES[config.difficulty], goal: config.goal },
        applied: ok ? { level: STAGE_NAMES[config.stage], difficulty: DIFF_NAMES[config.difficulty] } : null,
        notes: ["start_fight_via_cart_n6"]
      };
    },

    getState: function () {
      return buildState();
    },

    // In place: the cart's START FIGHT handler resets both fighters (eE()).
    reset: async function () {
      if (!cartLive()) {
        window.location.reload();
        return { ok: true, method: "reload", notes: [] };
      }
      session = newSession();
      requestStart();
      var ok = await waitForStart(8000);
      if (!ok) {
        window.location.reload();
        return { ok: true, method: "reload", notes: ["inplace_start_not_confirmed"] };
      }
      return { ok: true, method: "start_fight", notes: [] };
    }
  };

  // Present fighter geometry and poses for the policy probe, read from the
  // cart's own tables via pins 32-45. The CPU AI's next choice is not published.
  var ACTIONS = [null, "idle", "punch1", "punch1_2_idle", "punch2_2_idle", "kick1", "kick2",
    "enter_block", "block", "exit_block", "hit1", "hit2", "hit3", "dead", "won_transition", "won"];
  var ANIMS = [null, "idle", "walk_forward", "walk_back", "walk_right", "walk_left", "punch",
    "hit1", "enter_block", "block", "hit_blocked", "hit2", "hit3", "punch1_2_idle",
    "punch2_2_idle", "kick1", "exit_block", "kick2", "dying", "dead", "won_transition", "won"];

  function int16x10(i) {
    var g = ports();
    if (!g || typeof g[i] !== "number" || typeof g[i + 1] !== "number") return null;
    var v = g[i] + 256 * g[i + 1];
    if (v >= 32768) v -= 65536;
    return v / 10;
  }

  function fighterPos(i) {
    var x = int16x10(i), depth = int16x10(i + 2);
    return x === null || depth === null ? null : { x: x, depth: depth };
  }

  function fighterAction(i) {
    var code = pin(i);
    if (!ACTIONS[code]) return null;
    var anim = pin(i + 2);
    return { state: ACTIONS[code], code: code, frame: pin(i + 1), animation: ANIMS[anim] || null, animation_id: anim };
  }

  window.__policyObservation = {
    getState: function () {
      var inFight = cartLive() && (pin(PIN.screen) === 4 || pin(PIN.screen) === 5);
      return {
        p1_pos: inFight ? fighterPos(32) : null,
        cpu_pos: inFight ? fighterPos(36) : null,
        p1_action: inFight ? fighterAction(40) : null,
        cpu_action: inFight ? fighterAction(43) : null
      };
    }
  };

  autostart();
})();
