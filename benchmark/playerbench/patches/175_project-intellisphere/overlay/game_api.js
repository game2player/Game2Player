/* Project: Intellisphere, Godot 3.4.2.
 * The patched Mission publishes native snapshots; Target publishes effective
 * damage before explosion and Mission forwards the native destruction event.
 * All benchmark aggregation lives here, outside the compiled game resources.
 * No debug commands, navigation shortcuts, or task-completion setters are shipped.
 */
(() => {
  'use strict';
  const GAME_ID = '175_project-intellisphere';
  const TASKS = {
    '175_01': { title: 'Destroy one enemy aircraft', field: 'air_kill_progress', target: 1 },
    '175_02': { title: 'Destroy all three radar sites', field: 'radar_site_progress', target: 3 },
    '175_03': { title: 'Complete the mission', field: 'mission_progress', target: 6 }
  };
  const taskId = new URLSearchParams(location.search).get('task') || '175_03';
  const task = TASKS[taskId] || TASKS['175_03'];
  const targets = new Map();
  const playerAirKills = new Set();
  const events = [];
  let snapshot = null;
  let missionState = 0;
  let startFacts = null;
  let resetting = false;
  let focused = false;
  let explicitSeed = null;
  let panel = null;

  const clamp01 = value => Math.max(0, Math.min(1, value));
  const enemy = target => target.team === 2;
  const fraction = target => target.health > 0 ? clamp01(target.damage / target.health) : 0;

  function remember(nativeTarget) {
    const previous = targets.get(nativeTarget.id);
    const target = { ...nativeTarget, player_damage: previous?.player_damage || 0 };
    targets.set(target.id, target);
    return target;
  }

  function metrics() {
    const all = [...targets.values()].filter(enemy);
    const air = all.filter(target => target.type === 1);
    const radar = all.filter(target => target.type === 2);
    const primary = all.filter(target => target.primary);
    const radarDestroyed = radar.filter(target => target.destroyed).length;
    const radarDamage = radar.reduce((sum, target) => sum + fraction(target), 0);
    // Damage is attributed at its source; allied shots and collisions never add
    // to this measure. A kill requires the separate native destruction event.
    const airDamage = Math.max(0, ...air.map(target =>
      target.health > 0 ? target.player_damage / target.health : 0));
    const primaryDamage = primary.reduce((sum, target) => sum + fraction(target), 0);
    return {
      air_kill_progress: playerAirKills.size ? 1 : Math.min(0.999999, airDamage),
      // Native destruction flags also guard against rounded near-lethal damage.
      radar_site_progress: radarDestroyed === 3 ? 3 : Math.min(2.999999, radarDamage),
      // Health depletion alone must not satisfy the mission before SUCCESS.
      mission_progress: missionState === 10 ? 6 : Math.min(5.999999, primaryDamage),
      player_air_kills: playerAirKills.size,
      radar_sites_destroyed: radarDestroyed,
      primary_targets_destroyed: primary.filter(target => target.destroyed).length,
      primary_targets_remaining: primary.filter(target => !target.destroyed).length,
      enemy_aircraft_remaining: air.filter(target => !target.destroyed).length,
      missiles: snapshot?.player?.ammo?.total ?? 0,
      player_damage: targets.get(startFacts?.player_id)?.damage ?? snapshot?.player?.damage ?? 0
    };
  }

  function getState() {
    const current = metrics();
    const ready = !resetting && Boolean(startFacts?.matches_requested);
    const success = ready && current[task.field] >= task.target;
    const failed = ready && missionState === 20 && !success;
    return {
      schemaVersion: '1.0.0',
      gameId: GAME_ID,
      timestampMs: Date.now(),
      gameTimeMs: snapshot?.game_time_ms ?? 0,
      status: ready ? (success ? 'success' : failed ? 'fail' : 'playing') : 'loading',
      is_actionable: ready,
      terminal: { isTerminal: success || failed, outcome: success ? 'success' : failed ? 'fail' : null },
      game_state: {
        task_id: taskId,
        scene: snapshot?.scene ?? '',
        start: startFacts || { matches_requested: false },
        mission_state: missionState,
        mission_success: missionState === 10,
        mission_failed: missionState === 20,
        mission_time_ms: snapshot?.mission_time_ms ?? 0,
        player: snapshot?.player ?? null,
        // This retained ledger is verifier data, including targets the engine freed.
        targets: [...targets.values()].map(target => ({ ...target })),
        recent_destructions: events.map(event => ({ ...event }))
      },
      metrics: current,
      provenance: {
        'game_state.mission_state': 'internal',
        'game_state.player': 'internal',
        'game_state.targets': 'internal',
        'game_state.recent_destructions': 'internal',
        'metrics.air_kill_progress': 'internal',
        'metrics.radar_site_progress': 'internal',
        'metrics.mission_progress': 'internal',
        'metrics.player_air_kills': 'internal',
        'metrics.radar_sites_destroyed': 'internal',
        'metrics.primary_targets_destroyed': 'internal'
      }
    };
  }

  function render() {
    if (!document.body) return;
    if (!panel) {
      panel = document.createElement('aside');
      panel.id = 'mission-panel';
      panel.style.cssText = 'position:fixed;right:20px;top:20px;width:310px;' +
        'box-sizing:border-box;padding:12px 15px;background:rgba(8,22,28,.86);' +
        'border:1px solid #81d7b3;border-radius:4px;color:#e0fff0;' +
        'font:16px/1.45 Arial,sans-serif;text-align:left;pointer-events:none;z-index:5;';
      document.body.appendChild(panel);
    }
    const state = getState();
    const m = state.metrics;
    const p = snapshot?.player;
    const selected = targets.get(p?.selected_target);
    let selection = 'No nearby target selected';
    if (selected && !selected.destroyed) {
      const label = selected.primary ? 'required' : 'optional';
      const remaining = Math.max(0, selected.health - selected.damage);
      selection = `${selected.name} (${label}) · HP ${Math.ceil(remaining)}/${selected.health}` +
        `\n${Math.round(p.target_distance)} m · ${p.target_locked ? 'LOCKED' : 'NO LOCK'}`;
    }
    const outcome = state.terminal.outcome === 'success' ? 'Task complete — R to restart' :
      state.terminal.outcome === 'fail' ? 'Mission failed — R to retry' :
      !state.is_actionable ? 'Loading flight…' :
      missionState === 10 ? 'Mission accomplished · free flight' : 'Combat mission · R to restart';
    panel.replaceChildren();
    const heading = document.createElement('strong');
    heading.textContent = task.title;
    panel.appendChild(heading);
    const counts = document.createElement('div');
    counts.style.cssText = 'margin-top:6px;white-space:pre-line';
    counts.textContent = `Your air kills: ${m.player_air_kills}\nRadar sites: ${m.radar_sites_destroyed}/3` +
      ` · Required targets: ${m.primary_targets_destroyed}/6` +
      `\nMissiles: ${m.missiles} · Ready: ${p?.ammo?.loaded ?? 0}/${p?.ammo?.loaded_max ?? 2}`;
    panel.appendChild(counts);
    const detail = document.createElement('div');
    detail.style.cssText = 'border-top:1px solid #457c6c;margin-top:8px;padding-top:7px;white-space:pre-line';
    detail.textContent = selection;
    panel.appendChild(detail);
    const footer = document.createElement('div');
    footer.style.cssText = 'margin-top:7px;color:#b4e7d1;font-size:14px';
    footer.textContent = outcome;
    panel.appendChild(footer);
  }

  window.__intellisphere = Object.freeze({
    seed() {
      let saved = null;
      try { saved = sessionStorage.getItem('intellisphere.seed'); } catch (_) {}
      return explicitSeed ?? (saved === null ? (window.__getRandomSeed?.() ?? 42) : Number(saved));
    },
    receive(nativeState) {
      if (resetting) return;
      snapshot = nativeState;
      missionState = nativeState.mission_state;
      for (const target of nativeState.targets) remember(target);
      if (!startFacts && nativeState.ready) {
        const enemies = nativeState.targets.filter(enemy);
        startFacts = {
          scene: nativeState.scene,
          player_id: nativeState.player.id,
          initial_missiles: nativeState.player.ammo.total,
          initial_player_damage: nativeState.player.damage,
          initial_enemy_count: enemies.length,
          initial_primary_count: enemies.filter(target => target.primary).length,
          matches_requested: Boolean(TASKS[taskId]) && nativeState.scene === 'res://Levels/World.tscn' &&
            enemies.length === 10 && enemies.filter(target => target.primary).length === 6 &&
            enemies.every(target => target.damage === 0) && nativeState.player.damage === 0 &&
            nativeState.player.ammo.total === 90
        };
      }
      if (startFacts?.matches_requested && !focused) {
        document.getElementById('canvas')?.focus();
        focused = true;
      }
      render();
    },
    onDamage(event) {
      if (resetting) return;
      const target = remember(event.target);
      if (event.by_player && event.effective_damage > 0) {
        target.player_damage += event.effective_damage;
      }
      render();
    },
    onDestroyed(event) {
      if (resetting) return;
      const target = remember(event.target);
      if (event.by_player && enemy(target) && target.type === 1) playerAirKills.add(target.id);
      events.push({ id: target.id, type: target.type, primary: target.primary, by_player: event.by_player });
      if (events.length > 16) events.shift();
      render();
    },
    onMissionState(value) {
      if (resetting) return;
      missionState = value;
      render();
    }
  });

  function reset(options = {}) {
    if (resetting) return true;
    resetting = true;
    const seed = Number.isFinite(options.seed) ? options.seed : window.__intellisphere.seed();
    try { sessionStorage.setItem('intellisphere.seed', String(seed)); } catch (_) {}
    window.__resetRandomSeed?.();
    window.__resumeGame?.();
    render();
    // Return the reset acknowledgement before destroying its evaluation context.
    (window.__realSetTimeout || window.setTimeout)(() => location.reload(), 0);
    return true;
  }

  window.gameAPI = Object.freeze({
    init(options = {}) {
      if (!snapshot && Number.isFinite(options.seed)) explicitSeed = options.seed;
      return true; // The native main scene is already the requested playable start.
    },
    getState,
    reset,
    pause() { window.__pauseGame?.(); },
    resume() { window.__resumeGame?.(); }
  });
  window.addEventListener('keydown', event => {
    if (event.key.toLowerCase() === 'r' && !event.repeat && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      reset();
    }
    if (['Tab', ' ', 'ArrowUp', 'ArrowDown'].includes(event.key)) event.preventDefault();
  }, true);
})();

/* Policy observations are separate from the verifier snapshot above.
 * Provenance: internal — the HUD supplies its currently revealed contacts and
 * its own warning branch/urgency. Positions use the same Godot world frame as
 * game_state.player.position (meters, +Y up); velocities use those axes in m/s.
 * Contact IDs are the existing Target._gw_id values. No predicted motion is sent.
 */
(() => {
  'use strict';
  let observation = { observable_targets: null, missile_warning: null };

  window.__intellispherePolicy = Object.freeze({
    receive(nativeObservation) {
      observation = {
        observable_targets: nativeObservation.observable_targets,
        missile_warning: nativeObservation.missile_warning
      };
    }
  });

  window.__policyObservation = Object.freeze({
    getState() {
      // Return detached plain JSON so a caller cannot change later observations.
      return JSON.parse(JSON.stringify(observation));
    }
  });
})();
