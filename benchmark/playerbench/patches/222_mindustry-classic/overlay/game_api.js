/* Mindustry Classic Build 40 adapter. Native bridge names are fixed to the
 * captured GWT permutation; gameplay, event accounting, and task logic live here.
 * No pixels, storage-derived scores, or spawn-time wave numbers stand in for wins.
 */
(function () {
  'use strict';
  const gameId = '222_mindustry-classic';
  const tasks = {
    '222_01': { map: 'tutorial', mode: 'waves', field: 'tutorial_drill_steps' },
    '222_02': { map: 'tutorial', mode: 'waves', field: 'tutorial_completed_steps' },
    '222_03': { map: 'grassland', mode: 'freebuild', field: 'best_stone_stock', target: 100 },
    '222_04': { map: 'grassland', mode: 'freebuild', field: 'best_steel_stock', target: 30 },
    '222_05': { map: 'grassland', mode: 'waves', field: 'waves_cleared', target: 5 },
    '222_06': { map: 'fortress', mode: 'waves', field: 'waves_cleared', target: 5 }
  };
  const taskId = new URLSearchParams(location.search).get('task') || '222_01';
  const task = tasks[taskId];
  let bridge = null, error = task ? null : 'Unknown task: ' + taskId;
  let started = false, ready = false, frames = 0, gameTimeMs = 0;
  let seed = window.__getRandomSeed ? window.__getRandomSeed() : 42;
  let tutorialComplete = false, bestTutorialStep = 0;
  let tutorialStages = [], drillTarget = 6, tutorialTarget = 36;
  let schedulingWave = null, spawningWave = null;
  let waves = [], enemyWaves = new WeakMap();
  let metrics = freshMetrics();

  function freshMetrics() {
    return { tutorial_drill_steps: 0, tutorial_completed_steps: 0,
      best_stone_stock: 0, best_steel_stock: 0, waves_cleared: 0,
      enemies_defeated: 0 };
  }
  function stageIndex(stage) { return tutorialStages.indexOf(stage); }
  function sampleInventory() {
    if (!started || !bridge || !bridge.state()) return;
    const items = bridge.state().f.a;
    metrics.best_stone_stock = Math.max(metrics.best_stone_stock, items[0] || 0);
    metrics.best_steel_stock = Math.max(metrics.best_steel_stock, items[3] || 0);
  }
  function sampleTutorial() {
    if (!started || !bridge || !bridge.world().b || bridge.world().b.e !== 'tutorial') return;
    const index = stageIndex(bridge.tutorial().d);
    if (index >= 0) bestTutorialStep = Math.max(bestTutorialStep, index);
    metrics.tutorial_drill_steps = Math.min(drillTarget, bestTutorialStep);
    // Reaching the final instruction remains below the target until complete().
    metrics.tutorial_completed_steps = tutorialComplete ? tutorialTarget : bestTutorialStep;
  }
  function finishWave(wave) {
    if (!wave || wave.cleared || !wave.scheduled || wave.pending !== 0 ||
        wave.spawned === 0 || wave.alive.size !== 0 || wave.deaths !== wave.spawned ||
        bridge.state().e) return;
    wave.cleared = true;
    metrics.waves_cleared++;
  }
  function configureTutorial() {
    // b is androidOnly, j is desktopOnly. The HTML build uses desktop input.
    tutorialStages = Array.from(bridge.stages()).filter(stage => !stage.b);
    drillTarget = tutorialStages.findIndex(stage => stage.p === 'blockInfo');
    tutorialTarget = tutorialStages.length;
    if (drillTarget < 1 || tutorialTarget < 10) throw Error('Tutorial stage map mismatch');
  }
  function maybeStart() {
    if (error || started || !bridge || frames < 1 || !bridge.ready()) return;
    try {
      configureTutorial();
      started = true;
      bridge.seed(Number(seed) >>> 0);
      bridge.nativeStart(task.map, task.mode);
    } catch (e) { error = String(e); }
  }
  function sampleReady() {
    if (!started || !bridge || !bridge.ready()) return false;
    const state = bridge.state(), world = bridge.world();
    if (!world.b || !world.a || world.b.e !== task.map || bridge.mode() !== task.mode) return false;
    if (bridge.loading() || state.j.p === 'menu') return false;
    ready = true;
    return true;
  }

  window.__mindustryAttach = function (native) {
    bridge = native;
    bridge.hook('startMap', original => function () {
      bridge.seed(Number(seed) >>> 0);
      return original.apply(this, arguments);
    });
    bridge.hook('itemAdd', original => function (inventory, item, amount) {
      const result = original.apply(this, arguments);
      if (started && inventory === bridge.state().f) sampleInventory();
      return result;
    });
    bridge.hook('tutorialMove', original => function (tutorial, forward) {
      const last = tutorial.d;
      const result = original.apply(this, arguments);
      // GWT inlined Tutorial.complete() into move(): end -> menu + reset to intro.
      if (started && forward && last.p === 'end' && tutorial.d.p === 'intro' &&
          bridge.state().j.p === 'menu' && bridge.world().b.e === 'tutorial') {
        tutorialComplete = true;
      }
      sampleTutorial();
      return result;
    });
    bridge.hook('runWave', original => function () {
      const wave = { number: bridge.state().k, pending: 0, spawned: 0,
        deaths: 0, alive: new Set(), scheduled: false, cleared: false };
      waves.push(wave);
      const previous = schedulingWave;
      schedulingWave = wave;
      try { return original.apply(this, arguments); }
      finally {
        schedulingWave = previous;
        wave.scheduled = true;
        finishWave(wave);
      }
    });
    bridge.hook('schedule', original => function (delay, callback) {
      const wave = schedulingWave;
      if (!wave) return original.apply(this, arguments);
      // runWave schedules one native callable per enemy. Track the queued work as
      // well as live entities, so an empty interval between spawns cannot clear it.
      wave.pending++;
      const wrapped = Object.create(callback);
      wrapped.Vd = function () {
        const previous = spawningWave;
        spawningWave = wave;
        try { return callback.Vd(); }
        finally { spawningWave = previous; wave.pending--; finishWave(wave); }
      };
      return original.call(this, delay, wrapped);
    });
    bridge.hook('addEntity', original => function (entity, group) {
      const result = original.apply(this, arguments);
      if (spawningWave && group === bridge.enemyGroup() && !enemyWaves.has(entity)) {
        enemyWaves.set(entity, spawningWave);
        spawningWave.alive.add(entity);
        spawningWave.spawned++;
      }
      return result;
    });
    bridge.hook('enemyDeath', original => function (enemy) {
      const wave = enemyWaves.get(enemy);
      const alive = wave && wave.alive.has(enemy) && !enemy.v;
      const result = original.apply(this, arguments);
      if (alive && enemy.v) {
        wave.alive.delete(enemy);
        wave.deaths++;
        metrics.enemies_defeated++;
        finishWave(wave);
      }
      return result;
    });
    bridge.hook('resetGame', original => function () {
      ready = false;
      metrics = freshMetrics();
      tutorialComplete = false;
      bestTutorialStep = 0;
      waves = [];
      enemyWaves = new WeakMap();
      gameTimeMs = 0;
      const result = original.apply(this, arguments);
      sampleInventory();
      return result;
    });
    bridge.hook('logicUpdate', original => function () {
      const playing = started && bridge.state() && bridge.state().j.p === 'playing';
      const result = original.apply(this, arguments);
      frames++;
      if (playing && ready) gameTimeMs += Math.max(0, Math.min(50, bridge.deltaMs()));
      maybeStart();
      if (sampleReady()) { sampleInventory(); sampleTutorial(); }
      return result;
    });
  };

  function getState() {
    if (started && bridge && bridge.ready()) { sampleInventory(); sampleTutorial(); }
    const state = bridge && bridge.state();
    const world = bridge && bridge.world();
    const player = bridge && bridge.player();
    const target = task && (task.target || (taskId === '222_01' ? drillTarget : tutorialTarget));
    const success = !!(ready && task && metrics[task.field] >= target);
    const failed = !!(state && state.e && !success);
    const modeState = state && state.j ? state.j.p : 'loading';
    return {
      schemaVersion: '2.0', gameId, timestampMs: Date.now(), gameTimeMs,
      status: error ? 'error' : success ? 'success' : failed ? 'game_over' :
        !ready ? 'loading' : modeState === 'paused' ? 'paused' : modeState === 'menu' ? 'menu' : 'playing',
      is_actionable: !!(ready && !error),
      terminal: { isTerminal: success || failed, outcome: success ? 'success' : failed ? 'fail' : null,
        reason: success ? 'task_goal_reached' : failed ? 'core_destroyed' : null },
      game_state: {
        task_id: taskId, map: world && world.b ? world.b.e : null,
        mode: state && state.i ? state.i.p : null,
        difficulty: state && state.a ? state.a.p : null,
        native_state: modeState, wave: state ? state.k : 1,
        enemies: state ? state.b : 0, game_over: !!(state && state.e),
        inventory: { stone: state ? state.f.a[0] : 0, iron: state ? state.f.a[1] : 0,
          coal: state ? state.f.a[2] : 0, steel: state ? state.f.a[3] : 0 },
        player: player ? { x: player.I, y: player.J, health: player.w } : null,
        tutorial_stage: bridge && bridge.ready() ? bridge.tutorial().d.p : null,
        tutorial_complete: tutorialComplete,
        waves_cleared: metrics.waves_cleared
      },
      metrics: Object.assign({}, metrics),
      provenance: {
        'game_state': 'internal',
        'game_state.tutorial_complete': 'engine_hook',
        'game_state.waves_cleared': 'engine_hook',
        'metrics.best_stone_stock': 'internal', 'metrics.best_steel_stock': 'internal',
        'metrics.tutorial_drill_steps': 'engine_hook',
        'metrics.tutorial_completed_steps': 'engine_hook',
        'metrics.waves_cleared': 'engine_hook', 'metrics.enemies_defeated': 'engine_hook'
      },
      debug: { error, requested_map: task ? task.map : null, requested_mode: task ? task.mode : null,
        drill_target: drillTarget, tutorial_target: tutorialTarget, first_frame: frames > 0 }
    };
  }
  function waitForStart() {
    return new Promise((resolve, reject) => {
      const poll = setInterval(() => {
        if (error) { clearInterval(poll); clearTimeout(timeout); reject(Error(error)); }
        else if (ready) { clearInterval(poll); clearTimeout(timeout); resolve({ ok: true }); }
      }, 25);
      const timeout = setTimeout(() => {
        clearInterval(poll); reject(Error('Native task start did not become ready'));
      }, 55000);
    });
  }
  window.gameAPI = {
    version: '2.0',
    capabilities: { supports_seed: true, supports_level_select: true,
      supports_inplace_reset: false, supports_reload_reset: true,
      supports_pause_detection: true, supports_menu_detection: true, provides_actionable_flag: true },
    init(options) {
      if (!started && options && Number.isFinite(options.seed)) seed = options.seed;
      maybeStart();
      return waitForStart();
    },
    getState,
    reset() {
      ready = false;
      if (window.__resetRandomSeed) window.__resetRandomSeed();
      location.reload();
      return { ok: true, method: 'reload' };
    },
    pause() { if (bridge && ready) bridge.pause(true); },
    resume() { if (bridge && ready) bridge.pause(false); }
  };
})();
