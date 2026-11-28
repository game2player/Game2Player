/* A Dance of Fire and Ice (web demo v0.4.2), Unity 2018.3.0b12 / IL2CPP metadata v24, 32-bit.
 * All gameplay values are engine_hook reads of live IL2CPP objects through the shared
 * Unity bridge. No pixels, saves or debug switches are used.
 *
 * TypeInfo slots come from this build's Il2CppDumper symbols; a class pointer is
 * ptr(slot), its static fields are ptr(class + 92).
 *   scrController  statics: deaths@16 (cumulative misses), _instance@20
 *                  instance: _stateMachine@12, caption@40, currentseqID@112
 *   StateEngine    currentState@16 -> StateMapping.state@8 -> boxed scrController.States@8
 *                  (0 Start, 1 Countdown, 2 Checkpoint, 3 PlayerControl, 4 Fail, 5 Fail2, 6 Won)
 *   scrLevelMaker  statics: _instance@0; instance: listFloors@16 (List: items@8, size@12)
 * Tasks start with SceneManager.LoadScene(int) and are read back through the active
 * scene's build index, the level caption and its floor count.
 * WASM table indices are signature-local; Module.dynCall_<sig> adds the table base.
 */
(function () {
  'use strict';
  const ID = '167_a-dance-of-fire-and-ice';
  const PROFILE = {schema_version:1,game_id:ID,runtime:{pointer_size:4,class_static_fields_offset:92,allow_method_calls:false},state:{fields:{'game_state.native.total_misses':{kind:'memory',type:'i32',address:{kind:'chain',base:{kind:'static_fields',type_info:2063624},steps:[{offset:16}]}}}}};
  const TI = {controller: 2063624, levelMaker: 2064192};
  const CALIBRATION_SCENE = 19;
  const STATES = ['start', 'countdown', 'checkpoint', 'playing', 'failed', 'failed', 'won'];
  const TASKS = {
    '167_01': {scene: 2,  caption: '1-1 Playing it Straight', floors: 10},
    '167_02': {scene: 3,  caption: '1-2 S-Bend', floors: 18},
    '167_03': {scene: 8,  caption: '1-X A Dance of Fire and Ice', floors: 168},
    '167_04': {scene: 9,  caption: '2-1 North and South are Always Off Beat', floors: 14},
    '167_05': {scene: 11, caption: '2-3 Upbeat', floors: 15},
    '167_06': {scene: 12, caption: '2-X Offbeats', floors: 146},
    '167_07': {scene: 13, caption: '3-1 Three Point Turns', floors: 18},
    '167_08': {scene: 18, caption: '3-X THE WIND-UP', floors: 193}
  };
  const taskId = new URLSearchParams(location.search).get('task') || '167_01';
  const task = TASKS[taskId];
  let bridge = window.__unityBridge, module = null, starting = null;
  let armed = false, error = null, startupStep = 'Unity runtime';
  let startMisses = 0, bestFloor = 0, won = false, sample = {}, lastFloor = 0, armedAt = 0;

  const ptr = a => bridge.read('ptr', a);
  const int = a => bridge.read('i32', a);
  const str = a => a ? bridge.readIl2CppString(a, 256) : '';
  const call = (sig, index, ...args) => module['dynCall_' + sig](index, ...args);
  const klass = slot => ptr(slot);
  const statics = slot => { const k = klass(slot); return k ? ptr(k + 92) : 0; };
  const sceneHandle = () => call('ii', 1082, 0);                        // SceneManager.GetActiveScene
  const sceneIndex = h => call('iii', 5240, h, 0);                      // Scene.GetBuildIndexInternal
  const loadScene = index => call('vii', 2897, index, 0);               // SceneManager.LoadScene(int)
  const delay = ms => new Promise(resolve => (window.__realSetTimeout || setTimeout)(resolve, ms));

  async function until(test, label, timeoutMs = 45000) {
    startupStep = label;
    const t0 = performance.now();
    while (performance.now() - t0 < timeoutMs) {
      let result = false;
      try { result = test(); } catch (_) { result = false; }
      if (result) return result;
      await delay(50);
    }
    throw new Error('Timed out: ' + label);
  }

  // Live objects carry their class pointer at +0; freed scene objects fail this guard.
  function live(obj, slot) { return !!obj && ptr(obj) === klass(slot); }

  function read() {
    const s = statics(TI.controller);
    const c = s ? ptr(s + 20) : 0;
    if (!live(c, TI.controller)) throw new Error('scrController not live');
    const engine = ptr(c + 12), mapping = engine ? ptr(engine + 16) : 0;
    const boxed = mapping ? ptr(mapping + 8) : 0;
    const state = boxed ? int(boxed + 8) : -1;
    if (state < 0 || state > 6) throw new Error('Invalid controller state ' + state);
    const ls = statics(TI.levelMaker), lm = ls ? ptr(ls) : 0;
    let floors = 0;
    if (live(lm, TI.levelMaker)) {
      const list = ptr(lm + 16);
      floors = list ? int(list + 12) : 0;
    }
    return {controller: c, state, caption: str(ptr(c + 40)), floor: int(c + 112),
            floors, misses: int(s + 16)};
  }

  function update() {
    const r = read();
    const onLevel = r.caption === task.caption && r.floors === lastFloor + 1;
    if (onLevel) {
      if (r.state === 6) won = true;
      // currentseqID reaches the portal floor a frame before the Won state; the
      // last floor only counts once the level is actually won.
      bestFloor = Math.max(bestFloor, Math.min(r.floor, lastFloor - 1));
    }
    if (won) bestFloor = lastFloor;
    sample = {level: r.caption, on_task_level: onLevel, attempt_state: STATES[r.state],
              native_state: r.state, current_floor: r.floor, total_floors: r.floors,
              misses: Math.max(0, r.misses - startMisses)};
  }

  function renderHud() {
    const hud = document.getElementById('task-hud'), loading = document.getElementById('loading');
    if (!hud) return;
    if (!armed) { hud.textContent = error ? 'Error: ' + error : ''; return; }
    if (loading) loading.style.display = 'none';
    const where = won ? 'Level cleared!' : 'Tile ' + (sample.current_floor ?? 0) + ' / ' + lastFloor;
    // The game ignores input during its Fail animation (4); only Fail2 (5) restarts on a press.
    const retry = sample.native_state === 5 ? '  —  missed: press Space to retry'
      : sample.native_state === 4 ? '  —  missed' : '';
    hud.textContent = where + retry;
  }

  function state() {
    try { if (armed) { update(); error = null; } } catch (e) { error = e.message; }
    renderHud();
    const pct = lastFloor ? Math.round(10000 * bestFloor / lastFloor) / 100 : 0;
    return {
      schemaVersion: '1.0', gameId: ID, timestampMs: Date.now(),
      gameTimeMs: armed ? Math.max(0, performance.now() - armedAt) : 0,
      status: error ? 'error' : !armed ? 'loading' : won ? 'success' : 'playing',
      is_actionable: armed && !error,
      terminal: {isTerminal: won, outcome: won ? 'success' : null},
      game_state: {task: taskId, start_verified: armed, task_complete: won, ...sample,
                   error, startup_step: startupStep},
      metrics: {level_progress_pct: pct, best_floor: bestFloor, last_floor: lastFloor},
      provenance: {game_state: 'engine_hook', metrics: 'engine_hook'}
    };
  }
  async function begin(reset) {
    if (starting) return starting;
    if (armed && !reset) return true;
    starting = (async () => {
      armed = false; error = null;
      if (!task) throw new Error('Unknown task ' + taskId);
      await until(() => window.gameInstance && window.gameInstance.Module &&
        window.gameInstance.Module.calledRun && bridge.status().hasMemory, 'Unity runtime');
      module = window.gameInstance.Module;
      await until(() => read().controller, 'first scene');
      // A fresh boot passes through the hub (0) and then loads the calibration screen
      // (19) by itself; a task scene loaded before that would be replaced by it.
      if (!reset) await until(() => sceneIndex(sceneHandle()) === CALIBRATION_SCENE, 'boot calibration screen', 20000)
        .catch(() => {});
      if (window.__resetRandomSeed) window.__resetRandomSeed();
      won = false; bestFloor = 0; sample = {};
      let r = null;
      for (let attempt = 1; attempt <= 3 && !r; attempt++) {
        const previous = sceneHandle();
        loadScene(task.scene);
        const index = await until(() => { const h = sceneHandle(); return h !== previous ? sceneIndex(h) + 1 : 0; },
          'task scene') - 1;
        if (index !== task.scene) continue;
        r = await until(() => {
          const v = read();
          if (sceneIndex(sceneHandle()) !== task.scene) return {replaced: true};
          return v.caption === task.caption && v.floors > 1 && (!task.floors || v.floors === task.floors) &&
            v.floor === 0 && v.state <= 3 ? v : false;
        }, 'task level');
        if (r.replaced) r = null;
      }
      if (!r) throw new Error('Task scene ' + task.scene + ' did not stay loaded');
      lastFloor = r.floors - 1; startMisses = r.misses;
      const canvas = document.querySelector('#gameContainer canvas');
      if (canvas) { canvas.tabIndex = 0; canvas.focus(); }
      armedAt = performance.now(); startupStep = 'ready'; armed = true; error = null;
      update(); renderHud();
      return true;
    })().catch(e => { error = e.message; renderHud(); throw e; }).finally(() => { starting = null; });
    return starting;
  }

  // The conductor follows the Web Audio clock. The runner's inference pause freezes
  // performance.now and animation frames only, so suspend Unity's AudioContext with it.
  const contexts = [];
  const NativeAudioContext = window.AudioContext || window.webkitAudioContext;
  if (NativeAudioContext) {
    const Tracked = function (...args) { const ctx = new NativeAudioContext(...args); contexts.push(ctx); return ctx; };
    Tracked.prototype = NativeAudioContext.prototype;
    window.AudioContext = Tracked;
    if (window.webkitAudioContext) window.webkitAudioContext = Tracked;
  }
  let suspended = [];
  function wrapPause() {
    const pause = window.__pauseGame, resume = window.__resumeGame;
    if (typeof pause !== 'function' || pause.__adofai) return;
    window.__pauseGame = function () {
      suspended = contexts.filter(ctx => ctx.state === 'running');
      suspended.forEach(ctx => ctx.suspend());
      return pause.apply(this, arguments);
    };
    window.__resumeGame = function () {
      suspended.forEach(ctx => ctx.resume());
      suspended = [];
      return resume.apply(this, arguments);
    };
    window.__pauseGame.__adofai = true;
  }
  wrapPause();

  window.unityAdapter = {gameId: ID, init: () => begin(false), reset: () => begin(true), transformState: () => state()};
  if (bridge && bridge.status().profileGameId !== ID) bridge.configure(PROFILE);
  // Sample between state requests so a tile reached between polls is still credited;
  // the inference pause freezes this interval together with the game.
  setInterval(() => { if (armed) state(); }, 50);
  window.addEventListener('load', () => begin(false).catch(() => {}));
})();
