(function () {
  "use strict";

  // Auto Necrochess, GameMaker HTML5 export 10815431. All gameplay remains in
  // the original bundle. This adapter reads authored GML values and observes
  // named native events; it never awards progress from input or elapsed time.
  const GAME_ID = "174_auto-necrochess";
  const tasks = {
    "174_01": ["castle_damage", 5],
    "174_02": ["unit_upgrade_material", 4],
    "174_03": ["spell_upgrade_material", 4],
    "174_04": ["building_upgrade_material", 4],
    "174_05": ["siege_progress", 62]
  };
  const requestedTask = new URLSearchParams(location.search).get("task");
  const task = requestedTask && tasks[requestedTask] ? requestedTask : "174_01";
  let seed = typeof window.__getRandomSeed === "function" ? window.__getRandomSeed() : 42;
  let generation = 0;
  let startPromise = null;
  let episode = null;
  const realNow = () => window.__realDateNow ? window.__realDateNow() : Date.now();
  const n = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  // _x1 is this export's object manager, used inside GetWithArray. The helper
  // exists before the asynchronous engine boot has constructed that manager.
  const instances = index => window._x1 && typeof window.GetWithArray === "function"
    ? window.GetWithArray(index) : [];
  const first = index => instances(index)[0] || null;
  const globals = () => window.global || {};
  const room = () => window.g_pBuiltIn ? n(window.g_pBuiltIn.room) : -1;

  function clearEpisode() {
    episode = {
      generation: ++generation, board: null, ready: false, startVerified: false,
      startedAt: Date.now(), clock: 0, lastAt: Date.now(),
      castleDamage: 0, kingFraction: 0, kingDead: false, nativeWin: false,
      best: { unit: 0, spell: 0, building: 0 },
      final: { unit: false, spell: false, building: false },
      kings: new WeakMap(), events: []
    };
  }
  clearEpisode();

  function record(event, details) {
    episode.events.push({ event, gameTimeMs: Math.round(episode.clock), ...details });
    if (episode.events.length > 24) episode.events.shift();
  }

  function category(piece) {
    if (!piece || piece.gmlrace !== 1 || piece.gmlmarketed || piece.gmlisextra || piece.gmlisitem) return null;
    if (piece.gmlspell) return "spell";
    if (piece.gmlbuilding) return "building";
    return "unit";
  }

  function owned() {
    // This parent accessor includes its descendants: units, books, buildings,
    // consumables and enemy spawners. Only purchased allied pieces qualify.
    return instances(131).filter(p => category(p) && !p.gmllevelup);
  }

  function isCombatKing(pawn) {
    return pawn && pawn.gmlrace === -1 && pawn.gmltype === "king" &&
      pawn.gmlbossfight === true && !pawn.gmldummy && globals().gmlpause !== undefined &&
      first(54) && first(54).gmlking === true;
  }

  function readKing(pawn, initialHealth) {
    if (!isCombatKing(pawn)) return;
    let info = episode.kings.get(pawn);
    if (!info) {
      info = { initialHealth: Math.max(1, n(initialHealth === undefined ? pawn.gmllife : initialHealth)) };
      episode.kings.set(pawn, info);
    }
    // A zero health bar is still short of the native King death event. The
    // final siege point is independently reserved for the settled win event.
    const depleted = Math.max(0, 1 - Math.max(0, n(pawn.gmllife)) / info.initialHealth);
    episode.kingFraction = Math.max(episode.kingFraction, Math.min(0.999999, depleted));
  }

  function metrics() {
    return {
      castle_damage: episode.castleDamage,
      unit_upgrade_material: episode.best.unit,
      spell_upgrade_material: episode.best.spell,
      building_upgrade_material: episode.best.building,
      king_health_depleted_fraction: episode.kingDead ? 1 : episode.kingFraction,
      king_defeated: episode.kingDead ? 1 : 0,
      siege_progress: Math.min(60, episode.castleDamage) +
        (episode.kingDead ? 1 : episode.kingFraction) + (episode.nativeWin ? 1 : 0),
      native_won: episode.nativeWin ? 1 : 0,
      round: n(globals().gmlcurrentturn),
      gold: n(globals().gmlcoins),
      player_health: n(globals().gmllife),
      castle_health: n(globals().gmlenemylife)
    };
  }

  function observe() {
    const board = first(54), camera = first(113), g = globals();
    if (!board || !camera || room() !== 3) return;
    episode.board = board;
    if (!episode.ready) {
      const viewX = window.gml_Script___view_get(board, board, 0, 0);
      const validStart = board.gmlFirstCheck === true && board.gmlstartdark === 0 &&
        board.gmltutorialstep === 100 && instances(170).length === 0 &&
        g.gmlcoins === 10 && g.gmllife === 10 && g.gmlenemylife === 60 &&
        g.gmlcurrentturn === 0 && g.gmldeck === "deck1" && camera.gmlstate === 1 &&
        Math.abs(viewX + 3024) < 0.25 && owned().length === 0;
      if (!validStart) return;
      episode.ready = true;
      episode.startVerified = true;
      episode.startedAt = episode.lastAt = Date.now();
      record("native_fresh_run_ready", { deck: g.gmldeck, gold: 10, life: 10, castle: 60 });
    }
    const now = Date.now();
    // Diagnostic game time, never task progress. Native battle pause stops it.
    if (!g.gmlpause) episode.clock += Math.max(0, now - episode.lastAt);
    episode.lastAt = now;
    const damage = Math.max(0, 60 - n(g.gmlenemylife));
    if (damage > episode.castleDamage) {
      record("castle_health_reduced", { before: episode.castleDamage, after: damage });
      episode.castleDamage = damage;
    }
    const material = { unit: new Map(), spell: new Map(), building: new Map() };
    for (const piece of owned()) {
      const kind = category(piece), name = String(piece.gmlname);
      const equivalent = piece.gmllevel >= 3 ? 4 : piece.gmllevel === 2 ? 2 : 1;
      material[kind].set(name, (material[kind].get(name) || 0) + equivalent);
    }
    for (const kind of Object.keys(material)) {
      const copies = Math.max(0, ...material[kind].values());
      const value = episode.final[kind] ? 4 : Math.min(3, copies);
      episode.best[kind] = Math.max(episode.best[kind], value);
    }
    for (const pawn of instances(76)) readKing(pawn);
    episode.kingDead = episode.kingDead || board.gmlkingisdead === true;
    // outcome is initialized to 'loss', so it alone is never a loss signal.
    episode.nativeWin = episode.nativeWin ||
      (g.gmloutcome === "win" && board.gmlkingisdead === true && g.gmllife > 0);
    updateReadout(board, camera);
  }

  function wrapScript(name, after, before) {
    const original = window[name];
    if (typeof original !== "function") throw new Error("Missing native event " + name);
    window[name] = function () {
      if (before) before.apply(null, arguments);
      const result = original.apply(this, arguments);
      after.apply(null, arguments);
      return result;
    };
  }

  // These functions are referenced by name from the original GML event bodies.
  // Observe the target after its level increment, before the source dissolves.
  wrapScript("gml_Script_EffectTypesOnMerge", function (target) {
    if (!episode.ready) return;
    const kind = category(target);
    if (kind && n(target.gmllevel) >= 3) {
      episode.final[kind] = true;
      episode.best[kind] = 4;
      record("final_" + kind + "_merge", { name: target.gmlname, level: n(target.gmllevel) });
    }
  });

  // Register initial King health after StartSummon copies the spawner's stats.
  wrapScript("gml_Script_StartSummon", function (spawner) {
    const pawn = first(spawner.gmlsum);
    readKing(pawn);
  });
  for (const name of ["gml_Script_ReloadStats", "gml_Script_ReloadStatsBasic"]) {
    wrapScript(name, function (pawn) { readKing(pawn); },
      function (pawn) { readKing(pawn); });
  }
  wrapScript("gml_Script_EffectTypesAfterDeath", function (pawn, other, type) {
    if (episode.ready && type === "king" && isCombatKing(pawn)) {
      episode.kingDead = true;
      record("combat_king_death", {});
    }
  });

  // Replace export-table callbacks BEFORE GameMaker_Init binds them. No generated
  // source patch is needed. Room2 is exactly the game's own restart/new-run path.
  const objects = window.JSON_game.GMObjects;
  const nativeStart = objects[52].CreateEvent;
  objects[52].CreateEvent = function () {
    clearEpisode();
    if (typeof window.__resetRandomSeed === "function") window.__resetRandomSeed();
    window.random_set_seed(seed);
    return nativeStart.apply(this, arguments);
  };
  const boardStep = objects[54].StepNormalEvent;
  objects[54].StepNormalEvent = function () {
    const value = boardStep.apply(this, arguments);
    observe();
    return value;
  };
  const pawnStep = objects[76].StepNormalEvent;
  objects[76].StepNormalEvent = function () {
    const value = pawnStep.apply(this, arguments);
    // Castle entry damage is applied here, not in ReloadStats.
    if (episode.ready) observe();
    return value;
  };

  function updateReadout(board, camera) {
    const element = document.getElementById("run-readout");
    if (!element) return;
    const g = globals();
    const king = instances(76).find(p => isCombatKing(p) && n(p.gmllife) > 0);
    const phase = camera.gmlstate === 1 ? "Preparation" : camera.gmlstate === 3 ? "Spy" :
      g.gmlpause ? "Battle paused" : "Battle";
    element.hidden = false;
    element.textContent = "Gold " + n(g.gmlcoins) + " · Witch " + n(g.gmllife) +
      " · Castle " + Math.max(0, n(g.gmlenemylife)) + " · Round " + (n(g.gmlcurrentturn) + 1) +
      " · " + phase + (king ? " · King health " + n(king.gmllife) : "");
  }

  async function until(predicate, label) {
    const deadline = realNow() + 50000;
    while (!predicate()) {
      if (realNow() > deadline) throw new Error("Auto Necrochess: " + label);
      await new Promise(resolve => requestAnimationFrame(resolve));
    }
  }

  async function nativeReset(options) {
    if (options && Number.isFinite(options.seed)) seed = options.seed | 0;
    // Ending room4 has neither the title nor the Board, but the engine is ready.
    await until(() => room() >= 0 && (first(57) || first(54) || room() === 4), "engine did not initialize");
    const previousGeneration = generation;
    const previousBoard = first(54);
    window.global.gmltutorial = false;
    window.audio_stop_all();
    window.room_goto(2);
    await until(() => {
      observe();
      return generation > previousGeneration && first(54) !== previousBoard && episode.ready;
    }, "fresh run did not become ready");
    return { ok: true, method: "native_room_2", applied: { seed, deck: "deck1" } };
  }

  function reset(options) {
    if (startPromise) return startPromise;
    startPromise = nativeReset(options).finally(() => { startPromise = null; });
    return startPromise;
  }

  function terminal(m) {
    if (!episode.ready) return { isTerminal: false, outcome: null };
    const [field, target] = tasks[task];
    if (m[field] >= target) return { isTerminal: true, outcome: "success", reason: task };
    if (n(globals().gmllife) <= 0 || (room() === 4 && globals().gmloutcome !== "win")) {
      return { isTerminal: true, outcome: "fail", reason: globals().gmloutcome === "tie" ? "tie" : "witch_defeated" };
    }
    // Native victory can finish the episode even if a different requested
    // milestone was missed; it must not award that milestone's score.
    if (episode.nativeWin) return { isTerminal: true, outcome: "fail", reason: "run_ended_before_milestone" };
    return { isTerminal: false, outcome: null };
  }

  window.gameAPI = {
    init: function (options) {
      if (episode.ready && (!options || options.seed === undefined || options.seed === seed)) {
        return Promise.resolve({ ok: true, applied: { seed, deck: "deck1" } });
      }
      return reset(options);
    },
    reset,
    getState: function () {
      observe();
      const g = globals(), m = metrics(), end = terminal(m);
      const camera = first(113), board = first(54);
      const phase = camera ? camera.gmlstate === 1 ? "preparation" : camera.gmlstate === 3 ? "spy" : "battle" : "loading";
      const pieces = owned().map(p => ({
        name: p.gmlname, kind: category(p), level: p.gmlspell && p.gmllevel === 5 ? 3 : n(p.gmllevel),
        lane: n(p.gmllane), space: n(p.gmlspace), attack: n(p.gmlattack), health: n(p.gmllife)
      }));
      return {
        schemaVersion: "1.0", gameId: GAME_ID, timestampMs: Date.now(),
        gameTimeMs: Math.round(episode.clock), status: !episode.ready ? "loading" : end.isTerminal ? "finished" : g.gmlpause ? "paused" : "playing",
        is_actionable: episode.ready && !end.isTerminal,
        terminal: end,
        game_state: {
          phase, deck: g.gmldeck || null, round: n(g.gmlcurrentturn) + 1,
          gold: n(g.gmlcoins), player_health: n(g.gmllife), castle_health: n(g.gmlenemylife),
          battle_paused: !!g.gmlpause, king_entered: !!(board && board.gmlking),
          king_defeated: episode.kingDead, native_outcome: episode.nativeWin ? "win" : n(g.gmllife) <= 0 ? g.gmloutcome : null,
          owned_pieces: pieces
        },
        metrics: m,
        provenance: {
          game_state: "internal", metrics: "internal",
          "debug.room": "engine_hook",
          explanation: "Authored GML fields, named merge/damage/death events, and episode high-water marks derived from those values. No visual estimates."
        },
        debug: { room: room(), task, seed, generation, start_verified: episode.startVerified, events: episode.events.slice() }
      };
    }
  };
  window.addEventListener("load", function () {
    window.gameAPI.init().catch(error => console.error(error));
  });
})();
