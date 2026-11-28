/* Frogfall: original GameMaker export, native room entry and event observers.
 * Loaded after Frogfall.js and before GameMaker_Init. No physics or puzzle edits.
 */
(function () {
  "use strict";

  const GAME_ID = "197_frogfall";
  const TASKS = {
    "197_01": { rooms: [7, 8, 9], hits: [1, 2, 3], stage: 0, target: 6 },
    "197_02": { rooms: [10, 11, 12], hits: [3, 3, 3], stage: 0, target: 9 },
    "197_03": { rooms: [21], hits: [6], stage: 1, target: 6 },
    "197_04": { rooms: [39], hits: [7], stage: 2, target: 7 },
    "197_05": { rooms: [29], hits: [3], stage: 3, target: 3 },
    "197_06": { rooms: [48], hits: [6], stage: 4, target: 6 }
  };
  const taskId = new URLSearchParams(location.search).get("task") || "197_01";
  const task = TASKS[taskId];
  const native = window.JSON_game;
  const objectId = name => native.GMObjects.findIndex(o => o.pName === name);
  const PLAYER = objectId("oPlayer"), GAME = objectId("oGame");
  const TRANSITION = objectId("oTransition"), SHADE = objectId("oTutoBlack");
  const delay = window.__realSetTimeout || window.setTimeout.bind(window);
  const realNow = window.__realPerfNow || performance.now.bind(performance);
  let seed = typeof window.__getRandomSeed === "function" ? window.__getRandomSeed() : 42;
  let startPromise = null, resetting = null, startupError = null;
  let run = freshRun();

  function freshRun() {
    return { ready: false, timeMs: 0, best: (task?.rooms || []).map(() => 0),
      cleared: (task?.rooms || []).map(() => false), success: false };
  }
  function instances(index) {
    if (index < 0 || !window._I1 || !window._x1) return [];
    return window.GetWithArray(index);
  }
  function player() { return instances(PLAYER)[0] || null; }
  function game() { return instances(GAME)[0] || null; }
  function room() { return window._I1 ? window.g_pBuiltIn.get_current_room() : -1; }
  function roomName() { return native.GMRooms[room()]?.pName || null; }
  function roomIndex() { return task ? task.rooms.indexOf(room()) : -1; }
  function activeRequiredRoom() {
    const index = roomIndex();
    return index >= 0 && run.cleared.slice(0, index).every(Boolean) ? index : -1;
  }
  function progress() {
    return run.best.reduce((total, best, i) => total + (run.cleared[i] ? task.hits[i] : best), 0);
  }

  // Called from named gameplay events, not a resetting counter sampled by a timer.
  // Best count is from one attempt; retries never add their hits together.
  function observeCount(p) {
    if (!run.ready || !p || p !== player()) return;
    const i = activeRequiredRoom();
    if (i < 0 || run.cleared[i]) return;
    const count = Math.max(0, Number(p.gmlcount) || 0);
    // The last unit is reserved for the native clear event, even if a caller
    // requests state in the middle of the collision that collects the final fly.
    run.best[i] = Math.max(run.best[i], Math.min(task.hits[i] - 1, count));
  }
  function observeClear(p) {
    if (!run.ready || !p || p !== player()) return;
    const i = activeRequiredRoom();
    if (i < 0 || run.cleared[i] || !p.gmlended) return;
    if (p.gmlenemies !== task.hits[i] || p.gmlcount < p.gmlenemies) return;
    run.best[i] = task.hits[i];
    run.cleared[i] = true;
    run.success = run.cleared.every(Boolean);
  }

  const replacements = new Map();
  function observe(name, after) {
    const original = window[name];
    if (typeof original !== "function") throw new Error("Missing native event: " + name);
    const wrapped = function (...args) {
      const result = original.apply(this, args);
      after(args[0]);
      return result;
    };
    window[name] = wrapped;
    replacements.set(original, wrapped);
  }
  // The original randomize uses new Date().getMilliseconds(), bypassing the
  // launcher's Math.random seed. Retain GameMaker's RNG, changing only its seed.
  const originalRandomize = window.randomize;
  window.randomize = window.randomise = function () { return window.random_set_seed(seed); };
  replacements.set(originalRandomize, window.randomize);
  observe("gml_Object_oPlayer_Collision_oEnemy", observeCount);
  observe("gml_Object_oPlayer_Step_0", observeCount);
  observe("gml_Script_scr_endstage", observeClear);
  observe("gml_Object_oGame_Step_0", function () {
    if (run.ready) run.timeMs += 1000 / window.g_pBuiltIn.get_room_speed();
    drawHud();
  });
  // GameMaker captures object event references when JSON_game is parsed.
  // Replace those references too; direct GML script calls use the globals above.
  const seen = new WeakSet(), pending = [native.GMObjects];
  while (pending.length) {
    const obj = pending.pop();
    if (!obj || typeof obj !== "object" || seen.has(obj)) continue;
    seen.add(obj);
    for (const key of Object.keys(obj)) {
      const item = obj[key];
      if (replacements.has(item)) obj[key] = replacements.get(item);
      else if (item && typeof item === "object") pending.push(item);
    }
  }

  function clearSave() {
    // Only this game's native save; never clear another environment's storage.
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith("Frogfall.0.")) localStorage.removeItem(key);
    }
  }
  async function until(condition, label) {
    const deadline = realNow() + 50000;
    while (!condition()) {
      if (realNow() > deadline) throw new Error("Timed out waiting for " + label);
      await new Promise(resolve => delay(resolve, 20));
    }
  }
  async function begin(options, restart) {
    if (!task) throw new Error("Unknown task: " + taskId);
    startupError = null;
    run = freshRun();
    if (options?.seed != null && Number.isFinite(Number(options.seed))) seed = Number(options.seed) >>> 0;
    else if (typeof window.__getRandomSeed === "function") seed = window.__getRandomSeed() >>> 0;
    clearSave();
    if (typeof window.__resetRandomSeed === "function") window.__resetRandomSeed();
    if (restart) {
      const oldGame = game();
      if (typeof window.__resumeGame === "function") window.__resumeGame();
      window.game_restart();
      await until(() => !!game() && game() !== oldGame, "native game restart");
    }
    // Wait for the authored global scripts and input initialization. The native
    // focus room follows oGame.Create; no coordinate click or menu timing needed.
    await until(() => !!game() && room() === 112, "GameMaker focus room");
    const g = game();
    window.random_set_seed(seed);
    window.audio_stop_all();
    for (let key = 0; key < 256; key++) window.keyboard_clear(key);
    g.gmlSTAGE = task.stage;
    g.gmlSPEEDRUN = false;
    g.gmlTUTO = task.rooms[0] <= 9;
    g.gmlGAME_STARTED = true;
    // Fresh native campaign values, even if the engine had cached an INI read.
    g.gmlLEVELS = [0, 0, 0, 0, 0, 0, 20];
    g.gmlFLYCOUNT = 0;
    g.gmlHITCOUNT = 0;
    window.gml_Script_scr_PALETTE(g, g, task.stage);
    const oldPlayer = player();
    window.room_goto(task.rooms[0]);
    await until(() => {
      const p = player();
      return room() === task.rooms[0] && p && p !== oldPlayer &&
        p.gmlcolor_init && p.gmlgrounded && p.alarm[5] === -1 &&
        instances(TRANSITION).length === 0 && instances(SHADE).every(o => o.image_alpha <= 0.01);
    }, "playable task room");
    const p = player();
    if (p.gmlcount !== 0 || p.gmlenemies !== task.hits[0] ||
        g.gmlSTAGE !== task.stage || g.gmlSPEEDRUN || p.gmlended) {
      throw new Error("Native task start does not match " + taskId);
    }
    // Wait for the ready scene to render, rather than accepting the room-change frame.
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    document.getElementById("canvas").focus();
    run.ready = true;
    drawHud();
    return { ok: true, task: taskId, room: roomName(), seed };
  }
  function start(options, restart) {
    startPromise = begin(options || {}, restart).catch(error => {
      startupError = error.message;
      run.ready = false;
      throw error;
    });
    return startPromise;
  }

  function readableRoom() {
    const name = roomName() || "Loading";
    if (/^rTUTO_/.test(name)) return "Tutorial " + Number(name.slice(-2));
    const p = player(), data = p?.gmlLEVELDATA;
    const area = ["", "Roof", "Swamp", "Cave", "Skate", "Mountain"][data?.[0]];
    return area ? area + " " + data[1] + " · " + String(data[2]).replace(/\n/g, " ") : name;
  }
  function drawHud() {
    const hud = document.getElementById("room-info");
    if (!hud) return;
    hud.style.visibility = run.ready ? "visible" : "hidden";
    if (!run.ready) return;
    const p = player();
    document.getElementById("room-name").textContent = readableRoom();
    const cleared = run.cleared.filter(Boolean).length;
    document.getElementById("room-hits").textContent = run.success ? "Task complete" :
      "Hits " + (p?.gmlcount ?? 0) + "/" + (p?.gmlenemies ?? 0) +
      (task.rooms.length > 1 ? " · Rooms " + cleared + "/" + task.rooms.length : "");
  }

  function getState() {
    const p = player(), g = game();
    const count = Number(p?.gmlcount) || 0, required = Number(p?.gmlenemies) || 0;
    return {
      schemaVersion: "1.0", gameId: GAME_ID, timestampMs: Date.now(), gameTimeMs: run.timeMs,
      status: startupError ? "error" : !run.ready ? "loading" : run.success ? "success" : "playing",
      is_actionable: run.ready && !startupError && (run.success ||
        !!p && !p.gmlended && p.alarm[5] === -1 && instances(TRANSITION).length === 0),
      terminal: { isTerminal: run.success, outcome: run.success ? "success" : null },
      game_state: {
        task: taskId, room: room(), room_name: roomName(),
        level_data: p ? Array.from(p.gmlLEVELDATA) : null,
        stage: g?.gmlSTAGE ?? null, normal_mode: !!g && !g.gmlSPEEDRUN,
        player: p ? { x: p.x, y: p.y, vx: p.gmlxvel_cur, vy: p.gmlyvel,
          grounded: !!p.gmlgrounded, jumped: !!p.gmljumped, skating: !!p.gmlSKATE,
          held: !!p.gmlHOLD } : null,
        attempt_hits: count, required_hits: required, room_clear: !!p?.gmlended,
        task_complete: run.success, startup_error: startupError
      },
      metrics: {
        task_progress: progress(), target: task?.target ?? 0,
        rooms_cleared: run.cleared.filter(Boolean).length,
        attempt_hits: count, required_hits: required
      },
      provenance: {
        "game_state.room": "engine_hook", "game_state.room_name": "internal",
        "game_state.level_data": "internal", "game_state.stage": "internal",
        "game_state.normal_mode": "internal", "game_state.player": "internal",
        "game_state.attempt_hits": "internal", "game_state.required_hits": "internal",
        "game_state.room_clear": "internal", "game_state.task_complete": "engine_hook",
        "metrics.task_progress": "engine_hook", "metrics.rooms_cleared": "engine_hook",
        "metrics.attempt_hits": "internal", "metrics.required_hits": "internal"
      },
      verifier: { task: taskId, required_rooms: task?.rooms || [],
        room_best_hits: run.best.slice(), room_clear_events: run.cleared.slice(), success: run.success }
    };
  }

  window.gameAPI = {
    version: "1.0",
    capabilities: { supports_seed: true, supports_level_select: true,
      supports_inplace_reset: true, provides_actionable_flag: true },
    init(options) { return startPromise || start(options, false); },
    reset(options) {
      if (resetting) return resetting;
      resetting = (startPromise || Promise.resolve()).catch(() => {}).then(() => start(options, true));
      return resetting.finally(() => { resetting = null; });
    },
    getState
  };
  // Direct browser visits and runner launches both start at the requested room.
  window.gameAPI.init().catch(error => console.error("Frogfall startup:", error));
})();
