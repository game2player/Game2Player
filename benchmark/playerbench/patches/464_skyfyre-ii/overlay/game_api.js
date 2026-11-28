/* SkyFyre II — original SWF, local Ruffle, direct ActionScript values.
 * The SWF bridge only reads native state, enters a fresh native stage, and emits
 * attempt/spawn/damage/victory events. All verifier calculations live here.
 */
(() => {
  "use strict";
  const GAME_ID = "464_skyfyre-ii";
  const TASKS = {
    "464_01": {character:"Warrior", stage:"STAGE_01", healthBudget:9400},
    "464_02": {character:"Hunter", stage:"STAGE_01", healthBudget:9400},
    "464_03": {character:"Paladin", stage:"STAGE_01", healthBudget:9400},
    "464_04": {character:"BattleMage", stage:"STAGE_01", healthBudget:9400},
    "464_05": {character:"Warrior", stage:"STAGE_02", healthBudget:17590},
    "464_06": {character:"Warrior", stage:"STAGE_03", healthBudget:21330}
  };
  const taskId = new URLSearchParams(location.search).get("task") || "464_01";
  const task = TASKS[taskId];
  const now = () => window.__realDateNow ? window.__realDateNow() : Date.now();
  const basePause = window.__pauseGame;
  const baseResume = window.__resumeGame;
  let player, bootPromise, pollTimer;
  let generation = 0;
  let ready = false, suspended = false, error = null;
  let character = {}, play = {}, pauseMenu = {};
  let transition = {active:true};
  let attempt = null, enemies = new Map();
  let attemptDamage = 0, bestDamage = 0, attemptsStarted = 0;
  let succeeded = false, lastEvent = null;

  const matches = data => !!task && data.stage === task.stage && data.type === task.character;
  const progress = () => succeeded ? 1 : Math.min(0.99, 0.99 * bestDamage / (task?.healthBudget || 1));

  window.__skyEvent = (session, kind, data) => {
    if (String(session) !== String(generation) || !data || !task) return;
    lastEvent = {kind, data};
    if (kind === "attempt_start") {
      attempt = {stage:data.stage, type:data.type};
      attemptDamage = 0;
      enemies = new Map();
      if (matches(attempt)) attemptsStarted++;
      return;
    }
    if (!attempt || !matches(attempt)) return;
    if (kind === "enemy_spawn") {
      enemies.set(data.id, {maxHp:Math.max(0, Number(data.maxHp) || 0), credited:0});
    } else if (kind === "enemy_damage") {
      const enemy = enemies.get(data.id);
      if (!enemy) return;
      // Enemy.applyHit invokes Actor.applyHit and then subtracts damage again.
      // Preserve that native behavior. The bridge reports its effective loss,
      // capped by the observed HP before the hit. Spawn HP additionally prevents
      // repeated callbacks or pooled objects from awarding duplicate health.
      const amount = Math.max(0, Math.min(Number(data.amount) || 0,
        Number(data.hpBefore) || 0, enemy.maxHp - enemy.credited));
      enemy.credited += amount;
      attemptDamage += amount;
      bestDamage = Math.max(bestDamage, attemptDamage);
    } else if (kind === "victory" && matches(data)) {
      // Neither zero boss HP nor exhausting the damage budget is stage victory.
      // This event is emitted exactly where the game sets PlayVO.isVictory.
      succeeded = true;
    }
  };

  function sample() {
    if (typeof player?.skyReadState !== "function") return;
    const next = player.skyReadState();
    if (!next || String(next.session) !== String(generation)) return;
    character = next;
    if (typeof player.skyReadTransition === "function") {
      const view = player.skyReadTransition();
      if (view && String(view.session) === String(generation)) transition = view;
    }
    if (next.screen === "playing" && typeof player.skyReadPlay === "function") {
      const live = player.skyReadPlay();
      if (live && String(live.session) === String(generation)) play = live;
      if (typeof player.skyReadPause === "function") {
        const menu = player.skyReadPause();
        if (menu && String(menu.session) === String(generation)) pauseMenu = menu;
      }
    }
  }

  function getState() {
    try { sample(); } catch (failure) { error = String(failure); }
    const inPlay = character.screen === "playing";
    const screen = inPlay && pauseMenu.open ? "paused" : character.screen || "loading";
    return {
      schemaVersion:"1.0",
      gameId:GAME_ID,
      timestampMs:now(),
      gameTimeMs:Math.max(0, Number(play.time) || 0) * 1000,
      status:error ? "error" : !ready ? "loading" : succeeded ? "success" : transition.active ? "transition" : screen,
      is_actionable:ready && !transition.active && !error,
      // A native defeat leads to results, recovery, equipment, and another try.
      terminal:{isTerminal:succeeded, outcome:succeeded ? "success" : null},
      game_state:{
        task_id:taskId, screen,
        native_paused:inPlay && !!pauseMenu.open,
        pause_menu_ready:inPlay && !!pauseMenu.ready,
        transitioning:!!transition.active,
        character:character.type || null, stage:character.stage || null,
        rank:Number(character.rank) || 0,
        attributes:{STR:Number(character.STR)||0,WIL:Number(character.WIL)||0,
          AGI:Number(character.AGI)||0,WIS:Number(character.WIS)||0},
        health:Number(inPlay ? play.hp : character.health) || 0,
        max_health:Number(inPlay ? play.maxHp : character.maxHealth) || 0,
        magic:Number(inPlay ? play.mp : character.magic) || 0,
        max_magic:Number(inPlay ? play.maxMp : character.maxMagic) || 0,
        spirit:Number(inPlay ? play.spirit : character.spirit) || 0,
        spirit_fraction:inPlay ? Number(play.spiritFraction) || 0 : 0,
        spirit_ready:inPlay && !!play.spiritReady,
        player_x:Number(play.x) || 0, player_y:Number(play.y) || 0,
        hero_present:inPlay && !!play.heroPresent,
        coins:Number(character.coins) || 0, earned_coins:Number(play.coins) || 0,
        rank_experience:Number(character.exp) || 0,
        attempt_experience:Number(play.exp) || 0,
        native_score:Number(play.score) || 0,
        shots:Number(play.shots) || 0, hits:Number(play.hits) || 0,
        casts:Number(play.casts) || 0,
        remaining_waves:Number(play.waves) || 0,
        enemies:inPlay ? play.targets || [] : [],
        equipment:character.equips || [], items:character.items || [],
        unlocked_stages:character.stagesUnlocked || [],
        native_victory:!!play.isVictory,
        inference_suspended:suspended
      },
      metrics:{
        stage_clear_progress:progress(), attempt_enemy_health_removed:attemptDamage,
        best_attempt_enemy_health_removed:bestDamage,
        stage_health_budget:task?.healthBudget || 0,
        attempts_started:attemptsStarted, stage_cleared:succeeded,
        last_event:lastEvent, error,
        provenance:{live_game_values:"internal",enemy_spawn_and_damage:"internal",
          stage_victory:"internal",stage_clear_progress:"internal",game_time:"internal"}
      }
    };
  }

  // The policy receives the native HUD and screen, not verifier history or
  // offscreen enemy lists, retained progress, spawn budgets, or event payloads.
  function getPolicyState() {
    const g = getState().game_state;
    return {screen:g.screen, health:g.health, max_health:g.max_health,
      magic:g.magic, max_magic:g.max_magic, spirit_fraction:g.spirit_fraction};
  }

  async function until(predicate, label, deadline) {
    while (now() < deadline) {
      if (predicate()) return;
      await new Promise(resolve => (window.__realSetTimeout || setTimeout)(resolve, 40));
    }
    throw new Error("Timed out waiting for " + label);
  }

  async function boot() {
    ready = false; suspended = false; error = null;
    character = {}; play = {}; pauseMenu = {}; attempt = null; enemies = new Map();
    transition = {active:true};
    attemptDamage = bestDamage = attemptsStarted = 0;
    succeeded = false; lastEvent = null;
    generation++;
    if (pollTimer) clearInterval(pollTimer);
    if (baseResume) baseResume();
    if (window.__resetRandomSeed) window.__resetRandomSeed();
    if (player) player.remove();
    if (!task) throw new Error("Unknown task " + taskId);
    player = window.RufflePlayer.newest().createPlayer();
    player.id = "game-player";
    player.tabIndex = 0;
    document.getElementById("stage").replaceChildren(player);
    const deadline = now() + 50000;
    await player.ruffle().load({url:"game.swf",allowScriptAccess:true,
      parameters:{gwSession:String(generation)}});
    await until(() => { sample(); return character.screen === "title" &&
      !transition.active && typeof player.skyStart === "function"; }, "native title", deadline);
    player.skyStart(task.character, task.stage);
    await until(() => { sample(); return character.screen === "playing" &&
      !transition.active && play.heroPresent && play.hp > 0 && Number(play.waves) >= 0; }, "playable stage", deadline);
    if (character.type !== task.character || character.stage !== task.stage ||
        character.rank !== 0 || character.coins !== 0 || character.exp !== 0 ||
        character.equips.length || character.items.length || character.hasFinished ||
        play.hp !== character.maxHealth || play.mp !== character.maxMagic || play.spirit !== 0 ||
        !attempt || !matches(attempt) || attemptsStarted !== 1) {
      throw new Error("Native task start contract failed");
    }
    player.focus();
    player.shadowRoot?.querySelector("canvas")?.focus();
    ready = true;
    pollTimer = setInterval(() => {
      try { sample(); } catch (failure) { error = String(failure); }
    }, 100);
    return getState();
  }

  function beginBoot() {
    bootPromise = boot().catch(failure => {
      error = String(failure);
      console.error("SkyFyre II startup:", failure);
      throw failure;
    });
    return bootPromise;
  }
  function pause() {
    if (basePause) basePause();
    if (ready && !suspended) { player.ruffle().suspend(); suspended = true; }
  }
  function resume() {
    if (baseResume) baseResume();
    if (ready && suspended) { player.ruffle().resume(); suspended = false; }
  }
  window.gameAPI = {
    init() { return bootPromise || beginBoot(); },
    reset() { return beginBoot(); },
    getState, getPolicyState, pause, resume,
    capabilities:{supports_seed:false,supports_inplace_reset:true,
      provides_actionable_flag:true,state_provenance:"internal"}
  };
  window.__pauseGame = pause;
  window.__resumeGame = resume;
  beginBoot().catch(() => {});
})();

/* Policy-only visible observations. Provenance: internal ActionScript values.
 * This adapter is independent of gameAPI and its verifier/event history.
 * Geometry uses native 640x480 stage coordinates and native rectangular tests.
 * Values include only text attached to the currently displayed menu/tooltip.
 */
(() => {
  "use strict";
  const fields = ["visible_hazards", "visible_enemy_geometry", "visible_pickups",
    "player_indicators", "visible_ui_targets", "visible_ui_values"];
  const empty = () => Object.fromEntries(fields.map(name => [name, null]));
  const contained = (a, b) => a && b && a.x >= b.x && a.y >= b.y &&
    a.x + a.width <= b.x + b.width && a.y + a.height <= b.y + b.height;
  const visible = (entity, ui) => ![...(ui.covered_bounds || []), ...(ui.overlay_bounds || [])].some(
    bounds => contained(entity.rendered_bounds, bounds));
  const intersects = (a, b) => a && b && a.x < b.x + b.width &&
    a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
  const ordered = entities => entities.sort((a, b) => String(a.id).localeCompare(String(b.id)));

  window.__policyObservation = {
    getState() {
      const result = empty();
      // reset() replaces the Ruffle player. Never retain its old callbacks/data.
      const player = document.getElementById("game-player");
      if (!player || typeof player.skyReadVisibleUI !== "function") return result;
      let ui;
      try { ui = player.skyReadVisibleUI(); } catch (_) { return result; }
      if (!ui) return result;
      result.visible_ui_targets = (ui.targets || []).filter(target =>
        !(ui.overlay_bounds || []).some(bounds => contained(target.bounds, bounds)));
      result.visible_ui_values = (ui.values || []).filter(value =>
        String(value.id).startsWith("tooltip/") ||
        !(ui.overlay_bounds || []).some(bounds => intersects(value.bounds, bounds)));
      if (ui.fading) result.visible_ui_targets.forEach(target => { target.enabled = false; });
      if (typeof player.skyReadVisibleCombat !== "function") return result;
      if (ui.screen !== "playing" || ui.blackout) {
        result.visible_hazards = [];
        result.visible_enemy_geometry = [];
        result.visible_pickups = [];
        return result;
      }
      let combat;
      try { combat = player.skyReadVisibleCombat(); } catch (_) { return result; }
      if (!combat || String(combat.session) !== String(ui.session)) return result;
      for (const name of ["visible_hazards", "visible_enemy_geometry", "visible_pickups"]) {
        result[name] = ordered((combat[name] || []).filter(entity => visible(entity, ui)));
      }
      if (ui.paused || ui.fading) {
        result.visible_hazards.forEach(entity => { entity.damaging = false; });
        result.visible_enemy_geometry.forEach(entity => { entity.contact_damaging = false; });
      }
      result.player_indicators = combat.player_indicators || null;
      // Clone away all runtime wrappers: callers receive plain JSON only.
      return JSON.parse(JSON.stringify(result));
    }
  };
})();
