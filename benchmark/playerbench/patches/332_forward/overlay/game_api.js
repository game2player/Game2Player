/* Forward (2019 jam build): native Vuex starts and event-based instrumentation.
 * Gameplay, card generation, combat and rendering remain in the original bundle.
 * All gameplay values below have internal provenance. The readiness checks alone
 * use DOM visibility. No unseen board cards are exported to the player.
 */
(() => {
  'use strict';
  const GAME_ID = '332_forward';
  const TASKS = {
    '332_01': {hero: 'headless-knight', target: 15},
    '332_02': {hero: 'headless-knight', target: 30},
    '332_03': {hero: 'headless-knight', target: 90},
    '332_04': {hero: 'thief', target: 30},
    '332_05': {hero: 'harpy', target: 30},
    '332_06': {hero: 'warrior', target: 30},
  };
  const NAMES = {'headless-knight': 'Headless Knight', thief: 'Thief',
    harpy: 'Harpy', warrior: 'Warrior'};
  const taskId = new URLSearchParams(location.search).get('task') || '332_01';
  const task = TASKS[taskId];
  const realNow = window.__realDateNow || Date.now.bind(Date);
  const later = window.__realSetTimeout || window.setTimeout.bind(window);
  let store = null, boot = null, error = task ? null : 'Unknown task: ' + taskId;
  let startedAt = 0, reloading = false, routeProgress = 0, completedAreas = 0;
  let nativeWin = false, turn = null, exit = null, endCard = false;

  // Account saves contain unlocks and preferences, not a run. Clear only this
  // game's key before its synchronous bundle initializes, ensuring original kits.
  try { localStorage.removeItem('drop-the-volunteer'); } catch (_) {}

  function correctHero(g) { return !!task && g.player.hero.slug === task.hero; }

  function resetCounters() {
    routeProgress = completedAreas = 0;
    nativeWin = endCard = false;
    turn = exit = null;
    startedAt = Date.now();
  }

  function updateCaption() {
    if (!store) return;
    let caption = document.getElementById('forward-run-info');
    if (!caption) {
      caption = document.createElement('div');
      caption.id = 'forward-run-info';
      caption.style.cssText = 'position:fixed;right:20px;top:20px;z-index:20;' +
        'font:16px Zepto-Regular,monospace;color:#d6c6a5;pointer-events:none';
      document.body.appendChild(caption);
    }
    const g = store.state.game;
    caption.hidden = store.state.app.screen !== 'game';
    caption.textContent = (NAMES[g.player.hero.slug] || '') +
      ' · Area ' + g.current.level + ' / 6';
  }

  function hookStore() {
    store.subscribeAction({
      before(action) {
        const g = store.state.game;
        if (action.type === 'init_game') {
          window.__resetRandomSeed?.();
          resetCounters();
        } else if (action.type === 'next_turn') {
          turn = {level: g.current.level, location: g.current.location,
            row: g.current.main.row, board: g.current.main.board};
        } else if (action.type === 'next_level') {
          exit = {level: g.current.level, card: action.payload.card};
        } else if (action.type === 'play_card') {
          endCard = g.current.location === 'main' && g.current.level === 6 &&
            action.payload.card?.type === 'end';
        }
      },
      after(action) { if (action.type === 'next_turn') turn = null; },
    });
    store.subscribe(mutation => {
      const g = store.state.game, c = g.current, p = mutation.payload || {};
      if (mutation.type === 'EDIT_CURRENT' && correctHero(g)) {
        // next_level commits the new area before its board is rebuilt. Latch
        // that actual exit, rather than infer it from a temporary row counter.
        if (exit && p.level === exit.level + 1 && p.biome === exit.card &&
            c.location === 'main' && g.player.hero.life > 0) {
          completedAreas = c.level - 1;
          routeProgress = Math.max(routeProgress, completedAreas * 15);
          exit = null;
        }
        // next_turn increments even when die() has just set status=lose.
        // Its extra increment must never earn route progress. Board identity
        // also excludes the transient turn during an area-board replacement.
        if (turn && turn.location === 'main' && c.location === 'main' &&
            c.level === turn.level && c.main.board === turn.board &&
            turn.board.length === 15 && p.row === turn.row + 1 &&
            !Object.prototype.hasOwnProperty.call(p, 'board') &&
            g.player.hero.life > 0 && c.status !== 'lose') {
          routeProgress = Math.max(routeProgress,
            completedAreas * 15 + Math.min(14, c.main.row));
        }
        // Only the native final end-card event may supply the last unit.
        if (p.status === 'win' && endCard && c.level === 6 &&
            g.player.hero.life > 0) {
          nativeWin = true;
          completedAreas = 6;
          routeProgress = 90;
        }
      }
      updateCaption();
    });
  }

  function visible(el) {
    return !!el && getComputedStyle(el).display !== 'none' &&
      getComputedStyle(el).visibility !== 'hidden';
  }

  function inputReady() {
    if (!store || reloading || error) return false;
    if (store.state.app.screen === 'home') return visible(document.querySelector('.home'));
    if (store.state.app.screen !== 'game') return false;
    const g = store.state.game;
    if (!g.player.hero.slug || !['ready', 'win', 'lose'].includes(g.current.status)) return false;
    const protector = document.querySelector('.screen-protection');
    if (!protector || visible(protector) || visible(document.querySelector('.level'))) return false;
    // Native draw/row/title transitions change drag target rectangles.
    return !document.getAnimations().some(a =>
      a.effect?.getTiming().iterations !== Infinity && a.playState === 'running');
  }

  async function until(predicate, label) {
    const deadline = realNow() + 30000;
    while (!predicate()) {
      if (realNow() > deadline) throw new Error('Timed out: ' + label);
      await new Promise(resolve => later(resolve, 25));
    }
  }

  function init() {
    if (boot) return boot;
    boot = (async () => {
      if (error) throw new Error(error);
      await until(() => document.querySelector('#app')?.__vue__?.loaded,
        'native app and asset loading');
      store = document.querySelector('#app').__vue__.$store;
      hookStore();
      const hero = store.state.app.account.collection.heroes.find(h => h.slug === task.hero);
      if (!hero) throw new Error('Missing native hero: ' + task.hero);
      // Exactly the two actions dispatched by home.new_game(hero_slug).
      store.dispatch('set_screen', {screen: 'game'});
      await store.dispatch('init_game', {hero});
      await until(inputReady, 'native playable task start');
      const g = store.state.game;
      if (!correctHero(g) || g.current.level !== 1 || g.current.location !== 'main' ||
          g.current.main.row !== 0 || g.player.hero.gold !== 0) {
        throw new Error('Native task start did not match requested variant');
      }
      return {ok: true, task: taskId, character: task.hero};
    })().catch(e => { error = String(e.message || e); return {ok: false, error}; });
    return boot;
  }

  function getState() {
    const g = store?.state.game, c = g?.current, hero = g?.player.hero;
    const success = !!task && correctHero(g || {player: {hero: {}}}) && routeProgress >= task.target;
    const failed = c?.status === 'lose';
    const outcome = success ? 'success' : failed ? 'fail' : null;
    return {
      schemaVersion: '2.0', gameId: GAME_ID,
      timestampMs: realNow(), gameTimeMs: startedAt ? Math.max(0, Date.now() - startedAt) : 0,
      status: error ? 'error' : reloading ? 'loading' : outcome ||
        (c ? (store.state.app.screen === 'game' ? 'playing' : 'menu') : 'loading'),
      is_actionable: inputReady(),
      terminal: {isTerminal: !!outcome, outcome,
        reason: success ? 'requested_area_goal' : failed ? 'native_death' : null},
      game_state: {
        task_id: taskId, character: hero?.slug || null,
        character_matches: !!hero && correctHero(g),
        native_status: c?.status || 'not-started', area: c?.level || 1,
        biome: c?.biome.slug || null, location: c?.location || 'main',
        main_row: c?.main.row || 0, row: store?.getters.current.row || 0,
        position: store?.getters.current.position ?? 1,
        health: hero?.life || 0,
        max_health: (hero?.life_max || 0) + (store?.getters.hero_life_max_modifier || 0),
        shield: hero?.shield || 0, gold: hero?.gold || 0,
        inventory: (hero?.bag || []).map(card => ({slug: card.slug, name: card.en.name,
          effect: card.en.effect || ''})),
        native_win: nativeWin,
      },
      metrics: {
        route_progress: routeProgress, completed_areas: completedAreas,
        gold: hero?.gold || 0,
      },
      provenance: {
        game_state: 'internal', metrics: 'internal', is_actionable: 'dom',
      },
      debug: {error,
        route_progress_source: 'Vuex next_turn, next_level and final play_card events'},
    };
  }

  function reset() {
    reloading = true;
    window.__resetRandomSeed?.();
    // Return before navigation destroys the evaluator's execution context.
    // The new page auto-initializes the same URL/character with fresh timers.
    later(() => location.reload(), 0);
    return {ok: true, method: 'reload'};
  }

  window.gameAPI = {
    version: '2.0', init, getState, reset,
    capabilities: {supports_seed: true, supports_reload_reset: true,
      supports_inplace_reset: false, supports_level_select: false,
      provides_actionable_flag: true},
  };
  document.addEventListener('keydown', event => {
    if (event.key.toLowerCase() === 'r' && !event.repeat && !reloading) {
      event.preventDefault();
      reset();
    }
  });
  init();
})();
