/* Original MC2: Burning Skies. AS3 hooks expose native values/events only.
 * Task scoring and lifecycle live here so instrumentation remains editable.
 * Reset replaces the iframe: reloading a SWF inside the same Ruffle instance
 * leaves stale keyboard handlers in this build. A new frame gives it a cold VM.
 */
(() => {
  "use strict";
  const GAME_ID = "456_mechanical-commando-2-burning-skies";
  const taskId = new URLSearchParams(location.search).get("task") || "456_01";
  const mission = {"456_01":1,"456_02":2,"456_03":3,"456_04":4,"456_05":5}[taskId];
  const titles = ["", "Plarzus Headquarter", "Plarzus Atmosphere", "Plarzus Orbit",
    "New Jordaw Facility", "Kobu III Orbit"];
  // Source gates: Boss2.CheckWingState requires parts 0..11. Boss4 requires
  // 0..6, 7..10, 11..14, 20..23 before its vulnerable body phase. Bosses 1/3/5
  // change phases on body HP; their weapon parts are optional, not progress.
  const requiredParts = mission === 2 ? [...Array(12).keys()] : mission === 4 ?
    [...Array(15).keys(),20,21,22,23] : [];
  let frame = null, raw = null, epoch = 0, attempt = 0;
  let ready = false, starting = false, error = null, success = false;
  let ordinaryKills = 0, bossDamage = {}, bossBudget = {}, bestProgress = 0;
  let initialization = null, resetting = null, transportPaused = false;
  const realNow = () => (window.__realDateNow || Date.now)();
  const number = value => typeof value === "number" && Number.isFinite(value) ? value : 0;
  const required = part => part === -1 || requiredParts.includes(part);
  function nativePlayer() { return frame?.contentWindow?.player || null; }
  function rememberBudget(boss) {
    if (!boss) return;
    if (number(boss.maxHp) > 0 && !bossBudget[-1]) bossBudget[-1] = boss.maxHp;
    for (const part of boss.parts || []) {
      if (required(part.index) && number(part.maxHp) > 0 && !bossBudget[part.index])
        bossBudget[part.index] = part.maxHp;
    }
  }
  function bossFraction() {
    let removed = 0, total = 0;
    for (const [part, hp] of Object.entries(bossBudget)) {
      total += hp;
      removed += Math.min(hp, Math.max(0, number(bossDamage[part])));
    }
    return total > 0 ? removed / total : 0;
  }
  function updateProgress() {
    const current = Math.min(30, ordinaryKills) / 100 + 0.69 * bossFraction();
    bestProgress = Math.max(bestProgress, success ? 1 : Math.min(0.99, current));
  }
  window.__mcEvent = (generation, kind, data) => {
    if (generation !== epoch || !data || data.mission !== mission) return;
    if (kind === "attempt") {
      attempt = number(data.attempt);
      ordinaryKills = 0; bossDamage = {}; bossBudget = {};
      return;
    }
    if (data.attempt !== attempt) return;
    if (kind === "ordinary_kill") ordinaryKills++;
    if (kind === "boss_damage" && required(data.part)) {
      rememberBudget(data.budget);
      bossDamage[data.part] = number(bossDamage[data.part]) + Math.max(0, number(data.amount));
    }
    // This event is inside MC2_Game.Victory, after the native guard and flag.
    // Boss HP alone cannot complete a task: the concluding briefing must finish.
    if (kind === "victory") success = true;
    updateProgress();
  };
  window.__mcFrame = (generation, state) => {
    if (generation !== epoch || !state) return;
    raw = state;
    if (state.mission === mission) rememberBudget(state.boss);
  };
  window.__mcError = (generation, message) => { if (generation === epoch) error = message; };
  function read() {
    const player = nativePlayer();
    if (typeof player?.gwRead === "function") {
      const snapshot = player.gwRead();
      // Drain source events in order, including attempt boundaries. Polling a
      // resetting kill/HP counter could miss play immediately before a retry.
      if (typeof player.gwDrainNativeEvents === "function") {
        for (const event of player.gwDrainNativeEvents() || [])
          window.__mcEvent(epoch, event.kind, event.data);
      }
      if (snapshot) window.__mcFrame(epoch, snapshot);
    }
    return raw || {};
  }
  function focus() {
    const player = nativePlayer();
    frame?.focus();
    player?.focus({preventScroll:true});
    player?.shadowRoot?.querySelector("#container")?.focus({preventScroll:true});
  }
  async function until(test, label) {
    const deadline = realNow() + 45000;
    while (realNow() < deadline) {
      if (error) throw new Error(error);
      if (test()) return;
      await new Promise(resolve => (window.__realSetTimeout || setTimeout)(resolve, 16));
    }
    throw new Error(`Timed out waiting for ${label}`);
  }
  function validStart(s) {
    return s.ready === true && s.mission === mission && s.hero && s.hero.hp === 100 &&
      s.savedXP === 0 && s.xp === 0 && s.completedMissions === 0 && !s.gameOver &&
      !s.victory && !s.paused && s.screens.length === 1 && s.screens[0] === "IngameScreen";
  }
  async function freshStart() {
    if (!mission) throw new Error(`Unknown task ${taskId}`);
    // Initialization must run even when the harness paused the previous attempt.
    oldResume?.();
    window.__resetRandomSeed?.();
    starting = true; ready = false; error = null; raw = null; success = false;
    ordinaryKills = 0; bossDamage = {}; bossBudget = {}; bestProgress = 0; attempt = 0;
    epoch++;
    document.getElementById("loading").hidden = false;
    frame?.remove();
    frame = document.createElement("iframe");
    frame.title = "Mechanical Commando 2: Burning Skies";
    frame.src = `runtime.html?epoch=${epoch}`;
    document.getElementById("stage").replaceChildren(frame);
    await until(() => read().screens?.includes("TitleScreen"), "native title");
    const response = nativePlayer().gwStart(mission);
    if (response?.requested !== mission) throw new Error("Native mission selection failed");
    await until(() => validStart(read()), "active fresh mission");
    ready = true; starting = false;
    focus();
    document.getElementById("loading").hidden = true;
    if (transportPaused) { suspend(); oldPause?.(); }
    return getState();
  }
  function checked(promise) {
    return promise.catch(failure => {
      starting = false; error = String(failure);
      const overlay = document.getElementById("loading");
      overlay.hidden = false; overlay.textContent = error;
      throw failure;
    });
  }
  function init() {
    if (!initialization) initialization = checked((async () => {
      if (document.readyState === "loading") await new Promise(resolve =>
        document.addEventListener("DOMContentLoaded", resolve, {once:true}));
      return freshStart();
    })());
    return initialization;
  }
  async function reset() {
    if (resetting) return resetting;
    await init();
    resetting = checked(freshStart());
    try { return await resetting; } finally { resetting = null; }
  }
  function getState() {
    const s = read();
    const screens = s.screens || [];
    const failed = !!s.gameOver && !success;
    // Native menus after death remain playable; failure does not end the task.
    return {
      schemaVersion:"1.0.0", gameId:GAME_ID, taskId,
      timestampMs:Date.now(), gameTimeMs:number(s.gameTimeMs),
      status:error ? "error" : !ready || starting ? "loading" : success ? "success" :
        failed ? "game_over" : s.paused ? "paused" : "playing",
      is_actionable:ready && !starting && !error,
      terminal:{isTerminal:success, outcome:success ? "success" : null},
      game_state:{mission:number(s.mission), missionName:titles[s.mission] || "",
        screen:screens.filter(x => x !== "IngameScreen").join(",") || (ready ? "flight" : "loading"),
        player:s.hero || {x:0,y:0,hp:0,maxHp:0,dead:false}, boss:s.boss || null,
        bossDefeated:!!s.bossDead, nativeKills:number(s.nativeKills), ordinaryKills,
        xp:number(s.xp), savedXP:number(s.savedXP), completedMissions:number(s.completedMissions),
        gear:s.gear || {}, projectiles:number(s.ammo), attempt:number(s.attempt),
        paused:!!s.paused, gameOver:!!s.gameOver, victory:!!s.victory, error},
      metrics:{mission_progress:bestProgress, ordinary_kills:ordinaryKills,
        boss_damage_fraction:bossFraction()},
      provenance:{game_state:"internal", metrics:"internal", gameTimeMs:"internal"}
    };
  }
  function suspend() {
    const p = nativePlayer();
    p?.ruffle().suspend();
    const overlay = p?.shadowRoot?.querySelector("#play-button");
    if (overlay) overlay.style.display = "none";
  }
  function resume() { nativePlayer()?.ruffle().resume(); }
  const oldPause = window.__pauseGame, oldResume = window.__resumeGame;
  window.__pauseGame = function() {
    read(); transportPaused = true; suspend(); return oldPause?.();
  };
  window.__resumeGame = function() {
    const result = oldResume?.(); transportPaused = false; resume(); return result;
  };
  window.gameAPI = {init,reset,getState,pause:() => window.__pauseGame(),resume:() => window.__resumeGame()};
  (window.__realSetInterval || setInterval)(() => { if (ready) read(); }, 100);
  init().catch(() => {});
})();

/* Policy observations are intentionally independent of gameAPI and its verifier
 * state. gwObserveVisible is an observation-only AS3 callback: current rendered
 * objects, native collision shapes, and the two values drawn by the HUD.
 * Coordinates use the native 700 x 550 Flash stage, top-left origin, +x right,
 * +y down. No hidden HP, AI timers, drop rolls, or future spawns are exported.
 *
 * Visibility combines native display-shape hits with engine-rendered alpha for
 * foreground coverage, including fades and transparent vector/gradient fills.
 * Isolated edge slivers without sufficient shape exposure are omitted.
 * bounds is the rendered extent; visible_bounds clips it to the stage, not to
 * a pixel mask of the remaining exposed shape.
 * collision is native collision geometry, independent of decorative pixels.
 * Beam collision is null; its current rendered extent is still available.
 * Native velocities exclude camera motion and use pixels per native frame;
 * sample_clock.frame lets callers derive actual motion between observations.
 * movement_limits are the native center clamps. Plane integration may overshoot
 * them by the reported maximum one-frame step. HUD meter ranges are [0, 1].
 * Unverified vulnerability guards stay null. Empty entity lists mean no current
 * visible entities; null fields mean the corresponding view is unavailable.
 */
(() => {
  "use strict";
  const fields = ["player_geometry", "visible_targets", "visible_hazards",
    "visible_pickups", "weapon_indicators", "visible_ui_targets"];
  // Some Flash buttons use outlined vector text instead of TextFields. These
  // names are the game's actual listener targets, not screen coordinates.
  const buttonLabels = {
    nextBrief: ["NEXT", "advance_dialogue"],
    restart: ["RESTART", "restart"],
    backMap: ["BACK/RESUME", "resume"],
    backUpgrade: ["BACK/RESUME", "continue_summary"],
    backGameOver: ["BACK", "back"],
    backVictory: ["NEXT", "continue"],
    skip: ["SKIP", "skip_tutorial"],
    next: ["NEXT", "next"], prev: ["PREV", "previous"],
    abort: ["ABORT MISSION", "abort_mission"],
    btnMouseOn: ["MOUSE", "select_mouse_controls"],
    btnMouseOff: ["KEYBOARD", "select_keyboard_controls"],
    yes: ["YES", "confirm"], no: ["NO", "cancel"],
    sound: ["SOUND", "toggle_sound"], music: ["MUSIC", "toggle_music"],
    sfxSlider: ["SOUND", "adjust_sound_volume"],
    musicSlider: ["MUSIC", "adjust_music_volume"]
  };
  function getState() {
    const empty = Object.fromEntries(fields.map(key => [key, null]));
    // Reset replaces the entire Flash VM. Resolve its current iframe on every
    // read, and never fall back to a snapshot from the previous VM.
    const frame = document.querySelector("#stage iframe");
    const player = frame?.contentWindow?.player;
    if (typeof player?.gwObserveVisible !== "function") return empty;
    const native = player.gwObserveVisible();
    if (!native || typeof native !== "object") return empty;
    const state = Object.fromEntries(fields.map(key => [key, native[key] ?? null]));
    const generation = new URL(frame.src, location.href).searchParams.get("epoch");
    const identity = id => id == null ? null : `${generation}:${id}`;
    for (const key of ["player_geometry", "visible_targets", "visible_hazards", "visible_pickups"]) {
      const entries = Array.isArray(state[key]) ? state[key] : state[key] ? [state[key]] : [];
      for (const entity of entries) {
        entity.id = identity(entity.id);
        if ("parent_id" in entity) entity.parent_id = identity(entity.parent_id);
        if ("owner_id" in entity) entity.owner_id = identity(entity.owner_id);
      }
    }
    if (Array.isArray(state.visible_ui_targets)) {
      state.visible_ui_targets = state.visible_ui_targets.map(button => {
        const known = buttonLabels[button.name];
        // Native TextField content takes precedence over the source-backed
        // vector-label map. Unmapped controls keep their native target name.
        const label = button.text?.trim() || known?.[0] || null;
        return {id: identity(button.id), screen: button.screen, label,
          action: known?.[1] || button.name, enabled: button.enabled,
          opening: button.opening, bounds: button.bounds, click_point: button.click_point,
          provenance: button.provenance};
      });
    }
    // ExternalInterface already supplies JSON-compatible values. Cloning also
    // ensures callers cannot mutate a cached native observation through a read.
    return JSON.parse(JSON.stringify(state));
  }
  window.__policyObservation = {getState};
})();
