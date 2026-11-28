/* ---------------------------------------------------------------------------
 * Policy observation bridge for 89_celeste-classic-2  --  window.__policyObservation
 *
 * Present state only, from two sources.
 *
 * 1. Geometry and entity placement come from live PICO-8 RAM inside the wasm
 *    heap. The cart's goto_level() decompresses each map into cartridge RAM at
 *    0x4300 + x + y*width (source: px9_decomp / tile_at), and a level's tile ids
 *    are the sprite ids of its object types (new_type(spr) -> types[spr]), so
 *    that grid carries the terrain, the spikes, the springs, the crumbling
 *    blocks, the bridge, the grapple anchors and orbs, the checkpoints and the
 *    spawn. Tile solidity and "ice" come from the same cart's sprite-flag page at
 *    RAM 0x3000 + tile: flag 1 is what object.check_solid() tests, flag 2 is what
 *    player.grapple_check() refuses (ice: solid but not hookable). Ground and
 *    wall contact are reproduced from that grid with the player's own hit box
 *    (object.check_solid), so the cartridge spends no tokens on them. The RAM
 *    base is found once per page load by matching the cartridge image (the global
 *    `_cartdat`) against the heap, and the ROM copy is rejected by its code
 *    header at 0x4300, so no address is hard-coded.
 *
 * 2. Lani's own values live only in Lua tables, so the cartridge publishes them
 *    once per _draw() onto the PICO-8 GPIO bank, a raw scalar per pin (see
 *    gw_pub.lua; pins 9..41, while 0/1 are the shell bank protocol and 100..104
 *    the cart's own berries/level/time/hook row).
 *
 * Frame of reference for every position below: the current map's own pixel frame,
 * origin at the top-left corner of tile (0,0), +x right, +y down, tile (i,j)
 * spanning x in [i*8, i*8+8). Positions are Lani's sprite top-left. Speeds are
 * pixels per PICO-8 frame (the cart defines _update, so 30 frames per second).
 *
 * Nothing here is read from gameAPI, and none of these keys appear in
 * gameAPI.getState().
 * ------------------------------------------------------------------------- */
(function () {
  "use strict";

  var MAGIC = 89;
  var RAM_GRID = 0x4300;
  var RAM_FLAGS = 0x3000;
  var RAM_SHARED = 0x1000;
  var ROM_GRID = 0x4300;
  var PIN_MAGIC = 9;          // 0x5f89
  var PIN_END = 42;           // 0x5fa9 inclusive
  var BERRY_CAP = 6;

  var K_EMPTY = 0, K_SOLID = 1, K_ICE = 2, K_SPIKE = 3, K_CRUMBLE = 4,
      K_BRIDGE = 5, K_SPRING = 6, K_ANCHOR = 7, K_ORB = 8, K_CHECKPOINT = 9,
      K_SPAWNER = 10, K_PLAYER_SPAWN = 11, K_BERRY = 12;

  // Static level table from the cart's own `levels` (tiles), plus the cart's
  // finish rule per map (player.update).
  var LEVELS = {
    1: { w: 96, h: 16, goal: "ravine_fall" },
    2: { w: 32, h: 32, goal: "right_edge" },
    3: { w: 128, h: 22, goal: "right_edge" },
    4: { w: 128, h: 32, goal: "right_edge" },
    5: { w: 128, h: 16, goal: "right_edge" },
    6: { w: 128, h: 16, goal: "right_edge" },
    7: { w: 128, h: 16, goal: "right_edge" },
    8: { w: 16, h: 62, goal: "bottom_destination" }
  };

  var FIELDS = [
    "player_position", "player_velocity", "player_motion_state", "hook_state",
    "terrain", "hazards", "strawberries", "interactables",
    "map_goal_and_respawn", "input_gate"
  ];

  var base = -1;
  var baseTriedMs = 0;

  function heap() {
    var m = window.Module;
    return (m && m.HEAPU8) || null;
  }

  function cartImage() {
    var cd = window._cartdat;
    if (!cd || cd.length < 0x8000) return null;
    function slice(off, n) {
      var a = new Array(n);
      for (var i = 0; i < n; i++) a[i] = cd[off + i];
      return a;
    }
    return { sprite: slice(4, 12), shared: slice(RAM_SHARED, 12), code: slice(ROM_GRID, 4) };
  }

  function matchAt(H, at, pat) {
    if (at < 0 || at + pat.length > H.length) return false;
    for (var i = 0; i < pat.length; i++) if (H[at + i] !== pat[i]) return false;
    return true;
  }

  function locateRam() {
    var H = heap(), img = cartImage();
    if (!H || !img) return -1;
    for (var i = 0; i + 12 < H.length; i++) {
      if (H[i] !== img.sprite[0] || H[i + 5] !== img.sprite[5]) continue;
      if (!matchAt(H, i, img.sprite)) continue;
      var b = i - 4;
      if (b < 0) continue;
      if (!matchAt(H, b + RAM_SHARED, img.shared)) continue;
      if (matchAt(H, b + ROM_GRID, img.code)) continue;
      return b;
    }
    return -1;
  }

  function ramBase() {
    if (base >= 0) return base;
    var now = Date.now();
    if (now - baseTriedMs < 1000) return -1;
    baseTriedMs = now;
    base = locateRam();
    return base;
  }

  function levelIndex() {
    var ports = window.pico8_gpio || window.gpio_data;
    var v = ports ? ports[101] : 0;      // the cart's own level_index poke
    return typeof v === "number" && isFinite(v) ? v : 0;
  }

  function u8(i) {
    var ports = window.pico8_gpio || window.gpio_data;
    if (!ports) return 0;
    var v = ports[i];
    return typeof v === "number" && isFinite(v) ? (v & 0xff) : 0;
  }

  function s16(lo, hi) {
    var v = lo + hi * 256;
    return v >= 32768 ? v - 65536 : v;
  }

  function keyOf(x, y) {
    return x + "," + y;
  }

  function unproduced() {
    var out = {};
    for (var i = 0; i < FIELDS.length; i++) out[FIELDS[i]] = null;
    return out;
  }

  function readGrid(mapIndex) {
    var spec = LEVELS[mapIndex];
    if (!spec) return null;
    var H = heap();
    if (!H) return null;
    var origin = base + RAM_GRID;
    if (origin + spec.w * spec.h > H.length) return null;
    var tiles = new Array(spec.w * spec.h);
    var filled = 0;
    for (var i = 0; i < tiles.length; i++) {
      var t = H[origin + i];
      tiles[i] = t;
      if (t) filled += 1;
    }
    if (!filled) return null;
    return { spec: spec, tiles: tiles, heap: H };
  }

  function flagsOf(H, tile) {
    return H[base + RAM_FLAGS + tile] & 0xff;
  }

  function kindOf(tile, flags) {
    if (tile === 0) return K_EMPTY;
    if (tile === 2) return K_PLAYER_SPAWN;
    if (tile === 11) return K_SPRING;
    if (tile === 13) return K_CHECKPOINT;
    if (tile === 14 || tile === 15) return K_SPAWNER;
    if (tile === 19) return K_CRUMBLE;
    if (tile === 20) return K_ORB;
    if (tile === 21) return K_BERRY;
    if (tile === 36 || tile === 37) return K_SPIKE;
    if (tile === 46) return K_ANCHOR;
    if (tile === 62) return K_EMPTY;
    if (tile === 63) return K_BRIDGE;
    if (flags & 2) return (flags & 4) ? K_ICE : K_SOLID;
    return K_EMPTY;
  }

  function spikePoints(g, i, j, tile) {
    var w = g.spec.w, h = g.spec.h;
    function solidAt(x, y) {
      if (x < 0 || y < 0 || x >= w || y >= h) return false;
      var t = g.tiles[y * w + x];
      return t ? !!(flagsOf(g.heap, t) & 2) : false;
    }
    if (tile === 36) return solidAt(i, j + 1) ? "up" : "down";
    return solidAt(i - 1, j) ? "right" : "left";
  }

  // object.check_solid(): scan the tiles the hit box would cover when displaced,
  // clamping the row like the cart's tile_y(); solid objects on top of that are
  // the crumble/bridge tiles, which are already in the grid.
  function checkSolid(g, px, py, ox, oy) {
    var hitX = -3, hitY = -6, hitW = 6, hitH = 6;
    var i0 = Math.floor((ox + px + hitX) / 8);
    var i1 = Math.floor((ox + px + hitX + hitW - 1) / 8);
    var j0 = Math.max(0, Math.min(Math.floor((oy + py + hitY) / 8), g.spec.h - 1));
    var j1 = Math.max(0, Math.min(Math.floor((oy + py + hitY + hitH - 1) / 8), g.spec.h - 1));
    for (var i = i0; i <= i1; i++) {
      for (var j = j0; j <= j1; j++) {
        if (i < 0 || i >= g.spec.w) continue;
        var t = g.tiles[j * g.spec.w + i];
        if (t && (flagsOf(g.heap, t) & 2)) return true;
      }
    }
    return false;
  }

  window.__policyObservation = {
    getState: function () {
      if (u8(PIN_MAGIC) !== MAGIC) return unproduced();
      if (ramBase() < 0) return unproduced();
      var mapIndex = levelIndex();
      if (!LEVELS[mapIndex]) return unproduced();

      var x = s16(u8(10), u8(11)) / 8;
      var y = s16(u8(12), u8(13)) / 8;
      var vx = s16(u8(14), u8(15)) / 16;
      var vy = s16(u8(16), u8(17)) / 16;
      var gx = s16(u8(18), u8(19)) / 8;
      var gy = s16(u8(20), u8(21)) / 8;
      var state = u8(22);
      var grace = u8(23);
      var varJump = u8(24);
      var flags = u8(25);
      var frame = u8(26);

      var berries = [];
      for (var b = 0; b < BERRY_CAP; b++) {
        var id = s16(u8(28 + b * 2), u8(29 + b * 2));
        if (!id) break;
        var v = id - mapIndex * 100;
        berries.push({ tile_x: ((v % 128) + 128) % 128, tile_y: Math.floor(v / 128) });
      }

      var terr = readGrid(mapIndex);
      if (!terr) return unproduced();
      var w = terr.spec.w, h = terr.spec.h, kinds = [], spikes = [], springs = [],
          crumbles = [], bridges = [], anchors = [], orbs = [], checkpoints = [],
          spawners = [], spawn = null, i, j, tile, kind;
      var takenOrb = !!(flags & 2);        // the single grapple pickup, once held
      for (j = 0; j < h; j++) {
        var row = new Array(w);
        for (i = 0; i < w; i++) {
          tile = terr.tiles[j * w + i];
          kind = kindOf(tile, tile ? flagsOf(terr.heap, tile) : 0);
          row[i] = kind;
          if (kind === K_SPIKE) spikes.push({ x: i * 8, y: j * 8, points: spikePoints(terr, i, j, tile) });
          else if (kind === K_SPRING) springs.push({ x: i * 8, y: j * 8 });
          else if (kind === K_CRUMBLE) crumbles.push({ x: i * 8, y: j * 8, breaking: null });
          else if (kind === K_BRIDGE) bridges.push({ x: i * 8, y: j * 8, falling: null });
          else if (kind === K_ANCHOR) anchors.push({ x: i * 8, y: j * 8 });
          else if (kind === K_ORB) orbs.push({ x: i * 8, y: j * 8, taken: takenOrb });
          else if (kind === K_CHECKPOINT) checkpoints.push({ x: i * 8, y: j * 8 });
          else if (kind === K_SPAWNER) spawners.push({ x: i * 8, y: j * 8 });
          else if (kind === K_PLAYER_SPAWN && !spawn) spawn = { tile_x: i, tile_y: j };
        }
        kinds.push(row);
      }

      var onGround = checkSolid(terr, x, y, 0, 1);
      var left = checkSolid(terr, x, y, -1, 0);
      var right = checkSolid(terr, x, y, 1, 0);
      var above = checkSolid(terr, x, y, 0, -1);
      var carrying = !!(flags & 8);
      var holding = !!(flags & 32);
      var alive = state < 99;
      var level = LEVELS[mapIndex];

      var out = {};

      out.player_position = {
        x: x, y: y,
        frame: "current_map_pixels",
        origin: "top-left corner of tile (0,0) of the current map",
        x_axis: "right", y_axis: "down", units: "pixels",
        anchor: "Lani's sprite top-left corner",
        map_index: mapIndex, tile: { x: Math.floor(x / 8), y: Math.floor(y / 8) }
      };

      out.player_velocity = {
        x: vx, y: vy,
        units: "pixels_per_pico8_frame", frames_per_second: 30
      };

      out.player_motion_state = {
        on_ground: onGround,
        jump_grace_frames: grace,
        solid_below: onGround, solid_left: left, solid_right: right, solid_above: above,
        wall_contact: left ? "left" : (right ? "right" : null),
        rising: vy < 0, falling: vy > 0,
        jump_held: !!(flags & 1),
        variable_jump_armed: varJump > 0,
        facing: (flags & 16) ? "right" : "left",
        alive: alive, dying: state === 99, finished: state === 100,
        state: state, frame: frame
      };

      out.hook_state = {
        carrying_hook: !!(flags & 2),
        fired: state === 10 || state === 11 || state === 12,
        attached: state === 11 || state === 12,
        anchor: (state === 10 || state === 11 || state === 12) ? { x: gx, y: gy } : null,
        holding_object: holding,
        held_object_position: holding ? { x: x - 4, y: y - 14 } : null,
        note: "the cart parks a held object at (player.x-4, player.y-14); the object's own kind and map tile are not published"
      };

      out.terrain = {
        map_index: mapIndex, tile_size: 8, width: w, height: h,
        origin: { x: 0, y: 0 }, x_axis: "right", y_axis: "down",
        kinds: kinds,
        legend: {
          "0": "empty", "1": "solid", "2": "ice (solid, not hookable)",
          "3": "spike", "4": "crumbling block", "5": "bridge tile",
          "6": "spring/bouncer", "7": "grapple anchor", "8": "grapple orb",
          "9": "checkpoint flag", "10": "snowball spawner", "11": "player spawn",
          "12": "strawberry"
        },
        note: "one row per tile y (top to bottom), one column per tile x"
      };

      out.hazards = {
        spikes: spikes,
        pits: [{ kind: "bottom", y: h * 8 + 16, x0: 0, x1: w * 8,
                 note: "falling past this y is lethal" }]
      };

      out.strawberries = {
        remaining: berries.map(function (p) {
          return { x: p.tile_x * 8, y: p.tile_y * 8, tile_x: p.tile_x, tile_y: p.tile_y };
        }),
        carried: carrying,
        map_tile_count: (function () {
          var n = 0;
          for (var k = 0; k < terr.tiles.length; k++) if (kindOf(terr.tiles[k], 0) === K_BERRY) n++;
          return n;
        })()
      };

      out.interactables = {
        springs: springs,
        crumbling_blocks: crumbles,
        bridge_tiles: bridges,
        grapple_anchors: anchors,
        grapple_orbs: orbs,
        snowball_spawners: spawners,
        snowballs: null,
        note: "crumbling blocks and bridge tiles give position only: whether each has begun to break or fall is Lua-only and did not fit the cartridge's token budget; snowballs are spawned and thrown at run time and their positions are not published"
      };

      var goal;
      if (level.goal === "ravine_fall") {
        goal = { kind: "ravine_fall", x: level.w * 8 - 64, y: h * 8,
                 note: "map 1 ends once Lani is past this x and then falls below the map or touches a hazard" };
      } else if (level.goal === "right_edge") {
        goal = { kind: "right_edge", x: level.w * 8 - 4, y: y,
                 note: "the map ends when Lani passes the right edge" };
      } else {
        goal = { kind: "bottom_destination", x: level.w * 8 - 4, y: 376,
                 note: "final map: the run ends when Lani drops past y 376" };
      }
      var respawn = null;
      if (flags & 4) {
        var last = null;
        for (var ci = 0; ci < checkpoints.length; ci++) last = checkpoints[ci];
        if (last) respawn = { x: last.x + 4, y: last.y + 8, source: "checkpoint_flag" };
      } else if (spawn) {
        respawn = { x: spawn.tile_x * 8 + 4, y: spawn.tile_y * 8 + 8, source: "level_start_tile" };
      }
      out.map_goal_and_respawn = {
        map_index: mapIndex, goal: goal, respawn: respawn,
        checkpoint_active: !!(flags & 4)
      };

      var gate = null;
      if (state === 99 || state === 100) gate = "death_wipe";
      else if (state === 50) gate = "hook_pickup";
      out.input_gate = {
        blocked: gate !== null,
        reason: gate,
        card: null,
        player_state: state,
        note: "this build leaves the cart's map-name card at level_intro 0, so input is gated only by the death wipe and the hook-pickup animation"
      };

      return out;
    }
  };
}());

(function () {
  "use strict";

  var GAME_ID = "89_celeste-classic-2";
  if (window.gameAPI && window.gameAPI.gameIdMarker === GAME_ID) return;

  var DEFAULT_SEED = 42;
  var PIN_BERRIES = 100;
  var PIN_LEVEL = 101;
  var PIN_TIME_LO = 102;
  var PIN_TIME_HI = 103;
  var PIN_HOOK = 104;

  // Route progress inside the current map, from the position the cartridge publishes
  // for the policy bridge (pin 9 reads 89 once published; Lani's x at pins 10/11 and y
  // at 12/13, signed 16-bit eighths of a pixel, current map's pixel frame). The goal is
  // where the cart ends each map (player.update): map 1 past x 704 into the ravine,
  // maps 2-7 the right edge (map width * 8 - 4), map 8 down to y 376.
  var PIN_PUBLISHED = 9;
  var PUBLISHED = 89;
  var ROUTE_GOALS = {
    1: ["x", 704], 2: ["x", 252], 3: ["x", 1020], 4: ["x", 1020],
    5: ["x", 1020], 6: ["x", 1020], 7: ["x", 1020], 8: ["y", 376]
  };
  var ROUTE_CAP = 0.999;  // the whole number only advances when the cart changes map

  if (!window.pico8_gpio) window.pico8_gpio = new Array(128);

  var session = {
    seed: DEFAULT_SEED,
    requestedLevel: null,
    requestedDifficulty: null,
    episodeStartMs: Date.now(),
    lastPlayMs: null,
    survivedMs: 0,
    episodeCount: 0,
    lastResetMethod: null,
    autostartArmed: false,
    route: { map: 0, start: null, fraction: 0 }
  };

  var capabilities = {
    supports_seed: false,
    supports_level_select: false,
    supports_difficulty: false,
    supports_inplace_reset: false,
    supports_reload_reset: true,
    supports_pause_detection: true,
    supports_menu_detection: true,
    provides_actionable_flag: true
  };

  function finiteOrNull(value) {
    return typeof value === "number" && isFinite(value) ? value : null;
  }

  function numberOrZero(value) {
    var result = finiteOrNull(value);
    return result === null ? 0 : result;
  }

  function gpio(pin) {
    var ports = window.pico8_gpio || window.gpio_data;
    if (!ports) return 0;
    return numberOrZero(ports[pin]);
  }

  function pinS16(lo) {
    var v = (gpio(lo) & 0xff) + (gpio(lo + 1) & 0xff) * 256;
    return v >= 32768 ? v - 65536 : v;
  }

  // Best share of the current map's route covered this episode, 0..ROUTE_CAP. It is
  // measured from the least advanced position seen on the map (the first frame after
  // a map change can still hold the previous map's position) and never decreases,
  // so a death that returns Lani to a checkpoint keeps the ground already gained.
  function routeFraction(level) {
    var route = session.route;
    if (route.map !== level) {
      route.map = level;
      route.start = null;
      route.fraction = 0;
    }
    var goal = ROUTE_GOALS[level];
    if (!goal || (gpio(PIN_PUBLISHED) & 0xff) !== PUBLISHED) return route.fraction;
    var pos = (goal[0] === "x" ? pinS16(10) : pinS16(12)) / 8;
    if (route.start === null || pos < route.start) route.start = pos;
    var span = goal[1] - route.start;
    if (span > 0) {
      route.fraction = Math.max(route.fraction, Math.min(ROUTE_CAP, Math.max(0, (pos - route.start) / span)));
    }
    return route.fraction;
  }

  function normalizeOptions(options) {
    var opts = options || {};
    var seed = Number(opts.seed);
    return {
      seed: isFinite(seed) ? seed >>> 0 : null,
      level: opts.level === undefined || opts.level === null ? null : Number(opts.level),
      difficulty: opts.difficulty === undefined || opts.difficulty === null
        ? null
        : String(opts.difficulty)
    };
  }

  function beginEpisode(options) {
    var accepted = normalizeOptions(options);
    var notes = [];
    if (accepted.seed !== null) notes.push("seed_not_supported");
    if (accepted.level !== null) notes.push("level_select_not_supported");
    if (accepted.difficulty !== null) notes.push("difficulty_not_supported");
    session.requestedLevel = accepted.level;
    session.requestedDifficulty = accepted.difficulty;
    session.episodeStartMs = Date.now();
    session.lastPlayMs = null;
    session.survivedMs = 0;
    session.episodeCount += 1;
    session.route = { map: 0, start: null, fraction: 0 };
    return {
      accepted: accepted,
      applied: { seed: null, level: null, difficulty: null },
      notes: notes
    };
  }

  function hideShellUi() {
    var ids = ["p8_start_button", "p8_menu_buttons", "p8_menu_buttons_touch"];
    for (var i = 0; i < ids.length; i++) {
      var el = document.getElementById(ids[i]);
      if (el) el.style.display = "none";
    }
  }

  function startCart() {
    try {
      if (typeof p8_create_audio_context === "function") p8_create_audio_context();
    } catch (err) {}
    try {
      if (typeof p8_run_cart === "function") p8_run_cart();
    } catch (err2) {}
    var box = document.getElementById("p8_container");
    if (box && !window.p8_is_running) {
      try {
        box.click();
      } catch (err3) {}
    }
    hideShellUi();
  }

  function autostart() {
    if (session.autostartArmed) {
      if (!window.p8_is_running) startCart();
      hideShellUi();
      return;
    }
    session.autostartArmed = true;
    startCart();
  }

  setTimeout(autostart, 80);
  setTimeout(autostart, 400);
  setTimeout(autostart, 1200);

  window.gameAPI = {
    version: "2.0",
    gameIdMarker: GAME_ID,
    capabilities: capabilities,

    init: async function (config) {
      var episode = beginEpisode(config || null);
      session.autostartArmed = false;
      autostart();
      return {
        ok: true,
        accepted: episode.accepted,
        applied: episode.applied,
        notes: episode.notes.concat(["local_snapshot", "starts_in_first_map_skip_menu"])
      };
    },

    getState: function () {
      var now = Date.now();
      autostart();
      var running = !!window.p8_is_running;
      var paused = !!(window.pico8_state && window.pico8_state.is_paused) || document.hidden;
      var berries = gpio(PIN_BERRIES);
      var level = gpio(PIN_LEVEL);
      var seconds = gpio(PIN_TIME_LO) + gpio(PIN_TIME_HI) * 256;
      var hook = gpio(PIN_HOOK) > 0 ? 1 : 0;
      var inPlay = running && level > 0;
      var routeProgress = level > 0 ? level + routeFraction(level) : 0;

      if (inPlay && !paused) {
        if (session.lastPlayMs !== null) {
          session.survivedMs += Math.max(0, now - session.lastPlayMs);
        }
        session.lastPlayMs = now;
      } else {
        session.lastPlayMs = null;
      }
      var survivedS = session.survivedMs / 1000;

      var status = "playing";
      if (!running || !inPlay) status = "loading";
      else if (paused) status = "paused";

      return {
        schemaVersion: "2.0",
        gameId: GAME_ID,
        seed: session.seed,
        timestampMs: now,
        gameTimeMs: Math.max(0, now - session.episodeStartMs),
        status: status,
        is_actionable: inPlay && !paused,
        terminal: { isTerminal: false, outcome: null, reason: null },
        game_state: {
          score: level,
          level: level,
          player: {
            berries: berries,
            level: level,
            hook: hook
          },
          environment: {
            seconds: seconds,
            survived_s: survivedS,
            paused: !!paused
          },
          route_progress: routeProgress,
          completion_progress: Math.max(0, Math.min(1, (routeProgress - 1) / 7))
        },
        metrics: {
          primary_score: level,
          berries: berries,
          level: level,
          route_progress: routeProgress,
          hook: hook,
          seconds: seconds,
          survived_s: survivedS
        },
        debug: {
          started: inPlay,
          running: running,
          episode_count: session.episodeCount,
          last_reset_method: session.lastResetMethod,
          delivery: "local_snapshot"
        }
      };
    },

    reset: async function (options) {
      var episode = beginEpisode(options || null);
      session.lastResetMethod = "reload";
      window.location.reload();
      return {
        ok: true,
        method: "reload",
        accepted: episode.accepted,
        applied: episode.applied,
        notes: episode.notes
      };
    }
  };
})();
