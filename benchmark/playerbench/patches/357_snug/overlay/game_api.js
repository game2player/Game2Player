/* Snug: direct bag starts and live packing state from the IL2CPP objects.
 * A task start calls the engine's own SceneManager.LoadScene(int), the call the
 * game makes between bags. Every packing value is read from the scene's Item and
 * GameManager objects (engine_hook); the adapter never writes game memory. */
(() => {
  'use strict';
  const ID = '357_snug';
  // Build scene index -> bag; item counts are the scenes' authored packing lists.
  const BAGS = {
    2: { name: 'Backpack', items: 22 },
    3: { name: 'Suitcase', items: 16 },
    4: { name: 'Sack', items: 16 },
    5: { name: 'Lumpy Bag', items: 23 },
    6: { name: 'Hiking Backpack', items: 22 },
  };
  // target: number of items packed at once; null means the whole bag (GameManager.win).
  const TASKS = {
    '357_01': { scene: 2, target: 8 },
    '357_02': { scene: 2, target: null },
    '357_03': { scene: 3, target: null },
    '357_04': { scene: 4, target: 8 },
    '357_05': { scene: 4, target: null },
    '357_06': { scene: 5, target: null },
    '357_07': { scene: 6, target: null },
  };
  // symbols.sqlite3 table indices (alias_matched) for this build.
  const NATIVE = {
    loadScene: 12832,        // SceneManager.LoadScene(int sceneBuildIndex)
    activeScene: 12824,      // SceneManager.GetActiveScene() -> Scene handle
    buildIndex: 12808,       // Scene.get_buildIndex(Scene* this)
    objectName: 1813,        // Object.get_name()
    transform: 4281,         // Component.get_transform()
    position: 12681,         // Transform.get_position_Injected(Vector3* out)
    frameCount: 1869,        // Time.get_frameCount()
  };
  const ITEM_TYPEINFO = 5080756;   // Item_TypeInfo slot
  const CLASS_STATICS = 92;
  const ITEM = { col: 0x34, dragging: 0x38, bagState: 0x88 };   // static anyItemHeld @ statics+0
  const GM_WIN = 0x10;
  const INTRO = { cam: 0x30, camEnd: 0x40 };
  const BAG_STATES = ['In', 'Out', 'Half In'];

  const task = new URLSearchParams(location.search).get('task') || '357_01';
  const spec = TASKS[task];
  const bag = spec ? BAGS[spec.scene] : null;

  let phase = 'boot', error = null, scratch = 0, fromHandle = null, boundHandle = null;
  let itemClass = 0, gmClass = 0, introClass = 0, items = [], gm = 0, intro = 0;
  let bindStartedAt = 0, lastBindAttempt = 0, readyAt = 0, everReady = false;
  let best = 0, bagComplete = false, snapshot = null, sceneIndex = -1, bootFrame = -1;

  const bridge = () => window.__unityBridge;
  const module = () => window.unityInstance && window.unityInstance.Module;
  const ptr = a => bridge().read('ptr', a);
  const u8 = a => bridge().read('u8', a);
  const f32 = a => bridge().read('f32', a);
  const call = (name, args) => bridge().call(NATIVE[name], args.concat([0]));
  const text = p => { try { return p ? bridge().readIl2CppString(p, 128) : null; } catch (_) { return null; } };
  const alive = (p, klass) => p > 1048576 && ptr(p) === klass && ptr(p + 8) !== 0;

  function memory() {
    const asm = module().asm || {};
    return asm.memory || Object.values(asm).find(v => v instanceof WebAssembly.Memory);
  }
  function activeScene() {
    if (!scratch) scratch = module().asm.malloc(32);
    const handle = call('activeScene', []);
    new Int32Array(memory().buffer)[scratch >> 2] = handle;
    return { handle, index: call('buildIndex', [scratch]) };
  }
  function uniqueClass(name) {
    const found = bridge().findUnityClasses(name, { limit: 2 });
    if (found.length !== 1) throw new Error(name + ' class is ambiguous or missing');
    return found[0].address;
  }
  function liveInstances(klass, limit) {
    return bridge().findUnityInstances(klass, { limit }).filter(p => ptr(p + 8) !== 0);
  }

  // One scan per scene load; the result must match the bag's authored packing list.
  function bind(handle) {
    itemClass = ptr(ITEM_TYPEINFO);
    if (!itemClass || ptr(itemClass + 60) !== itemClass) return false;
    if (!gmClass) gmClass = uniqueClass('GameManager');
    if (!introClass) introClass = uniqueClass('LevelIntroOutro');
    const found = liveInstances(itemClass, 64)
      .filter(p => BAG_STATES.includes(text(ptr(p + ITEM.bagState))));
    const gms = liveInstances(gmClass, 8), intros = liveInstances(introClass, 8);
    if (found.length !== bag.items || gms.length !== 1 || intros.length !== 1) return false;
    const named = found.map(p => ({ p, name: text(call('objectName', [p])) }));
    if (new Set(named.map(i => i.name)).size !== bag.items) return false;
    items = named.sort((a, b) => a.name.localeCompare(b.name));
    gm = gms[0]; intro = intros[0]; boundHandle = handle;
    return true;
  }

  // LevelIntroOutro.Start pans the camera onto the bag; play starts once it arrives.
  function cameraArrived() {
    const cam = ptr(intro + INTRO.cam);
    if (!cam) return false;
    // _Injected natives take the native object (m_CachedPtr), not the managed wrapper.
    call('position', [ptr(call('transform', [cam]) + 8), scratch + 8]);
    const end = [0, 4, 8].map(o => f32(intro + INTRO.camEnd + o));
    const now = [0, 4, 8].map(o => f32(scratch + 8 + o));
    return now.every((v, i) => Math.abs(v - end[i]) < 0.01);
  }

  function read() {
    if (!alive(gm, gmClass) || items.some(i => !alive(i.p, itemClass))) return null;
    const list = items.map(i => {
      const state = text(ptr(i.p + ITEM.bagState));
      const held = u8(i.p + ITEM.dragging) === 1;
      return { name: i.name, bag_state: state, held, packed: state === 'In' && !held };
    });
    const statics = ptr(itemClass + CLASS_STATICS);
    return {
      items: list,
      packed_count: list.filter(i => i.packed).length,
      item_held: statics ? u8(statics) === 1 : list.some(i => i.held),
      win: u8(gm + GM_WIN) === 1,
    };
  }

  function startScene() {
    fromHandle = activeScene().handle;
    boundHandle = null; items = []; gm = 0; intro = 0; snapshot = null;
    bindStartedAt = performance.now();
    call('loadScene', [spec.scene]);
    phase = 'loading';
  }

  // The scene object set changed under us (reload or replaced objects): scan again.
  function rebind() {
    fromHandle = boundHandle; boundHandle = null; snapshot = null;
    bindStartedAt = performance.now(); phase = 'loading';
  }

  function tick() {
    if (phase === 'error') return;
    try {
      if (!spec) throw new Error('Unknown task: ' + task);
      const b = bridge(), m = module();
      if (!b || !m || !m.asm || !b.status().hasTable) return;
      // Leave from the title scene, as its Play button does. Loading earlier hangs
      // the page during player start-up, and the logo scene's fade panel would
      // later load the title over the bag.
      if (phase === 'boot') {
        const frame = call('frameCount', []);
        if (bootFrame < 0) { bootFrame = frame; return; }
        if (frame - bootFrame < 5) return;
        if (activeScene().index !== 1) { bootFrame = frame; return; }
        startScene(); return;
      }
      const scene = activeScene();
      sceneIndex = scene.index;
      if (phase === 'loading') {
        if (scene.index !== spec.scene || scene.handle === fromHandle) return;
        const now = performance.now();
        if (now - lastBindAttempt < 300) return;
        lastBindAttempt = now;
        if (bind(scene.handle)) phase = 'intro';
        else if (now - bindStartedAt > 30000) throw new Error('Bag objects did not match ' + bag.name);
        return;
      }
      if (phase === 'intro') {
        if (scene.handle !== boundHandle) { rebind(); return; }
        snapshot = read();
        if (snapshot && cameraArrived()) {
          phase = 'ready'; everReady = true;
          if (!readyAt) readyAt = performance.now();
        }
        return;
      }
      // ready / away: follow the player's own scene changes (reset button, R,
      // the completion checkmark) without losing what was already achieved.
      if (scene.handle !== boundHandle) {
        if (scene.index === spec.scene) rebind();
        else { phase = 'away'; snapshot = null; }
        return;
      }
      if (phase !== 'ready') return;
      const s = read();
      if (!s) { rebind(); fromHandle = null; return; }
      snapshot = s;
      best = Math.max(best, s.packed_count);
      // GameManager.win is the game's own all-items-packed verdict for this scene.
      if (s.win) bagComplete = true;
    } catch (e) {
      error = String(e && e.message || e); phase = 'error';
      console.error('Snug adapter:', e);
    }
  }

  function taskComplete() {
    return spec ? (spec.target === null ? bagComplete : best >= spec.target) : false;
  }
  function progress() {
    if (!bag) return 0;
    if (spec.target !== null) return Math.min(best, spec.target) / spec.target;
    return bagComplete ? 1 : Math.min(best, bag.items - 1) / bag.items;
  }

  function state() {
    tick();
    const s = snapshot, done = taskComplete();
    const screen = phase === 'ready' ? (s && s.win ? 'bag_complete' : 'packing')
      : phase === 'away' ? 'other_scene' : phase === 'error' ? 'error' : 'loading';
    return {
      schemaVersion: '1.0',
      gameId: ID,
      timestampMs: Date.now(),
      gameTimeMs: readyAt ? Math.max(0, performance.now() - readyAt) : 0,
      status: phase === 'error' ? 'error' : done ? 'success' : everReady ? 'playing' : 'loading',
      is_actionable: everReady && phase !== 'error',
      terminal: { isTerminal: done, outcome: done ? 'success' : null, reason: done ? (spec.target === null ? 'bag_complete' : 'items_packed') : null },
      game_state: {
        task,
        bag: bag ? bag.name : null,
        bag_scene: spec ? spec.scene : null,
        scene_index: sceneIndex,
        screen,
        engine_hook: {
          items: s ? s.items.map(i => ({ name: i.name, bag_state: i.bag_state, held: i.held })) : [],
          packed_items: s ? s.items.filter(i => i.packed).map(i => i.name) : [],
          item_held: s ? s.item_held : false,
          win: s ? s.win : false,
        },
        start_error: error,
      },
      metrics: {
        packed_count: s ? s.packed_count : 0,
        best_packed_count: best,
        total_items: bag ? bag.items : 0,
        progress: progress(),
        bag_complete: Number(bagComplete),
        item_held: Number(Boolean(s && s.item_held)),
      },
      provenance: { 'game_state.engine_hook': 'engine_hook', metrics: 'engine_hook' },
    };
  }

  window.unityAdapter = {
    init() { tick(); return true; },
    transformState() { return state(); },
    reset() {
      if (window.__resetRandomSeed) window.__resetRandomSeed();
      best = 0; bagComplete = false; readyAt = 0; everReady = false; error = null;
      if (phase === 'boot' || !module()) return true;
      try { startScene(); } catch (e) { error = String(e && e.message || e); phase = 'error'; }
      return true;
    },
  };

  fetch('unity_profile.json').then(r => {
    if (!r.ok) throw new Error('Missing packaged Unity profile');
    return r.json();
  }).then(profile => {
    if (bridge().status().profileGameId !== ID) bridge().configure(profile);
  }).catch(e => { error = String(e); phase = 'error'; });

  setInterval(tick, 100);
})();
