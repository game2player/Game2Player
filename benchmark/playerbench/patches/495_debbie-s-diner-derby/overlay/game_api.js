/* Live IL2CPP state via the repository Unity bridge. No gameplay or memory writes.
 * ABI/offsets: metadata v29, build fingerprint
 * 1770e6388d0c37f3ab48b2c13c1c607f7bae24fe9e6458c1720ba6ced18bf840.
 * CustomerState: Idle=0, Ordering=1 (requesting an order), Waiting=2,
 * Satisfied=3. The dictionary contains active customers, not decorative chairs.
 */
(function installDinerAdapter() {
  "use strict";
  const GAME_ID = "495_debbie-s-diner-derby";
  const TASKS = {
    "495_01": { level: 0, name: "New Girl", customers: 3 },
    "495_02": { level: 1, name: "Donuts", customers: 4 },
    "495_03": { level: 2, name: "Slippery When Wet", customers: 6 },
    "495_04": { level: 4, name: "Foot Traffic", customers: 7 },
    "495_05": { level: 6, name: "The Fat Rat Gang", customers: 3 },
    "495_06": { level: 10, name: "Like Waves", customers: 7 }
  };
  const METHODS = { game: 15711, transition: 15759, load: 15765, title: 15854, seed: 8072 };
  const requestedTask = new URLSearchParams(location.search).get("task") || "495_01";
  const target = TASKS[requestedTask];
  const goal = target ? 2 * target.customers + 1 : 0;
  const ROOT = { kind: "method", table_index: METHODS.game, args: [], expected_type_info: 4455288 };
  const realNow = () => (window.__realPerfNow || performance.now.bind(performance))();
  const later = (fn, ms) => (window.__realSetTimeout || window.setTimeout)(fn, ms);
  let phase = "loading", error = null, startPromise = null, bridge = null;
  let bestUnits = 0, completed = false, seed = null;

  function read(type, address) { return bridge.read(type, address); }
  function pointer(address) { return read("ptr", address); }
  function gamePointer() { return bridge.resolveAddress(ROOT); }
  function boundedCount(address, max) {
    const n = read("i32", address);
    if (n < 0 || n > max) throw new Error("Invalid native collection length");
    return n;
  }
  function listItems(list, max) {
    if (!list) throw new Error("Missing native list");
    const count = boundedCount(list + 12, max), items = pointer(list + 8);
    if (count && !items) throw new Error("Missing native array");
    return Array.from({ length: count }, (_, i) => pointer(items + 16 + i * 4));
  }
  function customers(game) {
    const dictionary = pointer(game + 80);
    if (!dictionary) throw new Error("Customers not initialized");
    const entries = pointer(dictionary + 12), count = boundedCount(dictionary + 16, 32);
    const result = [];
    for (let i = 0; i < count; i++) {
      const entry = entries + 16 + i * 16;
      if (read("i32", entry) < 0) continue; // Dictionary free slot.
      for (const customer of listItems(pointer(entry + 12), 64)) {
        const state = read("i32", customer + 28);
        if (state < 0 || state > 3) throw new Error("Invalid CustomerState");
        result.push({ wave: read("i32", customer + 24), state,
          dish: state >= 2 ? read("i32", customer + 32) : null });
      }
    }
    return result;
  }
  function live() {
    const game = gamePointer(), player = pointer(game + 20), world = pointer(game + 24);
    if (!player || !world) throw new Error("Scene not initialized");
    const total = boundedCount(game + 84, 64), people = customers(game);
    if (!total || people.length !== total) throw new Error("Customer population not initialized");
    const held = listItems(pointer(player + 88), 64).map(plate => read("i32", plate + 12));
    return {
      level: read("i32", game + 16), worldLevel: read("i32", world + 12),
      name: bridge.readIl2CppString(pointer(world + 24)), total,
      served: read("i32", game + 88), won: read("bool", game + 60),
      wave: read("i32", game + 64), elapsed: read("f32", game + 56),
      stars: read("i32", game + 76), speed: read("f32", player + 56),
      locked: read("bool", player + 60), atRest: read("bool", player + 62),
      braking: read("bool", player + 61), stunned: read("bool", player + 48),
      inKitchen: read("bool", player + 50), people, held
    };
  }
  function matches(s) {
    return Boolean(target && s && s.level === target.level && s.worldLevel === target.level
      && s.name === target.name && s.total === target.customers);
  }
  async function until(condition, description, timeout = 45000) {
    const deadline = realNow() + timeout;
    while (realNow() < deadline) {
      try { const value = condition(); if (value) return value; } catch (_) { /* scene in flight */ }
      await new Promise(resolve => later(resolve, 30));
    }
    throw new Error("Timed out waiting for " + description);
  }
  function chooseSeed(options) {
    if (Number.isFinite(options.seed)) seed = options.seed | 0;
    else if (seed === null && typeof window.__getRandomSeed === "function") seed = window.__getRandomSeed() | 0;
  }
  async function enter(options, cold) {
    if (!target) throw new Error("Unknown task: " + requestedTask);
    chooseSeed(options);
    if (typeof window.__resumeGame === "function") window.__resumeGame();
    await until(() => window.__unityBridge?.status().hasUnityInstance, "Unity runtime");
    bridge = window.__unityBridge;
    await until(() => bridge.call(METHODS.transition, [0]) && (!cold || bridge.call(METHODS.title, [0])), "native scene selector");
    window.__resetRandomSeed?.();
    if (seed !== null) bridge.call(METHODS.seed, [seed, 0]);
    // Observe a new scene root, not a stale requested-level value from before reset.
    let previous = 0;
    try { previous = gamePointer(); } catch (_) {}
    let changed = !previous;
    bridge.call(METHODS.load, [bridge.call(METHODS.transition, [0]), target.level, 0]);
    await until(() => {
      let current = 0;
      try { current = gamePointer(); } catch (_) { changed = true; return false; }
      if (current !== previous) changed = true;
      const s = live();
      return changed && matches(s) && !s.won && !s.locked && s.atRest && s.inKitchen
        && s.served === 0 && s.people.every(c => c.state < 2) && s.held.length === 0;
    }, "fresh " + target.name);
    // Scene construction/transition sounds also use Unity's global RNG. Establish
    // the gameplay sequence at the verified boundary, not at a timing-dependent
    // point inside the scene-loading coroutine.
    if (seed !== null) bridge.call(METHODS.seed, [seed, 0]);
    document.getElementById("unity-canvas")?.focus();
    phase = "ready";
    return { ok: true, task: requestedTask, level: target.level };
  }
  function start(options = {}, reset = false) {
    if (startPromise && !reset) return startPromise;
    if (phase === "starting") return startPromise;
    const cold = !bridge;
    phase = "starting"; error = null; bestUnits = 0; completed = false;
    startPromise = enter(options, cold).catch(cause => {
      phase = "error"; error = String(cause.message || cause); throw cause;
    });
    return startPromise;
  }
  function snapshot() {
    let s = null;
    try { if (bridge?.status().hasMemory) s = live(); } catch (_) {}
    const valid = matches(s), active = phase === "ready" && valid;
    let orders = 0, delivered = 0;
    if (valid) {
      orders = s.people.filter(c => c.state >= 2).length;
      delivered = s.people.filter(c => c.state === 3).length;
    }
    if (active) {
      // Win alone, or the aggregate counter alone, cannot finish a task.
      const won = s.won && delivered === target.customers && s.served === target.customers;
      completed ||= won;
      bestUnits = Math.max(bestUnits, orders + delivered + (won ? 1 : 0));
    }
    return {
      schemaVersion: "1.0", gameId: GAME_ID, timestampMs: Date.now(),
      gameTimeMs: s && Number.isFinite(s.elapsed) ? Math.max(0, s.elapsed * 1000) : 0,
      status: error ? "error" : completed ? "terminal" : active ? "playing" : "loading",
      is_actionable: active && !s.locked,
      terminal: { isTerminal: completed, outcome: completed ? "success" : null,
        reason: completed ? "shift_complete" : null },
      game_state: {
        variant: { task: requestedTask, requested_level: target?.level ?? null,
          actual_level: s?.level ?? null, world_level: s?.worldLevel ?? null, matches_requested: valid },
        level: { day: s ? s.level + 1 : null, name: s?.name ?? null, wave: s?.wave ?? null,
          customers_total: s?.total ?? 0, customers_served: s?.served ?? 0, won: s?.won ?? false,
          elapsed_seconds: s?.elapsed ?? 0, stars: s?.stars ?? 0 },
        player: { speed: s?.speed ?? 0, at_rest: s?.atRest ?? false, braking: s?.braking ?? false,
          stunned: s?.stunned ?? false, in_kitchen: s?.inKitchen ?? false, carried_dishes: s?.held ?? [] },
        customers: s ? s.people.map((c, i) => ({ id: i, wave: c.wave,
          state: ["idle", "ordering", "waiting", "satisfied"][c.state], dish: c.dish })) : []
      },
      // Retained verifier progress is separate from the live player/game fields.
      metrics: { service_units: bestUnits, service_target: goal, orders_taken: orders,
        customers_served: delivered, customers_total: s?.total ?? 0, shift_complete: completed },
      provenance: {
        "game_state.level": "engine_hook", "game_state.player": "engine_hook",
        "game_state.customers": "engine_hook", "game_state.variant.actual_level": "engine_hook",
        "game_state.variant.world_level": "engine_hook", "metrics.service_units": "engine_hook",
        "metrics.orders_taken": "engine_hook", "metrics.customers_served": "engine_hook",
        "metrics.customers_total": "engine_hook", "metrics.shift_complete": "engine_hook"
      },
      diagnostics: { phase, error }
    };
  }
  window.unityAdapter = {
    init({ options }) { return start(options); },
    reset({ options }) { return start(options, true); },
    transformState() { return snapshot(); }
  };
  // Observe persistent semantic states during native retries/menu transitions too.
  function monitor() {
    if (phase === "ready") snapshot();
    later(monitor, 50);
  }
  monitor();
}());
