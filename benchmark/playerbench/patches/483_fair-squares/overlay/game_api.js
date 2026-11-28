/* Fair Squares adapter. Godot publishes direct values and named source events.
 * Projection/progress logic stays here; no debug gameplay commands are shipped.
 */
(() => {
  'use strict';
  const variants = {
    '483_01': {world: 0, kind: 0, arena: 0, choice: false},
    '483_02': {world: 1, kind: 0, arena: 0, choice: false},
    '483_03': {world: 2, kind: 0, arena: 0, choice: false},
    '483_04': {world: 0, kind: 0, arena: 1, choice: true},
    '483_05': {world: 0, kind: 1, arena: 3, choice: false},
    '483_06': {world: 1, kind: 1, arena: 3, choice: false},
    '483_07': {world: 2, kind: 1, arena: 3, choice: false}
  };
  const query = new URLSearchParams(location.search);
  const taskId = Object.hasOwn(variants, query.get('task')) ? query.get('task') : '483_01';
  const selected = variants[taskId];
  let seed = Number(query.get('seed') ?? window.__getRandomSeed?.() ?? 42);
  let raw = null, attempt = 0, ready = false, outcome = null;
  let enrolled = new Map(), completedRounds = 0, roundLoaded = false;
  let accepted = false, declined = false, upgrades = null, bossDead = false;
  let currentArenaProgress = 0, elapsedMs = 0;
  let lastEvent = null, eventSerial = 0;
  const metrics = {arena_progress: 0, upgrade_arena_progress: 0, boss_progress: 0};
  const matches = p => p.world === selected.world && p.kind === selected.kind && p.arena === selected.arena;
  const clamp = x => Math.max(0, Math.min(1, x));
  const targetMetric = selected.kind === 1 ? 'boss_progress' : selected.choice ? 'upgrade_arena_progress' : 'arena_progress';

  function updateProgress() {
    if (selected.kind === 1) {
      const boss = [...enrolled.values()].find(e => e.boss);
      if (boss) {
        const fraction = bossDead ? 1 : Math.min(0.999999, clamp(1 - boss.hp / boss.max_hp));
        metrics.boss_progress = Math.max(metrics.boss_progress, fraction);
      }
    } else {
      const enemies = [...enrolled.values()];
      const total = enemies.reduce((sum, e) => sum + e.max_hp, 0);
      const resolved = enemies.reduce((sum, e) => sum + (e.dead ? e.max_hp : e.max_hp - Math.max(0, e.hp)), 0);
      const fraction = total > 0 ? clamp(resolved / total) : 0;
      // A round counter is latched at its source before the next spawn resets it.
      currentArenaProgress = Math.max(currentArenaProgress,
        Math.min(0.999999, (completedRounds + fraction) / 3));
      metrics.arena_progress = Math.max(metrics.arena_progress, currentArenaProgress);
      if (accepted && !declined) {
        metrics.upgrade_arena_progress = Math.max(metrics.upgrade_arena_progress, 0.1 + 0.9 * currentArenaProgress);
      }
    }
    if (metrics[targetMetric] >= 1) outcome = 'success';
  }

  function event(name, p = {}) {
    lastEvent = {name, serial: ++eventSerial};
    if (name === 'attempt_start') {
      attempt++;
      ready = false; outcome = null; raw = null;
      enrolled = new Map(); completedRounds = 0; roundLoaded = false;
      accepted = false; declined = false; upgrades = null; bossDead = false;
      currentArenaProgress = 0;
      return;
    }
    if (name === 'round_ready' && matches(p)) {
      enrolled = new Map(p.enemies.map(e => [e.id, {...e, dead: false}]));
      roundLoaded = true;
    } else if (name === 'enemy_hp') {
      const enemy = enrolled.get(p.id);
      if (enemy) enemy.hp = p.hp;
    } else if (name === 'enemy_died' || name === 'boss_died') {
      const enemy = enrolled.get(p.id);
      if (enemy) {
        enemy.hp = 0; enemy.dead = true;
        if (name === 'boss_died' && enemy.boss) bossDead = true;
      }
    } else if (name === 'round_clear' && matches(p) && selected.kind === 0 && roundLoaded) {
      completedRounds = Math.min(3, completedRounds + 1);
      enrolled.clear(); roundLoaded = false;
    } else if (name === 'arena_clear' && matches(p) && selected.kind === 0) {
      currentArenaProgress = 1;
      metrics.arena_progress = 1;
      if (accepted && !declined) metrics.upgrade_arena_progress = 1;
    } else if (name === 'upgrade_applied' && selected.choice && !accepted && !declined) {
      accepted = true;
      upgrades = {player: p.player, enemy: p.enemy};
    } else if (name === 'portal_declined' && selected.choice && !accepted && !p.custom) {
      declined = true;
      if (outcome !== 'success') outcome = 'fail';
    } else if (name === 'player_died') {
      ready = false;
      if (outcome !== 'success') outcome = 'fail';
    }
    updateProgress();
  }

  function snapshot(state) {
    raw = state;
    elapsedMs = state.game_time_ms;
    const activePlayer = state.player && state.player.hp > 0 && !state.player.cutscene && !state.resetting;
    if (activePlayer) {
      const choiceReady = selected.choice && !accepted && !declined && state.portals.length === 2 && state.portals.every(p => p.ready);
      const fightReady = matches(state) && roundLoaded && state.fighting && state.gun?.enabled;
      if (choiceReady || fightReady) ready = true;
    }
    renderHud();
  }

  function renderHud() {
    const hud = document.getElementById('game-hud');
    if (!hud || !raw) return;
    const worldName = ['Red', 'Green', 'Pink'][selected.world];
    let text;
    if (outcome === 'success') text = 'Goal complete';
    else if (raw.resetting || !raw.player) text = 'Defeated — retry begins automatically';
    else if (selected.choice && !accepted && !declined) text = 'Choose an upgrade: walk into a portal';
    else if (selected.kind === 1) {
      const boss = raw.enemies.find(e => e.boss);
      text = `${worldName} boss${boss ? ` — HP ${Math.max(0, boss.hp)}/${boss.max_hp}` : ''}`;
    } else text = `${worldName} arena — wave ${Math.min(3, completedRounds + 1)}/3`;
    if (upgrades) text += `\nYou: ${upgrades.player} | Enemies: ${upgrades.enemy}`;
    hud.textContent = text;
  }

  function getState() {
    const player = raw?.player;
    const boss = raw?.enemies.find(e => e.boss);
    return {
      schemaVersion: '1.0', gameId: '483_fair-squares',
      timestampMs: Date.now(), gameTimeMs: elapsedMs,
      status: outcome ? outcome : ready ? 'playing' : 'loading',
      is_actionable: ready,
      terminal: {isTerminal: outcome !== null, outcome},
      game_state: {
        task_id: taskId, attempt, world: raw?.world ?? selected.world,
        arena_index: raw?.arena ?? selected.arena, encounter_type: raw?.kind ?? selected.kind,
        layout: raw?.layout ?? '', fighting: raw?.fighting ?? false,
        rounds_left: raw?.rounds_left ?? (selected.kind === 0 ? 3 : 1),
        rounds_cleared: completedRounds,
        player_hp: player?.hp ?? 0, player_max_hp: player?.max_hp ?? 3,
        player_x: player?.x ?? 160, player_y: player?.y ?? 120,
        ammo: raw?.gun?.ammo ?? 10, max_ammo: raw?.gun?.max_ammo ?? 10,
        reloading: raw?.gun?.reloading ?? false,
        enemies: raw?.enemies ?? [], portals: raw?.portals ?? [],
        boss_hp: boss?.hp ?? (bossDead ? 0 : null), boss_max_hp: boss?.max_hp ?? null,
        upgrade_accepted: accepted, upgrade_declined: declined, upgrades,
        last_event: lastEvent
      },
      metrics: {...metrics},
      provenance: {
        game_state: 'internal', metrics: 'internal',
        detail: 'Direct GDScript values and source-latched damage, defeat, round-clear, upgrade-application and boss-death events. JS derives monotonic objective progress.'
      }
    };
  }

  window.__fairSquares = {
    config: () => ({...selected, seed, task_id: taskId}),
    event, snapshot
  };
  window.gameAPI = {
    init(options = {}) {
      if (Number.isFinite(options.seed) && !raw) seed = options.seed;
      return getState();
    },
    getState,
    reset(options = {}) {
      window.__resetRandomSeed?.();
      const url = new URL(location.href);
      if (Number.isFinite(options.seed)) url.searchParams.set('seed', String(options.seed));
      // Reload preserves the task query. No save data exists in this export.
      ready = false;
      location.replace(url.href);
      return true;
    }
  };
})();
