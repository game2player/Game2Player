/* Petal Quest 1.2 — thin PICO-8 GPIO adapter.
 * Cartridge writes at 0x5f80 (GPIO index zero):
 * 0 magic246, 1 room, 2 carried, 3 deposited, 4 flower-ever, 5 native win,
 * 6 secret_found, 7 has_bow, 8 singing stance, 9 sprite, 10 chorus solved,
 * 11 fade finished/visible, 12..13 x, 14..15 y (unsigned little-endian),
 * 16 reset epoch, 17 carrying flower, 18 last sung color index,
 * 19..20 bark count, 21..22 successful sung-note count (unsigned LE16).
 * All these values have provenance engine_hook. No pixel inference or saves.
 * GPIO127 commands: 1 invokes the native title-start body; 2 invokes _init
 * and that same start body. There are no checkpoint or completion commands.
 */
(() => {
  'use strict';
  const GAME_ID = '246_petal-quest';
  const TASKS = {
    '246_01': ['birds_collected', 1],
    '246_02': ['chorus_progress', 24],
    '246_03': ['quest_progress', 26],
    '246_04': ['secret_bow_progress', 26]
  };
  const task = new URLSearchParams(location.search).get('task') || '246_03';
  const contract = TASKS[task];
  let initialized = false;
  let initialization = null;
  let startFrame = 0;
  let error = null;

  function pins() {
    const p = window.pico8_gpio;
    return p && p[0] === 246 ? p : null;
  }
  function frame() {
    return Number(window.pico8_state?.frame_number) || 0;
  }
  function waitFor(test, label) {
    return new Promise((resolve, reject) => {
      let attempts = 0;
      function check() {
        if (test()) return resolve();
        if (++attempts > 1800) return reject(new Error('PICO-8 timeout: ' + label));
        requestAnimationFrame(check);
      }
      check();
    });
  }
  function getState() {
    const p = pins();
    const carried = p ? p[2] : 0;
    const deposited = p ? p[3] : 0;
    const collected = carried + deposited;
    const flower = p ? p[4] : 0;
    const won = !!(p && p[5]);
    const secret = p ? p[6] : 0;
    const bow = p ? p[7] : 0;
    let chorus = Math.min(collected, 12) + Math.min(deposited, 12);
    if (!p || !p[10]) chorus = Math.min(chorus, 23);
    const metrics = {
      birds_collected: collected,
      birds_carried: carried,
      birds_deposited: deposited,
      chorus_progress: chorus,
      quest_progress: chorus + flower + Number(won),
      secret_bow_progress: chorus + secret + bow
    };
    const success = !!contract && initialized && metrics[contract[0]] >= contract[1];
    const terminal = success || (initialized && won);
    const actionable = initialized && !!p && !!p[11] && !window.pico8_state?.is_paused && !terminal;
    return {
      schemaVersion: '1.0', gameId: GAME_ID,
      timestampMs: Date.now(),
      gameTimeMs: initialized ? Math.max(0, frame() - startFrame) * (1000 / 30) : 0,
      status: error ? 'error' : terminal ? (success ? 'success' : 'fail') : initialized ? 'playing' : 'loading',
      is_actionable: actionable,
      terminal: {isTerminal: terminal, outcome: terminal ? (success ? 'success' : 'fail') : null},
      game_state: {
        room: p ? p[1] : -1,
        player: {x: p ? p[12] + 256 * p[13] : 0, y: p ? p[14] + 256 * p[15] : 0},
        singing: !!(p && p[8]), sprite: p ? p[9] : 0,
        last_note: p ? p[18] : 0,
        barks: p ? p[19] + p[20] * 256 : 0,
        notes_sung: p ? p[21] + p[22] * 256 : 0,
        flower_obtained: !!flower, carrying_flower: !!(p && p[17]),
        chorus_solved: !!(p && p[10]), secret_found: !!secret, has_bow: !!bow,
        native_win: won
      },
      metrics, taskId: task, error,
      provenance: {game_state: 'engine_hook', metrics: 'engine_hook'}
    };
  }
  async function enterStart(command) {
    initialized = false;
    const oldEpoch = pins()?.[16];
    window.pico8_gpio[127] = command;
    await waitFor(() => {
      const p = pins();
      return p && (command !== 2 || p[16] !== oldEpoch) && p[1] === 27 && p[11] === 1 &&
        p[2] === 0 && p[3] === 0 && p[4] === 0 && p[5] === 0 && p[6] === 0 && p[7] === 0 &&
        p[12] + p[13] * 256 === 432 && p[14] + p[15] * 256 === 446;
    }, 'clean room27 start');
    startFrame = frame();
    initialized = true;
    document.getElementById('canvas').focus();
    return getState();
  }
  function init() {
    if (initialization) return initialization;
    initialization = (async () => {
      if (!contract) throw new Error('Unknown task: ' + task);
      if (document.readyState === 'loading') {
        await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once: true}));
      }
      window.p8_run_cart();
      await waitFor(() => pins() && frame() > 1, 'cartridge publication');
      return enterStart(1);
    })().catch(e => {error = String(e); throw e;});
    return initialization;
  }
  async function reset() {
    await init();
    window.__resetRandomSeed?.();
    error = null;
    return enterStart(2);
  }
  window.gameAPI = {init, reset, getState};
  // A small player-facing ledger makes collected/deposited progress visible.
  function renderLedger() {
    const el = document.getElementById('quest-ledger');
    if (el) {
      const s = getState(), g = s.game_state;
      el.textContent = initialized
        ? `Birds: ${s.metrics.birds_collected}   Perches: ${s.metrics.birds_deposited}/12   Flower: ${g.native_win ? 'returned' : g.flower_obtained ? 'found' : '—'}   Bow: ${g.has_bow ? 'found' : '—'}`
        : error ? 'Unable to start adventure' : 'Loading adventure…';
    }
    requestAnimationFrame(renderLedger);
  }
  renderLedger();
  init().catch(e => console.error(e));
})();
