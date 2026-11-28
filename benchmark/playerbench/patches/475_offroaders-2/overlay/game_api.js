/* Offroaders 2: thin adapter for the original ActionScript simulation.
 * gwRead/gwStart are the SWF's reviewed ExternalInterface bridge. Native
 * Car.Update supplies course distance; GameObject.UpdateToken supplies stars;
 * UIScreenRaceResults supplies the required completion boundary. Nothing here
 * drives, awards a pickup, changes an upgrade, or completes a race.
 */
(() => {
  "use strict";
  const GAME_ID = "475_offroaders-2";
  const TASKS = {
    "475_01": {race:0, laps:2, field:"raceCompletionProgress", target:1},
    "475_02": {race:0, laps:2, field:"podiumProgress", target:1},
    "475_03": {race:0, laps:2, field:"bestAttemptStars", target:42},
    "475_04": {race:1, laps:2, field:"raceCompletionProgress", target:1},
    "475_05": {race:2, laps:3, field:"raceCompletionProgress", target:1}
  };
  const taskId = new URLSearchParams(location.search).get("task") || "475_01";
  const task = TASKS[taskId];
  const number = (value, fallback = 0) =>
    typeof value === "number" && Number.isFinite(value) ? value : fallback;
  let player = null, raw = null, generation = 0, ready = false, starting = false;
  let initialization = null, resetting = null, error = null, success = false;
  let seed = 42;
  let metrics = emptyMetrics();

  function emptyMetrics() {
    return {raceCompletionProgress:0, podiumProgress:0, bestAttemptStars:0};
  }
  function acceptFrame(state) {
    if (!state || state.epoch !== generation) return;
    raw = state;
    if (!task || !state.inGame || state.raceId !== task.race || state.attempt <= 0) return;
    const length = number(state.pathLength) * number(state.laps);
    const fraction = length > 0 ? Math.min(0.999, Math.max(0, number(state.distance)) / length) : 0;
    metrics.raceCompletionProgress = Math.max(metrics.raceCompletionProgress, fraction);
    metrics.podiumProgress = Math.max(metrics.podiumProgress, fraction);
    // The native count is per attempt. Taking a maximum cannot add retries.
    metrics.bestAttemptStars = Math.max(metrics.bestAttemptStars, Math.max(0, number(state.stars)));
    const finished = state.resultsOpen === true && state.lap === state.laps + 1 &&
      state.finishPosition >= 1 && state.finishPosition <= 8;
    if (finished) {
      metrics.raceCompletionProgress = 1;
      if (state.finishPosition <= 3) metrics.podiumProgress = 1;
    }
    if (metrics[task.field] >= task.target) success = true;
  }
  window.gwNativeFrame = acceptFrame;

  function readNative() {
    if (player && typeof player.gwRead === "function") acceptFrame(player.gwRead());
    return raw || {};
  }
  function focusPlayer() {
    if (!player) return;
    player.tabIndex = 0;
    player.focus({preventScroll:true});
    player.shadowRoot?.querySelector("#container")?.focus({preventScroll:true});
  }
  async function until(condition, description) {
    const now = window.__realDateNow || Date.now;
    const deadline = now() + 45000;
    while (now() < deadline) {
      if (condition()) return;
      await new Promise(resolve => setTimeout(resolve, 30));
    }
    throw new Error(`Timed out waiting for ${description}`);
  }
  function validStart(state) {
    return state.ready && state.inGame && !state.resultsOpen && !state.paused &&
      state.raceId === task.race && state.laps === task.laps && state.pathLength > 0 &&
      state.carIndex === 0 && state.money === 0 && state.racesUnlocked === 1 &&
      state.lap === 0 && state.stars === 0 && state.finishPosition === 0 &&
      state.aiControl === false && state.attempt > 0;
  }
  async function freshStart() {
    if (!task) throw new Error(`Unknown task ${taskId}`);
    window.__resumeGame?.();
    window.__resetRandomSeed?.();
    starting = true;
    ready = false;
    success = false;
    error = null;
    raw = null;
    metrics = emptyMetrics();
    generation++;
    const overlay = document.getElementById("loading");
    overlay.textContent = "Loading Offroaders 2…";
    overlay.hidden = false;
    if (player) {
      player.ruffle().suspend();
      player.remove();
    }
    player = window.RufflePlayer.newest().createPlayer();
    player.id = "game-player";
    document.getElementById("stage").replaceChildren(player);
    await player.ruffle().load({url:"game.swf", allowScriptAccess:true});
    await until(() => {
      // Before gwStart the new SWF has epoch zero, so inspect it directly.
      const state = typeof player.gwRead === "function" ? player.gwRead() : null;
      return state?.ready && state.title && typeof player.gwStart === "function";
    }, "native title initialization");
    if (player.gwStart(task.race, generation, seed) !== true) throw new Error("Native start rejected");
    await until(() => validStart(readNative()), "fresh selected race grid");
    focusPlayer();
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    if (!validStart(readNative())) throw new Error("Selected grid failed initial-state readback");
    ready = true;
    starting = false;
    overlay.hidden = true;
    return getState();
  }
  function withError(promise) {
    return promise.catch(failure => {
      error = String(failure);
      starting = false;
      const overlay = document.getElementById("loading");
      if (overlay) overlay.textContent = error;
      throw failure;
    });
  }
  function init(options = {}) {
    if (!initialization) {
      seed = number(options.seed, window.__getRandomSeed?.() ?? 42) >>> 0;
      initialization = withError((async () => {
        if (document.readyState === "loading") await new Promise(resolve =>
          document.addEventListener("DOMContentLoaded", resolve, {once:true}));
        return freshStart();
      })());
    }
    return initialization;
  }
  async function reset(options = {}) {
    if (resetting) return resetting;
    if (!initialization) return init(options);
    await initialization;
    seed = number(options.seed, seed) >>> 0;
    resetting = withError(freshStart());
    try { return await resetting; } finally { resetting = null; }
  }
  function getState() {
    let state = raw || {};
    try { state = readNative(); } catch (_) { /* old SWF being disposed during reset */ }
    const actionable = ready && !starting && !error && state.ready === true;
    return {
      schemaVersion:"1.0.0", gameId:GAME_ID, taskId,
      timestampMs:Date.now(), gameTimeMs:number(state.gameTimeMs),
      status:error ? "error" : !actionable ? "loading" : success ? "success" :
        state.paused ? "paused" : state.resultsOpen ? "results" :
        !state.inGame ? "menu" : state.startState < 4 ? "countdown" : "racing",
      is_actionable:actionable,
      terminal:{isTerminal:success, outcome:success ? "success" : null},
      game_state:{
        raceNumber:number(state.raceId, -1) + 1, lap:number(state.lap), totalLaps:number(state.laps),
        position:number(state.position), finishPosition:number(state.finishPosition),
        stars:number(state.stars), starTarget:number(state.starTarget), hoops:number(state.hoops),
        raceDistance:number(state.distance), lapLength:number(state.pathLength),
        pathEntries:number(state.pathEntries), speedKPH:number(state.speed), boost:number(state.boost),
        x:number(state.x), y:number(state.y), z:number(state.z), heading:number(state.heading),
        throttle:number(state.throttle), steering:number(state.steering), brake:number(state.brake),
        raceTimeSeconds:number(state.raceTime), carIndex:number(state.carIndex),
        upgrades:number(state.upgrades), money:number(state.money), racesUnlocked:number(state.racesUnlocked),
        attempt:number(state.attempt), countdownState:number(state.startState),
        paused:!!state.paused, resultsOpen:!!state.resultsOpen, aiControl:!!state.aiControl,
        taskComplete:success, error
      },
      metrics:{...metrics},
      provenance:{game_state:"internal", metrics:"internal", "game_state.attempt":"engine_hook",
        gameTimeMs:"engine_hook"}
    };
  }
  window.gameAPI = {init, reset, getState};
  init().catch(() => {});
})();
