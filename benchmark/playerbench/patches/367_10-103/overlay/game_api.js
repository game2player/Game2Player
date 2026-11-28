/* 10-103 -- Unity 2019.2.8f1, IL2CPP metadata v24, legacy UnityLoader.
 * Every gameplay value below is read from IL2CPP objects through window.__unityBridge
 * (engine_hook). Calls use the build's legacy dynCall_<sig>(tableIndex, ...) ABI; the
 * indices come from Il2CppDumper's symbols.sqlite3.
 *
 * The game is twelve scenes, Floor1..Floor12 (build index 0..11), with no menu or save.
 * Task start: SceneManager.LoadScene(floor - 1), the same call the game's own elevator
 * makes; the started floor is read back from GetActiveScene().buildIndex and the
 * player's spawn position.
 *
 * Progress on a floor is the best fraction of that floor's route (spawn -> scripted
 * checkpoints -> exit) the player has reached, scaled to 0.95; leaving the floor
 * through its exit makes it 1.0. Floor 11 adds its finale stages (radio call,
 * detonation, the final monster's health) before its scene switch.
 */
(function () {
  'use strict';
  const GAME_ID = '367_10-103';
  // floor: 1-based floor the task starts on; target: run_progress needed (run tasks).
  const TASKS = {
    '367_01': { floor: 1 }, '367_02': { floor: 2 }, '367_03': { floor: 3 },
    '367_04': { floor: 5 }, '367_05': { floor: 6 }, '367_06': { floor: 9 },
    '367_07': { floor: 10 }, '367_08': { floor: 11 },
    '367_09': { floor: 1, target: 5 }, '367_10': { floor: 1, target: 11 }
  };
  const params = new URLSearchParams(location.search);
  const taskId = TASKS[params.get('task')] ? params.get('task') : '367_01';
  const TASK = TASKS[taskId];
  const START_FLOOR = TASK.floor;

  // WASM table indices (dynCall signature, index).
  const FN = {
    loadScene: ['vii', 3521],      // SceneManager.LoadScene(int buildIndex)
    activeScene: ['ii', 1223],     // SceneManager.GetActiveScene() -> Scene{handle}
    buildIndex: ['iii', 4382],     // Scene.GetBuildIndexInternal(int handle)
    transform: ['iii', 4395],      // Component.get_transform()
    position: ['viii', 2952],      // Transform.get_position_Injected(out Vector3)
    gameObject: ['iii', 4396],     // Component.get_gameObject()
    enabled: ['iii', 4394],        // Behaviour.get_isActiveAndEnabled()
    name: ['iii', 4424],           // Object.get_name()
    time: ['fi', 72]               // Time.get_time()
  };
  const TYPE_INFO = { RadioManager: 2142376, Stress: 2142372 };
  const STATIC_FIELDS = 92, CACHED_PTR = 8, NATIVE_BACKREF = 24;
  // Field offsets from dump.cs.
  const PC = { dead: 0x50 };                       // PlayerController
  const DMG = { hp: 0x0C, maxHp: 0x2C };           // Damageable
  const ELEV = { descending: 0x0C };               // ElevatorExit
  const RADIO = { inTransmission: 0x38, requestResponse: 0x3A };
  const STRESS = { factor: 0x0C };
  const ROUTE_SHARE = 0.95;

  // Per-floor route: player spawn x, then legs in order. A leg with `done` finishes on
  // that game event (the scripted trigger the floor needs), otherwise on reaching `to`.
  // The last leg ends at the exit; the floor is finished only by actually leaving it.
  const ROUTES = {
    1: { spawn: -12, legs: [{ to: 22.78 }] },
    2: { spawn: 24.56, legs: [{ to: -11.98 }] },
    3: { spawn: -12, legs: [{ to: 46.24 }] },
    4: { spawn: 45, legs: [{ to: -12 }] },
    5: { spawn: -12, legs: [{ to: 56.06 }] },
    // The elevator beside the spawn is disabled until Trigger (1) deep in the floor.
    6: { spawn: 58, legs: [{ to: 16.13, done: 'elevator_enabled' }, { to: 57.9 }] },
    7: { spawn: -12, legs: [{ to: 22.14 }] },
    8: { spawn: 45, legs: [{ to: -12 }] },
    9: { spawn: -11.2, legs: [{ to: 45.3 }] },
    // Trigger wakes a monster on the left and arms Trigger (1); Trigger (1) wakes one on
    // the right: left, right, then left again to the elevator.
    10: { spawn: 45, legs: [{ to: 13.71, done: 'trigger:Trigger' },
      { to: 33.66, done: 'trigger:Trigger (1)' }, { to: -12 }] },
    // Trigger (2) starts the FRB radio call and releases FinalMonster.
    11: { spawn: -11.6, legs: [{ to: 48.28, done: 'radio_call' }], finale: true },
    12: { spawn: 45, legs: [] }
  };
  // Floor 11 shares: route to the call, detonation (FinalMonster becomes damageable after
  // the confirmed FRBConfirm transmission), then its health down to MonsterDead.
  const FINALE = { route: 0.5, detonated: 0.6, killed: 0.9 };

  const bridge = () => window.__unityBridge;
  const now = () => performance.now();
  let module = null, out = 0, phase = 'loading', error = null, busy = false;
  let classes = new Map(), lastPoll = -Infinity;
  let startHandle = null, startRequestedAt = 0, startAttempts = 0;
  let scene = null;          // { handle, floor, player, playerDmg, elevators, triggers, ... }
  let floorBest = {};        // floor -> best route progress (0..1)
  let floorsDescended = 0, deaths = 0, kills = 0, exitVia = null, startVerified = null;
  let gameTimeMs = 0, lastTick = 0, success = false, frozen = null, enteringFloor = null;

  function call(name, ...args) {
    const [sig, index] = FN[name];
    return module['dynCall_' + sig](index, ...args, 0);
  }
  const ptr = a => bridge().read('ptr', a);
  const f32 = a => bridge().read('f32', a);
  const u8 = a => bridge().read('u8', a);
  const round = (v, d) => Math.round(v * Math.pow(10, d)) / Math.pow(10, d);
  const clamp01 = v => Math.max(0, Math.min(1, v));
  // A live engine object has a native counterpart whose +24 points back to it; stale
  // objects from an unloaded scene fail this, and calling into them is unsafe.
  const live = a => {
    if (!(a > 65536)) return false;
    const native = ptr(a + CACHED_PTR);
    return native !== 0 && native % 4 === 0 && native < module.HEAPU8.length && ptr(native + NATIVE_BACKREF) === a;
  };

  function klass(name) {
    if (classes.has(name)) return classes.get(name);
    const found = bridge().findUnityClasses(name, { limit: 2 });
    if (found.length !== 1) return 0;
    const address = found[0].address;
    if (bridge().describeUnityClass(address).name !== name) throw new Error('class mismatch ' + name);
    classes.set(name, address);
    return address;
  }
  function liveInstances(name) {
    const k = klass(name);
    return k ? [...bridge().findUnityInstances(k, { limit: 64 })].filter(live) : [];
  }
  function staticInstance(name) {
    const k = ptr(TYPE_INFO[name]);
    const statics = k ? ptr(k + STATIC_FIELDS) : 0;
    const object = statics ? ptr(statics) : 0;
    return object && ptr(object) === k && live(object) ? object : 0;
  }
  function activeScene() {
    const handle = call('activeScene');
    return { handle, floor: call('buildIndex', handle) + 1 };
  }
  function nameOf(component) { return bridge().readIl2CppString(call('name', component)); }
  function xOf(component) {
    call('position', call('transform', component), out);
    return f32(out);
  }
  const isOn = c => live(c) && call('enabled', c) !== 0;

  // ---- scene discovery -------------------------------------------------------------

  function discover(current) {
    const players = liveInstances('PlayerController');
    if (players.length !== 1) return null;
    const player = players[0];
    const playerGo = call('gameObject', player);
    const damageables = liveInstances('Damageable').map(a => ({
      a, go: call('gameObject', a), name: nameOf(a)
    }));
    const playerDmg = damageables.find(d => d.go === playerGo);
    if (!playerDmg) return null;
    const triggers = {};
    for (const t of liveInstances('Trigger')) triggers[nameOf(t)] = t;
    const finalMonster = liveInstances('Monster').find(m => nameOf(m) === 'FinalMonster') || 0;
    const finalDmg = damageables.find(d => d.name === 'FinalMonster');
    return {
      handle: current.handle,
      floor: current.floor,
      player,
      playerDmg: playerDmg.a,
      enemies: damageables.filter(d => d !== playerDmg).map(d => ({ a: d.a, dead: false })),
      elevators: liveInstances('ElevatorExit'),
      triggers,
      triggerSeen: {},
      finalMonster,
      finalDmg: finalDmg ? finalDmg.a : 0,
      legsDone: 0,
      events: { elevator_enabled: false, radio_call: false, detonated: false, monster_dead: false },
      descending: false,
      wasDead: false,
      spawnX: round(xOf(player), 2)
    };
  }

  // ---- per-tick sampling -----------------------------------------------------------

  function legDone(leg, s, x) {
    const done = leg.done;
    if (!done) return Math.abs(x - leg.to) < 0.5 || (leg.to > leg.from ? x > leg.to : x < leg.to);
    if (done === 'elevator_enabled') return s.events.elevator_enabled;
    if (done === 'radio_call') return s.events.radio_call;
    if (done.startsWith('trigger:')) {
      const name = done.slice(8);
      const t = s.triggers[name];
      // A floor trigger deactivates its own object when it fires.
      return Boolean(t && s.triggerSeen[name] && !isOn(t));
    }
    return false;
  }

  function routeProgress(s, x) {
    const route = ROUTES[s.floor];
    if (!route || !route.legs.length) return 0;
    let from = route.spawn, total = 0;
    const legs = route.legs.map(leg => {
      const l = Object.assign({ from }, leg, { len: Math.abs(leg.to - from) });
      total += l.len; from = leg.to; return l;
    });
    while (s.legsDone < legs.length - 1 && legDone(legs[s.legsDone], s, x)) s.legsDone += 1;
    let covered = 0;
    for (let i = 0; i < s.legsDone; i++) covered += legs[i].len;
    const leg = legs[s.legsDone];
    covered += leg.len * clamp01((x - leg.from) / (leg.to - leg.from));
    return total > 0 ? clamp01(covered / total) : 0;
  }

  function sample() {
    const s = scene;
    if (!live(s.player) || !live(s.playerDmg)) return false;
    const x = xOf(s.player);
    const dead = u8(s.player + PC.dead) !== 0;
    if (dead && !s.wasDead) deaths += 1;
    s.wasDead = dead;
    const hp = f32(s.playerDmg + DMG.hp), maxHp = f32(s.playerDmg + DMG.maxHp);
    for (const e of s.enemies) {
      if (!e.dead && live(e.a) && f32(e.a + DMG.hp) <= 0) { e.dead = true; kills += 1; }
    }
    let elevatorOn = false;
    for (const el of s.elevators) {
      if (!live(el)) continue;
      if (u8(el + ELEV.descending)) s.descending = true;
      if (call('enabled', el)) elevatorOn = true;
    }
    if (elevatorOn) s.events.elevator_enabled = true;
    for (const [name, t] of Object.entries(s.triggers)) if (isOn(t)) s.triggerSeen[name] = true;
    let monsterHp = null;
    if (s.floor === 11) {
      if (s.finalMonster && isOn(s.finalMonster)) s.events.radio_call = true;
      if (s.finalDmg && live(s.finalDmg)) {
        monsterHp = Math.max(0, f32(s.finalDmg + DMG.hp));
        if (call('enabled', s.finalDmg)) s.events.detonated = true;
        if (s.events.detonated && monsterHp <= 0) s.events.monster_dead = true;
      }
    }
    let progress = ROUTE_SHARE * routeProgress(s, x);
    if (ROUTES[s.floor] && ROUTES[s.floor].finale) {
      progress = FINALE.route * routeProgress(s, x);
      if (s.events.radio_call) progress = Math.max(progress, FINALE.route);
      if (s.events.detonated) {
        const dmg = monsterHp === null ? 0 : clamp01(1 - monsterHp / 20);
        progress = Math.max(progress, FINALE.detonated + (FINALE.killed - FINALE.detonated) * dmg);
      }
      if (s.events.monster_dead) progress = Math.max(progress, FINALE.killed);
    }
    floorBest[s.floor] = Math.max(floorBest[s.floor] || 0, round(progress, 4));

    const radio = staticInstance('RadioManager');
    const stress = staticInstance('Stress');
    const route = ROUTES[s.floor];
    frozen = {
      floor: s.floor,
      player: { x: round(x, 2), hp: round(hp, 2), max_hp: round(maxHp, 2), dead },
      route: { legs_done: s.legsDone, leg_count: route ? route.legs.length : 0,
        progress: round(progress, 4), best: floorBest[s.floor] },
      elevator: { enabled: elevatorOn, descending: s.descending },
      radio: radio ? { report_prompt: u8(radio + RADIO.requestResponse) !== 0,
        in_transmission: u8(radio + RADIO.inTransmission) !== 0 } : { report_prompt: false, in_transmission: false },
      stress: stress ? round(f32(stress + STRESS.factor), 3) : 0,
      enemies_alive: s.enemies.filter(e => !e.dead && live(e.a) && call('enabled', e.a) && f32(e.a + DMG.hp) > 0).length,
      finale: s.floor === 11 ? Object.assign({ monster_hp: monsterHp === null ? null : round(monsterHp, 2) },
        { radio_call: s.events.radio_call, detonated: s.events.detonated, monster_dead: s.events.monster_dead }) : null
    };
    return true;
  }

  // ---- task start ------------------------------------------------------------------

  function requestStart() {
    startHandle = call('activeScene');
    scene = null; frozen = null;
    call('loadScene', START_FLOOR - 1);
    startRequestedAt = now(); startAttempts += 1;
    phase = 'starting';
  }

  function resetProgress() {
    floorBest = {}; floorsDescended = 0; deaths = 0; kills = 0; exitVia = null;
    startVerified = null; gameTimeMs = 0; success = false; startAttempts = 0; enteringFloor = null;
  }

  function onSceneChange(current) {
    const previous = scene;
    if (previous && current.floor === previous.floor + 1) {
      // The only ways forward are the floor's elevator and Floor 11's BombEvent.SwitchScene.
      floorBest[previous.floor] = 1;
      floorsDescended += 1;
      exitVia = previous.descending ? 'elevator' : (previous.floor === 11 ? 'switch_scene' : 'scene_change');
    } else if (previous && current.floor === previous.floor) {
      // Death retry reloads the same floor; best progress on it is kept.
    } else if (previous) {
      throw new Error('unexpected scene change from floor ' + previous.floor + ' to ' + current.floor);
    }
    scene = null;
    enteringFloor = current.floor;
    phase = 'transition';
  }

  function tick() {
    if (busy || !module || error) return;
    busy = true;
    try {
      const t = now();
      if (phase === 'boot') {
        if (t - lastPoll < 250) return;
        lastPoll = t;
        // Let the first floor run for a moment before replacing it; a LoadScene issued
        // while the boot scene is still initialising was observed to be dropped.
        try {
          if (!bridge().status().hasMemory || !staticInstance('RadioManager') || call('time') < 2) return;
          if (liveInstances('PlayerController').length !== 1) return;
        } catch (e) { return; }
        requestStart();
        return;
      }
      if (phase === 'starting' || phase === 'transition') {
        if (t - lastPoll < 250) return;
        lastPoll = t;
        const current = activeScene();
        if (phase === 'starting') {
          if (current.handle === startHandle || current.floor !== START_FLOOR) {
            if (t - startRequestedAt > 5000) {
              if (startAttempts >= 5) throw new Error('LoadScene(' + (START_FLOOR - 1) + ') did not take effect');
              requestStart();
            }
            return;
          }
        }
        const found = discover(current);
        if (!found) return;
        scene = found;
        if (phase === 'starting') {
          startVerified = current.floor === START_FLOOR && Math.abs(found.spawnX - ROUTES[START_FLOOR].spawn) < 1.5;
          if (!startVerified) throw new Error('started floor ' + current.floor + ' at x ' + found.spawnX);
          lastTick = t;
        }
        sample();
        phase = 'playing';
        const canvas = document.querySelector('#unityContainer canvas');
        if (canvas) { canvas.tabIndex = 0; canvas.focus(); }
        return;
      }
      if (phase !== 'playing') return;
      gameTimeMs += Math.max(0, t - lastTick);
      lastTick = t;
      const current = activeScene();
      if (current.handle !== scene.handle) { onSceneChange(current); return; }
      sample();
    } catch (e) {
      error = String((e && e.message) || e); phase = 'error';
    } finally { busy = false; }
  }

  // ---- public state ----------------------------------------------------------------

  function emptyState() {
    return { floor: null, player: { x: 0, hp: 0, max_hp: 0, dead: false },
      route: { legs_done: 0, leg_count: 0, progress: 0, best: 0 },
      elevator: { enabled: false, descending: false },
      radio: { report_prompt: false, in_transmission: false }, stress: 0, enemies_alive: 0, finale: null };
  }

  function transformState(state) {
    tick();
    const s = frozen || emptyState();
    // Between a floor change and the new floor's discovery, count the floor being
    // entered (no progress yet), not the one just left.
    const currentFloor = scene ? scene.floor : (enteringFloor || s.floor);
    const currentBest = currentFloor ? (floorBest[currentFloor] || 0) : 0;
    const floorRouteProgress = round(floorBest[START_FLOOR] || 0, 4);
    const runProgress = round(Math.min(11, floorsDescended + (currentFloor === 12 ? 0 : currentBest)), 4);
    if (!success && phase !== 'error' && startVerified) {
      success = TASK.target ? runProgress >= TASK.target : floorsDescended >= 1;
    }
    const playing = ['playing', 'transition'].includes(phase) && startVerified === true;
    state.schemaVersion = '2.0';
    state.gameId = GAME_ID;
    state.gameTimeMs = Math.round(gameTimeMs);
    state.status = success ? 'complete' : (phase === 'playing' && s.player.dead ? 'dead' : phase);
    state.is_actionable = playing && !error;
    state.terminal = { isTerminal: success, outcome: success ? 'success' : null,
      reason: success ? (TASK.target ? 'target_floor_reached' : 'floor_exited') : null };
    state.game_state = Object.assign({}, state.game_state || {}, s, {
      floor: currentFloor,
      start_floor: START_FLOOR,
      floors_descended: floorsDescended,
      deaths,
      kills,
      exit_via: exitVia,
      floor_best: Object.assign({}, floorBest),
      variant: { task: taskId, requested_floor: START_FLOOR, start_verified: startVerified,
        matches_requested: startVerified === true }
    });
    state.metrics = {
      floor_route_progress: floorRouteProgress,
      run_progress: runProgress,
      current_floor: currentFloor || 0,
      floors_descended: floorsDescended,
      current_floor_progress: round(currentBest, 4),
      deaths,
      kills,
      player_hp: s.player.hp
    };
    state.provenance = { game_state: 'engine_hook', metrics: 'engine_hook', gameTimeMs: 'engine_hook' };
    state.debug = { phase, error, start_attempts: startAttempts, seed_supported: false,
      load_progress: window.tenOhThreeLoadProgress === undefined ? null : window.tenOhThreeLoadProgress,
      page_errors: pageErrors.slice(-3), runtime: runtimeProbe() };
    return state;
  }

  function reset() {
    if (!module) return { ok: false, reason: 'runtime not ready' };
    if (typeof window.__resetRandomSeed === 'function') window.__resetRandomSeed();
    error = null;
    resetProgress();
    try { requestStart(); } catch (e) { error = String(e.message || e); phase = 'error'; }
    return { ok: !error, method: 'load_scene', task: taskId, floor: START_FLOOR };
  }

  const pageErrors = [];
  let readyCalls = 0;
  function runtimeProbe() {
    const instance = window.unityInstance, m = instance && instance.Module;
    return { ready_calls: readyCalls, module: Boolean(m), heap: Boolean(m && m.HEAPU8),
      dyncall: Boolean(m && m.dynCall_vii), captured: Boolean(module) };
  }
  window.addEventListener('error', event => pageErrors.push(String(event.message || event.error).slice(0, 200)));
  window.addEventListener('unhandledrejection', event => pageErrors.push(String(event.reason).slice(0, 200)));

  window.unityAdapter = {
    gameId: GAME_ID,
    init() { return { ok: !error, pending: phase !== 'playing', task: taskId, floor: START_FLOOR }; },
    transformState,
    reset
  };
  // Called by UnityLoader's onsuccess, and polled as a fallback. Commit only once the
  // Emscripten runtime accepts calls: _malloc throws before it is initialised, and a
  // half-committed handle would block the later onsuccess call.
  window.tenOhThreeRuntimeReady = function (instance) {
    readyCalls += 1;
    if (module || !instance || !instance.Module) return;
    try {
      const candidate = instance.Module;
      const buffer = candidate._malloc(16);
      bridge().captureUnityInstance(instance);
      out = buffer;
      module = candidate;
      phase = 'boot';
    } catch (e) { /* runtime not initialised yet; retried on the next poll */ }
  };
  window.setInterval(() => {
    const instance = window.unityInstance;
    if (!module && instance && instance.Module && instance.Module.HEAPU8 && instance.Module.dynCall_vii) {
      window.tenOhThreeRuntimeReady(instance);
    }
    if (window.gameAPI) window.gameAPI.getState();
  }, 100);
}());
