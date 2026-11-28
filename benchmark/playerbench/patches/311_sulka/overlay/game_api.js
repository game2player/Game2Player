/* Sulka: native GameMaker room entry and source-backed gameplay observers.
 * Loaded after Sulka.js, before GameMaker_Init captures its object events.
 * The route metric is a spatial lower bound, not a physics solver or walkthrough.
 */
(function () {
  "use strict";

  const GAME_ID = "311_sulka";
  const TASKS = {
    "311_01": { level: 4, room: 20, name: "rTuto4", keys: 0, spawn: [48, 71] },
    "311_02": { level: 8, room: 17, name: "rViiva2", keys: 0, spawn: [32, 87] },
    "311_03": { level: 12, room: 7, name: "rViiva6", keys: 0, spawn: [32, 95] },
    "311_04": { level: 16, room: 24, name: "rKey3", keys: 3, spawn: [73, 88] },
    "311_05": { level: 20, room: 10, name: "rGrav2", keys: 2, spawn: [48, 119] }
  };
  const taskId = new URLSearchParams(location.search).get("task") || "311_01";
  const task = TASKS[taskId];
  const data = window._R2;
  const objectId = name => data._04.findIndex(o => o._X2 === name);
  const PLAYER = objectId("oPlayer"), GAME = objectId("oGame");
  const KEY = objectId("oKey"), KEY_PLACE = objectId("oKeyPlace");
  const DOOR = objectId("oDoor"), SOLID = objectId("oColli");
  const FADER = objectId("oFader"), PAUSE = objectId("oPause");
  const realNow = window.__realPerfNow || performance.now.bind(performance);
  const delay = window.__realSetTimeout || window.setTimeout.bind(window);
  const levelByName = new Map(Array.from({ length: 24 }, (_, i) =>
    [data._S7[window._ea(null, null, i)]._X2, i + 1]));
  let seed = typeof window.__getRandomSeed === "function" ? window.__getRandomSeed() : 42;
  let starting = null, resetting = null, startupError = null, graph = null;
  let run = freshRun();

  function freshRun() {
    return { ready: false, success: false, sceneDrawn: false, frames: 0, timeMs: 0,
      initialCost: null, currentCost: null, bestCost: null, pickups: 0,
      inversions: 0, respawns: 0, completionEvent: null, lastPickup: null };
  }
  function instances(index) {
    return window._qk && window._bk && index >= 0 ? window._Sa(index) : [];
  }
  function player() { return instances(PLAYER)[0] || null; }
  function game() { return instances(GAME)[0] || null; }
  function room() { return window._qk && window._6a ? window._6a._7a() : -1; }
  function roomName() { return data._S7[room()]?._X2 || null; }
  function inTaskRoom() { return !!task && room() === task.room && roomName() === task.name; }
  function uncovered() { return instances(FADER).length > 0 && instances(FADER).every(f => f._0d === 0 && f._tc === 0); }
  function playableScene() {
    const p = player();
    return inTaskRoom() && uncovered() && p && !p._Uc && p._cb === 1;
  }
  function collectedKeys() { return instances(KEY_PLACE).filter(k => k._1b === 1).length; }
  function clearSave() {
    // Only Sulka's native save. Other games can share this host in development.
    for (const key of Object.keys(localStorage)) {
      if (key.endsWith("sulka_save.ini")) localStorage.removeItem(key);
    }
  }
  clearSave();

  // This build's native RNG is separate from Math.random. Keep its algorithm and
  // seed it through random_set_seed (_AZ); randomize aliases must honor the seed.
  window._JZ = window._KZ = () => window._AZ(seed);

  const replacements = new Map();
  function observe(name, after, before) {
    const original = window[name];
    if (typeof original !== "function") throw new Error("Missing Sulka event: " + name);
    const replacement = function (...args) {
      const previous = before ? before(...args) : null;
      const result = original.apply(this, args);
      after(args, previous);
      return result;
    };
    window[name] = replacement;
    replacements.set(original, replacement);
  }
  // oPlayer Collision(oDoor): observe the authored win check, including its
  // no-remaining-keys and matching-gravity conditions. Proximity never wins.
  observe("_S4", ([p, door], previous) => {
    if (run.ready && inTaskRoom() && p === player() && !previous && p._Tc) {
      run.success = true;
      run.completionEvent = { event: "oPlayer.Collision(oDoor):win", room: room(),
        doorX: door.x, doorY: door.y, frame: run.frames };
    }
  }, p => !!p._Tc);
  observe("_Q4", ([p, key], previous) => {
    if (run.ready && inTaskRoom() && p === player() && previous === "FLOAT" && key._1b === "GET") {
      run.pickups++;
      run.lastPickup = { event: "oPlayer.Collision(oKey):FLOAT->GET", x: key.x, y: key.y, frame: run.frames };
    }
  }, (p, key) => key._1b);
  observe("_P4", ([p], previous) => {
    if (run.ready && inTaskRoom() && p === player() && previous !== p._Na) run.inversions++;
  }, p => p._Na);
  observe("_Aa", () => {
    if (run.ready && inTaskRoom()) run.respawns++;
  });
  // oGame Step: native simulation time, independent of wall time and inference.
  observe("_B5", () => {
    if (run.ready && !run.success && inTaskRoom()) {
      run.frames++;
      run.timeMs += 1000 / window._6a._lc();
    }
  });
  // oPlayer Draw occurs after movement and collision processing. Sampling here
  // avoids rewarding a position from the same frame as a lethal collision.
  observe("_N4", ([p]) => {
    if (p !== player() || !playableScene()) return;
    run.sceneDrawn = true;
    sampleProgress();
    drawHud();
  });
  // The native export captures function references during initialization.
  const seen = new WeakSet(), pending = [data._04, data._Pi];
  while (pending.length) {
    const value = pending.pop();
    if (!value || typeof value !== "object" || seen.has(value)) continue;
    seen.add(value);
    for (const key of Object.keys(value)) {
      const child = value[key];
      if (replacements.has(child)) value[key] = replacements.get(child);
      else if (child && typeof child === "object") pending.push(child);
    }
  }

  // Frozen route contract: 2px four-neighbor grid, solid AABBs expanded by 2px.
  // These bounds come from native oColli instances, not sprites or screenshots.
  function buildGraph() {
    const width = Math.ceil(window._6a._Bc / 2), height = Math.ceil(window._6a._pc / 2);
    const solids = instances(SOLID).map(b => ({ left: b._2U2 - 2, right: b._3U2 + 2,
      top: b._4U2 - 2, bottom: b._5U2 + 2 }));
    const free = new Uint8Array(width * height), cells = [];
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        if (solids.some(b => 2 * x >= b.left && 2 * x <= b.right && 2 * y >= b.top && 2 * y <= b.bottom)) continue;
        const index = y * width + x;
        free[index] = 1;
        cells.push(index);
      }
    }
    const nearest = (x, y) => {
      let best = -1, distance = Infinity;
      for (const index of cells) {
        const dx = (index % width) * 2 - x, dy = Math.floor(index / width) * 2 - y;
        const d = dx * dx + dy * dy;
        if (d < distance) { best = index; distance = d; }
      }
      return best;
    };
    const bfs = start => {
      const distances = new Int32Array(free.length).fill(-1);
      const queue = new Int32Array(free.length);
      let read = 0, write = 0;
      distances[start] = 0;
      queue[write++] = start;
      while (read < write) {
        const u = queue[read++], x = u % width, y = Math.floor(u / width);
        const neighbors = [x > 0 ? u - 1 : -1, x + 1 < width ? u + 1 : -1,
          y > 0 ? u - width : -1, y + 1 < height ? u + width : -1];
        for (const v of neighbors) {
          if (v < 0 || !free[v] || distances[v] >= 0) continue;
          distances[v] = distances[u] + 2;
          queue[write++] = v;
        }
      }
      return distances;
    };
    const door = instances(DOOR)[0];
    if (!door) throw new Error("Task room has no native exit");
    const targets = instances(KEY_PLACE).map(k => ({ x: k.x, y: k.y, key: true }));
    targets.push({ x: door.x, y: door.y - 6 * door._jc, key: false });
    for (const target of targets) {
      target.cell = nearest(target.x, target.y);
      target.distances = bfs(target.cell);
    }
    return { width, height, nearest, targets };
  }
  function routeCost() {
    const p = player();
    if (!graph || !p) return null;
    // Native gravity inversion also shifts draw_bonus by the sprite height.
    // The occupied body stays y-12..y in either orientation; inversion alone
    // must never manufacture a geometric approach to a target.
    const cell = graph.nearest(p.x, p.y - 6);
    const places = instances(KEY_PLACE);
    const remaining = graph.targets.filter(t => t.key && !places.some(k =>
      k.x === t.x && k.y === t.y && k._1b === 1));
    const exit = graph.targets[graph.targets.length - 1];
    function cheapest(from, keys) {
      if (!keys.length) return exit.distances[from] < 0 ? Infinity : exit.distances[from];
      let best = Infinity;
      for (let i = 0; i < keys.length; i++) {
        const key = keys[i], d = key.distances[from];
        if (d >= 0) best = Math.min(best, d + cheapest(key.cell, keys.filter((_, j) => i !== j)));
      }
      return best;
    }
    const cost = cheapest(cell, remaining);
    return Number.isFinite(cost) ? cost : null;
  }
  function sampleProgress() {
    const p = player();
    if (!run.ready || run.success || !playableScene() || !p || p._fb || instances(PAUSE).length ||
        p.x < 0 || p.x >= window._6a._Bc || p.y < 0 || p.y >= window._6a._pc) return;
    const cost = routeCost();
    if (cost === null) return;
    run.currentCost = cost;
    run.bestCost = Math.min(run.bestCost, cost);
  }
  function progress() {
    if (run.success) return 100;
    if (!(run.initialCost > 0) || run.bestCost === null) return 0;
    return Math.max(0, Math.min(99, 99 * (run.initialCost - run.bestCost) / run.initialCost));
  }
  function drawHud() {
    const hud = document.getElementById("level-info");
    if (!hud) return;
    hud.style.visibility = run.ready ? "visible" : "hidden";
    if (run.ready) hud.textContent = "Level " + task.level + (run.success ? " · Cleared" :
      task.keys ? " · Keys " + collectedKeys() + "/" + task.keys : " · Reach the exit");
  }
  async function until(condition, label) {
    const deadline = realNow() + 45000;
    while (!condition()) {
      if (realNow() > deadline) throw new Error("Timed out waiting for " + label);
      await new Promise(resolve => delay(resolve, 20));
    }
  }
  async function begin(options) {
    if (!task) throw new Error("Unknown Sulka task: " + taskId);
    startupError = null;
    run = freshRun();
    graph = null;
    drawHud();
    if (options?.seed != null && Number.isFinite(Number(options.seed))) seed = Number(options.seed) >>> 0;
    else if (typeof window.__getRandomSeed === "function") seed = window.__getRandomSeed() >>> 0;
    if (typeof window.__resetRandomSeed === "function") window.__resetRandomSeed();
    if (typeof window.__resumeGame === "function") window.__resumeGame();
    await until(() => game() && window._Lw && room() >= 0, "native oGame initialization");
    clearSave();
    const g = game();
    // Restore the native standard-mode globals; room creation restores every
    // player, key, gate, hazard and fade object. No completion or unlock is set.
    g._mb = 0; g.__b = 0; g._Ab = 0; g._Bb = 0; g._Xd = 0;
    g._1c = 0; g._zb = 0; g._Fb = 0; g._Eb = 0; g._Cb = 0; g._Kb = -1;
    window._0Z2();
    window._Lw._2Z2();
    window._QY2.call(window._Lw);
    window._AZ(seed);
    const previous = player();
    const selectedRoom = window._ea(g, g, task.level - 1);
    if (selectedRoom !== task.room) throw new Error("Native level mapping differs from task start");
    window._pd(selectedRoom);
    await until(() => inTaskRoom() && player() && player() !== previous && playableScene() &&
      instances(KEY).every(k => k.__d === 1) && run.sceneDrawn, "drawn playable task room");
    const p = player();
    if (p._db !== task.spawn[0] || p._eb !== task.spawn[1] || p._Na !== 1 || p._fb || p._Tc ||
        instances(KEY_PLACE).length !== task.keys || instances(KEY).length !== task.keys ||
        instances(KEY).some(k => k._1b !== "FLOAT") || collectedKeys() !== 0 || !!g._mb) {
      throw new Error("Native task initial state mismatch: " + taskId);
    }
    graph = buildGraph();
    const cost = routeCost();
    if (!(cost > 0)) throw new Error("Task spatial route is disconnected or empty");
    run.initialCost = run.currentCost = run.bestCost = cost;
    // The draw observer has seen the task; let its rendered surface reach canvas.
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    document.getElementById("canvas").focus();
    run.ready = true;
    drawHud();
    return { ok: true, task: taskId, level: task.level, room: roomName(), seed };
  }
  function start(options) {
    starting = begin(options || {}).catch(error => {
      startupError = error.message;
      run.ready = false;
      throw error;
    });
    return starting;
  }
  function getState() {
    const p = player(), door = instances(DOOR)[0];
    return {
      schemaVersion: "1.0", gameId: GAME_ID, timestampMs: Date.now(), gameTimeMs: run.timeMs,
      status: startupError ? "error" : !run.ready ? "loading" : run.success ? "success" :
        instances(PAUSE).length ? "paused" : p?._fb ? "respawning" : "playing",
      is_actionable: !startupError && run.ready && (run.success || inTaskRoom() && !!p && !p._fb &&
        (!!playableScene() || instances(PAUSE).length > 0)),
      terminal: { isTerminal: run.success, outcome: run.success ? "success" : null },
      game_state: {
        task: taskId, room: room(), room_name: roomName(), level: levelByName.get(roomName()) ?? null,
        requested_level_match: inTaskRoom(), task_complete: run.success,
        player: p ? { x: p.x, y: p.y, velocity_x: p._Mc, velocity_y: p._La,
          gravity: p._Na, dead: !!p._fb, win: !!p._Tc, tutorial: !!p._Uc } : null,
        keys: instances(KEY_PLACE).map(k => ({ x: k.x, y: k.y, collected: k._1b === 1 })),
        exit: door ? { x: door.x, y: door.y, gravity: door._jc,
          unlocked: instances(KEY).length === 0 } : null,
        paused: instances(PAUSE).length > 0, startup_error: startupError
      },
      metrics: { taskProgress: progress(), keys_collected: collectedKeys(), keys_total: instances(KEY_PLACE).length,
        gravity_inversions: run.inversions, respawns: run.respawns },
      provenance: {
        "game_state.room": "engine_hook", "game_state.room_name": "internal",
        "game_state.player": "internal", "game_state.keys": "internal", "game_state.exit": "internal",
        "game_state.task_complete": "engine_hook", "metrics.taskProgress": "engine_hook",
        "metrics.keys_collected": "internal", "metrics.keys_total": "internal",
        "metrics.gravity_inversions": "engine_hook", "metrics.respawns": "engine_hook"
      },
      // Verifier history is intentionally separate from native player state.
      verifier: { initial_route_cost: run.initialCost, current_route_cost: run.currentCost,
        best_route_cost: run.bestCost, pickup_events: run.pickups, last_pickup: run.lastPickup,
        completion_event: run.completionEvent, requested_room: task?.name ?? null, seed }
    };
  }
  window.gameAPI = {
    version: "1.0",
    capabilities: { supports_seed: true, supports_level_select: true,
      supports_inplace_reset: true, provides_actionable_flag: true },
    init(options) { return starting || start(options); },
    reset(options) {
      if (resetting) return resetting;
      resetting = (starting || Promise.resolve()).catch(() => {}).then(() => start(options));
      return resetting.finally(() => { resetting = null; });
    },
    getState
  };
  window.gameAPI.init().catch(error => console.error("Sulka startup:", error));
})();
