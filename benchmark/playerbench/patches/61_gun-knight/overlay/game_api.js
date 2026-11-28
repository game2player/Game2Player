(function () {
  "use strict";

  // This file runs before GameMaker_Init copies object callbacks out of _04.
  // No gameplay bundle edits: callbacks below call the original native event.
  const GAME_ID = "61_gun-knight";
  const TASKS = {
    "61_01": ["floors_cleared", 3],
    "61_02": ["attachment_slots_replaced", 2],
    "61_03": ["shop_purchases", 2],
    "61_04": ["first_boss_progress", 10],
    "61_05": ["second_boss_progress", 20]
  };
  const requested = new URLSearchParams(location.search).get("task") || "61_01";
  const task = Object.prototype.hasOwnProperty.call(TASKS, requested) ? requested : "61_01";
  const slots = {
    26: ["magazine", "_vy"], 27: ["trigger", "_by"],
    28: ["grip", "_uy"], 29: ["barrel", "_ky"],
    30: ["stock", "_sy"], 31: ["scope", "_Tz"], 32: ["ammunition", "_Cy"]
  };
  let seed = 42;
  let phase = "boot";
  let previousPlayer = null;
  let ready = false;
  let waiters = [];
  let elapsed = 0;
  let cleared = new Set();
  let replaced = new Set();
  let purchased = 0;
  let kills = 0;
  let lastEvent = null;
  let lastGameplay = null;
  let firstBoss = { damage: 0, defeated: false };
  let secondBoss = { damage: 0, defeated: false };
  let terminal = { isTerminal: false, outcome: null, reason: null };

  const num = value => typeof value === "number" && Number.isFinite(value) ? value : 0;
  const globals = () => window.global || {};
  const instances = index => typeof window._ef === "function" && window._9E
    ? window._ef(index).filter(instance => !instance._hf) : [];
  const room = () => window._9E ? window._9E.id : -1;
  const player = () => instances(1)[0] || null;
  const active = () => phase === "playing" && !terminal.isTerminal && room() === 4;

  // The native save only stores a name and options, not dungeon progress. Give
  // its name prompt the same answer on each clean browser launch.
  localStorage.setItem("GUNKNIGHT.297858076.savedata.ini",
    '[save1]\r\nname="Knight"\r\nfullscreen=0\r\n');

  // GameMaker uses its own RNG and randomize() reads Date milliseconds. Replace
  // only randomize with the engine's supported seed operation; draws stay native.
  window._3w = window._nf1 = function () { return window._ef1(seed); };

  function record(name, detail) {
    lastEvent = { name, gameTimeMs: elapsed, floor: num(globals()._Jy), ...detail };
  }

  function wrap(objectIndex, key, after, before) {
    const original = window._04._c7[objectIndex][key];
    if (typeof original !== "function") throw new Error("Missing Gun Knight native callback");
    window._04._c7[objectIndex][key] = function (self, other) {
      const context = before ? before(self) : null;
      const result = original.apply(this, arguments);
      after(self, context);
      return result;
    };
  }

  // Native obj_roomgen Step (_c8). Entry increments _Jy; clearing sets _5z.
  // A generation frame must not clear its newly entered floor. The Set also
  // excludes repeated Step events and the empty floor-zero foyer.
  wrap(9, "_q7", function (self, before) {
    const g = globals();
    if (active() && before.floor > 0 && before.generate === 0 &&
        num(g._Jy) === before.floor && g._5z === 1 && self._8B === 0 &&
        !cleared.has(before.floor)) {
      cleared.add(before.floor);
      record("floor_cleared", { floor: before.floor });
    }
  }, () => ({ floor: num(globals()._Jy), generate: num(globals()._7z) }));

  // Successful pickup/purchase handlers set _2D before destroying the item.
  // Position, affordability and E key checks remain entirely in the game.
  function exchanged(self, before) {
    if (!active() || before.done || self._2D !== 1 || !slots[before.sprite]) return;
    replaced.add(slots[before.sprite][0]);
    if (before.paid && before.gold - num(globals()._bz) === before.price) purchased++;
    record(before.paid ? "attachment_purchased" : "attachment_replaced", {
      slot: slots[before.sprite][0], price: before.paid ? before.price : 0
    });
  }
  for (const index of [63, 64]) {
    wrap(index, "_q7", exchanged, self => ({
      done: self._2D === 1, sprite: self._Sg, paid: index === 64,
      gold: num(globals()._bz), price: num(self._4D)
    }));
  }

  // Destroy events also run during room teardown. HP <= 0, active run and the
  // exact floor distinguish combat death from teardown/restart.
  wrap(21, "_P7", function (self, before) {
    if (before.combat && before.hp <= 0) {
      kills++;
      record("enemy_defeated", { object: before.type });
    }
  }, self => ({ combat: active(), hp: num(self._Ix), type: self._5B }));

  function bossHook(index, floor, tracker) {
    wrap(index, "_P7", function (self, before) {
      if (before.combat && before.floor === floor && before.hp <= 0) {
        kills++;
        tracker().defeated = true;
        tracker().damage = 1;
        record("boss_defeated", { object: index });
      }
    }, self => ({ combat: active(), floor: num(globals()._Jy), hp: num(self._Ix) }));
  }
  bossHook(37, 10, () => firstBoss);
  bossHook(40, 20, () => secondBoss);

  function metrics() {
    function bossProgress(targetFloor, boss) {
      if (boss.defeated) return targetFloor;
      const prior = [...cleared].filter(floor => floor < targetFloor).length;
      // Zero HP alone cannot reach success: require the native Destroy event.
      return prior + Math.min(0.999999, boss.damage);
    }
    return {
      floors_cleared: cleared.size,
      attachment_slots_replaced: replaced.size,
      shop_purchases: purchased,
      first_boss_progress: bossProgress(10, firstBoss),
      second_boss_progress: bossProgress(20, secondBoss),
      enemies_defeated: kills
    };
  }

  function readGameplay() {
    const g = globals(), p = player();
    const enemies = instances(21);
    const boss = instances(37)[0] || instances(40)[0] || instances(43)[0];
    const attachments = {};
    for (const [name, key] of Object.values(slots)) attachments[name] = p ? num(p[key]) : 0;
    function item(self) {
      return { x: self.x, y: self.y, slot: slots[self._Sg]?.[0] || "unknown",
        name: String(self._ah || ""), variant: num(self._Rg), price: num(self._4D) };
    }
    return {
      floor: num(g._Jy), character: String(g._nz || "gunknight"),
      health: num(g._Ix), max_health: num(g._Hx), gold: num(g._bz),
      floor_clear: g._5z === 1, transitioning: g._6z === 1,
      dead: g._Ix <= 0 || !!(p && p._9A),
      player: { x: p ? p.x : 0, y: p ? p.y : 0,
        ammo: p ? num(p._kx) : 0, capacity: p ? num(p._ty) : 0,
        reloading: !!(p && p._Qz), trigger: p ? String(p._Xw) : "semi" },
      attachments,
      enemy_count: enemies.length,
      enemies: enemies.slice(0, 128).map(self => ({ id: self.id,
        object: num(self._5B), x: self.x, y: self.y, health: num(self._Ix) })),
      boss: { active: !!boss, health: boss ? num(boss._Ix) : 0,
        max_health: boss ? num(g._fz) : 0,
        first_defeated: firstBoss.defeated, second_defeated: secondBoss.defeated },
      dropped_parts: instances(63).map(item), shop_parts: instances(64).map(item)
    };
  }

  function observe() {
    const g = globals();
    for (const [floor, index, tracker] of [[10, 37, firstBoss], [20, 40, secondBoss]]) {
      const boss = instances(index)[0];
      if (g._Jy === floor && boss && num(g._fz) > 0) {
        tracker.damage = Math.max(tracker.damage,
          Math.min(1, Math.max(0, 1 - num(boss._Ix) / g._fz)));
      }
    }
    lastGameplay = readGameplay();
    if (!terminal.isTerminal) {
      const [field, target] = TASKS[task];
      if (metrics()[field] >= target) {
        terminal = { isTerminal: true, outcome: "success", reason: task };
      } else if (lastGameplay.dead) {
        terminal = { isTerminal: true, outcome: "fail", reason: "knight_died" };
      }
    }
  }

  function driveStart() {
    if (phase === "boot" && room() >= 0) {
      // Use the disclaimer button's named handler, then let rm_init load options.
      window._mc(null, null);
      phase = "menu";
    } else if (phase === "menu" && room() === 3 && instances(75).length) {
      const play = instances(75)[0];
      window._Eb(play, play);
      phase = "starting";
    }
  }

  const nativeTick = window._cf3;
  window._cf3 = function () {
    driveStart();
    const result = nativeTick.apply(this, arguments);
    const p = player(), g = globals();
    // _Ai only schedules a room change; _Oe3 applies it AFTER this callback.
    // Never resolve against the old player while that change is still pending.
    if (phase === "starting" && window._mk1 === -1 && room() === 4 && p &&
        p !== previousPlayer && p._Gz === 1 && p._e7 === 1 &&
        g._Ix === 3 && g._Hx === 3 && g._Jy === 0 && g._bz === 0 &&
        p._kx === 6 && instances(85).length === 0) {
      phase = "playing";
      ready = true;
      elapsed = 0;
      lastGameplay = readGameplay();
      for (const resolve of waiters.splice(0)) resolve(getState());
    } else if (phase === "playing" && room() === 4) {
      elapsed += 1000 / Math.max(1, window._5e._Le());
      observe();
    }
    return result;
  };

  function getState() {
    const playing = room() === 4 && phase === "playing";
    const data = playing ? readGameplay() : lastGameplay;
    const empty = { floor: 0, character: "gunknight", health: 0, max_health: 0,
      gold: 0, floor_clear: false, transitioning: false, dead: false,
      player: { x: 0, y: 0, ammo: 0, capacity: 0, reloading: false, trigger: "semi" },
      attachments: {}, enemy_count: 0, enemies: [], boss: { active: false,
        health: 0, max_health: 0, first_defeated: false, second_defeated: false },
      dropped_parts: [], shop_parts: [] };
    return {
      schemaVersion: "1.0", gameId: GAME_ID, timestampMs: Date.now(), gameTimeMs: elapsed,
      status: terminal.isTerminal ? terminal.outcome === "success" ? "success" : "failed"
        : ready ? "playing" : "loading",
      is_actionable: ready, terminal: { ...terminal },
      game_state: { ...(data || empty), task_id: task, seed, room: room(),
        room_name: room() >= 0 ? window._04._oc[room()]?._34 || "unknown" : "loading",
        cleared_floors: [...cleared].sort((a, b) => a - b),
        replaced_slots: [...replaced].sort(), last_event: lastEvent },
      metrics: metrics(),
      provenance: {
        game_state: "internal", "game_state.cleared_floors": "engine_hook",
        "game_state.replaced_slots": "engine_hook", "game_state.last_event": "engine_hook",
        "game_state.boss.first_defeated": "engine_hook",
        "game_state.boss.second_defeated": "engine_hook", metrics: "engine_hook"
      }
    };
  }

  function reset(options = {}) {
    if (Number.isFinite(Number(options.seed))) seed = Number(options.seed) >>> 0;
    previousPlayer = player();
    ready = false;
    cleared = new Set(); replaced = new Set(); purchased = 0; kills = 0;
    firstBoss = { damage: 0, defeated: false };
    secondBoss = { damage: 0, defeated: false };
    terminal = { isTerminal: false, outcome: null, reason: null };
    elapsed = 0; lastEvent = null; lastGameplay = null;
    window.__resetRandomSeed?.();
    window._Ar2 = false;
    // Reentering nonpersistent rm_1 runs the same native Create events as Play.
    if (room() === 4) { phase = "starting"; window._Ai(4); }
    else phase = "boot";
    return new Promise(resolve => waiters.push(resolve));
  }

  window.gameAPI = {
    getState, reset,
    init(options = {}) {
      const incoming = Number.isFinite(Number(options.seed)) ? Number(options.seed) >>> 0 : seed;
      if (ready && incoming !== seed) return reset(options);
      seed = incoming;
      if (ready) return Promise.resolve(getState());
      return new Promise(resolve => waiters.push(resolve));
    },
    pause() { window._Ar2 = true; },
    resume() { window._Ar2 = false; }
  };
  document.addEventListener("contextmenu", event => event.preventDefault());
})();
