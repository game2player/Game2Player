(function installTwoTiminTowersAdapter() {
  "use strict";

  // Thin adapter over the shared Unity bridge. The raw IL2CPP reads live in
  // catalog/unity/241_two-timin-towers.yaml (WavesManager, PlayerResources and
  // MapsLoadHandler singletons); this file starts the Classic map from the title
  // screen and shapes those reads into the canonical gameAPI state.

  const GAME_ID = "241_two-timin-towers";
  const MAP_NAME = "ClassicMap";
  const GAME_MODE_CLASSIC = 0;

  // TypeInfo slots of the singleton classes (32-bit IL2CPP: static fields at class+92).
  const TYPE_INFO = Object.freeze({ maps: 3453376, loading: 3453380 });
  const STATIC_FIELDS_OFFSET = 92;
  const OFFSET = Object.freeze({
    mapsMode: 16,
    mapsCurrentMap: 20,
    mapsInitialized: 28,
    loadingActive: 28,
    menuButtons: 16,
    menuPlayButton: 20,
    nativeObject: 8,
  });
  // The title screen's PLAY button handler is a private method on the GameObject
  // named MenuManager; Unity's SendMessage reaches private methods. Direct WASM calls
  // into these methods throw, so SendMessage is the supported entry.
  const MENU_OBJECT = "MenuManager";
  const PLAY_METHOD = "OnPlayBtn";
  const RESOURCE_NAMES = ["yellow", "red", "blue", "green", "grey"];

  const TASK_PARAM = new URLSearchParams(window.location.search).get("task");
  const session = {
    taskId: /^241_\d\d$/.test(TASK_PARAM || "") ? TASK_PARAM : "241_01",
    phase: "loading",
    ready: false,
    playSent: false,
    startedAt: 0,
    startupError: null,
    menuClass: null,
    bootPromise: null,
  };

  const delay = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
  const finite = (value) => (Number.isFinite(value) ? value : null);

  function bridgeReady() {
    const bridge = window.__unityBridge;
    const status = bridge && typeof bridge.status === "function" ? bridge.status() : null;
    return status && status.hasUnityInstance && status.hasMemory && status.hasTable && status.runtimeLocked
      ? bridge
      : null;
  }

  function staticInstance(bridge, typeInfo) {
    const klass = bridge.read("ptr", typeInfo);
    const fields = klass ? bridge.read("ptr", klass + STATIC_FIELDS_OFFSET) : 0;
    return fields ? bridge.read("ptr", fields) : 0;
  }

  // The live MainMenu has a non-null native object; the scan also returns a stale
  // lookalike whose native pointer is 0. The menu is ready once Start has wired its
  // button array and PLAY button.
  function findReadyMainMenu(bridge) {
    const memoryBytes = bridge.status().memoryBytes;
    if (!session.menuClass) {
      const classes = bridge.findUnityClasses("MainMenu", { start: 1 << 20, end: memoryBytes, limit: 8 });
      if (classes.length !== 1 || classes[0].self !== classes[0].address) return null;
      session.menuClass = classes[0].address;
    }
    for (let start = 1 << 20; start < memoryBytes; start += 32 << 20) {
      const found = bridge.findUnityInstances(session.menuClass, {
        start,
        end: Math.min(memoryBytes, start + (32 << 20)),
        limit: 8,
      });
      for (const address of found) {
        if (
          bridge.read("ptr", address + OFFSET.nativeObject)
          && bridge.read("ptr", address + OFFSET.menuButtons)
          && bridge.read("ptr", address + OFFSET.menuPlayButton)
        ) {
          return address;
        }
      }
    }
    return null;
  }

  function classicMapLoaded(bridge) {
    const maps = staticInstance(bridge, TYPE_INFO.maps);
    if (!maps || !bridge.read("u8", maps + OFFSET.mapsInitialized)) return false;
    if (bridge.read("i32", maps + OFFSET.mapsMode) !== GAME_MODE_CLASSIC) return false;
    const name = bridge.readIl2CppString(bridge.read("ptr", maps + OFFSET.mapsCurrentMap));
    return name === MAP_NAME;
  }

  function sceneLoading(bridge) {
    const loading = staticInstance(bridge, TYPE_INFO.loading);
    return loading ? bridge.read("u8", loading + OFFSET.loadingActive) !== 0 : false;
  }

  async function waitFor(predicate, label, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    let lastError = null;
    while (Date.now() < deadline) {
      try {
        const value = predicate();
        if (value) return value;
      } catch (error) {
        lastError = error;
      }
      await delay(200);
    }
    throw new Error(`Timed out waiting for ${label}${lastError ? `: ${lastError.message || lastError}` : ""}`);
  }

  async function boot() {
    const bridge = await waitFor(bridgeReady, "the Unity runtime", 60000);
    const menu = await waitFor(() => findReadyMainMenu(bridge), "the title screen", 60000);
    if (!menu) throw new Error("MainMenu was not found");
    bridge.sendMessage(MENU_OBJECT, PLAY_METHOD);
    session.playSent = true;
    await waitFor(() => classicMapLoaded(bridge) && !sceneLoading(bridge), "the Classic map", 60000);
    session.startedAt = performance.now();
    session.ready = true;
    return true;
  }

  function startBoot() {
    if (!session.bootPromise) {
      session.bootPromise = boot().catch((error) => {
        session.startupError = error && error.message ? error.message : String(error);
        throw error;
      });
      session.bootPromise.catch(() => {});
    }
    return session.bootPromise;
  }

  function internal(raw) {
    return (raw && raw.game_state && raw.game_state.internal) || {};
  }

  function transformState(raw, context) {
    const bridge = context && context.bridge;
    const live = internal(raw);
    let mapName = null;
    let readError = null;
    if (bridge && session.ready) {
      try {
        const maps = staticInstance(bridge, TYPE_INFO.maps);
        mapName = maps ? bridge.readIl2CppString(bridge.read("ptr", maps + OFFSET.mapsCurrentMap)) : null;
      } catch (error) {
        readError = error && error.message ? error.message : String(error);
      }
    }

    const waveIndex = finite(live.waves_cleared);
    const hitpoints = finite(live.player_hitpoints);
    const singletonsReady = waveIndex !== null && hitpoints !== null && mapName === MAP_NAME;
    const dead = Boolean(live.player_dead) || (hitpoints !== null && hitpoints <= 0);
    const inWave = Boolean(live.wave_in_effect);
    const playing = session.ready && singletonsReady && !session.startupError;
    // WavesManager.m_currentWave is the index of the wave being played: it stays 0
    // through wave 1 and only becomes 1 when START for wave 2 is pressed. A wave has
    // been cleared once it ended with the player alive, which the game shows by
    // clearing WavesManager.current_wave (null between waves, set again by START).
    const waveFinished = playing && !dead && !inWave && !live.current_wave_object;
    const wavesCleared = waveIndex === null ? null : waveIndex + (waveFinished ? 1 : 0);
    const phase = !playing ? (session.startupError ? "error" : "loading") : dead ? "game_over" : inWave ? "wave" : "build";
    session.phase = phase;

    const resources = {};
    RESOURCE_NAMES.forEach((name, index) => {
      resources[name] = finite(live[`resource_${index}`]);
    });
    const collected = finite(live.total_collected_currency);
    const killed = finite(live.enemies_killed_this_wave);
    return {
      schemaVersion: "1",
      gameId: GAME_ID,
      timestampMs: Date.now(),
      gameTimeMs: session.startedAt ? Math.max(0, Math.round(performance.now() - session.startedAt)) : 0,
      status: phase,
      is_actionable: playing && !dead,
      terminal: {
        isTerminal: playing && dead,
        outcome: playing && dead ? "fail" : null,
        reason: playing && dead ? "The player's hitpoints reached zero (Game Over screen)." : null,
      },
      game_state: {
        task_id: session.taskId,
        map: mapName,
        phase,
        waves_cleared: wavesCleared === null ? 0 : wavesCleared,
        wave_in_effect: inWave,
        enemies_alive: finite(live.enemies_alive),
        enemies_killed_this_wave: killed === null ? 0 : killed,
        player_hitpoints: hitpoints,
        player_max_hitpoints: finite(live.player_max_hitpoints),
        player_dead: dead,
        total_collected_currency: collected === null ? 0 : collected,
        resources,
        internal: {
          startup_error: session.startupError,
          read_error: readError,
          play_sent: session.playSent,
        },
      },
      metrics: {
        waves_cleared: wavesCleared === null ? 0 : wavesCleared,
        total_collected_currency: collected === null ? 0 : collected,
        enemies_killed_this_wave: killed === null ? 0 : killed,
        player_hitpoints: hitpoints,
      },
      provenance: {
        "game_state.waves_cleared": "engine_hook",
        "game_state.wave_in_effect": "engine_hook",
        "game_state.enemies_alive": "engine_hook",
        "game_state.enemies_killed_this_wave": "engine_hook",
        "game_state.player_hitpoints": "engine_hook",
        "game_state.total_collected_currency": "engine_hook",
        "game_state.resources": "engine_hook",
        "game_state.map": "engine_hook",
      },
    };
  }

  window.unityAdapter = {
    gameId: GAME_ID,
    transformState,
    async init() {
      return startBoot();
    },
    // The game exposes no in-place restart reachable without clicking, so a reset
    // reloads the page; the boot above runs again on load and starts a fresh Classic
    // run. is_actionable drops immediately so nothing acts on the dying page.
    async reset() {
      session.ready = false;
      session.phase = "loading";
      window.setTimeout(() => window.location.reload(), 50);
      return true;
    },
  };

  startBoot();
})();
