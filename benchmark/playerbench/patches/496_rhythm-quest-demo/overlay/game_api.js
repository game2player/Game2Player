/* Rhythm Quest v0.43.0: thin adapter for tools/unity_webgl/bridge.js.
 * All gameplay observations use named IL2CPP objects/methods (engine_hook).
 * The song clock is diagnostic only. Progress uses checkpoints and ScoreHit's
 * native chain, with per-section best values so retries cannot farm credit.
 */
(() => {
  'use strict';
  const GAME_ID = '496_rhythm-quest-demo';
  const LEVELS = ['level1-1','level1-2','level1-3','level1-4',
    'level2-1','level2-2','level2-3','level3-1','level3-4'];
  const taskId = new URLSearchParams(location.search).get('task') || '496_01';
  const taskIndex = Number(taskId.slice(-2)) - 1;
  const level = LEVELS[taskIndex];
  const difficulty = taskIndex === 0 ? 0 : 1;
  const b = () => window.__unityBridge;
  const call = (index, ...args) => b().call(index, [...args, 0]);
  const ptr = address => address ? b().read('ptr', address) : 0;
  const read = (type, address) => b().read(type, address);
  const realNow = () => (window.__realPerfNow || performance.now.bind(performance))();
  const delay = ms => new Promise(resolve =>
    (window.__realSetTimeout || window.setTimeout)(resolve, ms));
  let initialized = false, resetting = false, startupPromise = null;
  let startupError = null, stage = 'engine', lastStart = null;
  let checkpointCount = 0, sectionActions = [], sectionBest = [];
  let lastCheckpoint = -1, lastChain = 0, lastDeaths = 0, chainBaseline = 0;
  let bestProgress = 0, completed = false, runId = null, sampleError = null;
  let lastSnapshot = null, assistsUsed = false;

  function statics(typeInfo) {
    return b().resolveAddress({kind:'static_fields', type_info:typeInfo});
  }
  function controller() {
    const p = ptr(statics(9314700) + 12);
    return p && ptr(p) === ptr(9314700) && call(7674, p) ? p : 0;
  }
  function listValues(list, type = 'i32') {
    if (!list) return [];
    const count = read('i32', list + 12), array = ptr(list + 8);
    if (!array || count < 0 || count > 20000) throw new Error('Invalid native list');
    return Array.from({length:count}, (_, i) => read(type, array + 16 + 4*i));
  }
  function setting(index) { return call(27537, index); }
  function setSetting(index, value) {
    const array = ptr(statics(9314712) + 4);
    const object = ptr(array + 16 + 4*index);
    if (!object) throw new Error('Native setting unavailable: ' + index);
    call(28383, object, value);
    if (setting(index) !== value) throw new Error('Native setting rejected: ' + index);
  }
  function settingsForTask() {
    for (const [id, value] of [[14,0],[32,1],[35,0],[60,difficulty],[61,0],[66,0]]) {
      setSetting(id, value);
    }
  }
  function levelPointer() {
    const array = call(32363);
    for (let i = 0; i < read('i32', array + 12); ++i) {
      const key = ptr(array + 16 + 4*i);
      if (b().readIl2CppString(key) === level) return key;
    }
    throw new Error('Level absent from native demo: ' + level);
  }
  function menuController(key) {
    const dictionary = ptr(statics(9319488) + 8);
    const entries = ptr(dictionary + 12), count = read('i32', dictionary + 16);
    if (!entries || count < 0 || count > 200) throw new Error('Invalid native menu registry');
    for (let i = 0; i < count; i++) {
      const entry = entries + 16 + i*16;
      if (read('i32', entry) < 0) continue;
      const name = b().readIl2CppString(ptr(entry + 8));
      const object = ptr(entry + 12);
      if (name === key && object && call(7674,object) &&
          b().readIl2CppString(ptr(object + 40)) === key) return object;
    }
    throw new Error('Native menu absent: ' + key);
  }
  function nativeState() {
    if (!b()?.status().hasUnityInstance || !call(27954)) return null;
    const gc = controller();
    if (!gc || !call(27955) || !call(27611)) return null;
    const player = call(88449);
    if (!player) return null;
    const song = call(27555);
    return {
      level:b().readIl2CppString(call(87979)), difficulty:setting(60),
      autoplay:!!setting(14), checkpoints_enabled:!!setting(32),
      music_speed:call(88016), practice_mode:!!call(27720),
      checkpoint_index:read('i32', gc+28), chain:read('i32', gc+32),
      deaths:read('i32', gc+36), section_failures:read('i32', gc+44),
      level_ended:!!call(27612), paused:!!call(27592),
      respawning:!!call(27522), jumping:!!call(27675, player),
      flying:!!call(27601, player), song_beat:call(27770),
      bpm:call(27791, song), native_id:call(7661, gc),
    };
  }
  function variantMatches(s) {
    return s && s.level === level && s.difficulty === difficulty &&
      !s.autoplay && s.checkpoints_enabled && !s.practice_mode &&
      Math.abs(s.music_speed - 1) < 0.0001;
  }
  async function waitFor(test, description, timeout = 50000) {
    const deadline = realNow() + timeout;
    while (realNow() < deadline) {
      let result;
      try { result = test(); } catch (_) { /* Native scene still being constructed. */ }
      if (result) return result;
      if (window.rhythmQuestLoadError) throw new Error(window.rhythmQuestLoadError);
      if (window.rhythmQuestAudioErrors?.length) throw new Error(window.rhythmQuestAudioErrors[0]);
      await delay(20);
    }
    throw new Error('Timed out: ' + description);
  }
  function clearProgress() {
    bestProgress = 0; completed = false; sectionBest = []; assistsUsed = false;
    lastCheckpoint = -1; lastChain = lastDeaths = chainBaseline = 0;
  }
  function readChart() {
    checkpointCount = call(32542);
    const metadata = call(28603, call(28146), levelPointer(), difficulty);
    sectionActions = listValues(ptr(metadata + 108));
    // Runtime checkpoints include tutorial subdivisions. Bin authored action
    // beats against the actual checkpoint beats when their section counts differ.
    if (sectionActions.length !== checkpointCount) {
      const checkpoints = call(32256);
      const beats = Array.from({length:checkpointCount}, (_, i) =>
        read('f32', ptr(checkpoints + 16 + i*4) + 20));
      sectionActions = Array(checkpointCount).fill(0);
      for (const offset of [76,80,88]) {
        for (const beat of listValues(ptr(metadata + offset), 'f32')) {
          let section = 0;
          while (section+1 < beats.length && beat >= beats[section+1]) section++;
          sectionActions[section]++;
        }
      }
    }
    if (!(checkpointCount > 0) || sectionActions.some(n => n < 0)) {
      throw new Error('Native chart section metadata unavailable');
    }
  }
  function account(s) {
    if (!initialized || resetting || !s) return;
    // Native Restart and replay replace the GameController. Accept only a fresh
    // matching task start; preserve legitimate best progress across native retries.
    if (s.native_id !== runId) {
      if (!variantMatches(s) || s.checkpoint_index !== 0 || s.deaths !== 0) return;
      runId = s.native_id; assistsUsed = false;
      lastCheckpoint = -1; lastChain = lastDeaths = chainBaseline = 0;
    }
    if (!variantMatches(s)) { assistsUsed = true; return; }
    if (assistsUsed) return;
    const cp = s.checkpoint_index;
    if (cp < 0 || cp >= checkpointCount || s.chain < 0) return;
    if (cp !== lastCheckpoint) {
      chainBaseline = s.chain >= lastChain ? lastChain : 0;
    }
    if (s.deaths !== lastDeaths || s.chain < lastChain) chainBaseline = 0;
    const hits = Math.max(0, s.chain - chainBaseline);
    sectionBest[cp] = Math.max(sectionBest[cp] || 0, hits);
    const fraction = Math.min(0.999, sectionBest[cp] / (sectionActions[cp] + 1));
    bestProgress = Math.max(bestProgress, Math.min(0.999, (cp + fraction) / checkpointCount));
    // The native EndLevel event, not a hit-count or timer threshold, grants clear.
    if (s.level_ended) { completed = true; bestProgress = 1; }
    lastCheckpoint = cp; lastChain = s.chain; lastDeaths = s.deaths;
    lastSnapshot = s;
  }
  function finishStart(s) {
    clearProgress(); readChart(); runId = s.native_id;
    lastSnapshot = s;
    lastStart = {level:s.level, difficulty:s.difficulty,
      checkpoint:s.checkpoint_index, deaths:s.deaths, autoplay:s.autoplay,
      practice_mode:s.practice_mode, music_speed:s.music_speed,
      checkpoints_enabled:s.checkpoints_enabled, checkpoint_count:checkpointCount};
    initialized = true; resetting = false; startupError = null; stage = 'ready';
    document.getElementById('unity-canvas').focus();
  }
  function init() {
    if (startupPromise) return startupPromise;
    startupPromise = (async () => {
      if (!/^496_0[1-9]$/.test(taskId) || !level) throw new Error('Unknown task: '+taskId);
      await waitFor(() => window.rhythmQuestLoaded && b()?.status().hasUnityInstance &&
        call(28496) && ptr(statics(9314712) + 4), 'native startup initialization');
      stage = 'native_menu';
      // StartupSceneController initializes this native MenuScene literal while
      // presenting notices. Enter the same scene directly for task selection.
      const menuScene = await waitFor(() => {
        const scene = ptr(0x959bbc);
        return !call(28152) && b().readIl2CppString(scene) === 'MenuScene' ? scene : null;
      }, 'native MenuScene destination');
      call(28345,menuScene,0,0,0,0);
      const selector = await waitFor(() => {
        if (!call(27565) || call(28152)) return null;
        const p = ptr(statics(9320268)+4);
        return p && call(7674,p) ? p : null;
      }, 'native level selector');
      call(28576,menuController('levelselect'));
      await waitFor(() => !call(28153) && b().readIl2CppString(ptr(statics(9319488))) === 'levelselect',
        'active native level-select menu');
      settingsForTask();
      const [world, number] = level.slice(5).split('-').map(Number);
      let selected = false;
      for (const w of [world-1,world]) for (const n of [number-1,number]) {
        if (selected) continue;
        if (b().readIl2CppString(call(28727,w,n)) === level) {
          call(28689,w,n); selected = true;
        }
      }
      if (!selected || b().readIl2CppString(call(28730,selector)) !== level) {
        throw new Error('Native level selection mismatch');
      }
      stage = 'native_level';
      call(89113,selector); // Initializes the native MainScene destination.
      // The menu handler can leave the selector open. Task
      // entry uses the same native LevelLoader and SceneTransitioner directly.
      // No completed levels or saved scores are granted by this entry route.
      if (!call(28152)) {
        call(87978,levelPointer());
        const scene = ptr(0x959a10);
        if (b().readIl2CppString(scene) !== 'MainScene') throw new Error('MainScene unavailable');
        call(28345,scene,0,0,0,0);
      }
      const s = await waitFor(() => {
        const value = nativeState();
        return variantMatches(value) && !window.rhythmQuestAudioPending && !call(28152) && value.checkpoint_index === 0 &&
          value.deaths === 0 ? value : null;
      }, 'requested playable level');
      finishStart(s);
      return true;
    })().catch(error => { startupError = String(error.message || error); stage = 'error'; return false; });
    return startupPromise;
  }
  async function reset() {
    if (!initialized && !await init()) throw new Error(startupError);
    initialized = false; resetting = true; clearProgress(); stage = 'reset';
    window.__resetRandomSeed?.();
    settingsForTask();
    call(87978,levelPointer());
    const scene = ptr(0x959a10); // MainScene string literal initialized by native Play.
    if (b().readIl2CppString(scene) !== 'MainScene') throw new Error('MainScene literal unavailable');
    const oldId = runId;
    call(28345,scene,0,0,0,0);
    const s = await waitFor(() => {
      const value = nativeState();
      return variantMatches(value) && !window.rhythmQuestAudioPending && value.native_id !== oldId && !call(28152) &&
        value.checkpoint_index === 0 && value.deaths === 0 ? value : null;
    }, 'fresh replacement level');
    finishStart(s);
    return true;
  }
  window.unityAdapter = {
    init, reset,
    transformState(state) {
      let s = null;
      try { if (initialized && !resetting) { s = nativeState(); account(s); } }
      catch (error) { sampleError = String(error.message || error); }
      const ready = !!(initialized && !resetting && variantMatches(s) && s.native_id === runId);
      const display = s || lastSnapshot || {};
      state.schemaVersion = '1.0'; state.gameId = GAME_ID;
      state.gameTimeMs = Math.max(0, (display.song_beat || 0) * 60000 / (display.bpm || 120));
      state.status = completed ? 'success' : assistsUsed ? 'settings_changed' : !ready ? (startupError ? 'error' : 'loading') :
        display.paused ? 'paused' : display.respawning ? 'respawning' : 'playing';
      state.is_actionable = ready || completed;
      state.terminal = {isTerminal:completed, outcome:completed ? 'success' : null};
      state.game_state = {
        level:display.level || level, difficulty:display.difficulty ?? difficulty,
        autoplay:display.autoplay ?? false, practice_mode:display.practice_mode ?? false,
        music_speed:display.music_speed ?? 1, checkpoints_enabled:display.checkpoints_enabled ?? true,
        checkpoint_index:display.checkpoint_index || 0, checkpoint_count:checkpointCount,
        chain:display.chain || 0, deaths:display.deaths || 0,
        paused:display.paused || false, respawning:display.respawning || false,
        jumping:display.jumping || false, flying:display.flying || false,
        level_ended:display.level_ended || completed, song_beat:display.song_beat || 0,
      };
      state.metrics = {level_progress:bestProgress, completed:completed ? 1 : 0};
      state.provenance = {game_state:'engine_hook',metrics:'engine_hook',gameTimeMs:'engine_hook'};
      state.raw = {...state.raw, taskId, stage, startupError, sampleError,
        lastStart, assistsUsed, sectionActions:[...sectionActions], sectionBest:[...sectionBest], chainBaseline};
      return state;
    },
  };
  // Sampling is independent of policy observations; no input count or wall clock
  // earns credit. Native counters are sampled at each rendered frame.
  function sample() {
    try { if (initialized && !resetting) account(nativeState()); }
    catch (error) { sampleError = String(error.message || error); }
    requestAnimationFrame(sample);
  }
  requestAnimationFrame(sample);

  // The harness freezes JS time during inference. Suspend WebAudio too so Unity's
  // DSP clock and rhythm chart stay synchronized without opening a pause menu.
  const pauseClock = window.__pauseGame, resumeClock = window.__resumeGame;
  let suspendedAudio = [];
  window.__pauseGame = () => {
    if (suspendedAudio.length === 0) {
      suspendedAudio = (window.rhythmQuestAudioContexts || []).filter(c => c.state === 'running');
      suspendedAudio.forEach(c => c.suspend());
    }
    pauseClock?.();
  };
  window.__resumeGame = () => {
    resumeClock?.();
    suspendedAudio.forEach(c => c.resume()); suspendedAudio = [];
  };
})();
