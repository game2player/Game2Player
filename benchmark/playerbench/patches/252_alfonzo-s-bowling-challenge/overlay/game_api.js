/* Alfonzo's Bowling Challenge — PICO-8 GPIO adapter.
 * All gameplay values below have provenance engine_hook: ship-carts/abc.p8
 * publishes its own variables and named score/damage/end events. The cartridge
 * latches points for the requested round, so polling cannot miss its ending or
 * accidentally count points from later minigames. No pixels are interpreted.
 *
 * GPIO: 0 requested level; 1 state; 2 level; 3 mode; 4 player count;
 * 5..6 signed round score; 7..8 ball x*100; 9..10 ball y*100;
 * 11 boss HP; 12 presentation ready (latched); 13 round ended (latched);
 * 14 dialogue prompt; 15 accepted level; 16..17 latched round points;
 * 18 latched boss damage; 19 dialogue visible; 20 tutorial visible;
 * 21 pinbot state; 22 remaining targets. 32..33 requested RNG seed.
 * Multi-byte values are little endian, signed 16-bit where indicated.
 *
 * Policy observation (window.__policyObservation, separate from gameAPI): the
 * cartridge's gw_obs() pokes geometry from its Lua tables, provenance engine_hook.
 * 23..26 ball velocity (pos-prev) vx,vy int16 = (n-16384)/256; 27 flags (1 line
 * revealed, 2 Alonzo hit, 4 interrupted, 8/16 platform 1/2 enabled); 28 entity
 * count; 29 yolk count; 30..31 knocked/cracked bitmask; 34..47 two platforms of 7
 * bytes (x,w,h,y int16 (n-16384)/64,vy int16 (n-16384)/256); 48..127 per-mode block
 * (only the block of the current mode is valid, the rest is zeroed each frame).
 * Positions in lists are 3 bytes: two 12-bit values, (n-2048)/8 pixels (1/8 px
 * resolution). Velocity bytes are (n-128)/k. Layouts are in decode* below.
 */
(() => {
  'use strict';
  const GAME_ID = '252_alfonzo-s-bowling-challenge';
  const TASKS = {
    '252_01': {level: 1, mode: 1, field: 'bowling_pins', target: 10},
    '252_02': {level: 4, mode: 2, field: 'eggs_cooked', target: 5},
    '252_03': {level: 5, mode: 3, field: 'bricks_destroyed', target: 40},
    '252_04': {level: 6, mode: 4, field: 'baskets_scored', target: 3},
    '252_05': {level: 9, mode: 7, field: 'networking_points', target: 5},
    '252_06': {level: 10, mode: 8, field: 'hair_removed', target: 6},
    '252_07': {level: 11, mode: 9, field: 'boss_damage', target: 100}
  };
  const query = new URLSearchParams(location.search);
  const taskId = query.get('task') || '252_01';
  const task = TASKS[taskId];
  const modes = ['', 'bowling', 'breakfast', 'breakout', 'basketball',
    'blackjack', 'birdwatching', 'networking', 'barbershop', 'bossfight'];
  const phases = ['', 'coach', 'pinbot', 'play', 'score', 'credits', 'end'];
  let booted = false;
  let seedUsed = 42;

  function byte(pin) {
    const value = window.pico8_gpio && window.pico8_gpio[pin];
    return Number.isFinite(value) ? value & 255 : 0;
  }
  function signed16(pin) {
    const n = byte(pin) | (byte(pin + 1) << 8);
    return n >= 32768 ? n - 65536 : n;
  }
  function boot(options = {}) {
    if (booted || !task) return;
    const supplied = Number.isFinite(options.seed) ? options.seed :
      (typeof window.__getRandomSeed === 'function' ? window.__getRandomSeed() : 42);
    seedUsed = (supplied >>> 0) % 32768;
    // The cartridge reads these during its update, after the player mirrors GPIO.
    window.pico8_gpio[0] = task.level;
    window.pico8_gpio[32] = seedUsed & 255;
    window.pico8_gpio[33] = (seedUsed >>> 8) & 255;
    booted = true;
    window.p8_run_cart();
    const canvas = document.getElementById('canvas');
    canvas.tabIndex = 0;
    canvas.focus();
  }
  function getState() {
    const frame = Number(window.pico8_state?.frame_number) || 0;
    const accepted = !!task && byte(15) === task.level;
    const ready = accepted && byte(12) === 1 && frame > 0;
    const points = Math.max(0, signed16(16));
    const metrics = {
      bowling_pins: 0, eggs_cooked: 0, bricks_destroyed: 0,
      baskets_scored: 0, networking_points: 0, hair_removed: 0,
      boss_damage: 0
    };
    if (accepted) {
      metrics[task.field] = task.field === 'boss_damage' ? byte(18) :
        task.field === 'baskets_scored' ? points / 2 : points;
    }
    const success = accepted && metrics[task.field] >= task.target;
    const ended = accepted && byte(13) === 1;
    const terminal = success || ended;
    return {
      schemaVersion: '1.0.0', gameId: GAME_ID,
      timestampMs: Date.now(), gameTimeMs: frame * 1000 / 60,
      status: !task ? 'invalid_task' : !ready ? 'loading' :
        success ? 'success' : ended ? 'fail' : 'playing',
      is_actionable: ready,
      terminal: {isTerminal: terminal, outcome: success ? 'success' : ended ? 'fail' : null},
      game_state: {
        task_id: taskId, phase: phases[byte(1)] || 'loading',
        level: byte(2), mode: modes[byte(3)] || 'loading', mode_id: byte(3),
        requested_level: task?.level || 0, accepted_level: byte(15),
        presentation_complete: ready, round_ended: ended,
        player_count: byte(4), round_score: signed16(5),
        ball_x: signed16(7) / 100, ball_y: signed16(9) / 100,
        boss_hp: byte(11), dialogue_visible: byte(19) === 1,
        dialogue_prompt: byte(14) === 1, remaining_targets: byte(22),
        seed: seedUsed
      },
      metrics,
      provenance: {
        'game_state.level': 'engine_hook', 'game_state.mode_id': 'engine_hook',
        'game_state.round_score': 'engine_hook', 'game_state.player_count': 'engine_hook',
        'game_state.ball_x': 'engine_hook', 'game_state.ball_y': 'engine_hook',
        'game_state.boss_hp': 'engine_hook', 'game_state.round_ended': 'engine_hook',
        metrics: 'engine_hook'
      }
    };
  }
  function reset() {
    if (typeof window.__resetRandomSeed === 'function') window.__resetRandomSeed();
    // Reload is the player's safe reset. Keep exactly the current task query.
    // Schedule after returning, so callers can observe reset's return value.
    (window.__realSetTimeout || window.setTimeout)(() => location.reload(), 0);
    return true;
  }
  window.gameAPI = {init: boot, getState, reset};

  // ---- policy observation: entity geometry published by the cartridge ----
  const i16 = (pin, k) => ((byte(pin) | (byte(pin + 1) << 8)) - 16384) / k;
  const vel = (pin, k) => (byte(pin) - 128) / k;
  function xy(pin) {
    const u = byte(pin) | ((byte(pin + 1) & 15) << 8);
    const v = (byte(pin + 1) >> 4) | (byte(pin + 2) << 4);
    return {x: (u - 2048) / 8, y: (v - 2048) / 8};
  }
  const bit = (mask, i) => ((mask >> i) & 1) === 1;
  const mask16 = () => byte(30) | (byte(31) << 8);
  function decodePins() {
    const out = [], mask = mask16();
    for (let i = 0; i < Math.min(byte(28), 10); i++) {
      const a = 48 + i * 5;
      out.push({...xy(a), w: byte(a + 3), h: byte(a + 4), isknocked: bit(mask, i)});
    }
    return out;
  }
  function decodeEggsYolks() {
    const eggs = [], yolks = [], mask = mask16();
    const ew = byte(48), eh = byte(49);
    for (let i = 0; i < Math.min(byte(28), 10); i++) {
      const a = 50 + i * 5;
      eggs.push({...xy(a), vx: vel(a + 3, 8), vy: vel(a + 4, 8), w: ew, h: eh,
        cracked: bit(mask, i)});
    }
    const total = byte(29);
    for (let i = 0; i < Math.min(total, 4); i++) {
      const a = 108 + i * 5;
      yolks.push({...xy(a), vx: vel(a + 3, 16), vy: vel(a + 4, 16),
        w: byte(106), h: byte(107)});
    }
    // yolks lists only the 4 oldest (lowest on screen); count is the true total
    return {eggs, yolks, yolk_count: total};
  }
  function decodePan() {
    return {...xy(100), w: byte(103), h: byte(104), vx: vel(105, 32)};
  }
  function decodeBricks() {
    const out = [];
    for (let g = 0; g < 40; g++) {
      if (!bit(byte(48 + (g >> 3)), g & 7)) continue;
      out.push({x: byte(69 + (g % 5)), y: i16(53 + 2 * Math.floor(g / 5), 64),
        w: byte(74), h: byte(75)});
    }
    return out;
  }
  function decodeHoop() {
    return {...xy(48), h: byte(51), netw: byte(52), flash: byte(53) === 1,
      bbstate: byte(54), vy: i16(55, 256)};
  }
  function decodeHair() {
    const out = [], mask = mask16();
    for (let i = 0; i < Math.min(byte(28), 7); i++) {
      const a = 48 + i * 5;
      out.push({...xy(a), w: byte(a + 3), h: byte(a + 4), isknocked: bit(mask, i),
        is_head: i === 0});
    }
    return out;
  }
  function decodeBoss() {
    return {...xy(98), w: byte(101), h: byte(102), hp: byte(103),
      shake: byte(104) / 32, pbstate: byte(21)};
  }
  function decodePlatforms() {
    const flags = byte(27), out = [];
    for (let i = 0; i < 2; i++) {
      const a = 34 + i * 7;
      out.push({x: byte(a), y: i16(a + 3, 64), w: byte(a + 1), h: byte(a + 2),
        vy: i16(a + 5, 256), enabled: bit(flags, 3 + i)});
    }
    return out;
  }
  function observation() {
    const live = !!task && window.pico8_gpio && byte(15) === task.level &&
      byte(2) === task.level;
    const none = {ball_vel: null, platforms: null, pins: null, eggs_yolks: null,
      pan: null, bricks: null, hoop: null, hair_targets: null, boss: null,
      dialogue: null};
    if (!live) return none;
    const mode = byte(3), flags = byte(27);
    const obs = {...none};
    if (byte(4) > 0) obs.ball_vel = {vx: i16(23, 256), vy: i16(25, 256)};
    obs.platforms = decodePlatforms();
    obs.dialogue = {
      visible: byte(19) === 1, line_revealed: bit(flags, 0),
      prompt_visible: byte(14) === 1, alonzo_hit: mode === 7 && bit(flags, 1),
      interrupted: bit(flags, 2)
    };
    if (mode === 1 || mode === 9) obs.pins = decodePins();
    if (mode === 2) { obs.eggs_yolks = decodeEggsYolks(); obs.pan = decodePan(); }
    if (mode === 3) obs.bricks = decodeBricks();
    if (mode === 4) obs.hoop = decodeHoop();
    if (mode === 8) obs.hair_targets = decodeHair();
    if (mode === 9) obs.boss = decodeBoss();
    return obs;
  }
  window.__policyObservation = {getState: observation};
  // A documented restart key gives screenshot-only players the same clean start.
  window.addEventListener('keydown', event => {
    if (event.key.toLowerCase() === 'r' && !event.repeat) {
      event.preventDefault();
      reset();
    }
  });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => boot(), {once: true});
  } else boot();
})();
