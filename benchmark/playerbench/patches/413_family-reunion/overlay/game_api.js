(function () {
  "use strict";

  const GAME_ID = "413_family-reunion";
  const TASK_ID = new URLSearchParams(window.location.search).get("task") || "";
  const TASK_ROLES = {
    "413_01": "chatty",
    "413_02": "adventurer",
    "413_03": "chatty"
  };
  const ROLE_NAMES = {
    chatty: "Chatty",
    adventurer: "Adventurer"
  };
  const requestedRole = TASK_ROLES[TASK_ID] || "";
  const requestedRoleName = ROLE_NAMES[requestedRole] || "";
  let preloadComplete = false;
  let startPromise = null;
  let startError = null;
  let startMethodSucceeded = false;
  let resetInProgress = false;
  let momInterventionProgress = 0;

  function finiteNumber(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function waitFor(check, timeoutMs, failureMessage) {
    return new Promise(function (resolve, reject) {
      const deadline = performance.now() + timeoutMs;
      function poll() {
        try {
          const result = check();
          if (result) {
            resolve(result);
            return;
          }
          if (performance.now() >= deadline) {
            reject(new Error(failureMessage));
            return;
          }
          requestAnimationFrame(poll);
        } catch (error) {
          reject(error);
        }
      }
      poll();
    });
  }

  function smallTalkCount() {
    try {
      const key = TEXTS.STATS_LABELS.small_talks.key.toLowerCase();
      return Math.max(0, finiteNumber(gameState.table.stats[key]?.current, 0));
    } catch (_) {
      return 0;
    }
  }

  function storytellerUnlocked() {
    try {
      return renderedUpgradeButtons.some(function (button) {
        return button.id === "upgrade.storyteller";
      });
    } catch (_) {
      return false;
    }
  }

  function adventurerRoomCount() {
    try {
      if (gameState.character.class !== "Adventurer") return 0;
      return Math.min(
        Math.max(0, finiteNumber(roomIterationCounter, 0)),
        Math.max(0, finiteNumber(gameState.character.exploration.energy, 0))
      );
    } catch (_) {
      return 0;
    }
  }

  function actualCharacter() {
    try {
      return String(gameState.character.class || "");
    } catch (_) {
      return "";
    }
  }

  function gameTimeMs() {
    try {
      if (gameState.time.sessionStart === null) return 0;
      return Math.max(
        0,
        finiteNumber(STARTING_TIME, 0) -
          finiteNumber(gameState.time.remaining, STARTING_TIME)
      );
    } catch (_) {
      return 0;
    }
  }

  function taskStartMatches() {
    if (!requestedRoleName) return false;
    return (
      startMethodSucceeded &&
      actualCharacter() === requestedRoleName
    );
  }

  function inputPhaseReady(phase) {
    return ["PLAYING", "EVENT", "EXPLORATION", "DINNERSTEP"].includes(phase);
  }

  function taskProgress() {
    if (TASK_ID === "413_01") return smallTalkCount();
    if (TASK_ID === "413_02") return adventurerRoomCount();
    if (TASK_ID === "413_03") return momInterventionProgress;
    return 0;
  }

  function taskSucceeded() {
    if (!taskStartMatches()) return false;
    if (TASK_ID === "413_01") {
      return smallTalkCount() >= 10 && storytellerUnlocked();
    }
    if (TASK_ID === "413_02") return adventurerRoomCount() >= 5;
    if (TASK_ID === "413_03") return momInterventionProgress >= 2;
    return false;
  }

  function taskFailed() {
    try {
      return gameState.state === "ENDING" || gameState.checkpoint === "ENDING";
    } catch (_) {
      return false;
    }
  }

  function stateProvenance() {
    return {
      "game_state.small_talks": "internal",
      "game_state.storyteller_unlocked": "internal",
      "game_state.adventurer_rooms_explored": "internal",
      "game_state.mom_intervention_progress": "engine_hook",
      "game_state.character_class": "internal",
      "game_state.checkpoint": "internal",
      "game_state.game_phase": "internal",
      "game_state.task_start_matches": "engine_hook",
      "game_state.start_method_succeeded": "engine_hook",
      "terminal.isTerminal": "engine_hook"
    };
  }

  function getState() {
    let phase = "loading";
    let checkpoint = "START";
    try {
      phase = String(gameState.state || "loading");
      checkpoint = String(gameState.checkpoint || "START");
    } catch (_) {}

    const characterClass = actualCharacter();
    const roomsExplored = adventurerRoomCount();
    const smallTalks = smallTalkCount();
    const unlocked = storytellerUnlocked();
    const matches = taskStartMatches();
    const succeeded = taskSucceeded();
    const failed = !succeeded && taskFailed();
    const terminal = succeeded || failed;
    const state = {
      schemaVersion: "1.0.0",
      gameId: GAME_ID,
      timestampMs: Date.now(),
      gameTimeMs: gameTimeMs(),
      status: resetInProgress
        ? "resetting"
        : startError
        ? "error"
        : succeeded
          ? "completed"
          : failed
            ? "failed"
            : requestedRoleName && !startMethodSucceeded
              ? "starting"
              : phase.toLowerCase(),
      is_actionable: requestedRoleName
        ? matches &&
          inputPhaseReady(phase) &&
          !terminal &&
          !startError &&
          !resetInProgress
        : preloadComplete && phase === "MENU",
      terminal: {
        isTerminal: terminal,
        outcome: succeeded ? "success" : failed ? "fail" : null
      },
      game_state: {
        task_id: TASK_ID,
        requested_character: requestedRoleName,
        character_class: characterClass,
        game_phase: phase,
        checkpoint: checkpoint,
        small_talks: smallTalks,
        storyteller_unlocked: unlocked,
        adventurer_rooms_explored: roomsExplored,
        exploration_energy: finiteNumber(
          gameState.character.exploration.energy,
          0
        ),
        mom_intervention_progress: momInterventionProgress,
        mom_interventions: finiteNumber(gameState.events.momInterventions, 0),
        task_progress: taskProgress(),
        task_start_matches: matches,
        start_method_succeeded: startMethodSucceeded,
        start_error: startError
      },
      metrics: {
        task_progress: taskProgress(),
        small_talks: smallTalks,
        storyteller_unlocked: unlocked,
        adventurer_rooms_explored: roomsExplored,
        mom_intervention_progress: momInterventionProgress,
        task_start_matches: matches,
        task_succeeded: succeeded
      },
      provenance: stateProvenance()
    };
    return state;
  }

  function installMomInterventionHooks() {
    if (TASK_ID !== "413_03") return;

    const originalTrigger = triggerFamilyIntervention;
    triggerFamilyIntervention = function (familyMember) {
      const result = originalTrigger.apply(this, arguments);
      if (
        familyMember === "mom" &&
        gameState.events.momInterventions > 0 &&
        gameState.state === "EVENT"
      ) {
        momInterventionProgress = Math.max(momInterventionProgress, 1);
      }
      return result;
    };

    const originalAnswer = AnswerButton.prototype.answer;
    AnswerButton.prototype.answer = function () {
      const resolvesMomConversation =
        momInterventionProgress === 1 && this.type === "cnv";
      const result = originalAnswer.apply(this, arguments);
      if (resolvesMomConversation && gameState.state === "PLAYING") {
        momInterventionProgress = 2;
      }
      return result;
    };
  }

  async function waitForPreload() {
    await waitFor(
      function () {
        return (
          preloadComplete &&
          gameState.state === "MENU" &&
          tables.family.length > 0 &&
          tables.upgrades.length > 0 &&
          TEXTS.DOM_TEXTS &&
          TEXTS.NARRATIVE_TEXT
        );
      },
      45000,
      "The local dinner data did not finish loading."
    );
  }

  async function beginTaskStart() {
    if (!requestedRoleName) return getState();
    if (startPromise) return startPromise;

    startPromise = (async function () {
      await waitForPreload();
      if (typeof window.__resetRandomSeed === "function") {
        window.__resetRandomSeed();
      }
      if (typeof newGame !== "function" || typeof applyCharacterSelection !== "function" || typeof startRegularGameplay !== "function") {
        throw new Error("The game's new dinner and character start methods are unavailable.");
      }

      // Use the game's own new-run, character-selection, and gameplay methods.
      // Suppress only the tutorial's text walkthrough; its final step is the
      // same call to startRegularGameplay used here.
      startTutorial = function () {};
      document.removeEventListener("click", playTitle);
      newGame();
      tutorialCounter = 5;
      applyCharacterSelection(requestedRole);
      close("opening");

      // The native tutorial enables these same character actions just before
      // starting regular play. Make that final-step state explicit here.
      for (const action of renderedActionButtons) {
        action.isTotalDisabled = false;
        action.button.disabled = false;
        action.button.classList.remove("total-disabled");
      }
      startRegularGameplay();

      // The normal first encounter advances the fresh run out of START and
      // enables its actions. This branch performs no random encounter effects.
      if (gameState.checkpoint === "START") randomEncounter();
      installMomInterventionHooks();

      await waitFor(
        function () {
          const actionsReady =
            renderedActionButtons.length > 0 &&
            renderedActionButtons.every(function (action) {
              return action.button && !action.button.disabled;
            });
          const gameplayUiReady = REMAINING_GAMEPLAY_ELEMENTS.every(
            function (id) {
              const element = document.getElementById(id);
              return (
                !element ||
                (!element.classList.contains("invisible") &&
                  Number(getComputedStyle(element).opacity) >= 0.95)
              );
            }
          );
          const actionPanel = document.getElementById("middleLeft");
          return (
            gameState.state === "PLAYING" &&
            gameState.checkpoint !== "START" &&
            gameState.character.class === requestedRoleName &&
            actionsReady &&
            gameplayUiReady &&
            actionPanel &&
            Number(getComputedStyle(actionPanel).opacity) >= 0.95 &&
            document.getElementById("grid").style.pointerEvents !== "none" &&
            document.getElementById("opening").style.display === "none" &&
            !isTyping &&
            !isWaitingForNextText &&
            textQueue.length === 0
          );
        },
        45000,
        "The requested character did not reach regular gameplay."
      );
      startMethodSucceeded = true;
      return getState();
    })().catch(function (error) {
      startError = String(error && error.message ? error.message : error);
      throw error;
    });
    return startPromise;
  }

  function init() {
    if (requestedRoleName) return beginTaskStart();
    return waitForPreload().then(getState);
  }

  function reset() {
    // Keep readiness false until the reload creates a fresh document; otherwise
    // polling can accept this page's old actionable state before navigation.
    resetInProgress = true;
    if (typeof window.__resetRandomSeed === "function") {
      window.__resetRandomSeed();
    }
    try {
      window.localStorage.removeItem("savedGameState");
    } catch (_) {}
    window.setTimeout(function () {
      window.location.reload();
    }, 0);
    return Promise.resolve({ ok: true, method: "reload-fresh-task-start" });
  }

  // callOnPageLoad invokes preload after loading the bundled CSV tables. Wrap
  // that native method so init waits on a real completion signal rather than a
  // guessed delay.
  const originalPreload = preload;
  preload = async function () {
    const result = await originalPreload.apply(this, arguments);
    preloadComplete = true;
    return result;
  };

  window.gameAPI = {
    version: "1.0",
    capabilities: {
      supports_seed: true,
      supports_level_select: false,
      supports_difficulty: false,
      supports_inplace_reset: false,
      supports_reload_reset: true,
      supports_pause_detection: false,
      supports_menu_detection: true,
      provides_actionable_flag: true
    },
    init: init,
    getState: getState,
    reset: reset
  };

  if (requestedRoleName) void init().catch(function () {});
})();
