(function () {
  "use strict";

  // Policy probe for 363_dusk-child (PICO-8 0.1.10B player).
  //
  // Read-only sources, all present or past state:
  //   * window.pico8_gpio: the cart's own per-frame pins (player px/py, room, flags,
  //     carried item, items with position/carried/water, tablet and door progress).
  //   * Module.HEAPU8. The PICO-8 memory image is located by its GPIO block at
  //     base+0x5f80 matching pico8_gpio byte for byte; its map area (0x2000 rows 0..31,
  //     0x1000 rows 32..63) holds the cart's map as loaded. The 0.1.10B player keeps
  //     the map that mget/mset use in a separate 128x64 byte array; it is found as the
  //     copy of that map whose static rows match while the tiles the cart's _init
  //     always rewrites (the room-2 shelf and its door) differ. Nothing is written.
  //   * A requestAnimationFrame sampler that keeps the last few cart frames of
  //     player position, for velocity.
  // Static world constants (door, shelf, statue, lock and tablet tiles) are taken from
  // the cart map. Doors and statue heads only update their tiles while the player is
  // in their room, so outside the current room their tiles are filled in from the
  // rules the cart applies (weighted shelf, open temple, lit statue).
  // Withheld: the Lua RNG, message text, and anything not yet happened.

  var ITEM = { 3: "orb", 4: "bucket", 9: "keystone" };
  var MODES = ["title", "fade", "playing", "dying", "cutscene", "ending"];
  // [tile x, tile y, kind] kind 0 shelf door, 1 west temple door, 2 east temple door;
  // a door fills tiles (x, y) and (x, y - 1).
  var DOORS = [[42, 10, 0], [51, 10, 0], [80, 19, 0], [102, 21, 0], [55, 54, 0],
    [87, 45, 0], [106, 53, 0], [125, 54, 0], [66, 23, 1], [25, 40, 1], [77, 23, 2],
    [107, 37, 2]];
  var SHELVES = [[38, 10], [55, 10], [83, 19], [98, 21], [58, 54], [83, 47], [103, 53],
    [115, 54]];
  // lightable statues [tile x, tile y, side] side 0 west (wlit), 1 east (elit);
  // the hand box is tile (x, y - 1), which turns into fire once lit.
  var STATUES = [[6, 34, 0], [67, 34, 0], [76, 34, 1], [123, 5, 1]];
  var LOCKS = [[68, 22, "west"], [74, 22, "east"]];
  var TABLET = [40, 47];
  var FINAL_DOOR = [70, 36];

  var base = -1, lastScan = -1e9, calls = 0, lastHash = null, eyedols = null;
  var live = -1, lastLiveScan = -1e9;
  var hist = [];

  function pins() { return window.pico8_gpio || window.gpio_data || []; }
  function pin(i) { return (pins()[i] | 0) & 255; }
  function word(i) { return pin(i) | (pin(i + 1) << 8); }
  function bit(i, n) { return ((pin(i) >> n) & 1) === 1; }
  function realNow() { return (window.__realDateNow || Date.now)(); }

  function emptyState() {
    return { ready: false, mode: "loading", frame: null, player: null, map: null,
      map_hash: null, items: [], doors: [], shelves: [], statues: [], eyedols: [] };
  }

  function heap() {
    var M = window.Module;
    return M && M.HEAPU8 && M.HEAPU8.length ? M.HEAPU8 : null;
  }

  function valid(h, b) {
    if (b < 0 || b + 0x6000 > h.length) return false;
    if (h[b + 0x5f80] !== 68 || h[b + 0x5f81] !== 1) return false;
    for (var k = 4; k < 12; k++) if (h[b + 0x5f80 + k] !== pin(k)) return false;
    return true;
  }

  function locate(h) {
    if (valid(h, base)) return base;
    base = -1;
    var now = realNow();
    if (now - lastScan < 500) return -1;
    lastScan = now;
    var i = -1, end = h.length - 0x6000 + 0x5f80;
    while ((i = h.indexOf(68, i + 1)) !== -1 && i < end) {
      if (valid(h, i - 0x5f80)) { base = i - 0x5f80; break; }
    }
    return base;
  }

  function romTile(h, b, x, y) {
    return h[b + (y < 32 ? 0x2000 + y * 128 : 0x1000 + (y - 32) * 128) + x];
  }

  // static rows used to recognise the live map array: 0 (top wall), 41 and 55 (floors)
  var CHECK_ROWS = [0, 13, 41, 55];
  function liveValid(h, b, m) {
    if (m < 0 || m + 8192 > h.length || m === b + 0x2000) return false;
    for (var i = 0; i < CHECK_ROWS.length; i++) {
      var y = CHECK_ROWS[i];
      for (var x = 0; x < 128; x += 3) if (h[m + y * 128 + x] !== romTile(h, b, x, y)) return false;
    }
    // _init clears the room-2 shelf (38,10) and the cell above it, which the ROM keeps
    return romTile(h, b, 38, 10) === 57 && h[m + 10 * 128 + 38] === 0 && h[m + 9 * 128 + 38] === 0;
  }

  function locateLive(h, b) {
    if (liveValid(h, b, live)) return live;
    live = -1;
    var now = realNow();
    if (now - lastLiveScan < 500) return -1;
    lastLiveScan = now;
    var first = romTile(h, b, 0, 0), i = -1;
    while ((i = h.indexOf(first, i + 1)) !== -1 && i + 8192 <= h.length) {
      if (liveValid(h, b, i)) { live = i; break; }
    }
    return live;
  }

  function tile(h, m, x, y) {
    if (x < 0 || x > 127 || y < 0 || y > 63) return 0;
    return h[m + y * 128 + x];
  }

  function cls(t) {
    if (t >= 64) return "#";
    if (t === 30 || t === 31) return "v";
    if (t === 19) return "f";
    if (t === 20) return "w";
    if (t === 47) return "m";
    if (t === 10) return "e";
    return ".";
  }

  function roomOfTile(x, y) { return [Math.floor(x * 8 / 128), Math.floor(y * 8 / 112)]; }

  function sample() {
    if (pin(0) !== 68 || pin(1) !== 1) return;
    var f = word(2), last = hist.length ? hist[hist.length - 1] : null;
    if (last && last.f === f) return;
    if (last && (f < last.f || f - last.f > 8)) hist = [];
    hist.push({ f: f, x: word(7), y: word(9), g: bit(13, 2) });
    if (hist.length > 6) hist.shift();
  }

  function loop() {
    try { sample(); } catch (e) { /* keep sampling */ }
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);

  function velocity() {
    if (hist.length < 2) return [0, 0];
    var a = hist[hist.length - 1], b = hist[Math.max(0, hist.length - 3)];
    var df = a.f - b.f;
    if (df <= 0) return [0, 0];
    return [(a.x - b.x) / df, (a.y - b.y) / df];
  }

  function readItems() {
    var items = [], n = Math.min(8, pin(31));
    for (var i = 0; i < n; i++) {
      var at = 32 + i * 5, info = pin(at + 4);
      items.push({ type: ITEM[info & 31] || String(info & 31), x: word(at), y: word(at + 2),
        carried: (info & 32) !== 0, water: (info & 64) !== 0 });
    }
    return items;
  }

  function hashStr(s) {
    var h = 5381;
    for (var i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) | 0;
    return (h >>> 0).toString(16);
  }

  function getState() {
    try {
      if (pin(0) !== 68 || pin(1) !== 1) return emptyState();
      sample();
      var h = heap();
      if (!h) return emptyState();
      var b = locate(h);
      if (b < 0) return emptyState();
      var m = locateLive(h, b);
      if (m < 0) return emptyState();
      calls++;

      var px = word(7), py = word(9), room = [pin(5), pin(6)];
      var flags = { west_open: bit(11, 0), west_lit: bit(11, 1), east_open: bit(11, 2),
        east_lit: bit(11, 3) };
      var items = readItems();
      var carrying = ITEM[pin(12)] || "none";
      var free = items.filter(function (it) { return !it.carried && it.x < 9000; });

      // live map as class chars, rows 0..63
      var rows = [];
      for (var y = 0; y < 64; y++) {
        var r = "";
        for (var x = 0; x < 128; x++) r += cls(tile(h, m, x, y));
        rows.push(r);
      }
      function put(x, y, c) { rows[y] = rows[y].substring(0, x) + c + rows[y].substring(x + 1); }
      function inRoom(x, y) { var q = roomOfTile(x, y); return q[0] === room[0] && q[1] === room[1]; }

      var shelves = SHELVES.map(function (s) {
        var sx = s[0] * 8, sy = s[1] * 8;
        var weighted = free.some(function (it) {
          return (it.type === "orb" || it.type === "keystone" || (it.type === "bucket" && it.water)) &&
            sx + 7 > it.x && sx < it.x + 7 && sy + 7 > it.y && sy < it.y + 7;
        });
        return { x: sx, y: sy, room: roomOfTile(s[0], s[1]), weighted: weighted };
      });
      var doors = DOORS.map(function (d) {
        var dr = roomOfTile(d[0], d[1]), open;
        if (inRoom(d[0], d[1])) open = tile(h, m, d[0], d[1]) < 64;
        else if (d[2] === 1) open = flags.west_open;
        else if (d[2] === 2) open = flags.east_open;
        else open = shelves.some(function (s) { return s.weighted && s.room[0] === dr[0] && s.room[1] === dr[1]; });
        if (!inRoom(d[0], d[1])) { put(d[0], d[1], open ? "." : "#"); put(d[0], d[1] - 1, open ? "." : "#"); }
        return { x: d[0] * 8, y: d[1] * 8, kind: ["shelf", "west", "east"][d[2]], room: dr, open: open };
      });
      var statues = STATUES.map(function (s) {
        var lit = s[2] === 0 ? flags.west_lit : flags.east_lit;
        if (!inRoom(s[0], s[1] - 1)) put(s[0], s[1] - 1, lit ? "f" : ".");
        return { hand_x: s[0] * 8, hand_y: s[1] * 8 - 8, side: s[2] === 0 ? "west" : "east",
          room: roomOfTile(s[0], s[1] - 1), lit: lit };
      });

      if (!eyedols) {
        eyedols = [];
        for (var ey = 0; ey < 56; ey++) for (var ex = 0; ex < 128; ex++)
          if (tile(h, m, ex, ey) === 10) eyedols.push([ex, ey]);
      }
      var carriedOrb = carrying === "orb";
      var eyes = eyedols.map(function (e) {
        var er = roomOfTile(e[0], e[1]);
        var pac = carriedOrb || free.some(function (it) {
          return it.type === "orb" && Math.floor(it.x / 128) === er[0] && Math.floor(it.y / 112) === er[1];
        });
        return { x: e[0] * 8 + 8, y: e[1] * 8 + 8, room: er, pacified: pac };
      });

      var mapStr = rows.join("\n"), mh = hashStr(mapStr);
      var sendMap = mh !== lastHash || calls % 30 === 0;
      lastHash = mh;
      var v = velocity();
      var visited = [];
      for (var rr = 0; rr < 32; rr++) if (bit(15 + (rr >> 3), rr & 7)) visited.push(rr);

      return {
        ready: true,
        mode: MODES[pin(4)] || "unknown",
        frame: word(2),
        room: room,
        player: { x: px, y: py, vx: v[0], vy: v[1], grounded: bit(13, 2), crouching: bit(13, 1),
          dead: bit(11, 6), fire_form: bit(11, 5) },
        cutscene: bit(13, 0),
        fire_power: bit(11, 4),
        ended: bit(11, 7),
        west_open: flags.west_open, west_lit: flags.west_lit,
        east_open: flags.east_open, east_lit: flags.east_lit,
        carrying: carrying,
        items: items,
        rooms_visited: pin(14),
        visited_rooms: visited,
        tablet_stage: pin(19),
        final_door_rise: pin(20),
        deaths: pin(21),
        ever: { orb: bit(22, 0), keystone: bit(22, 1), shelf_door_open: bit(22, 4),
          lock_slotted: bit(22, 5) },
        doors: doors,
        shelves: shelves,
        statues: statues,
        locks: LOCKS.map(function (l) {
          return { x: l[0] * 8, y: l[1] * 8, side: l[2],
            set: l[2] === "west" ? flags.west_open : flags.east_open };
        }),
        tablet: { x: TABLET[0] * 8, y: TABLET[1] * 8 },
        final_door: { x: FINAL_DOOR[0] * 8, y: FINAL_DOOR[1] * 8 },
        eyedols: eyes,
        map_hash: mh,
        map: sendMap ? mapStr : null
      };
    } catch (e) {
      var s = emptyState();
      s.error = String(e);
      return s;
    }
  }

  window.__policyProbe = { getState: getState };
})();
