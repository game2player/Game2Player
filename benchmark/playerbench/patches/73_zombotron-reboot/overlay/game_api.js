(function installZombotronAdapter() {
  "use strict";

  const GAME_ID = "73_zombotron-reboot";
  if (window.unityAdapter && window.unityAdapter.gameId === GAME_ID) return;

  const NATIVE_W = 960;
  const NATIVE_H = 600;
  const ANT_ENGINE_TYPE_INFO = 4957688;
  const GET_SCENARIO_TABLE = 28226;
  const RETURN_TO_MENU_TABLE = 24950; // PauseController.RetreatHandler(AntEvent).
  const REVIVE_TAP_TABLE = 25033; // ReviveController.TapHandler().

  const STORE_KEY = "gameapi." + GAME_ID;

  function storeRead() {
    try {
      return JSON.parse(window.sessionStorage.getItem(STORE_KEY) || "{}") || {};
    } catch (_error) {
      return {};
    }
  }

  function storeWrite(patch) {
    try {
      const stored = storeRead();
      Object.assign(stored, patch);
      window.sessionStorage.setItem(STORE_KEY, JSON.stringify(stored));
    } catch (_error) {}
  }

  const persisted = storeRead();

  const session = {
    episodeStartMs: Date.now(),
    survivedMs: 0,
    lastTickMs: null,
    episodeCount: (Number.isFinite(persisted.episodeCount) ? persisted.episodeCount : 0) + 1,
    lastResetMethod: typeof persisted.lastResetMethod === "string" ? persisted.lastResetMethod : null,
    lastHp: null,
    lastWeapons: null,
    won: 0,
    sawGameplay: false,
    dead: false
  };
  storeWrite({ episodeCount: session.episodeCount });

  const cache = {
    track: 0,
    stateSvc: 0,
    modeSvc: 0,
    ingame: 0,
    pause: 0,
    revive: 0,
    lobby: 0,
    transition: 0,
    klasses: Object.create(null)
  };

  let initPromise = null;
  let started = false;
  let startupError = null;

  const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
  const finite = (value) => Number.isFinite(value) ? Number(value) : null;
  const counter = (value) => {
    const number = finite(value);
    return number !== null && number >= 0 && number < 1000000 ? Math.floor(number) : null;
  };

  function tickSurvived(now, running) {
    if (session.lastTickMs != null && running) {
      session.survivedMs += Math.max(0, now - session.lastTickMs);
    }
    session.lastTickMs = now;
    return session.survivedMs / 1000;
  }

  function beginEpisode() {
    session.episodeStartMs = Date.now();
    session.survivedMs = 0;
    session.lastTickMs = null;
    session.lastHp = null;
    session.lastWeapons = null;
    session.won = 0;
    session.sawGameplay = false;
    session.dead = false;
    cache.track = 0;
    cache.stateSvc = 0;
    cache.modeSvc = 0;
    cache.ingame = 0;
    cache.pause = 0;
    cache.revive = 0;
    cache.lobby = 0;
    cache.transition = 0;
  }

  function internal(state) {
    return state && state.game_state && state.game_state.internal
      ? state.game_state.internal
      : {};
  }

  function rawState(bridge) {
    return bridge && typeof bridge.getState === "function" ? bridge.getState() : null;
  }

  function unityReady(bridge) {
    const status = bridge && typeof bridge.status === "function" ? bridge.status() : null;
    return Boolean(
      status
      && status.hasMemory
      && status.memoryBytes > 120 * 1024 * 1024
      && (status.hasUnityInstance || status.runtimeLocked)
    );
  }

  async function waitFor(bridge, label, predicate, timeoutMs) {
    const deadline = performance.now() + timeoutMs;
    while (performance.now() < deadline) {
      if (predicate(bridge)) return true;
      await sleep(100);
    }
    throw new Error(`Timed out waiting for ${label}`);
  }

  function cstr(bridge, addr) {
    if (!addr) return "";
    let value = "";
    for (let i = 0; i < 80; i += 1) {
      const code = bridge.read("u8", addr + i);
      if (!code) break;
      if (code < 32 || code > 126) return value;
      value += String.fromCharCode(code);
    }
    return value;
  }

  function className(bridge, obj) {
    if (!obj) return "";
    try {
      const klass = bridge.read("ptr", obj);
      return klass ? cstr(bridge, bridge.read("ptr", klass + 8)) : "";
    } catch (_error) {
      return "";
    }
  }

  function readScenario(bridge, values) {
    const fromProfile = finite(values && values.scenario);
    if (fromProfile) return fromProfile;
    try {
      const classPtr = bridge.read("ptr", ANT_ENGINE_TYPE_INFO);
      const staticFields = classPtr ? bridge.read("ptr", classPtr + 92) : 0;
      if (staticFields) return bridge.read("ptr", staticFields + 8) >>> 0;
    } catch (_error) {}
    try {
      return bridge.call(GET_SCENARIO_TABLE, [0]) >>> 0;
    } catch (_error) {
      return 0;
    }
  }

  function walkList(bridge, listPtr, pairLayout) {
    const found = [];
    if (!listPtr) return found;
    try {
      const items = bridge.read("ptr", listPtr + 8);
      const size = bridge.read("i32", listPtr + 12);
      const limit = Math.min(Math.max(size, 0), 160);
      for (let i = 0; i < limit; i += 1) {
        if (pairLayout) {
          const pair = items ? bridge.read("ptr", items + 16 + i * 4) : 0;
          const sys = pair ? bridge.read("ptr", pair + 8) : 0;
          if (sys) found.push(sys);
        } else {
          const obj = items ? bridge.read("ptr", items + 16 + i * 4) : 0;
          if (obj) found.push(obj);
        }
      }
    } catch (_error) {}
    return found;
  }

  function collectSystems(bridge, scenario, depth, seen, bucket) {
    if (!scenario || depth > 5 || seen.has(scenario)) return;
    seen.add(scenario);
    const systems = walkList(bridge, bridge.read("ptr", scenario + 16), true);
    for (let i = 0; i < systems.length; i += 1) {
      const obj = systems[i];
      const name = className(bridge, obj);
      if (!name) continue;
      bucket[name] = obj;
      if (name === "Menu" || name === "Service" || name === "Gameplay" || name === "Player" || name === "AntScenario") {
        collectSystems(bridge, obj, depth + 1, seen, bucket);
      }
    }
    const controllers = walkList(bridge, bridge.read("ptr", scenario + 64), false);
    for (let i = 0; i < controllers.length; i += 1) {
      const obj = controllers[i];
      const name = className(bridge, obj);
      if (name) bucket[name] = obj;
    }
  }

  function refreshServices(bridge, values) {
    const scenario = readScenario(bridge, values);
    if (!scenario) return null;
    const bucket = Object.create(null);
    collectSystems(bridge, scenario, 0, new Set(), bucket);
    const names = ["TrackService", "GameStateService", "GameModeService", "IngameController", "PauseController", "ReviveController", "LobbyController", "TransitionController"];
    for (let i = 0; i < names.length; i += 1) {
      const obj = bucket[names[i]] || 0;
      if (!obj) continue;
      const key = names[i] === "TrackService" ? "track"
        : names[i] === "GameStateService" ? "stateSvc"
        : names[i] === "GameModeService" ? "modeSvc"
        : names[i] === "IngameController" ? "ingame"
        : names[i] === "PauseController" ? "pause"
        : names[i] === "ReviveController" ? "revive"
        : names[i] === "TransitionController" ? "transition"
        : "lobby";
      cache[key] = obj;
      cache.klasses[names[i]] = bridge.read("ptr", obj);
    }
    return bucket;
  }

  function livePtr(bridge, ptr, klassName) {
    if (!ptr) return 0;
    try {
      const expected = cache.klasses[klassName];
      if (expected && bridge.read("ptr", ptr) !== expected) return 0;
      return ptr;
    } catch (_error) {
      return 0;
    }
  }

  function countWeapons(bridge, player) {
    if (!player) return null;
    try {
      const equipment = bridge.read("ptr", player + 56);
      const slots = equipment ? bridge.read("ptr", equipment + 36) : 0;
      if (!slots) return null;
      const size = bridge.read("i32", slots + 12);
      const limit = Math.min(Math.max(size, 0), 12);
      let weapons = 0;
      for (let i = 0; i < limit; i += 1) {
        const slot = bridge.read("ptr", slots + 16 + i * 4);
        if (!slot) continue;
        const kind = bridge.read("i32", slot + 8);
        const item = bridge.read("ptr", slot + 16);
        if (kind >= 0 && kind <= 3 && item) weapons += 1;
      }
      return weapons;
    } catch (_error) {
      return null;
    }
  }

  function snapshotWorld(bridge, values) {
    let track = livePtr(bridge, cache.track, "TrackService");
    let stateSvc = livePtr(bridge, cache.stateSvc, "GameStateService");
    let modeSvc = livePtr(bridge, cache.modeSvc, "GameModeService");
    let ingame = livePtr(bridge, cache.ingame, "IngameController");
    let pause = livePtr(bridge, cache.pause, "PauseController");
    let revive = livePtr(bridge, cache.revive, "ReviveController");
    if (!track || !stateSvc || !ingame) {
      refreshServices(bridge, values);
      track = cache.track;
      stateSvc = cache.stateSvc;
      modeSvc = cache.modeSvc;
      ingame = cache.ingame;
      pause = cache.pause;
      revive = cache.revive;
    }
    const world = {
      state: 0,
      kills: 0,
      money: 0,
      floor: 0,
      secrets: 0,
      weapons: null,
      hp: null,
      hpMax: null,
      paused: false,
      reviving: false,
      tapToStart: false,
      eventTap: 0
    };
    try {
      if (cache.transition) world.tapToStart = bridge.read("u8", cache.transition + 33) === 1;
    } catch (_error) {}
    try {
      world.eventTap = eventTapPtr(bridge);
    } catch (_error) {}
    try {
      if (stateSvc) world.state = bridge.read("i32", stateSvc + 12);
    } catch (_error) {}
    try {
      if (track) {
        world.kills = bridge.read("i32", track + 16);
        world.secrets = bridge.read("i32", track + 20);
        world.money = bridge.read("i32", track + 24);
        world.floor = bridge.read("i32", track + 36);
      }
    } catch (_error) {}
    try {
      if (ingame) {
        const player = bridge.read("ptr", ingame + 16);
        const health = player ? bridge.read("ptr", player + 60) : 0;
        if (health) {
          world.hp = bridge.read("f32", health + 76);
          world.hpMax = bridge.read("f32", health + 72);
        }
        const weapons = countWeapons(bridge, player);
        if (weapons != null) world.weapons = weapons;
      }
    } catch (_error) {}
    try {
      if (pause) world.paused = bridge.read("u8", pause + 32) === 1;
    } catch (_error) {}
    try {
      if (revive) world.reviving = bridge.read("u8", revive + 32) === 1;
    } catch (_error) {}
    return world;
  }

  function canvasPoint(x, y) {
    const canvas = document.querySelector("#unity-canvas") || document.querySelector("canvas");
    if (!canvas) throw new Error("Unity canvas is unavailable");
    const rect = canvas.getBoundingClientRect();
    return {
      canvas,
      clientX: rect.left + x * rect.width / NATIVE_W,
      clientY: rect.top + y * rect.height / NATIVE_H,
    };
  }

  async function clickCanvas(x, y) {
    const point = canvasPoint(x, y);
    point.canvas.focus();
    const base = {
      bubbles: true,
      cancelable: true,
      view: window,
      button: 0,
      clientX: point.clientX,
      clientY: point.clientY,
      pointerId: 1,
      pointerType: "mouse",
    };
    const down = Object.assign({}, base, { buttons: 1 });
    const up = Object.assign({}, base, { buttons: 0 });
    point.canvas.dispatchEvent(new MouseEvent("mousemove", up));
    if (window.PointerEvent) point.canvas.dispatchEvent(new PointerEvent("pointerdown", down));
    point.canvas.dispatchEvent(new MouseEvent("mousedown", down));
    await sleep(80);
    if (window.PointerEvent) point.canvas.dispatchEvent(new PointerEvent("pointerup", up));
    point.canvas.dispatchEvent(new MouseEvent("mouseup", up));
    point.canvas.dispatchEvent(new MouseEvent("click", up));
    await sleep(180);
  }

  function inGameplay(bridge) {
    const world = snapshotWorld(bridge, internal(rawState(bridge) || {}));
    return world.state === 2 && world.hp != null && world.hp > 0;
  }

  function safeCall(bridge, table, args) {
    try {
      return bridge.call(table, args);
    } catch (_error) {
      return null;
    }
  }

  function hasTapToStart(bridge) {
    const transition = cache.transition;
    if (!transition) return false;
    try {
      return bridge.read("u8", transition + 33) === 1;
    } catch (_error) {
      return false;
    }
  }

  function dismissTapToStart(bridge) {
    const transition = cache.transition;
    if (!transition) return false;
    return safeCall(bridge, 25272, [transition, 0]) !== null;
  }

  function eventTapPtr(bridge) {
    const lobby = livePtr(bridge, cache.lobby, "LobbyController") || cache.lobby;
    if (!lobby) return 0;
    try {
      const view = bridge.read("ptr", lobby + 8);
      const list = view ? bridge.read("ptr", view + 44) : 0;
      return list ? (bridge.read("ptr", list + 36) >>> 0) : 0;
    } catch (_error) {
      return 0;
    }
  }

  function tapOpeningLevel(bridge) {
    const eventTap = eventTapPtr(bridge);
    if (!eventTap) return false;
    safeCall(bridge, 81639, [eventTap, 0, 0]);
    return true;
  }

  async function startRun(bridge) {
    beginEpisode();
    await waitFor(bridge, "Unity memory", unityReady, 120000);
    await sleep(2500);
    refreshServices(bridge, internal(rawState(bridge) || {}));
    await waitFor(bridge, "lobby services", (api) => {
      refreshServices(api, internal(rawState(api) || {}));
      return Boolean(cache.lobby && cache.transition && cache.track);
    }, 30000);
    await waitFor(bridge, "stage 1 tap handler", (api) => {
      refreshServices(api, internal(rawState(api) || {}));
      if (hasTapToStart(api)) dismissTapToStart(api);
      return eventTapPtr(api) !== 0 && !hasTapToStart(api);
    }, 25000);
    if (!tapOpeningLevel(bridge)) {
      throw new Error("Opening stage tap handler is missing");
    }
    const playDeadline = performance.now() + 90000;
    while (performance.now() < playDeadline) {
      if (inGameplay(bridge)) break;
      if (hasTapToStart(bridge)) dismissTapToStart(bridge);
      await sleep(250);
    }
    if (!inGameplay(bridge)) {
      throw new Error("Timed out waiting for opening gameplay");
    }
    await sleep(800);
    const world = snapshotWorld(bridge, internal(rawState(bridge) || {}));
    if (!inGameplay(bridge)) {
      throw new Error("Opening stage did not remain active");
    }
    session.survivedMs = 0;
    session.lastTickMs = null;
    session.sawGameplay = true;
    started = true;
    return {
      ok: true,
      accepted: { seed: null, level: null, difficulty: null },
      applied: { seed: null, level: 1, difficulty: null },
      notes: ["local_snapshot", "auto_starts_stage_1"]
    };
  }

  function transformState(state, context) {
    const now = Date.now();
    const values = internal(state);
    const bridge = context && context.bridge;
    const world = bridge ? snapshotWorld(bridge, values) : {
      state: 0, kills: 0, money: 0, floor: 0, secrets: 0, weapons: 0, hp: null, hpMax: null, paused: false, reviving: false
    };
    const kills = counter(world.kills) || 0;
    const money = counter(world.money) || 0;
    const floor = counter(world.floor) || 0;
    const secrets = counter(world.secrets) || 0;
    const readHp = finite(world.hp);
    if (readHp != null) session.lastHp = readHp;
    const readWeapons = counter(world.weapons);
    if (readWeapons != null) session.lastWeapons = readWeapons;
    const weapons = session.lastWeapons != null ? session.lastWeapons : 0;
    const hp = session.lastHp;
    const playing = world.state === 2 && hp != null && hp > 0;
    const paused = world.paused || world.state === 3 || document.hidden;
    if (playing) session.sawGameplay = true;
    if (world.state === 4 && session.sawGameplay && hp != null && hp > 0) session.won = 1;
    if (started && session.sawGameplay && hp === 0 && !world.reviving && session.won === 0) {
      session.dead = true;
    }

    const inRun = Boolean(started && session.sawGameplay && !session.dead);
    const running = inRun && playing && !paused;
    const survivedS = tickSurvived(now, running);

    let terminal = { isTerminal: false, outcome: null, reason: null };
    if (startupError) {
      terminal = { isTerminal: true, outcome: "fail", reason: startupError };
    } else if (session.won === 1) {
      terminal = { isTerminal: true, outcome: "success", reason: "stage_cleared" };
    } else if (session.dead) {
      terminal = { isTerminal: true, outcome: "fail", reason: "player_defeated" };
    }

    const actionable = running && !terminal.isTerminal;

    state.schemaVersion = "2.0";
    state.gameId = GAME_ID;
    state.timestampMs = now;
    state.gameTimeMs = Math.max(0, Math.round(session.survivedMs));
    state.status = !started ? "loading"
      : terminal.isTerminal ? (terminal.outcome === "success" ? "won" : "dead")
      : paused ? "paused"
      : playing ? "playing" : "loading";
    state.is_actionable = actionable;
    state.terminal = terminal;
    state.game_state = {
      score: kills,
      level: floor,
      player: { kills: kills, money: money, floor: floor, secrets: secrets, weapons: weapons, hp: hp },
      environment: { survived_s: survivedS, paused: paused, state: world.state },
      completion_progress: session.won
    };
    state.metrics = {
      primary_score: kills,
      enemies: kills,
      money: money,
      floor: floor,
      secrets: secrets,
      weapons: weapons,
      won: session.won,
      survived_s: survivedS,
      hp: hp
    };
    state.debug = {
      started: started,
      state: world.state,
      hp: hp,
      hp_max: world.hpMax,
      paused: world.paused,
      reviving: world.reviving,
      secrets: secrets,
      weapons: weapons,
      track: cache.track,
      ingame: cache.ingame,
      tap_to_start: world.tapToStart,
      event_tap: world.eventTap,
      episode_count: session.episodeCount,
      last_reset_method: session.lastResetMethod,
      delivery: "local_snapshot"
    };
    return state;
  }

  window.unityAdapter = {
    gameId: GAME_ID,
    async init({ bridge }) {
      if (!initPromise) {
        initPromise = startRun(bridge).catch((error) => {
          startupError = error && error.message ? error.message : String(error);
          throw error;
        });
      }
      return initPromise;
    },
    transformState,
    async reset({ bridge }) {
      // Use the game's Main Menu action and stage-1 handler to recreate the run.
      // Reusing the loaded Unity instance avoids Chromium's reload-time crashes.
      started = false;
      startupError = null;
      if (typeof window.__resumeGame === "function") window.__resumeGame();
      refreshServices(bridge, internal(rawState(bridge) || {}));
      const world = snapshotWorld(bridge, internal(rawState(bridge) || {}));
      if (world.reviving) {
        // Finish the native revive UI's own cleanup before leaving the run.
        // Retreat is normally reached through Pause, outside this modal screen.
        const revive = livePtr(bridge, cache.revive, "ReviveController");
        if (!revive) throw new Error("Native revive controller is unavailable");
        bridge.call(REVIVE_TAP_TABLE, [revive, 0]);
        await waitFor(bridge, "native revive cleanup", (api) => {
          return !snapshotWorld(api, internal(rawState(api) || {})).reviving && inGameplay(api);
        }, 15000);
      }
      const pause = livePtr(bridge, cache.pause, "PauseController");
      if (!pause) throw new Error("Native Main Menu controller is unavailable");
      // Invoke the original Main Menu callback, also available on the revive screen
      // where the game's Escape binding intentionally does not open the pause UI.
      bridge.call(RETURN_TO_MENU_TABLE, [pause, 0, 0]);
      initPromise = startRun(bridge).catch((error) => {
        startupError = error && error.message ? error.message : String(error);
        throw error;
      });
      await initPromise;
      session.episodeCount += 1;
      session.lastResetMethod = "native_main_menu";
      storeWrite({ episodeCount: session.episodeCount, lastResetMethod: session.lastResetMethod });
      return {
        ok: true,
        method: "native_main_menu",
        accepted: { seed: null, level: null, difficulty: null },
        applied: { seed: null, level: 1, difficulty: null },
        notes: ["native_main_menu_then_stage_1"]
      };
    },
    clickCanvas,
  };

  let attempts = 0;
  const autoInit = () => {
    attempts += 1;
    if (window.gameAPI && typeof window.gameAPI.init === "function") {
      window.gameAPI.init({}).catch(() => {});
    } else if (attempts < 200) {
      window.setTimeout(autoInit, 50);
    }
  };
  window.setTimeout(autoInit, 0);
  document.documentElement.dataset.gameId = GAME_ID;
}());
