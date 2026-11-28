/* ---------------------------------------------------------------------------
 * gameAPI for 212_trichromat (PICO-8 cart "trichromat" by Samuel Pratt)
 *
 * The cart keeps its state in Lua globals nothing on the page can read, so the
 * cart itself publishes them: its _draw() ends with gw_pub(), which pokes the block
 * below onto the GPIO bank every frame (pico8_gpio[n] is memory 0x5f80+n). Task
 * starts go the other way: this page writes pins 0 and 1 before the cart boots and
 * the patched _init (gw_boot) loads that level, skipping title and level select.
 * The cart patch is listed in working-notes.md of the build cache; it is five small
 * hunks in trichromat.p8 (gw_boot/gw_pub appended, calls in _init, exit_level, step, _draw).
 *
 *   pin  0    start level + 1 (command; 0 = boot the normal title screen)
 *   pin  1    start level + 101 (check byte; both must match)
 *   pin  8    magic 83, set once the cart has drawn a frame
 *   pin  9    cart frame counter, low byte
 *   pin 10    screen: 0 title, 1 level select, 2 level
 *   pin 11    current level (0 = tutorial, 1..20, 21 = ending text)
 *   pin 12    dget(0): highest unlocked level (the cart's own save slot)
 *   pin 13    levels solved this episode: distinct levels >= the start level whose
 *             exit_level() has run (the cart's own level-complete function)
 *   pin 14    "groups" aligned: positions holding all 3 colour copies of a piece
 *   pin 15    groups on the board (one per player/box position); the level is won
 *             (exit animation starts) when pin 14 == pin 15
 *   pin 16    undo depth (the cart's history stack size)
 *   pin 17-18 total committed moves this episode (a move that changed something)
 *   pin 19    flags: 1 dialogue/email screen, 2 transition running (enter/exit/glitch),
 *             4 ending screen, 8 theme cmy, 16 dialogue accepts X
 *   pin 20-21 level width-1, height-1 (tiles)
 *   pin 22    actor count (max 48 published)
 *   pin 24+   per actor 2 bytes: x + 8*y + 64*(channel-1), then sprite (5 player, 6 box)
 *
 * Every number is a cart variable (or a count over the cart's actor list), so the
 * provenance is `internal`. Task rules live here, because the cart has none:
 *   ?start=N   begin on level N (0..20) with a fresh save; omit for the title screen
 *   ?goal=K    success once K levels have been solved
 * ------------------------------------------------------------------------- */
(function () {
  "use strict";

  var GAME_ID = "212_trichromat";
  if (window.gameAPI && window.gameAPI.gameIdMarker === GAME_ID) return;

  var MAGIC = 83;
  var PROV = "internal";
  var FRAME_MS = 1000 / 60; // pico8_state.frame_number advances at 60 Hz

  function queryInt(name, lo, hi) {
    var m = new RegExp("[?&]" + name + "=(\\d+)").exec(window.location.search);
    if (!m) return -1;
    var n = parseInt(m[1], 10);
    return n >= lo && n <= hi ? n : -1;
  }
  var START = queryInt("start", 0, 20);
  var GOAL = queryInt("goal", 1, 21);

  var session = { reachedLevel: false, episodes: 0, startFrame: 0, terminal: null, lastFrame: -1, lastFrameChangeMs: 0 };

  function pin(n) {
    var ports = window.pico8_gpio;
    var v = ports ? ports[n] : 0;
    return typeof v === "number" && isFinite(v) ? v & 0xff : 0;
  }

  // Task start command for the cart's gw_boot(); must be on the pins before it boots.
  function writeStartPins() {
    var ports = window.pico8_gpio;
    if (!ports) return;
    ports[0] = START >= 0 ? START + 1 : 0;
    ports[1] = START >= 0 ? START + 101 : 0;
  }
  writeStartPins();

  function frameNumber() {
    var st = window.pico8_state;
    return st && typeof st.frame_number === "number" ? st.frame_number : 0;
  }
  function cartStarted() { return frameNumber() > 0; }

  function framesAdvancing() {
    var f = frameNumber();
    var now = Date.now();
    if (f !== session.lastFrame) {
      session.lastFrame = f;
      session.lastFrameChangeMs = now;
      return f > 0;
    }
    return f > 0 && now - session.lastFrameChangeMs < 1500;
  }

  function startCart() {
    if (window.p8_is_running) return;
    try {
      if (typeof p8_create_audio_context === "function") p8_create_audio_context();
      if (typeof p8_run_cart === "function") p8_run_cart();
    } catch (err) {}
  }
  // The shell only boots the cart from a user gesture; call its own entry until frames move.
  setInterval(function () { if (!cartStarted()) startCart(); }, 400);

  function readCart() {
    if (pin(8) !== MAGIC) return null;
    var flags = pin(19);
    var n = Math.min(pin(22), 48);
    var actors = [];
    for (var i = 0; i < n; i++) {
      var b = pin(24 + i * 2);
      actors.push({
        x: b & 7, y: (b >> 3) & 7, channel: ((b >> 6) & 3) + 1,
        kind: pin(25 + i * 2) === 5 ? "player" : "box"
      });
    }
    return {
      screen: pin(10),
      level: pin(11),
      unlocked: pin(12),
      solved: pin(13),
      aligned: pin(14),
      groups: pin(15),
      undo: pin(16),
      moves: pin(17) + pin(18) * 256,
      dialogue: !!(flags & 1),
      transition: !!(flags & 2),
      ending: !!(flags & 4),
      theme: (flags & 8) ? "cmy" : "rgb",
      dialogueReady: !!(flags & 16),
      width: pin(20) + 1,
      height: pin(21) + 1,
      actors: actors
    };
  }

  function judge(c) {
    if (session.terminal || GOAL < 0) return;
    if (c.solved >= GOAL) session.terminal = { isTerminal: true, outcome: "success", reason: "levels_solved_goal" };
  }

  function phaseName(c) {
    if (c.screen === 0) return "title";
    if (c.screen === 1) return "level_select";
    if (c.ending) return "ending";
    if (c.dialogue) return "email";
    return c.transition ? "transition" : "playing";
  }

  function beginEpisode() {
    session.episodes += 1;
    session.terminal = null;
    session.reachedLevel = false;
    session.startFrame = frameNumber();
  }
  beginEpisode();

  window.gameAPI = {
    version: "2.1",
    gameIdMarker: GAME_ID,
    capabilities: {
      supports_seed: false,
      supports_level_select: true,
      supports_difficulty: false,
      supports_inplace_reset: false,
      supports_reload_reset: true,
      supports_pause_detection: true,
      supports_menu_detection: true,
      provides_actionable_flag: true
    },

    init: async function () {
      writeStartPins();
      beginEpisode();
      startCart();
      return {
        ok: true,
        accepted: { seed: null, level: START >= 0 ? START : null, difficulty: null },
        applied: { seed: null, level: START >= 0 ? START : null, difficulty: null },
        notes: [START >= 0 ? "gpio_start_level_" + START : "title_screen"]
      };
    },

    getState: function () {
      var now = Date.now();
      var started = cartStarted();
      var c = readCart();
      var paused = !!(window.pico8_state && window.pico8_state.is_paused);
      var empty = {
        schemaVersion: "2.1",
        gameId: GAME_ID,
        timestampMs: now,
        gameTimeMs: 0,
        status: "loading",
        is_actionable: false,
        terminal: { isTerminal: false, outcome: null, reason: null },
        game_state: {
          phase: "loading", level: 0, highest_unlocked: 0, levels_solved: 0,
          aligned_groups: 0, total_groups: 0, moves: 0, undo_depth: 0, theme: "rgb"
        },
        metrics: { levels_solved: 0, level: 0, moves: 0, aligned_groups: 0 },
        debug: { running: !!window.p8_is_running, requested_start: START, goal: GOAL }
      };
      if (!c) return empty;

      judge(c);

      var wantsPlay = START >= 0;
      // Once the task level has been reached, menus the player opens later (level select, title) are still playable.
      if (c.screen === 2) session.reachedLevel = true;
      var atTask = !wantsPlay || session.reachedLevel;
      var actionable = started && framesAdvancing() && !paused && atTask && !session.terminal &&
        (c.dialogue ? c.dialogueReady : !c.transition); // the enter animation only runs once the email is dismissed
      var phase = phaseName(c);
      var status = session.terminal ? "ended" : (paused ? "paused" : (c.screen === 0 ? "title" : "playing"));

      return {
        schemaVersion: "2.1",
        gameId: GAME_ID,
        timestampMs: now,
        gameTimeMs: Math.round(Math.max(0, frameNumber() - session.startFrame) * FRAME_MS),
        status: status,
        is_actionable: actionable,
        terminal: session.terminal || { isTerminal: false, outcome: null, reason: null },
        game_state: {
          phase: phase,
          level: c.level,
          highest_unlocked: c.unlocked,
          levels_solved: c.solved,
          aligned_groups: c.aligned,
          total_groups: c.groups,
          moves: c.moves,
          undo_depth: c.undo,
          theme: c.theme
        },
        metrics: {
          levels_solved: c.solved,
          level: c.level,
          moves: c.moves,
          aligned_groups: c.aligned
        },
        debug: {
          running: !!window.p8_is_running,
          requested_start: START,
          goal: GOAL,
          episode_count: session.episodes,
          provenance: PROV,
          delivery: "gpio_cart_patch"
        }
      };
    },

    reset: async function () {
      // A reload re-runs the cart's _init, which re-reads the start pins written below.
      writeStartPins();
      window.location.reload();
      return { ok: true, method: "reload", notes: [] };
    }
  };

  // What a player may see: the board as the cart holds it, no task goal or verdict.
  window.__policyObservation = {
    getState: function () {
      var c = readCart();
      if (!c) return { level: null, actors: [] };
      return {
        level: c.level,
        phase: phaseName(c),
        board_size: { width: c.width, height: c.height },
        actors: c.actors,
        aligned_groups: c.aligned,
        total_groups: c.groups,
        moves: c.moves,
        undo_depth: c.undo,
        coordinates: "tile units from the top-left of each of the three colour boards (channel 1 red, 2 green, 3 blue); a level is solved when every player and box sits on the same tile in all three boards"
      };
    }
  };
}());
