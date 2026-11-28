/*
 * Packed to the Gills (Ren'Py 8.1 web build) observation bridge.
 *
 * The official renpy-pre.js exposes `renpy_exec(python)`, which runs Python inside
 * the game's own interpreter on its next interaction. This file installs one small
 * Python module through it (PY_SOURCE below). The module
 *   - registers `renpy.config.label_callback` to see level_start / puzzletime /
 *     next_train / level_end (supported engine callback),
 *   - registers `renpy.config.periodic_callbacks` to (a) run one-shot commands on
 *     the game thread (jumping into a level) and (b) push a JSON snapshot of the
 *     game's own store variables to `window.__gwPy` whenever it changes.
 * `gameAPI.getState()` is synchronous and only reads `window.__gwPy`.
 *
 * Start variants come from the URL query `?start=level1|level2|level3|level4|endless|rush`
 * and mirror what the game's own menu labels set up (script.rpy: story_intro,
 * to_level, endless_mode/to_endless, challenge_mode/to_challenge). No click paths.
 * Without `?start` the title screen is left alone and the story can be played normally.
 *
 * Provenance: store variables read directly = internal; correct_trains is derived from
 * label events = engine_hook.
 */
(function () {
  "use strict";

  var GAME_ID = "299_packed-to-the-gills";
  var DEFAULT_SEED = 42;
  var VARIANTS = ["level1", "level2", "level3", "level4", "endless", "rush"];

  var PY_SOURCE = [
    "import time",
    "H = {'run': 0, 'correct': 0, 'seen': False, 'ended': False, 'outcome': None, 'reason': None,",
    "     'cmd': None, 'sig': None, 'last': 0.0, 'variant': None, 'seed': None, 'labels': []}",
    "",
    "def v(name, default=None):",
    "    return getattr(st, name, default)",
    "",
    "def setup(variant, seed):",
    "    st.instant = False; st.endless = False; st.wait = False; st.new_record = False",
    "    st.endless_carcount = 0",
    "    st.quick_menu = False",
    "    st._game_menu_screen = 'preferences'",
    "    st.renpy.random.seed(seed)",
    "    if variant == 'level1':",
    "        st.level = 1; st.sequence = st.create_sequence(cars=20, easymode=True)",
    "    elif variant == 'level2':",
    "        st.level = 2; st.sequence = st.create_sequence(cars=25)",
    "    elif variant == 'level3':",
    "        st.level = 3; st.sequence = st.create_sequence(cars=30, share=True)",
    "    elif variant == 'level4':",
    "        st.level = 4; st.sequence = st.create_sequence(cars=35, share=True, blocks=True)",
    "    elif variant == 'endless':",
    "        st.endless = True; st.level = 1",
    "        st.sequence = st.create_sequence(cars=10, share=True, blocks=True)",
    "    elif variant == 'rush':",
    "        st.instant = True; st.level = 2",
    "        st.sequence = st.create_sequence(cars=100, blocks=True)",
    "    else:",
    "        raise Exception('unknown variant %r' % (variant,))",
    "    st.place = 0",
    "",
    "def do_start(variant, seed):",
    "    setup(variant, seed)",
    "    H['variant'] = variant; H['seed'] = seed",
    "    for tag in ('crowd', 'interior', 'jelly'):",
    "        st.renpy.hide(tag)",
    "    if len(renpy.game.contexts) > 1:",
    "        raise renpy.game.JumpOutException('level_start')",
    "    raise renpy.game.JumpException('level_start')",
    "",
    "def on_label(name, abnormal):",
    "    H['labels'] = (H['labels'] + [name])[-8:]",
    "    if name == 'level_start':",
    "        H['run'] += 1; H['correct'] = 0; H['seen'] = False",
    "        H['ended'] = False; H['outcome'] = None; H['reason'] = None",
    "    elif name == 'puzzletime':",
    "        H['seen'] = True",
    "    elif name == 'next_train':",
    "        seq = v('sequence'); place = v('place')",
    "        if seq is not None and place is not None and v('pose') == seq[place]:",
    "            H['correct'] += 1",
    "    elif name == 'level_end':",
    "        H['ended'] = True",
    "        if v('instant'):",
    "            ok = v('misses', 0) == 0",
    "            H['outcome'] = 'success' if ok else 'fail'",
    "            H['reason'] = 'all_trains_caught' if ok else 'missed_a_train'",
    "        elif v('endless'):",
    "            H['outcome'] = 'fail'; H['reason'] = 'three_mistakes'",
    "        else:",
    "            H['outcome'] = None; H['reason'] = 'level_complete'",
    "    try:",
    "        push(True)",
    "    except Exception as e:",
    "        H['error'] = repr(e)",
    "",
    "def collect():",
    "    seq = v('sequence'); place = v('place')",
    "    in_run = H['run'] > 0",
    "    mode = 'menu'",
    "    if in_run:",
    "        mode = 'rush' if v('instant') else ('endless' if v('endless') else 'story')",
    "    puzzle = st.renpy.get_screen('puzzle') is not None",
    "    if not in_run: phase = 'menu'",
    "    elif H['ended']: phase = 'ended'",
    "    elif not H['seen']: phase = 'intro'",
    "    else: phase = 'playing'",
    "    total = len(seq) if (in_run and seq is not None) else 0",
    "    cur = None",
    "    if in_run and not H['ended'] and seq is not None and place is not None and place < len(seq):",
    "        cur = seq[place]",
    "    return {",
    "        'phase': phase, 'mode': mode, 'variant': H['variant'], 'run': H['run'],",
    "        'puzzle_visible': puzzle,",
    "        'level': v('level') if in_run else None,",
    "        'train_index': (place + 1) if (in_run and place is not None) else 0,",
    "        'trains_total': total if mode == 'story' else None,",
    "        'misses': v('misses', 0) if in_run else 0,",
    "        'steals': v('steals', 0) if in_run else 0,",
    "        'last_pose': v('pose') if in_run else None,",
    "        'correct_trains': H['correct'],",
    "        'trains_caught': v('endless_carcount', 0) if in_run else 0,",
    "        'level_timer_s': (v('playtimer', 0) / 10.0) if in_run else 0.0,",
    "        'seed': H['seed'],",
    "        'terminal': H['ended'], 'outcome': H['outcome'], 'reason': H['reason'],",
    "        'verifier': {'required_pose': cur}, 'error': H.get('error'),",
    "    }",
    "",
    "def push(force=False):",
    "    d = collect()",
    "    sig = json.dumps({k: d[k] for k in d if k != 'level_timer_s'}, sort_keys=True)",
    "    now = time.time()",
    "    if force or sig != H['sig'] or now - H['last'] > 0.5:",
    "        H['sig'] = sig; H['last'] = now",
    "        emscripten.run_script('window.__gwPy=' + json.dumps(d) + ';')",
    "",
    "def periodic():",
    "    c = H['cmd']",
    "    if c is not None:",
    "        H['cmd'] = None",
    "        try:",
    "            c()",
    "        except (renpy.game.JumpException, renpy.game.JumpOutException):",
    "            raise",
    "        except Exception as e:",
    "            H['error'] = repr(e)",
    "    try:",
    "        push()",
    "    except Exception as e:",
    "        H['error'] = repr(e)",
    "",
    "def install(variant, seed):",
    "    renpy.config.label_callback = on_label",
    "    if periodic not in renpy.config.periodic_callbacks:",
    "        renpy.config.periodic_callbacks.append(periodic)",
    "    if variant:",
    "        H['cmd'] = lambda: do_start(variant, seed)",
    "    push(True)",
    "    return 'installed'",
    "",
    "def request_start(variant, seed):",
    "    H['cmd'] = lambda: do_start(variant, seed)",
    "    return 'requested'"
  ].join("\n");

  var query = new URLSearchParams(window.location.search);
  var requested = (query.get("start") || "").toLowerCase();
  var variant = VARIANTS.indexOf(requested) >= 0 ? requested : null;
  var installed = false;
  var installError = null;
  var t0 = performance.now();

  function pyScript(call) {
    return "import json, emscripten, renpy\n" +
      "G = getattr(renpy.game, '_gw_ns', None)\n" +
      "if G is None:\n" +
      "    G = {'renpy': renpy, 'st': renpy.store, 'json': json, 'emscripten': emscripten}\n" +
      "    exec(" + JSON.stringify(PY_SOURCE) + ", G)\n" +
      "    renpy.game._gw_ns = G\n" +
      "G = renpy.game._gw_ns\n" +
      "result = " + call + "\n";
  }

  function pyCall(call) {
    return window.renpy_exec(pyScript(call));
  }

  function tryInstall() {
    if (typeof window.renpy_exec !== "function") {
      window.setTimeout(tryInstall, 200);
      return;
    }
    pyCall("G['install'](" + JSON.stringify(variant) + ", " + DEFAULT_SEED + ")")
      .then(function () { installed = true; })
      .catch(function (err) {
        installError = String((err && (err.message || err.name)) || err);
      });
  }
  tryInstall();

  function baseState(py) {
    var now = Date.now();
    var ready = installed && !!py;
    var phase = py ? py.phase : "loading";
    var terminal = !!(py && py.terminal);
    var startPending = !!variant && phase === "menu";
    var actionable = ready && !startPending && (phase === "playing" || phase === "ended" || phase === "menu");
    var status = !ready || startPending ? "loading" : phase;
    return {
      schemaVersion: "1.0",
      gameId: GAME_ID,
      timestampMs: now,
      gameTimeMs: Math.max(0, Math.round(performance.now() - t0)),
      status: status,
      is_actionable: actionable,
      terminal: {
        isTerminal: terminal,
        outcome: terminal ? py.outcome : null,
        reason: terminal ? py.reason : null
      },
      game_state: {
        phase: phase,
        mode: py ? py.mode : "menu",
        start_variant: py ? py.variant : variant,
        run: py ? py.run : 0,
        level: py ? py.level : null,
        train_index: py ? py.train_index : 0,
        trains_total: py ? py.trains_total : null,
        misses: py ? py.misses : 0,
        steals: py ? py.steals : 0,
        last_pose: py ? py.last_pose : null,
        level_timer_s: py ? py.level_timer_s : 0,
        seed: py ? py.seed : null,
        puzzle_visible: py ? py.puzzle_visible : false,
        verifier: { required_pose: py ? py.verifier.required_pose : null }
      },
      metrics: {
        correct_trains: py ? py.correct_trains : 0,
        trains_caught: py ? py.trains_caught : 0,
        misses: py ? py.misses : 0,
        steals: py ? py.steals : 0
      },
      provenance: {
        "game_state.*": "internal (Ren'Py store variables level, place, misses, steals, pose, playtimer, sequence)",
        "game_state.verifier.required_pose": "internal; verifier truth, the crowd on screen shows the same fact visually",
        "metrics.correct_trains": "engine_hook (label_callback at next_train compares pose with sequence[place])",
        "metrics.trains_caught": "internal (store.endless_carcount, the game's own 'trains caught' score)",
        "terminal": "engine_hook (label_callback at level_end)",
        "gameTimeMs": "wall time since page load, not progress"
      },
      error: installError
    };
  }

  function waitFor(predicate, timeoutMs) {
    return new Promise(function (resolve) {
      var started = performance.now();
      (function poll() {
        var py = window.__gwPy;
        if (predicate(py) || performance.now() - started > timeoutMs) resolve(!!(py && predicate(py)));
        else window.setTimeout(poll, 100);
      })();
    });
  }

  window.gameAPI = {
    version: "1.0",
    gameIdMarker: GAME_ID,
    capabilities: {
      supports_seed: true,
      supports_level_select: true,
      supports_difficulty: false,
      supports_inplace_reset: true,
      supports_reload_reset: true,
      supports_pause_detection: false,
      supports_menu_detection: true,
      provides_actionable_flag: true
    },

    init: async function () {
      return {
        ok: true,
        accepted: { start: variant },
        applied: { start: variant || "title", seed: variant ? DEFAULT_SEED : null },
        notes: ["start variant comes from the ?start= URL query and is applied by a game-thread hook once Ren'Py reaches its first interaction"]
      };
    },

    reset: async function (opts) {
      if (!variant) { window.location.reload(); return { ok: true, reload: true }; }
      var seed = opts && typeof opts.seed === "number" ? opts.seed : DEFAULT_SEED;
      var before = window.__gwPy ? window.__gwPy.run : 0;
      await pyCall("G['request_start'](" + JSON.stringify(variant) + ", " + (seed | 0) + ")");
      var ok = await waitFor(function (py) { return py && py.run > before; }, 20000);
      return { ok: ok, start: variant, seed: seed };
    },

    getState: function () {
      return baseState(window.__gwPy || null);
    }
  };
})();
