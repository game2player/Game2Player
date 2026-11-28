/* Native export defaults; instrumentation follows. */
const ASWC_DEFAULTS = {
  "team0": "arg,99,0,1,192,224,255,0,53,94",
  "team1": "bra,99,1,1,255,228,0,0,60,255",
  "team2": "cro,99,2,0,255,0,0,255,255,255",
  "team3": "eng,99,3,1,255,255,255,11,19,81",
  "team4": "fra,99,4,1,0,0,128,255,255,255",
  "team5": "ger,99,5,1,255,255,255,0,0,0",
  "team6": "ita,99,6,1,17,62,215,255,255,255",
  "team7": "jpn,99,7,0,0,0,128,0,0,0",
  "team8": "mex,99,8,0,255,255,255,150,0,0",
  "team9": "ned,99,9,1,255,97,0,255,255,255",
  "team10": "rus,99,10,0,255,0,0,255,255,255",
  "team11": "spa,99,11,1,255,0,0,0,0,255",
  "team12": "swe,99,12,0,255,228,0,3,35,146",
  "team13": "usa,99,13,0,255,0,0,0,0,255",
  "names": "argentina,brazil,croatia,england,france,germany,italy,japan,mexico,netherlands,russia,spain,sweden,usa",
  "npartita": 0,
  "mioteam": 99,
  "stats_played": 0,
  "stats_won": 0,
  "stats_lost": 0,
  "stats_gscored": 0,
  "stats_gconceded": 0,
  "stats_cups_easy": 0,
  "stats_cups_normal": 0,
  "stats_cups_hard": 0,
  "tutorial_seen": 0,
  "option_mode": 1,
  "option_difficulty": "normal",
  "option_duration": 45,
  "option_control": 1,
  "option_sound": 1,
  "option_vibrate": 1
};

(function () {
  'use strict';
  const GAME_ID = '224_a-small-world-cup';
  const requested = new URLSearchParams(location.search).get('task') || '224_01';
  const task = ['224_01', '224_02', '224_03', '224_04'].includes(requested) ? requested : '224_01';
  let ready = false, failedStart = null, bootPromise = null, startTime = 0;
  let goalsBest = 0, goldenWon = 0, matchProgress = 0, cupProgress = 0;
  let cupWon = false, matchResult = null;
  const wonMatches = new Set();
  function runtime() { return window.cr_getC2Runtime ? window.cr_getC2Runtime() : null; }
  function vars(r) { return Object.fromEntries(r.dU.map(v => [v.name, v.mf()])); }
  function setVars(r, values) { for (const v of r.dU) if (v.name in values) v.gd(values[v.name]); }
  function dict(r) { return r.types.t39.i[0].Ic; }
  function updateProgress() {
    const r = runtime();
    if (!ready || !r) return;
    const v = vars(r), goals = Math.max(0, Number(v.score0) || 0);
    if (v.npartita === 1) {
      goalsBest = Math.max(goalsBest, goals);
      matchProgress = Math.max(matchProgress, goals / (goals + 1));
    }
    // The completed current match must not also earn current-match goal credit.
    const partial = wonMatches.has(v.npartita) ? 0 : goals / (goals + 1);
    cupProgress = Math.max(cupProgress, Math.min(6.999999, wonMatches.size + partial));
    if (cupWon) cupProgress = 7;
  }
  function onResult(won) {
    if (!ready) return;
    const v = vars(runtime());
    matchResult = won ? 'win' : 'loss';
    if (won) {
      wonMatches.add(v.npartita);
      if (v.npartita === 1) {
        matchProgress = 1;
        if (dict(runtime()).option_mode === 2) goldenWon = 1;
      }
    }
    updateProgress();
  }
  function hookAction(r, id, callback) {
    const action = r.Ue[String(id)];
    if (!action || typeof action.Jb !== 'function') throw new Error('Missing native event action ' + id);
    const original = action.Jb;
    action.Jb = function () {
      const result = original.apply(this, arguments);
      callback();
      return result;
    };
  }
  function installHooks(r) {
    // IDs are stable exported event-action SIDs, recorded in score/result-events.json.
    hookAction(r, '523716416899282', updateProgress); // score0 += 1 (human goal)
    hookAction(r, '288675708975270', () => onResult(true)); // native human winner text action
    hookAction(r, '928960359677843', () => onResult(false)); // native opponent winner text action
    hookAction(r, '222540283071496', () => { if (ready) { cupWon = true; updateProgress(); } });
    // Layout starts delimit matches independently of score polling.
    const startLayout = r.lp.partita.dq;
    r.lp.partita.dq = function () {
      matchResult = null;
      return startLayout.apply(this, arguments);
    };
  }
  function waitFor(test, label) {
    return new Promise((resolve, reject) => {
      const deadline = performance.now() + 45000;
      function poll() {
        try { if (test()) { resolve(); return; } }
        catch (e) { reject(e); return; }
        if (performance.now() > deadline) { reject(new Error('Timed out: ' + label)); return; }
        requestAnimationFrame(poll);
      }
      poll();
    });
  }
  async function boot() {
    await waitFor(() => { const r = runtime(); return r && r.ja && r.ja.name === 'titolo' && r.types.t39.i.length; }, 'native title initialization');
    const r = runtime();
    installHooks(r);
    // Native save data is irrelevant to a fresh task. Rebuild the game's own
    // dictionary from its literal export defaults rather than racing IndexedDB.
    const d = dict(r);
    for (const key of Object.keys(d)) delete d[key];
    Object.assign(d, ASWC_DEFAULTS, {
      option_mode: task === '224_02' ? 2 : 1,
      option_difficulty: ['224_01', '224_02'].includes(task) ? 'easy' : 'normal',
      option_duration: 45, option_control: 1, tutorial_seen: 1,
      mioteam: 0, npartita: 1, matchDesc: 'match day 1'
    });
    for (let i = 0; i < 7; i++) d['match' + i] = '0-0';
    for (const v of r.dU) v.gd(v.Mo);
    const teamVars = {};
    for (let side = 0; side < 2; side++) {
      const team = d['team' + side].split(',');
      teamVars['team' + side + 'name'] = team[0];
      ['sR', 'sG', 'sB', 'pR', 'pG', 'pB'].forEach((suffix, i) => {
        teamVars['team' + side + suffix] = Number(team[i + 4]);
      });
      team[1] = side === 0 ? '100' : '1';
      d['team' + side] = team.join(',');
    }
    setVars(r, {...teamVars, mioteam: 0, otherTeam: 1, npartita: 1, score0: 0, score1: 0, tempo: 0});
    if (window.__resetRandomSeed) window.__resetRandomSeed();
    // Same engine operation as the native Go To Layout action; no menu coordinates.
    r.Mf = r.lp.partita;
    await waitFor(() => r.ja.name === 'partita' && r.types.t4.i.length === 2 && r.types.t1.i.length > 0, 'opening match');
    const v = vars(r);
    if (v.mioteam !== 0 || v.otherTeam !== 1 || v.npartita !== 1 || v.score0 !== 0 || v.score1 !== 0 ||
        v.team0name !== 'arg' || v.team1name !== 'bra' || d.option_control !== 1 ||
        (task === '224_02' && r.types.t1.i[0].rb[5] !== 1)) throw new Error('Task start readback mismatch');
    startTime = r.sb.R * 1000;
    ready = true;
  }
  function terminal() {
    let success = task === '224_01' ? goalsBest >= 3 : task === '224_02' ? goldenWon === 1 : task === '224_03' ? matchProgress === 1 : cupWon;
    let fail = task === '224_01' ? matchResult !== null && !success : matchResult === 'loss';
    return {isTerminal: success || fail, outcome: success ? 'success' : fail ? 'fail' : null};
  }
  function getState() {
    updateProgress();
    const r = runtime(), v = r && r.dU ? vars(r) : {}, d = r && r.types.t39 && r.types.t39.i.length ? dict(r) : {};
    const t = terminal();
    const entity = i => ({x: i.x, y: i.y});
    return {
      schemaVersion: '1.0.0', gameId: GAME_ID, timestampMs: Date.now(),
      gameTimeMs: ready ? Math.max(0, r.sb.R * 1000 - startTime) : 0,
      status: failedStart ? 'error' : !ready ? 'loading' : t.isTerminal ? (t.outcome === 'success' ? 'success' : 'fail') : 'playing',
      is_actionable: ready && !failedStart, terminal: t,
      game_state: {
        task, layout: r && r.ja ? r.ja.name : null,
        mode: d.option_mode === 2 ? 'golden_goal' : 'world_cup', difficulty: d.option_difficulty || null,
        duration_seconds: d.option_duration || 45, match_number: v.npartita || 1,
        human_team: v.team0name || null, opponent_team: v.team1name || null,
        human_goals: v.score0 || 0, opponent_goals: v.score1 || 0,
        match_result: matchResult, cup_won: cupWon, error: failedStart,
        players: ready ? r.types.t4.i.map(i => ({...entity(i), side: i.rb[1]})) : [],
        ball: ready && r.types.t1.i.length ? entity(r.types.t1.i[0]) : null
      },
      metrics: {match_goals_best: goalsBest, golden_match_won: goldenWon,
        match_victory_progress: matchProgress, cup_victory_progress: cupProgress, matches_won: wonMatches.size},
      provenance: {game_state: 'engine_hook', metrics: 'engine_hook',
        note: 'Native Construct event variables and exported action callbacks; progress latches are derived from those events.'}
    };
  }
  window.gameAPI = {
    init() { if (!bootPromise) bootPromise = boot().catch(e => { failedStart = String(e); throw e; }); return bootPromise; },
    getState,
    reset() { ready = false; if (window.__resetRandomSeed) window.__resetRandomSeed(); setTimeout(() => location.reload(), 0); return true; },
    pause() { window.cr_setSuspended(true); }, resume() { window.cr_setSuspended(false); }
  };
  window.__pauseGame = () => window.gameAPI.pause();
  window.__resumeGame = () => window.gameAPI.resume();
  window.gameAPI.init().catch(e => console.error('A Small World Cup task start:', e));
})();
