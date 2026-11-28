/* MOBS, INC — projection of the original Construct 3 runtime.
 * Native events are identified by their stable project SIDs in data.json.
 * No reward is inferred from inputs, elapsed time, or rendered pixels.
 */
(() => {
  'use strict';
  const GAME_ID = '38_mobs-inc';
  const TASKS = {
    '38_01': {rank: 0, field: 'best_level_progress', target: 10},
    '38_02': {rank: 0, field: 'first_promotion_progress', target: 21},
    '38_03': {rank: 1, field: 'q_kills', target: 5},
    '38_04': {rank: 2, field: 'w_kills', target: 5},
    '38_05': {rank: 3, field: 'e_kills', target: 5}
  };
  const taskId = new URLSearchParams(location.search).get('task') || '38_01';
  const task = TASKS[taskId];
  const VAR = {Points: 5803162930967589, Warning: 5697378649576065};
  const KILL_EVENTS = new Map([
    [3446044419299331, 'q_kills'], // Jump: area kill after the aimed landing.
    [4158233003081223, 'w_kills']  // Roll: adventurer collides with Boule.
  ]);
  const metrics = {
    best_level_progress: 0, first_promotion_progress: 0,
    q_kills: 0, w_kills: 0, e_kills: 0
  };
  let rt = null, ri = null, bootPromise = null, phase = 'loading';
  let startTime = 0, failed = false, succeeded = false, error = null, hud = null;

  function until(predicate, description) {
    return new Promise((resolve, reject) => {
      const deadline = (window.__realDateNow || Date.now)() + 55000;
      const poll = () => {
        try { if (predicate()) return resolve(); }
        catch (e) { return reject(e); }
        if ((window.__realDateNow || Date.now)() > deadline)
          return reject(new Error('Timed out waiting for ' + description));
        setTimeout(poll, 10);
      };
      poll();
    });
  }
  function layoutName() {
    return rt?.GetMainRunningLayout()?.GetName() || null;
  }
  function hero() { return ri?.objects.Hero.getFirstInstance() || null; }
  function hookVariable(name, callback) {
    const variable = rt.GetEventSheetManager().GetEventVariableBySID(VAR[name]);
    if (!variable) throw new Error('Missing native variable: ' + name);
    const setValue = variable.SetValue;
    variable.SetValue = function (value) {
      const previous = this.GetValue();
      const result = setValue.call(this, value);
      if (phase === 'playing') callback(previous, this.GetValue());
      return result;
    };
  }
  function installHooks() {
    hookVariable('Points', (previous, next) => {
      const field = KILL_EVENTS.get(rt.GetCurrentEvent()?.GetSID());
      if (field && next > previous) metrics[field] += next - previous;
    });
    // Multidash awards Points once for the entire selected group. Count the
    // actual enemy destructions instead, only under its native resolution event.
    const adventurers = rt.GetObjectClassByName('Adventurers');
    const counted = new WeakSet();
    const destroyInstance = rt.DestroyInstance;
    rt.DestroyInstance = function (instance, ...args) {
      const isEKill = phase === 'playing' &&
        this.GetCurrentEvent()?.GetSID() === 8445329646998817 &&
        adventurers.FamilyHasMember(instance.GetObjectClass()) &&
        !instance.IsDestroyed() &&
        !counted.has(instance);
      if (isEKill) counted.add(instance);
      const result = destroyInstance.call(this, instance, ...args);
      if (isEKill) metrics.e_kills += 1;
      return result;
    };
    // Latch firing before the game's title layout resets all native globals.
    hookVariable('Warning', (_, next) => {
      if (next >= ri.globalVars.WarningMax) failed = true;
    });
    ri.addEventListener('tick', update);
  }
  function update() {
    if (phase !== 'playing') return;
    const g = ri.globalVars;
    const levelProgress = Math.min(100, g.Level + g.Points / (Math.floor(g.Level / 10) + 1));
    metrics.best_level_progress = Math.max(metrics.best_level_progress, levelProgress);
    metrics.first_promotion_progress = Math.max(metrics.first_promotion_progress,
      g.Stade >= 1 ? 21 : Math.min(20, levelProgress));
    succeeded ||= metrics[task.field] >= task.target;
    if (!hud) {
      hud = document.createElement('div');
      hud.id = 'task-progress';
      Object.assign(hud.style, {
        position: 'absolute', left: '22px', top: '86px', zIndex: '20',
        color: '#fff', background: '#111b', padding: '4px 7px',
        font: '16px monospace', pointerEvents: 'none', borderRadius: '3px'
      });
      document.body.appendChild(hud);
    }
    const labels = {
      '38_01': `Level goal: ${Math.min(10, metrics.best_level_progress).toFixed(1)} / 10`,
      '38_02': g.Stade >= 1 ? 'First promotion earned' : `First promotion: level ${g.Level} / 20, then review`,
      '38_03': `Q defeats: ${metrics.q_kills} / 5`,
      '38_04': `W defeats: ${metrics.w_kills} / 5`,
      '38_05': `E defeats: ${metrics.e_kills} / 5`
    };
    hud.textContent = labels[taskId] + (succeeded ? ' — complete' : failed ? ' — fired' : '');
  }
  async function boot() {
    if (!task) throw new Error('Unknown task: ' + taskId);
    await until(() => window.c3_runtimeInterface?._localRuntime?.GetMainRunningLayout(), 'native title layout');
    rt = window.c3_runtimeInterface._localRuntime;
    ri = rt.GetIRuntime();
    await until(() => layoutName() === 'Title Mob' && !rt.IsLayoutFirstTick(), 'title global reset');
    installHooks();
    phase = 'starting';
    // No native task query/save exists. Use the same named layout transition as
    // the title/respawn events, with the frozen task's natural promotion profile.
    window.__resetRandomSeed?.();
    const rank = task.rank;
    Object.assign(ri.globalVars, {
      Stade: rank, Level: rank * 20, PrevLevel: rank * 20,
      Points: 0, Warning: 0, Promotion: 0, HeroMaxSpeed: 250
    });
    ri.goToLayout('Dungeon Mob');
    await until(() => layoutName() === 'Dungeon Mob' && hero() && !rt.IsLayoutFirstTick(), 'arena initialization');
    const g = ri.globalVars;
    const expectedHP = [2, 3, 3, 4][rank];
    if (g.Stade !== rank || g.Level !== rank * 20 || g.Points !== 0 ||
        g.Warning !== 0 || hero().instVars.HP !== expectedHP)
      throw new Error('Native task start does not match requested profile');
    startTime = rt.GetGameTime();
    await until(() => {
      const curtain = ri.objects.Volet.getFirstInstance();
      return curtain?.animationName === 'Ouverture' && curtain.animationFrame === 4;
    }, 'arena opening curtain');
    phase = 'playing';
    update();
    return {task: taskId, rank, seed: window.__getRandomSeed?.() ?? null};
  }
  function init() {
    if (!bootPromise) bootPromise = boot().catch(e => {
      phase = 'error'; error = String(e.message || e); throw e;
    });
    return bootPromise;
  }
  function getState() {
    update();
    const g = ri?.globalVars;
    const h = hero();
    const outcome = succeeded ? 'success' : failed ? 'fail' : null;
    return {
      schemaVersion: '1.0.0', gameId: GAME_ID, timestampMs: Date.now(),
      gameTimeMs: rt ? Math.max(0, (rt.GetGameTime() - startTime) * 1000) : 0,
      status: outcome === 'success' ? 'success' : outcome === 'fail' ? 'fail' : phase === 'playing' ? 'playing' : phase,
      is_actionable: phase === 'playing' && (
        (layoutName() === 'Dungeon Mob' && !!h) ||
        (layoutName() === 'Respawning Mob' && ri.objects.Spell_Popup2.getFirstInstance()?.animationName === 'blink')
      ),
      terminal: {isTerminal: outcome !== null, outcome},
      game_state: {
        task: taskId, layout: layoutName(), level: g?.Level ?? 0,
        experience_points: g?.Points ?? 0, promotion_rank: g?.Stade ?? 0,
        warnings: g?.Warning ?? 0, health: h?.instVars.HP ?? 0,
        player: h ? {x: h.x, y: h.y} : null,
        abilities: h ? {
          q_cooldown: h.instVars.JumpCooldown, w_cooldown: h.instVars.BouleCooldown,
          e_cooldown: h.instVars.MultiCooldown, q_aiming: h.instVars.isJumping === 1,
          w_mode: h.instVars.isRolling, e_dash_index: h.instVars.isMultiDashing
        } : null,
        error
      },
      metrics: {...metrics},
      provenance: {
        'game_state.level': 'internal', 'game_state.experience_points': 'internal',
        'game_state.promotion_rank': 'internal', 'game_state.warnings': 'internal',
        'game_state.health': 'internal', 'game_state.player': 'internal',
        'game_state.abilities': 'internal', 'game_state.layout': 'engine_hook',
        'metrics.best_level_progress': 'internal',
        'metrics.first_promotion_progress': 'internal',
        'metrics.q_kills': 'engine_hook', 'metrics.w_kills': 'engine_hook',
        'metrics.e_kills': 'engine_hook', gameTimeMs: 'engine_hook'
      }
    };
  }
  window.gameAPI = {
    init, getState,
    reset() {
      phase = 'resetting';
      window.__resetRandomSeed?.();
      // A reload clears native wait actions, inputs, effects and all instances.
      // The task query remains intact and boot re-enters its verified profile.
      setTimeout(() => location.reload(), 0);
      return {method: 'reload', task: taskId};
    }
  };
  // Also supports a direct browser visit without a harness calling init().
  init().catch(e => console.error('[MOBS environment]', e));
})();
