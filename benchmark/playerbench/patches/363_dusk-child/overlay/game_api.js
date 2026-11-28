/* Dusk Child verifier adapter. The official PICO-8 0.1.10B player stays local.
 *
 * The cart (duskchild_1_4) publishes its own globals over GPIO each update,
 * protocol 1 (provenance: internal):
 *  0 magic 68; 1 protocol; 2-3 update frames since start (u16); 4 mode
 *  (0 title, 1 fade, 2 playing, 3 dying, 4 cutscene, 5 ending);
 *  5/6 room x,y (flr(px/128), flr(py/112)); 7-8 px; 9-10 py (u16);
 *  11 flags wopen|wlit<<1|eopen<<2|elit<<3|cansuper<<4|super<<5|dead<<6|gameover<<7;
 *  12 carried actor type (0 none, 3 orb, 4 bucket, 9 keystone);
 *  13 cutscene|crouching<<1|grounded<<2; 14 distinct rooms entered;
 *  15..18 visited-room bitmask (room y*8+x); 19 tablet actor a (0..12);
 *  20 final door actor a (0..38); 21 deaths; 22 latched events
 *  orb-carried|keystone-carried<<1|bucket-carried<<2|shelf-weighted<<3|
 *  shelf-door-open<<4|lock-slotted<<5|wlit<<6|elit<<7;
 *  23 latched wopen|eopen<<1, cansuper<<2, gameover<<3; 24 weighted shelves;
 *  25 highest rise (0..16) of a pulley door whose room has a weighted shelf;
 *  31 item count; 32.. per orb/bucket/keystone: x u16, y u16,
 *  type + 32*carried + 64*holds-water.
 * Commands: 120 start (1 fresh game, 2 awakened at the tablet), 121 nonce,
 * 122 acknowledged nonce. The cart restores its own ROM data and globals,
 * reruns _init and starts the game the way the title button does.
 *
 * *_progress metrics are verifier-only: native milestones from the cart plus
 * an approach measure (rooms on the static room graph + capped pixel
 * distance) toward the current objective, kept as the episode best.
 */
(function () {
  'use strict';
  const GAME_ID = '363_dusk-child';
  const params = new URLSearchParams(location.search);
  const START = params.get('start') === 'awakened' ? 'awakened' : 'fresh';
  const COMMAND = START === 'awakened' ? 2 : 1;
  const MODES = ['title', 'fade', 'playing', 'dying', 'cutscene', 'ending'];
  const ITEM = {3: 'orb', 4: 'bucket', 9: 'keystone'};
  const realNow = () => (window.__realDateNow || Date.now)();

  // Static world geometry decoded from the cart map (8x4 rooms of 128x112 px).
  // Room links are open screen edges [roomA, roomB, x, y] with the midpoint of
  // the opening (room = y*8+x); temple and pulley doors count as open.
  const EDGES = [[0,1,128,92],[1,2,256,92],[1,9,244,112],[2,10,348,112],[3,4,512,84],
    [3,11,420,112],[4,5,640,84],[4,12,588,112],[5,6,768,92],[6,7,896,60],[7,15,964,112],
    [10,11,384,172],[10,18,324,224],[11,12,512,156],[11,19,436,224],[12,13,640,156],
    [12,20,628,224],[13,14,768,164],[14,22,788,224],[15,23,1012,224],[16,17,128,324],
    [16,24,44,336],[17,18,256,316],[18,19,384,276],[19,20,512,260],[20,21,640,252],
    [21,22,768,292],[21,29,732,336],[22,23,896,292],[23,31,956,336],[24,25,128,420],
    [25,26,256,436],[27,28,512,428],[28,29,640,420],[29,30,768,436]];
  const SHELVES = [[38, 10], [55, 10], [83, 19], [98, 21], [58, 54], [83, 47], [103, 53],
    [115, 54]].map(([x, y]) => ({x: x * 8 + 4, y: y * 8}));
  // where a carried keystone is dropped into each hub lock
  const LOCKS = {west: {x: 552, y: 191}, east: {x: 600, y: 191}};
  // statue hand boxes; west statues set wlit, east statues set elit
  const STATUES = {west: [{x: 52, y: 268}, {x: 540, y: 268}],
    east: [{x: 988, y: 36}, {x: 612, y: 268}]};
  const TABLET = {x: 322, y: 440};
  const FINAL_DOOR = {x: 576, y: 320};
  const ROOM_PX = 160, FAR_ROOMS = 12;

  const hops = Array.from({length: 32}, (_, from) => {
    const dist = new Array(32).fill(FAR_ROOMS), queue = [from];
    dist[from] = 0;
    while (queue.length) {
      const u = queue.shift();
      for (const [a, b] of EDGES) {
        const v = a === u ? b : b === u ? a : -1;
        if (v >= 0 && dist[v] === FAR_ROOMS && v !== from) { dist[v] = dist[u] + 1; queue.push(v); }
      }
    }
    return dist;
  });
  const roomOf = p => Math.min(3, Math.max(0, Math.floor(p.y / 112))) * 8 +
    Math.min(7, Math.max(0, Math.floor(p.x / 128)));
  // Route distance: whole rooms still to cross on the room graph, plus the
  // pixel distance (capped at one room) to the next opening on a shortest
  // route, or to the target itself once in its room.
  const capped = (p, q) => Math.min(ROOM_PX, Math.hypot(p.x - q.x, p.y - q.y));
  function distance(p, t) {
    const from = roomOf(p), to = roomOf(t), h = hops[from][to];
    if (h === 0) return capped(p, t);
    if (h >= FAR_ROOMS) return ROOM_PX * FAR_ROOMS;
    let step = ROOM_PX;
    for (const [a, b, x, y] of EDGES) {
      const next = a === from ? b : b === from ? a : -1;
      if (next >= 0 && hops[next][to] === h - 1) step = Math.min(step, capped(p, {x, y}));
    }
    return ROOM_PX * h + step;
  }

  const pins = () => window.pico8_gpio || [];
  const byte = i => (pins()[i] || 0) & 255;
  const word = i => byte(i) | (byte(i + 1) << 8);
  const bit = (i, n) => (byte(i) >> n & 1) === 1;

  let pending = null, nonce = 0, initialized = false, error = null;
  let lastTick = null, episodeTicks = 0, startMatch = false;
  let base = {}, best = {};

  function emptyBest() {
    return {orb_pickup_progress: 0, pulley_progress: 0, keystone_progress: 0,
      awakening_progress: 0, statue_progress: 0, ending_progress: 0};
  }
  best = emptyBest();

  // 0..1 closeness for one stage, against the distance when that stage was first seen
  function approach(key, d) {
    if (!(key in base)) base[key] = Math.max(d, 1);
    return Math.max(0, Math.min(1, 1 - d / base[key]));
  }
  const nearest = (p, targets) => targets.length ? Math.min(...targets.map(t => distance(p, t))) : null;

  function read() {
    const items = [];
    for (let i = 0; i < Math.min(8, byte(31)); i++) {
      const at = 32 + i * 5, info = byte(at + 4);
      items.push({type: ITEM[info & 31] || String(info & 31), x: word(at), y: word(at + 2),
        carried: (info & 32) !== 0, water: (info & 64) !== 0});
    }
    const visited = [];
    for (let r = 0; r < 32; r++) if (bit(15 + (r >> 3), r & 7)) visited.push([r % 8, r >> 3]);
    return {
      mode: MODES[byte(4)] || 'unknown', room: [byte(5), byte(6)],
      player: {x: word(7), y: word(9)},
      west_temple_open: bit(11, 0), west_statue_lit: bit(11, 1),
      east_temple_open: bit(11, 2), east_statue_lit: bit(11, 3),
      fire_power: bit(11, 4), fire_form: bit(11, 5), dead: bit(11, 6), ended: bit(11, 7),
      carrying: ITEM[byte(12)] || 'none', cutscene: bit(13, 0), crouching: bit(13, 1),
      grounded: bit(13, 2), rooms_visited: byte(14), visited_rooms: visited,
      tablet_reading: byte(19), final_door_rise: byte(20), deaths: byte(21),
      ever: {orb: bit(22, 0), keystone: bit(22, 1), bucket: bit(22, 2), shelf_weighted: bit(22, 3),
        shelf_door_open: bit(22, 4), lock_slotted: bit(22, 5), west_lit: bit(22, 6),
        east_lit: bit(22, 7), west_open: bit(23, 0), east_open: bit(23, 1)},
      weighted_shelves: byte(24), pulley_door_rise: byte(25), items
    };
  }

  function progress(s) {
    const p = s.player, free = s.items.filter(i => !i.carried && i.x < 9000);
    const out = {};
    // 363_01 pick up an orb
    out.orb_pickup_progress = s.ever.orb ? 100 :
      90 * approach('orb', nearest(p, free.filter(i => i.type === 'orb')) ?? 0);
    // 363_03 weigh a shelf and open its pulley door
    const carriedWeight = s.carrying === 'orb' || s.carrying === 'keystone' ||
      (s.carrying === 'bucket' && s.items.some(i => i.carried && i.water));
    if (s.ever.shelf_door_open) out.pulley_progress = 100;
    else if (s.weighted_shelves > 0) out.pulley_progress = Math.min(99, 75 + 25 * Math.min(16, s.pulley_door_rise) / 16);
    else if (carriedWeight) {
      const room = roomOf(p), rooms = Math.min(...SHELVES.map(t => hops[room][roomOf(t)]));
      if (rooms > 0) out.pulley_progress = 25 + 25 * approach('pulley_room', rooms);
      else {
        const px = Math.min(...SHELVES.filter(t => roomOf(t) === room).map(t => Math.min(128, Math.hypot(p.x - t.x, p.y - t.y))));
        out.pulley_progress = 50 + 25 * approach('pulley_shelf', px);
      }
    } else {
      const weights = free.filter(i => i.type !== 'bucket' || i.water);
      out.pulley_progress = 25 * approach('pulley_item', nearest(p, weights) ?? 0);
    }
    // 363_04 slot a keystone into a hub lock
    const keys = free.filter(i => i.type === 'keystone');
    const openLocks = [!s.west_temple_open && LOCKS.west, !s.east_temple_open && LOCKS.east].filter(Boolean);
    if (s.ever.lock_slotted) out.keystone_progress = 100;
    else if (s.carrying === 'keystone') out.keystone_progress = 50 + 45 * approach('key_lock', nearest(p, openLocks) ?? 0);
    else out.keystone_progress = 45 * approach('key_find', nearest(p, keys) ?? 0);
    // 363_05 open the west temple and receive the tablet's awakening
    if (s.fire_power) out.awakening_progress = 100;
    else if (s.tablet_reading > 0) out.awakening_progress = Math.min(99, 85 + 15 * Math.min(12, s.tablet_reading) / 12);
    else if (s.ever.west_open) out.awakening_progress = 40 + 45 * approach('awake_tablet', distance(p, TABLET));
    else if (s.carrying === 'keystone') out.awakening_progress = 20 + 20 * approach('awake_lock', distance(p, LOCKS.west));
    else out.awakening_progress = 18 * approach('awake_key', nearest(p, keys) ?? 0);
    // 363_06 light a statue with the fire form
    const unlit = [...(s.west_statue_lit ? [] : STATUES.west), ...(s.east_statue_lit ? [] : STATUES.east)];
    out.statue_progress = s.ever.west_lit || s.ever.east_lit ? 100 :
      90 * approach('statue', nearest(p, unlit) ?? 0);
    // 363_07 light both statues and open the great door
    const lit = Number(s.west_statue_lit) + Number(s.east_statue_lit);
    if (s.ended) out.ending_progress = 100;
    else if (lit === 2 && s.final_door_rise > 0) out.ending_progress = Math.min(99, 90 + 9 * Math.min(38, s.final_door_rise) / 38);
    else if (lit === 2) out.ending_progress = 60 + 30 * approach('end_door', distance(p, FINAL_DOOR));
    else out.ending_progress = 30 * lit + 30 * approach('end_statue' + lit, nearest(p, unlit) ?? 0);
    return out;
  }

  function startMatches(s) {
    if (START === 'awakened') {
      return s.fire_power && s.west_temple_open && !s.east_temple_open && !s.west_statue_lit &&
        !s.east_statue_lit && s.room[0] === 2 && s.room[1] === 3 && s.carrying === 'none' &&
        s.tablet_reading === 12 && !s.fire_form;
    }
    return !s.fire_power && !s.west_temple_open && !s.east_temple_open && s.room[0] === 0 &&
      s.room[1] === 0 && s.carrying === 'none' && s.player.x === 24 && s.rooms_visited === 1 &&
      s.weighted_shelves === 0;
  }

  function sample() {
    if (byte(0) !== 68 || byte(1) !== 1) return;
    if (pending) {
      if (!pending.sent) {
        const g = pins();
        g[121] = pending.nonce;
        g[120] = COMMAND;
        pending.sent = true;
      }
      if (byte(122) === pending.nonce && byte(120) === 0 && byte(4) === 2) {
        const s = read();
        startMatch = startMatches(s);
        if (!startMatch) {
          error = 'Requested start ' + START + ' did not read back';
          const failed = pending; pending = null; failed.reject(new Error(error));
          return;
        }
        initialized = true;
        lastTick = word(2);
        base = {}; best = emptyBest();
        const ready = pending; pending = null;
        document.getElementById('canvas')?.focus();
        ready.resolve({ok: true, start: START});
      }
    }
    if (!initialized || pending) return;
    const tick = word(2);
    episodeTicks += (tick - lastTick + 65536) % 65536;
    lastTick = tick;
    const now = progress(read());
    for (const k in now) best[k] = Math.max(best[k], Math.round(now[k] * 100) / 100);
  }

  function loop() {
    try {
      sample();
      if (pending && realNow() > pending.deadline) {
        error = 'Cart start did not acknowledge';
        const failed = pending; pending = null; failed.reject(new Error(error));
      }
    } catch (e) {
      error = String(e);
      if (pending) { const failed = pending; pending = null; failed.reject(e); }
    }
    requestAnimationFrame(loop);
  }

  function startEpisode() {
    if (typeof window.__resetRandomSeed === 'function') window.__resetRandomSeed();
    if (typeof window.__resumeGame === 'function') window.__resumeGame();
    initialized = false; error = null; startMatch = false;
    lastTick = null; episodeTicks = 0; base = {}; best = emptyBest();
    nonce = nonce % 255 + 1;
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    pending = {nonce, sent: false, deadline: realNow() + 45000, resolve, reject, promise};
    return promise;
  }

  function init() {
    if (pending) return pending.promise;
    if (initialized) return Promise.resolve({ok: true, start: START});
    return startEpisode();
  }

  function getState() {
    sample();
    const live = byte(0) === 68 && byte(1) === 1 && initialized && !pending;
    const s = live ? read() : null;
    const ended = !!s && s.ended;
    return {
      schemaVersion: '1.0.0', gameId: GAME_ID, timestampMs: Date.now(),
      gameTimeMs: Math.round(episodeTicks * 1000 / 30),
      status: error ? 'error' : !live ? 'loading' : ended ? 'success' : s.mode,
      is_actionable: live && !error && !ended,
      terminal: {isTerminal: ended, outcome: ended ? 'success' : null,
        reason: ended ? 'great_door_opened' : null},
      game_state: Object.assign({start: START, start_match: startMatch},
        s || {mode: 'loading', room: [0, 0], player: {x: 0, y: 0}, carrying: 'none',
          west_temple_open: false, east_temple_open: false, west_statue_lit: false,
          east_statue_lit: false, fire_power: false, fire_form: false, dead: false, ended: false,
          rooms_visited: 0, tablet_reading: 0, final_door_rise: 0, deaths: 0, items: []}),
      metrics: Object.assign({rooms_visited: s ? s.rooms_visited : 0, deaths: s ? s.deaths : 0}, best),
      provenance: {game_state: 'internal', metrics: 'internal',
        gameTimeMs: 'internal',
        transport: 'Cart code publishes its own globals and actor fields via GPIO; ' +
          '*_progress combines those with static room-graph distances from the cart map.'},
      debug: {protocol: byte(1), start_ack: byte(122), frame: word(2), error}
    };
  }

  window.gameAPI = {
    init, getState,
    reset() {
      if (pending) return pending.promise;
      return startEpisode();
    }
  };
  requestAnimationFrame(loop);
  init().catch(e => { error = String(e); console.error(e); });
})();
