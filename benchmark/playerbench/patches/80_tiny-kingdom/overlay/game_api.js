/* Tiny Kingdom 0.4.10 — IL2CPP state via the repository Unity bridge.
 * ABI/offsets: Il2CppDumper symbols for build fingerprint 0721531a56c8d3d3….
 * No gameplay values are written. Startup invokes the Play button's own two
 * persistent callbacks, followed by the active welcome letter's Continue.
 */
(function installTinyKingdom() {
  'use strict';
  const ID = '80_tiny-kingdom';
  const TASKS = {
    '80_01': ['best_population', 20], '80_02': ['best_population', 50],
    '80_03': ['best_wheat_region_amount', 4], '80_04': ['best_house_region_amount', 9]
  };
  const task = new URLSearchParams(location.search).get('task') || '80_01';
  const TYPE = {TilemapController: 6440020, GameplayState: 6432104};
  const METHOD = {play: 15955, load: 16035, continue: 15949, objectName: 3303, alpha: 52374, interactable: 52376};
  const classes = new Map(), names = new Map();
  let tile = 0, gameplay = 0, ready = false, startup = null, error = null;
  let loaded = false, continued = false, startedAt = 0, failure = false;
  let latest = null;
  const best = {best_population: 0, best_wheat_region_amount: 0, best_house_region_amount: 0};
  const bridge = () => window.__unityBridge;
  const ptr = p => p ? bridge().read('ptr', p) : 0;
  const int = p => bridge().read('i32', p);
  const bool = p => bridge().read('bool', p);
  const str = p => p ? bridge().readIl2CppString(p) : '';
  function className(p) {
    if (!p) return '';
    try { return bridge().describeUnityClass(ptr(p)).name || ''; } catch (_) { return ''; }
  }
  function validClass(p, name) {
    if (!p) return false;
    try { const c = bridge().describeUnityClass(p); return c.self === p && c.name === name; }
    catch (_) { return false; }
  }
  // Explicit windows, bounded matches. Cached class metadata is revalidated.
  function scan(method, subject, options = {}) {
    const end = bridge().status().memoryBytes, results = [];
    for (let start = 1 << 20; start < end; start += 32 << 20) {
      results.push(...bridge()[method](subject, {
        ...options, start, end: Math.min(end, start + (32 << 20)), limit: 32
      }));
      if (results.length > 64) throw new Error('Ambiguous Unity discovery: ' + subject);
    }
    return results;
  }
  function klass(name) {
    let p = classes.get(name) || 0;
    if (validClass(p, name)) return p;
    p = TYPE[name] ? ptr(TYPE[name]) : 0;
    if (!validClass(p, name)) {
      const found = scan('findUnityClasses', name);
      if (found.length !== 1) return 0;
      p = found[0].address;
    }
    classes.set(name, p);
    return p;
  }
  function instances(name, options = {}) {
    const c = klass(name);
    return c ? scan('findUnityInstances', c, options) : [];
  }
  function mono(name) {
    return instances(name).filter(p => ptr(p + 8) !== 0);
  }
  function list(p, max = 4096) {
    if (!p) return [];
    const count = int(p + 12), array = ptr(p + 8);
    if (count < 0 || count > max || (count && !array)) throw new Error('Invalid game list');
    return Array.from({length: count}, (_, n) => ptr(array + 16 + n * 4));
  }
  function objectName(p) {
    if (!names.has(p)) names.set(p, str(bridge().call(METHOD.objectName, [p, 0])));
    return names.get(p);
  }
  function tileValid(p) {
    return p && className(p) === 'TilemapController' && ptr(p + 8) !== 0 &&
      className(ptr(p + 116)) === 'Resources' && className(ptr(p + 120)) === 'Deck' &&
      className(ptr(p + 124)) === 'Hand';
  }
  function resolveGame() {
    if (!tileValid(tile)) {
      tile = 0; gameplay = 0;
      const candidates = mono('TilemapController').filter(tileValid);
      if (candidates.length !== 1) return false;
      tile = candidates[0];
    }
    if (!gameplay || className(gameplay) !== 'GameplayState' || ptr(gameplay + 20) !== tile) {
      const matches = instances('GameplayState', {
        validateCachedPointer: false,
        where: [{offset: 20, type: 'ptr', equals: tile}, {offset: 48, type: 'bool', equals: true}]
      }).filter(p => ptr(p + 40));
      if (matches.length !== 1) return false;
      gameplay = matches[0];
    }
    return true;
  }
  function sample() {
    if (!tileValid(tile) || !gameplay) return null;
    const resources = ptr(tile + 116), hand = ptr(tile + 124), deck = ptr(tile + 120);
    const population = int(resources + 16), gold = int(resources + 20);
    if (population < 0 || population > 10000000 || gold < 0 || gold > 10000000)
      throw new Error('Invalid Resources values');
    const stateObject = ptr(gameplay + 40);
    // PauseState belongs to the parent Gameplay machine. The child keeps its
    // suspended IdleState/TilePlacementState while the pause menu accepts input.
    const parent = ptr(gameplay + 52);
    const parentMode = parent ? className(ptr(parent + 12)) : '';
    const mode = parentMode === 'PauseState' ? parentMode : className(stateObject);
    const cards = list(ptr(hand + 164), 64).map(card => {
      const data = ptr(card + 128);
      return {name: str(ptr(data + 16)), description: str(ptr(data + 20))};
    });
    const dictionary = ptr(tile + 100), entries = ptr(dictionary + 12);
    const count = int(dictionary + 16), regions = [];
    if (count < 0 || count > 4096) throw new Error('Invalid region count');
    // Dictionary<Guid, Region>.Entry: hash@0, next@4, Guid@8, value@24; stride28.
    for (let n = 0; n < count; n++) {
      const entry = entries + 16 + n * 28;
      if (int(entry) < 0) continue;
      const region = ptr(entry + 24);
      if (className(region) !== 'Region') continue;
      const data = ptr(region + 32), parameter = ptr(region + 36);
      if (className(parameter) !== 'RegionParameter') throw new Error('Invalid region parameter');
      const label = str(ptr(ptr(parameter + 8) + 12));
      const amount = int(parameter + 20), target = int(parameter + 12), completed = bool(parameter + 16);
      const name = objectName(data);
      const family = name === 'Field Wheat Region' ? 'wheat' : label === 'House Size' ? 'house' : 'other';
      if (amount < 0 || target < 1 || target > 100000) throw new Error('Invalid region progress');
      regions.push({name, family, parameter: label, amount, target, completed});
      const value = completed ? Math.max(amount, target) : amount;
      if (family === 'wheat') best.best_wheat_region_amount = Math.max(best.best_wheat_region_amount, Math.min(4, value));
      if (family === 'house') best.best_house_region_amount = Math.max(best.best_house_region_amount, Math.min(9, value));
    }
    best.best_population = Math.max(best.best_population, population);
    if (mode === 'GameOverState') failure = true;
    latest = {population, gold, hand: cards, deck_count: list(ptr(deck + 84)).length,
      regions, mode, demo_has_ended: bool(resources + 24)};
    return latest;
  }
  async function waitFor(check, label, deadline) {
    while (performance.now() < deadline) {
      const value = check();
      if (value) return value;
      await new Promise(resolve => setTimeout(resolve, 150));
    }
    throw new Error('Timed out waiting for ' + label);
  }
  function ensureStart() {
    if (startup) return startup;
    startup = (async () => {
      if (!TASKS[task]) throw new Error('Unknown Tiny Kingdom task: ' + task);
      const deadline = performance.now() + 55000;
      await waitFor(() => bridge() && bridge().status().hasUnityInstance, 'Unity runtime', deadline);
      const menu = await waitFor(() => {
        const main = mono('MainMenu');
        const loaders = mono('LoadScene').filter(p => str(ptr(p + 16)) === 'Gameplay' && ptr(p + 24));
        return main.length === 1 && loaders.length === 1 ? {main: main[0], loader: loaders[0]} : null;
      }, 'native Play callbacks', deadline);
      // Serialized Play button has both callbacks, in this order. Load alone
      // misses MainMenu's setup and is not equivalent to playing from the menu.
      bridge().call(METHOD.play, [menu.main, 0]);
      bridge().call(METHOD.load, [menu.loader, 0]);
      loaded = true;
      await waitFor(() => resolveGame() && className(ptr(gameplay + 40)) === 'LetterState', 'welcome state', deadline);
      const letter = ptr(gameplay + 40), popup = ptr(letter + 12);
      if (className(popup) !== 'LetterPopup') throw new Error('Invalid welcome popup');
      // CanvasGroup properties are native Unity getters, not managed fields.
      await waitFor(() => {
        const group = ptr(popup + 16);
        return group && bridge().call(METHOD.alpha, [group, 0]) >= 0.99 && bridge().call(METHOD.interactable, [group, 0]);
      }, 'welcome input readiness', deadline);
      bridge().call(METHOD.continue, [popup, 0]);
      continued = true;
      await waitFor(() => {
        const s = sample();
        return s && s.mode === 'IdleState' && s.population === 0 && s.gold === 10 &&
          s.hand.length === int(ptr(gameplay + 8) + 20) && s.hand.every(c => c.name === 'Wooden House' || c.name === 'Island') &&
          s.deck_count + s.hand.length === int(ptr(ptr(gameplay + 8) + 28) + 12) + int(ptr(gameplay + 8) + 36);
      }, 'fresh playable run', deadline);
      startedAt = performance.now();
      ready = true;
      return true;
    })().catch(e => {error = String(e.message || e); return false;});
    return startup;
  }
  window.unityAdapter = {
    init() { return ensureStart(); },
    reset() {
      ready = false;
      if (window.__resetRandomSeed) window.__resetRandomSeed();
      location.reload();
      return true;
    },
    transformState(base) {
      if (!startup) ensureStart();
      let s = latest;
      if (ready) {
        try { s = sample(); }
        catch (e) { error = String(e.message || e); ready = false; }
      }
      const [field, target] = TASKS[task] || ['best_population', 20];
      const success = ready && best[field] >= target;
      return {
        schemaVersion: '1.0', gameId: ID, timestampMs: Date.now(),
        gameTimeMs: ready ? Math.max(0, performance.now() - startedAt) : 0,
        status: error ? 'error' : success ? 'success' : failure ? 'fail' : ready ? (s.mode === 'PauseState' ? 'paused' : 'playing') : 'loading',
        is_actionable: ready && !error,
        terminal: {isTerminal: Boolean(success || failure), outcome: success ? 'success' : failure ? 'fail' : null},
        game_state: {
          population: s ? s.population : 0, gold: s ? s.gold : 0,
          hand: s ? s.hand : [], deck_count: s ? s.deck_count : 0,
          regions: s ? s.regions : [], mode: s ? s.mode : 'Loading',
          demo_has_ended: s ? s.demo_has_ended : false,
          start: {task, variant: 'standard', verified: ready, native_scene_loaded: loaded, welcome_dismissed: continued},
          instrumentation_error: error,
          provenance: {population: 'engine_hook', gold: 'engine_hook', hand: 'engine_hook',
            deck_count: 'engine_hook', regions: 'engine_hook', mode: 'engine_hook'}
        },
        metrics: {...best},
        provenance: {'game_state': 'engine_hook', 'metrics': 'engine_hook'}
      };
    }
  };
  // Sample native region values every rendered frame, including between runner
  // observations. Best values survive merging/removal but reset with the run.
  function tick() {
    if (ready && !error) {
      try { sample(); } catch (e) { error = String(e.message || e); ready = false; }
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
})();
