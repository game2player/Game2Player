/*
 * Readable adapter for last seen online.
 * The native save is seeded before the React bundle starts. The two in-memory
 * puzzle signals below are reported only from the game's own input handlers.
 */
(function () {
  "use strict";

  const GAME_ID = "414_last-seen-online";
  const taskId = new URLSearchParams(window.location.search).get("task") || "";
  const taskStarts = {
    "414_01": {
      prologue: false,
      loggedin: false,
      visibleWindows: []
    },
    "414_02": {
      prologue: false,
      loggedin: true,
      photosUnzipped: false,
      visibleWindows: []
    },
    "414_03": {
      prologue: false,
      loggedin: true,
      photosUnzipped: true,
      ritualUnzipped: false,
      otherWorldActivated: false,
      loggedInOtherworld: false,
      visibleWindows: []
    },
    "414_04": {
      prologue: false,
      loggedin: true,
      otherWorldActivated: true,
      loggedInOtherworld: true,
      visibleWindows: ["otherworld"],
      level: "garden"
    },
    "414_05": {
      prologue: false,
      loggedin: true,
      otherWorldActivated: true,
      loggedInOtherworld: true,
      visibleWindows: ["otherworld"],
      level: "fountain2"
    }
  };

  function makeSave(task) {
    const save = {
      showLanguage: false,
      language: "English",
      photosUnzipped: false,
      ritualUnzipped: false,
      otherWorldActivated: false,
      loggedInOtherworld: false,
      visibleWindows: []
    };
    Object.assign(save, taskStarts[task]);
    return save;
  }

  function writeSave(save) {
    try {
      window.localStorage.setItem("save", JSON.stringify(save));
    } catch (_) {}
  }

  function readSave() {
    try {
      const value = window.localStorage.getItem("save");
      const parsed = value ? JSON.parse(value) : {};
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_) {
      return {};
    }
  }

  function configureNativeScenario() {
    // The authored liveVersion variant contains the ritual archive. Select it
    // for its task before React creates the native configuration store.
    try {
      const raw = JSON.parse(window.localStorage.getItem("conVarPersistMap") || "{}");
      const config = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
      if (taskId === "414_03") config.liveVersion = { enabled: true, value: true };
      else delete config.liveVersion;
      window.localStorage.setItem("conVarPersistMap", JSON.stringify(config));
    } catch (_) {}
  }

  if (taskStarts[taskId]) {
    configureNativeScenario();
    writeSave(makeSave(taskId));
  }

  const bootTime = performance.now();
  let bestLoginClues = 0;
  let bestPhotosProgress = 0;
  let ritualArchiveOpened = false;
  let bestRitualProgress = 0;
  let gardenFlags = [false, false, false, false];
  let sceneReadyLevel = null;
  let initialPuzzleSceneReady = false;
  let otherWorldSceneReady = false;
  let bestFountainPrefix = 0;
  let resetPending = false;

  function updateLoginClues(value) {
    const password = String(value || "").toLowerCase();
    const count = Number(password.includes("scotty")) + Number(password.includes("92"));
    bestLoginClues = Math.max(bestLoginClues, count);
  }

  document.addEventListener("input", function (event) {
    const input = event.target;
    if (input instanceof HTMLInputElement && input.name === "code" && input.closest(".login")) {
      updateLoginClues(input.value);
    }
  }, true);

  function loginFormReady() {
    return Boolean(document.querySelector("#root .login input[name='code']"));
  }

  function desktopReady() {
    return Boolean(document.querySelector("#root .desktop-icon"));
  }

  function worldReady(windows) {
    if (!Array.isArray(windows) || !windows.includes("otherworld")) return false;
    if (taskId === "414_03" && !otherWorldSceneReady) return false;
    if ((taskId === "414_04" || taskId === "414_05") && !initialPuzzleSceneReady) return false;
    const canvas = document.querySelector("#root canvas");
    return Boolean(canvas && canvas.width > 0 && canvas.height > 0);
  }

  function actionable(windows) {
    if (taskId === "414_01") return loginFormReady();
    if (taskId === "414_02" || taskId === "414_03") return desktopReady();
    if (taskId === "414_04" || taskId === "414_05") return worldReady(windows);
    return loginFormReady() || desktopReady() || worldReady(windows);
  }

  function collectState() {
    const save = readSave();
    const windows = Array.isArray(save.visibleWindows) ? save.visibleWindows : [];
    const level = typeof save.level === "string" ? save.level : null;

    updateLoginClues(document.querySelector("#root .login input[name='code']")?.value);
    const loginProgress = save.loggedin ? 2 : bestLoginClues;
    // Keep the clue count faithful to the visible field while holding evaluator
    // completion until the game's ordinary login handler accepts both clues.
    const loginSubmitAdjustment = save.loggedin ? 0 : loginProgress === 2 ? -0.01 : 0;

    const photosProgress = save.photosUnzipped ? 2 : Number(windows.includes("photos4em.zip"));
    bestPhotosProgress = Math.max(bestPhotosProgress, photosProgress);
    if (windows.includes("COOL!!!.zip")) ritualArchiveOpened = true;

    const loginReady = loginFormReady();
    const desktopVisible = desktopReady();
    const worldVisible = worldReady(windows);
    const ritualProgress = Number(ritualArchiveOpened) +
      Number(Boolean(save.ritualUnzipped)) +
      Number(Boolean(save.otherWorldActivated)) +
      Number(Boolean(save.loggedInOtherworld && worldVisible));
    bestRitualProgress = Math.max(bestRitualProgress, ritualProgress);

    const blessedCount = gardenFlags.filter(Boolean).length;
    const fountainProgress = bestFountainPrefix;
    const fountainRouteProgress = Number(level === "fountain");
    const isActionable = !resetPending && actionable(windows);

    const terminalByTask = {
      "414_01": Boolean(save.loggedin),
      "414_02": Boolean(save.photosUnzipped),
      "414_03": Boolean(save.loggedin && ritualProgress === 4),
      "414_04": Boolean(blessedCount === 4 && level === "house2"),
      "414_05": Boolean(fountainProgress === 7 && level === "fountain")
    };
    const isTerminal = Boolean(terminalByTask[taskId]);

    let taskProgress = 0;
    if (taskId === "414_01") taskProgress = save.loggedin ? 2 : Math.min(loginProgress, 1.99);
    if (taskId === "414_02") taskProgress = bestPhotosProgress;
    if (taskId === "414_03") taskProgress = bestRitualProgress;
    if (taskId === "414_04") taskProgress = blessedCount + Number(level === "house2");
    if (taskId === "414_05") taskProgress = fountainProgress + fountainRouteProgress;

    const startVerifiedByTask = {
      "414_01": Boolean(save.prologue === false && save.loggedin === false && loginReady),
      "414_02": Boolean(save.prologue === false && save.loggedin === true && save.photosUnzipped === false && windows.length === 0 && desktopVisible),
      "414_03": Boolean(save.prologue === false && save.loggedin === true && save.photosUnzipped === true && save.ritualUnzipped === false && !save.otherWorldActivated && !save.loggedInOtherworld && windows.length === 0 && desktopVisible),
      "414_04": Boolean(save.prologue === false && save.loggedin === true && save.otherWorldActivated === true && save.loggedInOtherworld === true && level === "garden" && windows.includes("otherworld") && worldVisible && initialPuzzleSceneReady && blessedCount === 0),
      "414_05": Boolean(save.prologue === false && save.loggedin === true && save.otherWorldActivated === true && save.loggedInOtherworld === true && level === "fountain2" && windows.includes("otherworld") && worldVisible && initialPuzzleSceneReady && fountainProgress === 0)
    };

    const screen = worldVisible ? "otherworld" : desktopVisible ? "desktop" : loginReady ? "login" : "loading";
    return {
      schemaVersion: "1.0.0",
      gameId: GAME_ID,
      timestampMs: Date.now(),
      gameTimeMs: Math.max(0, Math.floor(performance.now() - bootTime)),
      status: isTerminal ? "success" : isActionable ? "playing" : "loading",
      is_actionable: isActionable,
      terminal: { isTerminal: isTerminal, outcome: isTerminal ? "success" : null },
      game_state: {
        task_id: taskId,
        task_start_verified: Boolean(startVerifiedByTask[taskId]),
        screen: screen,
        level: level,
        scene_ready_level: sceneReadyLevel,
        loggedin: Boolean(save.loggedin),
        photosUnzipped: Boolean(save.photosUnzipped),
        ritualUnzipped: Boolean(save.ritualUnzipped),
        otherWorldActivated: Boolean(save.otherWorldActivated),
        loggedInOtherworld: Boolean(save.loggedInOtherworld),
        visible_windows: windows.slice(),
        login_clue_progress: loginProgress,
        photos_archive_progress: bestPhotosProgress,
        ritual_entry_progress: bestRitualProgress,
        garden_blessing_progress: blessedCount,
        garden_blessed_flags: gardenFlags.slice(),
        fountain_sequence_progress: fountainProgress,
        task_progress: taskProgress
      },
      metrics: {
        task_progress: taskProgress,
        login_submit_adjustment: loginSubmitAdjustment,
        fountain_route_progress: fountainRouteProgress
      },
      provenance: {
        "game_state.login_clue_progress": "dom",
        "game_state.photos_archive_progress": "storage",
        "game_state.ritual_entry_progress": "storage",
        "game_state.garden_blessing_progress": "engine_hook",
        "game_state.fountain_sequence_progress": "engine_hook",
        "game_state.level": "storage",
        "game_state.task_progress": "internal"
      }
    };
  }

  window.gameAPI = {
    getTaskId() {
      return taskId;
    },
    getState() {
      return collectState();
    },
    __onSceneReady(level) {
      sceneReadyLevel = String(level || "");
      if (sceneReadyLevel) otherWorldSceneReady = true;
      if (sceneReadyLevel === taskStarts[taskId]?.level) initialPuzzleSceneReady = true;
    },
    __onGardenState(flags) {
      if (!Array.isArray(flags)) return;
      gardenFlags = Array.from({ length: 4 }, (_, index) => Boolean(flags[index]));
    },
    __onFountainInput(sequence) {
      if (!Array.isArray(sequence)) return;
      const target = ["f", "o", "r", "e", "v", "e", "r"];
      let prefix = 0;
      while (prefix < target.length && sequence[prefix] === target[prefix]) prefix += 1;
      bestFountainPrefix = Math.max(bestFountainPrefix, prefix);
    },
    async init() {},
    reset() {
      resetPending = true;
      if (taskStarts[taskId]) configureNativeScenario();
      if (taskStarts[taskId]) writeSave(makeSave(taskId));
      else window.localStorage.removeItem("save");
      if (typeof window.__resetRandomSeed === "function") window.__resetRandomSeed();
      window.setTimeout(function () { window.location.reload(); }, 0);
      return true;
    }
  };
})();
