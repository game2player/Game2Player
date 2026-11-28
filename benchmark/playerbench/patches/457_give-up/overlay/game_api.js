(function () {
  "use strict";

  const GAME_ID = "457_give-up";
  const TASKS = Object.fromEntries(Array.from({length:8}, (_, i) =>
    [`457_0${i + 1}`, {start:i * 5, target:(i + 1) * 5}]));
  const taskId = new URLSearchParams(location.search).get("task") || "457_01";
  const task = TASKS[taskId];
  const realNow = () => window.__realDateNow ? window.__realDateNow() : Date.now();
  const schedule = (fn, ms) => (window.__realSetTimeout || setTimeout)(fn, ms);
  const delay = ms => new Promise(resolve => schedule(resolve, ms));
  const finite = x => Number.isFinite(x) ? x : 0;
  let player = null, snapshot = null, lastGame = null, route = null;
  let pending = null, geometryScheduled = false, ready = false, error = null;
  let outcome = null, nativeWon = false, lastExitIndex = -1, startTicks = 0;
  let pausedByRunner = false, inputStarted = false;
  let metrics = {segmentProgress:0, floorsCompleted:0, bestFloorApproach:0};

  const engine = () => player && player.ruffle();
  const call = (name, ...args) => engine().callExternalInterface(name, ...args);

  // This potential is a shortest free-space route to the real exit collision
  // rectangle, around native solid tile geometry. It measures approach, never
  // distance walked. Hazards still have their original timing and lethality.
  function buildRoute(g) {
    const cell = 10, cols = Math.ceil(g.width / cell), rows = Math.ceil(g.height / cell);
    const blocked = new Uint8Array(cols * rows);
    const distance = new Int32Array(cols * rows).fill(-1);
    const inside = (x, y, r) => x >= r.x && x < r.x + r.width &&
      y >= r.y && y < r.y + r.height;
    for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) {
      const px = (x + 0.5) * cell, py = (y + 0.5) * cell;
      if (g.solids.some(r => inside(px, py, r))) blocked[y * cols + x] = 1;
    }
    const queue = [];
    for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) {
      const i = y * cols + x;
      if (!blocked[i] && inside((x + 0.5) * cell, (y + 0.5) * cell, g.exit)) {
        distance[i] = 0;
        queue.push(i);
      }
    }
    if (!queue.length) throw new Error("Exit has no free collision-grid cell");
    for (let head = 0; head < queue.length; head++) {
      const i = queue[head], x = i % cols, y = Math.floor(i / cols);
      for (const [nx, ny] of [[x-1,y],[x+1,y],[x,y-1],[x,y+1]]) {
        if (nx < 0 || nx >= cols || ny < 0 || ny >= rows) continue;
        const n = ny * cols + nx;
        if (!blocked[n] && distance[n] < 0) {
          distance[n] = distance[i] + 1;
          queue.push(n);
        }
      }
    }
    function segmentFree(ax, ay, bx, by) {
      const steps = Math.max(1, Math.ceil(Math.hypot(bx-ax, by-ay) / 2));
      for (let k = 0; k <= steps; k++) {
        const x = ax + (bx-ax) * k / steps, y = ay + (by-ay) * k / steps;
        if (g.solids.some(r => inside(x, y, r))) return false;
      }
      return true;
    }
    function at(px, py) {
      const cx = Math.floor(px / cell), cy = Math.floor(py / cell);
      let best = Infinity;
      for (let y = cy-2; y <= cy+2; y++) for (let x = cx-2; x <= cx+2; x++) {
        if (x < 0 || x >= cols || y < 0 || y >= rows) continue;
        const i = y * cols + x, tx = (x+0.5) * cell, ty = (y+0.5) * cell;
        if (distance[i] >= 0 && segmentFree(px, py, tx, ty)) {
          best = Math.min(best, distance[i] * cell + Math.abs(px-tx) + Math.abs(py-ty));
        }
      }
      return best;
    }
    const baseline = at(g.entrance.x, g.entrance.y);
    if (!Number.isFinite(baseline) || baseline <= 0) {
      throw new Error("Entrance cannot reach exit in native collision geometry");
    }
    return {floor:g.floorIndex, baseline, at};
  }

  function requestGeometry() {
    if (geometryScheduled || !snapshot || !snapshot.loadedAssets) return;
    geometryScheduled = true;
    // Never re-enter WASM synchronously from its ExternalInterface push callback.
    schedule(() => {
      geometryScheduled = false;
      if (!engine() || !snapshot || !snapshot.loadedAssets) return;
      try {
        const g = call("giveUpGeometry");
        if (g && g.floorIndex === snapshot.floorIndex) {
          route = buildRoute(g);
          observe(snapshot);
        }
      } catch (e) {
        error = "Route instrumentation: " + e.message;
        paint();
      }
    }, 0);
  }

  function advanceMetrics() {
    if (!task) return;
    let completed = Math.min(5, Math.max(0, lastExitIndex - task.start));
    // The final room must invoke beatGame; proximity or its preliminary exit tap
    // cannot stand in for the native ending event.
    if (task.target === 40 && !nativeWon) completed = Math.min(4, completed);
    metrics.floorsCompleted = Math.max(metrics.floorsCompleted, completed);
    let fraction = 0;
    const g = lastGame;
    if (inputStarted && g && route && route.floor === g.floorIndex && g.floorIndex < task.target && g.body) {
      const remaining = route.at(g.body.x + g.body.width/2, g.body.y + g.body.height/2);
      if (Number.isFinite(remaining)) {
        // The native contact solver alternates the idle spawn by 0.6 pixels.
        // Ignore that correction rather than awarding progress for standing still.
        fraction = 0.95 * Math.max(0, Math.min(1,
          (route.baseline - remaining - 1) / Math.max(1, route.baseline - 1)));
      }
    }
    if (g && g.floorIndex >= task.start && g.floorIndex < task.target) {
      const floorCompleted = Math.max(0, g.floorIndex - task.start);
      metrics.segmentProgress = Math.max(metrics.segmentProgress, floorCompleted + fraction);
    }
    metrics.segmentProgress = Math.min(5, Math.max(metrics.segmentProgress, completed));
    metrics.bestFloorApproach = metrics.segmentProgress - Math.floor(metrics.segmentProgress);
    if (!outcome && completed >= 5) outcome = "success";
  }

  function observe(s) {
    if (!s || typeof s !== "object") return;
    snapshot = s;
    if (Number.isFinite(s.lastExitIndex)) lastExitIndex = Math.max(lastExitIndex, s.lastExitIndex);
    nativeWon = nativeWon || !!s.won;
    if (Number.isFinite(s.floorIndex)) lastGame = s;
    if (s.loadedAssets && (!route || route.floor !== s.floorIndex)) requestGeometry();
    if (ready) {
      advanceMetrics();
      if (!outcome && s.screen === "gaveUp") outcome = "fail";
    }
  }
  window.__giveUpReceive = observe;
  window.__giveUpEvent = function (event) {
    if (!event || !Number.isFinite(event.floorIndex)) return;
    if (event.type === "floorExit") lastExitIndex = Math.max(lastExitIndex, event.floorIndex);
    if (event.type === "gameComplete") nativeWon = true;
    if (ready) advanceMetrics();
  };

  function paint() {
    const loading = document.getElementById("loading");
    if (loading) {
      loading.hidden = ready && !error;
      loading.textContent = error ? "Could not start Give Up: " + error : "Loading Give Up…";
    }
  }
  function focusGame() {
    if (!player) return;
    player.tabIndex = 0;
    player.focus();
    const canvas = player.shadowRoot && player.shadowRoot.querySelector("canvas");
    if (canvas) { canvas.tabIndex = 0; canvas.focus(); }
  }
  function suspendMovie() {
    if (!engine()) return;
    if (typeof engine().suspend === "function") engine().suspend();
    else player.pause();
    const button = player.shadowRoot && player.shadowRoot.getElementById("play-button");
    if (button) button.style.display = "none";
  }
  function resumeMovie() {
    if (!engine()) return;
    if (typeof engine().resume === "function") engine().resume();
    else player.play();
    focusGame();
  }
  async function waitFor(test, name) {
    const deadline = realNow() + 15000;
    while (realNow() < deadline) {
      if (error) throw new Error(error);
      try { observe(call("giveUpState")); } catch (_) { /* SWF may still be loading. */ }
      if (test(snapshot)) return snapshot;
      await delay(20);
    }
    throw new Error("Timed out waiting for " + name);
  }
  function native(command) {
    if (call("giveUpNative", command, task.start) !== true) {
      throw new Error("Native start refused: " + command);
    }
  }
  async function boot() {
    const wasPaused = pausedByRunner;
    if (wasPaused && originalResume) originalResume();
    pausedByRunner = false;
    ready = false; error = null; outcome = null; nativeWon = false;
    inputStarted = false;
    snapshot = null; lastGame = null; route = null; lastExitIndex = -1;
    startTicks = 0;
    metrics = {segmentProgress:0, floorsCompleted:0, bestFloorApproach:0};
    paint();
    try {
      if (!task) throw new Error("Unknown task " + taskId);
      if (window.__resetRandomSeed) window.__resetRandomSeed();
      if (player) { player.remove(); player = null; }
      player = window.RufflePlayer.newest().createPlayer();
      player.id = "game-player";
      document.body.prepend(player);
      await engine().load({url:"game.swf", allowScriptAccess:true});
      await waitFor(s => s && s.preloaderReady, "the native completed preloader");
      native("boot");
      await waitFor(s => s && s.loaded, "native controller");
      native("menu");
      await waitFor(s => s && s.screen === "mainMenu" && s.menuReady, "native start menu");
      native("play");
      await waitFor(s => s && s.screen === "normalGame" && s.floorIndex === task.start &&
        s.loadedAssets && s.fadeReady && Math.abs(s.levelY) < 1 &&
        s.deaths === 0 && s.ticks > 0 && !s.paused &&
        route && route.floor === task.start, "requested playable floor and fade-in");
      startTicks = snapshot.ticks;
      ready = true;
      focusGame();
      if (wasPaused) {
        pausedByRunner = true;
        suspendMovie();
        if (originalPause) originalPause();
      }
      paint();
      return getState();
    } catch (e) {
      error = e.message;
      paint();
      throw e;
    }
  }
  function start() {
    if (!pending) pending = boot().finally(() => { pending = null; });
    return pending;
  }
  function getState() {
    if (engine()) {
      try { observe(call("giveUpState")); } catch (_) { /* Initial loading. */ }
    }
    const s = snapshot || {}, g = lastGame || {};
    return {
      schemaVersion:"1.0", gameId:GAME_ID, timestampMs:Date.now(),
      gameTimeMs:Math.max(0, finite(g.ticks) - startTicks) * 1000/30,
      status:error ? "error" : !ready ? "loading" : outcome || (g.paused ? "paused" : "playing"),
      is_actionable:ready && !error,
      terminal:{isTerminal:outcome !== null, outcome},
      game_state:{taskId, screen:s.screen || "loading", floor:finite(g.floorIndex) + 1,
        startFloor:task ? task.start + 1 : 0, targetFloor:task ? Math.min(40, task.target + 1) : 0,
        playerX:finite(g.x), playerY:finite(g.y), jumpCount:finite(g.jumpCount),
        deaths:finite(g.deaths), paused:!!g.paused, nativeGameComplete:nativeWon,
        nativeExitCount:finite(s.exits), routeReady:!!route, error},
      metrics:{...metrics},
      provenance:{"game_state.floor":"internal", "game_state.playerX":"internal",
        "game_state.playerY":"internal", "game_state.deaths":"internal",
        "game_state.jumpCount":"internal", "game_state.nativeGameComplete":"internal",
        "game_state.nativeExitCount":"internal", "metrics.segmentProgress":"internal",
        "metrics.floorsCompleted":"internal", "metrics.bestFloorApproach":"internal"}
    };
  }
  // Flash's own frame clock must also stop while the runner is thinking.
  const originalPause = window.__pauseGame, originalResume = window.__resumeGame;
  window.__pauseGame = function () {
    pausedByRunner = true;
    suspendMovie();
    return originalPause && originalPause();
  };
  window.__resumeGame = function () {
    const result = originalResume && originalResume();
    pausedByRunner = false;
    resumeMovie();
    return result;
  };
  window.gameAPI = {
    init:() => ready ? Promise.resolve(getState()) : start(),
    reset:start, getState,
    pause:() => window.__pauseGame(), resume:() => window.__resumeGame()
  };
  // Idle gravity/contact correction at the spawn is not player progress. Input
  // only enables geometric observation; it never awards progress by itself.
  document.addEventListener("keydown", event => {
    if (ready && ["ArrowLeft", "ArrowRight", "ArrowUp"].includes(event.key)) inputStarted = true;
  }, true);
  document.addEventListener("DOMContentLoaded", () => { start().catch(() => {}); });
})();

// Policy observations are deliberately separate from gameAPI and its evaluator.
// Provenance: internal (direct ActionScript display objects and collision state).
// All coordinates are native room pixels: x right, y down; room size 900 x 400.
(function () {
  "use strict";

  const empty = () => ({
    visible_terrain: null,
    visible_hazards: null,
    player_collision_contact: null
  });
  const terrainTypes = {solid: "solid_tile", revealed: "revealed_tile", falling: "falling_block"};
  const hazardTypes = {
    spike: "spike", saw: "saw", ground_saw: "ground_saw", moving_saw: "moving_saw",
    falling_spike: "falling_spike", laser_cannon: "laser_cannon",
    rocket_cannon: "rocket_cannon", laser: "laser", projectile: "projectile"
  };

  function observations(raw) {
    const terrain = raw.terrain.map(t => ({
      id: t.id,
      type: terrainTypes[t.kind],
      // Collision uses a native fixed-size grid, not the bevel artwork's bounds.
      // During a block's return, lookup stays at its grid slot while resolution
      // uses its current position. Both describe present collision behavior.
      collision_bounds: t.bounds,
      collision_grid_bounds: t.gridBounds,
      collidable: t.collidable,
      collision_mode: t.collisionType === 2 ? "one_way" : "solid",
      provenance: "internal"
    }));
    const beams = new Set(raw.hazards.filter(h => h.kind === "laser").map(h => h.id));
    const hazards = raw.hazards.map(h => {
      const result = {
        id: h.id,
        type: hazardTypes[h.kind],
        visible_bounds: h.bounds,
        lethal_now: h.lethal,
        lethal_geometry: null,
        player_collision_shape: h.target === "none" ? null :
          h.target === "saw_point" ? "saw_test_point" : h.target,
        activity: h.kind.endsWith("cannon") ? (beams.has(h.id + "/beam") ? "firing" : h.warningVisible ? "aiming" : "visible") :
          h.kind === "laser" ? "firing" : h.kind === "projectile" ? "spawned" : "visible",
        provenance: "internal"
      };
      if (h.lethalBounds) result.lethal_geometry = {kind: "rectangle", bounds: h.lethalBounds};
      if (h.circle) result.lethal_geometry = {kind: "circle", ...h.circle};
      if (h.segment) {
        // The drawn beam begins at the muzzle, but the actual lethal test samples
        // points from step 3 onward. Do not turn its random stroke width into a
        // collision width, or report the unseen ray used to acquire a target.
        result.visible_segment = h.segment;
        result.lethal_geometry = {
          kind: "sampled_segment",
          start: {x: h.segment.x1 + 3 * h.sampleStep.x, y: h.segment.y1 + 3 * h.sampleStep.y},
          end: {x: h.segment.x2, y: h.segment.y2},
          step: h.sampleStep
        };
      }
      return result;
    });

    const p = raw.contact;
    const grounded = p.nativeGrounded && p.verticalPhase === 0;
    // This is the source's jump predicate, not 2 minus jumpCount: walking off a
    // ledge spends the ground jump. A held Up must be released before a new edge.
    const jumpCount = p.verticalPhase > 0 && p.jumpCount === 0 ? 1 : p.jumpCount;
    const groundJump = grounded && jumpCount === 0;
    const jumpAvailable = groundJump || jumpCount === 1;
    // The solver resolves the player's feet to a tile's top, with a native idle
    // correction of 0.6px. Only already-observed collidable supports are named.
    const footY = p.body.y + p.body.height;
    const supports = terrain.filter(t => t.collidable &&
      Math.abs(footY - t.collision_bounds.y) <= 1 &&
      p.body.x + p.body.width > t.collision_bounds.x &&
      p.body.x < t.collision_bounds.x + t.collision_bounds.width);
    return {
      visible_terrain: terrain,
      visible_hazards: hazards,
      player_collision_contact: {
        collision_bounds: {
          body: p.body,
          spikes: p.spikes,
          projectiles_and_lasers: p.projectilesAndLasers
        },
        saw_test_point: p.sawPoint,
        grounded,
        support_ids: grounded ? supports.map(t => t.id) : [],
        jumps_available: jumpAvailable ? (groundJump ? 2 : 1) : 0,
        can_jump_now: jumpAvailable && !p.jumpHeld,
        requires_jump_release: p.jumpHeld,
        provenance: "internal"
      }
    };
  }

  // Re-resolve the movie every call: reset replaces the Ruffle player. No cached
  // room, hidden tile list, evaluator state, clock or gameplay update is accessed.
  window.__policyObservation = {
    getState() {
      const player = document.getElementById("game-player");
      if (!player || typeof player.ruffle !== "function") return empty();
      let raw;
      try {
        raw = player.ruffle().callExternalInterface("giveUpPolicySnapshot");
      } catch (_) {
        return empty(); // The new movie has not produced a playable room yet.
      }
      return raw ? observations(raw) : empty();
    }
  };
})();
