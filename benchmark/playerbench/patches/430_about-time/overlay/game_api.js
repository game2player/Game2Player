/*
 * About Time! state adapter. The patched Godot scripts publish values and
 * named gameplay events from their source locations; this file only projects
 * those hooks into the runner contract.
 */
(function () {
  "use strict";

  var GAME_ID = "430_about-time";
  var taskId = new URLSearchParams(window.location.search).get("task") || "";
  var expectedMode = taskId === "430_01" ? "tutorial" : "standard";
  var startedAt = performance.now();
  var currentRound = 0;
  var currentRoundHigh = 0;
  var completedRound = 0;
  var firstShiftScoreProgress = 0;
  var clearedShifts = 0;
  var highWaterCash = 0;
  var gearsAdded = 0;
  var firstShiftPayout = false;
  var paperBoxPurchased = false;
  var paperNumberEdits = 0;
  var tutorialDialogueBeats = 0;
  var tutorialCallouts = 0;
  var tutorialForwardEnabled = false;
  var reloadPending = false;

  window.__aboutTimeTaskId = taskId;

  var state = {
    schemaVersion: "1.0.0",
    gameId: GAME_ID,
    timestampMs: Date.now(),
    gameTimeMs: 0,
    status: "loading",
    is_actionable: false,
    terminal: { isTerminal: false, outcome: null },
    game_state: {
      task_id: taskId,
      mode: "loading",
      round: 0,
      current_score: 0,
      score_target: 0,
      current_score_progress: 0,
      cleared_shifts: 0,
      shift_progress: 0,
      run_progress: 0,
      first_shift_payout: false,
      gears_added: 0,
      gear_shop_progress: 0,
      cash_high_water: 0,
      paper_box_purchased: false,
      paper_number_edits: 0,
      paper_edit_progress: 0,
      tutorial_dialogue_beats: 0,
      tutorial_callouts: 0,
      tutorial_progress: 0,
      tutorial_completion_progress: 0,
      forward_time_enabled: false,
      task_start_ready: false,
      task_start_matches: false,
      task_success: false,
      provenance: "engine_hook"
    },
    metrics: {}
  };

  function copy(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function ratio(score, target) {
    if (!Number.isFinite(score) || !Number.isFinite(target) || target <= 0) return 0;
    return Math.max(0, Math.min(1, score / target));
  }

  function incompleteRatio(score, target) {
    return Math.min(0.999999, ratio(score, target));
  }

  function refresh() {
    var game = state.game_state;
    game.cleared_shifts = clearedShifts;
    game.shift_progress = firstShiftScoreProgress;
    game.first_shift_payout = firstShiftPayout;
    game.gears_added = gearsAdded;
    game.gear_shop_progress = (firstShiftPayout ? 1 : 0) + Math.min(1, gearsAdded);
    game.cash_high_water = highWaterCash;
    game.paper_box_purchased = paperBoxPurchased;
    game.paper_number_edits = paperNumberEdits;
    game.paper_edit_progress = firstShiftScoreProgress + Math.min(1, highWaterCash / 6) +
      (paperBoxPurchased ? 1 : 0) + Math.min(1, paperNumberEdits);
    game.tutorial_dialogue_beats = tutorialDialogueBeats;
    game.tutorial_callouts = tutorialCallouts;
    game.tutorial_progress = tutorialDialogueBeats + tutorialCallouts;
    game.forward_time_enabled = tutorialForwardEnabled;
    // The frozen tutorial progress counter reaches seven before this final hook
    // enables Forward Time. Keep its real value intact, while holding the
    // evaluator score below target until both parts of the success condition hold.
    game.tutorial_completion_progress = tutorialForwardEnabled && game.tutorial_progress === 7
      ? 7
      : Math.min(6, game.tutorial_progress);

    var partial = completedRound === currentRound ? 0 : incompleteRatio(currentRoundHigh, game.score_target);
    game.current_score_progress = partial;
    game.run_progress = clearedShifts + partial;

    var success = false;
    switch (taskId) {
      case "430_01": success = tutorialForwardEnabled && game.tutorial_progress === 7; break;
      case "430_02": success = clearedShifts >= 1; break;
      case "430_03": success = firstShiftPayout && gearsAdded >= 1; break;
      case "430_04": success = game.paper_edit_progress >= 4; break;
      case "430_05": success = clearedShifts >= 3; break;
      case "430_06": success = clearedShifts >= 6; break;
    }
    game.task_success = success;
    if (success && !state.terminal.isTerminal) {
      state.terminal = { isTerminal: true, outcome: "success" };
      state.status = "success";
    }

    state.metrics = copy(game);
    state.metrics.terminal_outcome = state.terminal.outcome;
    state.metrics.terminal_is_terminal = state.terminal.isTerminal;
  }

  window.__aboutTimeSceneLoaded = function (mode, round, target, score, forwards) {
    var game = state.game_state;
    game.mode = mode;
    game.round = Number(round) || 0;
    game.current_score = Number(score) || 0;
    game.score_target = Number(target) || 0;
    game.forwards_left = Number(forwards) || 0;
    game.task_start_ready = mode === "standard";
    game.task_start_matches = mode === expectedMode && mode === "standard" &&
      game.round === 1 && game.score_target === 45 && game.current_score === 0 && game.forwards_left === 5;
    state.status = mode === "tutorial" ? "tutorial_intro" : "playing";
    state.is_actionable = mode === "standard";
    currentRound = game.round;
    currentRoundHigh = Math.max(0, game.current_score);
    refresh();
  };

  window.__aboutTimeTutorialReady = function (dialogueIndex) {
    var game = state.game_state;
    game.mode = "tutorial";
    game.task_start_ready = Number(dialogueIndex) === 1;
    game.task_start_matches = taskId === "430_01" && game.mode === expectedMode &&
      game.round === 1 && game.score_target === 45 && game.current_score === 0 &&
      game.forwards_left === 5 && game.task_start_ready && game.tutorial_progress === 0;
    state.status = "tutorial";
    state.is_actionable = true;
    refresh();
  };

  window.__aboutTimeTutorialDialogueCompleted = function () {
    tutorialDialogueBeats = Math.min(3, tutorialDialogueBeats + 1);
    refresh();
  };

  window.__aboutTimeTutorialCalloutCompleted = function () {
    tutorialCallouts = Math.min(4, tutorialCallouts + 1);
    refresh();
  };

  window.__aboutTimeTutorialForwardEnabled = function () {
    tutorialForwardEnabled = true;
    refresh();
  };

  window.__aboutTimeScoreChanged = function (round, score, target) {
    var game = state.game_state;
    var nextRound = Number(round) || 0;
    var nextScore = Number(score) || 0;
    var nextTarget = Number(target) || 0;
    if (nextRound !== currentRound) {
      currentRound = nextRound;
      currentRoundHigh = Math.max(0, nextScore);
      completedRound = 0;
    } else {
      currentRoundHigh = Math.max(currentRoundHigh, nextScore);
    }
    game.round = nextRound;
    game.current_score = nextScore;
    game.score_target = nextTarget;
    if (nextRound === 1) firstShiftScoreProgress = Math.max(firstShiftScoreProgress, incompleteRatio(currentRoundHigh, nextTarget));
    refresh();
  };

  window.__aboutTimeForwardsChanged = function (remaining) {
    state.game_state.forwards_left = Number(remaining) || 0;
    refresh();
  };

  window.__aboutTimeShiftCleared = function (round) {
    var clearedRound = Number(round) || 0;
    if (clearedRound === clearedShifts + 1) clearedShifts += 1;
    completedRound = Math.max(completedRound, clearedRound);
    if (clearedRound === 1) firstShiftScoreProgress = 1;
    state.game_state.current_score = 0;
    refresh();
  };

  window.__aboutTimePayoutClaimed = function () {
    if (clearedShifts >= 1) firstShiftPayout = true;
    refresh();
  };

  window.__aboutTimeGearAdded = function () {
    gearsAdded += 1;
    refresh();
  };

  window.__aboutTimeCashHighWater = function (amount) {
    var value = Number(amount);
    if (Number.isFinite(value)) highWaterCash = Math.max(highWaterCash, value);
    refresh();
  };

  window.__aboutTimePaperBoxPurchased = function () {
    paperBoxPurchased = true;
    refresh();
  };

  window.__aboutTimePaperNumberEdited = function () {
    paperNumberEdits = Math.min(1, paperNumberEdits + 1);
    refresh();
  };

  window.__aboutTimeRunFailed = function () {
    if (!state.terminal.isTerminal) {
      state.status = "game_over";
      state.terminal = { isTerminal: true, outcome: "fail" };
      refresh();
    }
  };

  function getState() {
    var result = copy(state);
    result.schemaVersion = "1.0.0";
    result.gameId = GAME_ID;
    result.timestampMs = Date.now();
    result.gameTimeMs = Math.max(0, performance.now() - startedAt);
    if (reloadPending) result.is_actionable = false;
    return result;
  }

  function init() {
    return true;
  }

  function reset() {
    reloadPending = true;
    if (typeof window.__resetRandomSeed === "function") window.__resetRandomSeed();
    try { window.localStorage.removeItem("AMEB/AboutTime"); } catch (_error) { /* storage may be disabled */ }
    window.location.reload();
    return true;
  }

  refresh();
  window.gameAPI = { init: init, reset: reset, getState: getState };
})();

/*
 * Player-visible Godot UI snapshot. This channel is separate from gameAPI,
 * which remains the trusted task/evaluator projection.
 */
(function () {
  "use strict";

  var observation = {
    screen_phase: null,
    active_controls: null,
    clock_readouts: null,
    tutorial_text_state: null,
    visible_shop_edit_state: null
  };

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  window.__setAboutTimePolicyObservation = function (snapshot) {
    if (!snapshot || typeof snapshot !== "object") return;
    var keys = [
      "screen_phase",
      "active_controls",
      "clock_readouts",
      "tutorial_text_state",
      "visible_shop_edit_state"
    ];
    var next = {};
    keys.forEach(function (key) {
      next[key] = Object.prototype.hasOwnProperty.call(snapshot, key) ? snapshot[key] : null;
    });

    var viewportSize = snapshot._viewport_size;
    var canvas = document.getElementById("canvas");
    if (Array.isArray(next.active_controls) && Array.isArray(viewportSize) &&
        viewportSize.length === 2 && viewportSize[0] > 0 && viewportSize[1] > 0 && canvas) {
      var rect = canvas.getBoundingClientRect();
      var scaleX = rect.width / viewportSize[0];
      var scaleY = rect.height / viewportSize[1];
      next.active_controls.forEach(function (control) {
        var box = control && control.bbox;
        if (!box) return;
        box.x = rect.left + box.x * scaleX;
        box.y = rect.top + box.y * scaleY;
        box.width *= scaleX;
        box.height *= scaleY;
      });
    }
    observation = next;
  };

  window.__policyObservation = {
    getState() {
      return clone(observation);
    }
  };
})();
