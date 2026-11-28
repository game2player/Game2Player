/* Handshakes 1.1.3: adapter for the repository's shared Unity bridge.
 * Live values and derived handshake counts have provenance engine_hook.
 * No pixels, memory writes, fake wins, or progress inferred from level changes.
 */
(() => {
  "use strict";

  const GAME_ID = "300_handshakes";
  const TYPE_INFO = 1760016; // LevelController_TypeInfo; IL2CPP metadata v27.
  // Actual table entries, resolved from the build's own WebGL.symbols.json
  // function IDs and its WASM element section. Dumper RVAs do not match this build.
  const METHOD = {
    getType: 7442,              // System.Object.GetType()
    getComponent: 25386,        // GameObject.GetComponent(Type)
    isTweening: 25663,          // DOTween.IsTweening(object, bool)
    inputAssetEnabled: 9243,    // InputActionAsset.get_enabled()
    setPrefInt: 82594,          // PlayerPrefs.SetInt(string, int)
    setStartLevel: 70702,       // LevelController.SetStartLevel()
    acceptPressed: 87841,       // LevelController.AcceptPressed(ctx), ctx unused
  };
  const names = [
    "first steps", "green buttons", "push push shake", "the first baby", "straight ",
    "purple buttons", "that had to hurt", "no pain no gain", "there's a box in the way", "just short",
    "sharing is caring", "togetherness", "it kinda looks like a horse", "always pushing me around", "put the thing on the thing",
    "lift me up", "three times the fun", "down and up", "boxing match", "preparation is key",
    "removal service", "see saw", "closey doors", "not what it seems", "eegnrs",
    "use and discard", "you push my back i push yours", "reach around", "boxes, boxes everywhere", "thanks for playing!",
  ];
  const taskId = new URLSearchParams(location.search).get("task") || "300_01";
  const taskNumber = /^300_0[1-6]$/.test(taskId) ? Number(taskId.slice(-2)) : 0;
  const firstLevel = taskNumber ? (taskNumber - 1) * 5 : 0;
  const lastLevel = firstLevel + 4;
  let phase = "loading";
  let error = null;
  let initialization = null;
  let controller = 0;
  let controllerClass = 0;
  let statics = 0;
  let levelType = 0;
  let activeObject = 0;
  let activeLevel = 0;
  let armedObject = 0;
  let armedIndex = -1;
  let completionIsHandshake = null;
  let startVerified = false;
  let startedAt = 0;
  const completed = new Set();
  const b = () => window.__unityBridge;
  const ptr = address => b().read("ptr", address);
  const int = address => b().read("i32", address);
  const bool = address => b().read("bool", address);
  const call = (method, ...args) => b().call(method, [...args, 0]);

  // Poll a native condition, bounded in frames. There is no guessed menu delay.
  async function until(condition, label) {
    for (let frames = 0; frames < 2400; frames++) {
      try { if (condition()) return; } catch (_) { /* Native scene not initialized yet. */ }
      await new Promise(requestAnimationFrame);
    }
    throw new Error(`Timed out waiting for ${label}`);
  }

  function resolveController() {
    if (!b() || !b().status().hasMemory) return false;
    const klass = ptr(TYPE_INFO);
    if (!klass || b().describeUnityClass(klass).name !== "LevelController") return false;
    const fields = ptr(klass + 92);
    if (!fields) return false;
    // static Action<int> ForceLoadLevel -> Delegate.m_target -> LevelController.
    // This is the native subscription installed by LevelController.OnEnable.
    const delegate = ptr(fields + 4);
    const target = delegate ? ptr(delegate + 16) : 0;
    if (!target || !b().isUnityInstance(target, klass)) return false;
    const levels = ptr(target + 120);
    if (!levels || int(levels + 12) !== 30) return false;
    controller = target;
    controllerClass = klass;
    statics = fields;
    return true;
  }

  function board() {
    if (!controller || ptr(controller) !== controllerClass) return 0;
    const object = ptr(controller + 152);
    if (!object) return 0;
    if (object !== activeObject) {
      const component = call(METHOD.getComponent, object, levelType);
      if (!component || b().describeUnityClass(ptr(component)).name !== "Level") return 0;
      const index = int(controller + 144);
      const name = b().readIl2CppString(ptr(component + 12));
      if (name !== names[index]) throw new Error("Active board and level index disagree");
      activeObject = object;
      activeLevel = component;
    }
    return activeLevel;
  }

  function titleIsHidden() {
    const title = ptr(controller + 16);
    const id = title ? ptr(title + 56) : 0; // HideShowRectTransform.tweenId.
    const label = ptr(controller + 32);
    const labelId = label ? ptr(label + 56) : 0;
    return !!id && !!labelId && !call(METHOD.isTweening, id, 0) && !call(METHOD.isTweening, labelId, 0);
  }

  function handCallbacksDisabled() {
    const level = board();
    if (!level) return false;
    return [36, 40].every(offset => {
      const hand = ptr(level + offset);
      const actions = ptr(hand + 80); // HandMovement.inputActions.
      const asset = actions ? ptr(actions + 8) : 0;
      return !!asset && !call(METHOD.inputAssetEnabled, asset);
    });
  }

  function sampleCompletion() {
    if (phase !== "playing" || !controller) return;
    const object = ptr(controller + 152);
    const index = int(controller + 144);
    if (!object || bool(controller + 149)) return;
    const canContinue = bool(controller + 148);
    if (bool(statics + 24) && !canContinue) {
      armedObject = object;
      armedIndex = index;
      completionIsHandshake = null;
    }
    // A handshake broadcasts LevelComplete: both HandMovement.OnLevelComplete
    // callbacks disable their InputActionAssets, and the controller disables
    // controls/skipping before its delayed continuation screen. SkipLevel calls
    // only DoMenuToggle and the controller callback, leaving hand assets enabled.
    // Classify ONCE at entry, before any later menu toggle can disable those assets.
    if (!bool(statics + 24) && !bool(statics + 26) && object === armedObject && index === armedIndex) {
      if (completionIsHandshake === null) completionIsHandshake = handCallbacksDisabled();
    }
    // Credit the handshake broadcast itself. Waiting for its delayed UI flag
    // could miss a clear if a continuation key arrives in that same later frame.
    if (completionIsHandshake === true && object === armedObject && index === armedIndex) {
      if (index >= firstLevel && index <= lastLevel) completed.add(index);
    }
  }

  function watchCompletion() {
    try { sampleCompletion(); } catch (cause) { error = String(cause); }
    if (phase !== "resetting") requestAnimationFrame(watchCompletion);
  }

  async function initialize() {
    if (!taskNumber) throw new Error(`Unknown task: ${taskId}`);
    await until(resolveController, "LevelController native subscription");
    await until(() => bool(controller + 149) && bool(controller + 148), "native title readiness");
    const prefKey = ptr(statics + 32); // The game's own storedLevelPrefKey.
    if (b().readIl2CppString(prefKey) !== "currentLvl1") throw new Error("Save-key mismatch");
    // Native storage entry point: SetStartLevel reads this PlayerPrefs key.
    call(METHOD.setPrefInt, prefKey, firstLevel);
    call(METHOD.setStartLevel, controller);
    const prefabs = ptr(controller + 120);
    levelType = call(METHOD.getType, ptr(prefabs + 16));
    call(METHOD.acceptPressed, controller, 0);
    await until(() => !bool(controller + 149) && bool(statics + 24) && board() && titleIsHidden(),
      "playable board and finished title transition");
    if (int(controller + 144) !== firstLevel) throw new Error("Requested task did not start");
    const level = board();
    for (const offset of [36, 40]) {
      const hand = ptr(level + offset);
      if (int(hand + 64) !== int(hand + 16)) throw new Error("Task began with an extended arm");
    }
    completed.clear();
    startVerified = true;
    startedAt = performance.now();
    phase = "playing";
    sampleCompletion();
    document.getElementById("unity-canvas").focus();
    requestAnimationFrame(watchCompletion);
    return true;
  }

  function handState(hand) {
    return {
      x: int(hand + 68), y: int(hand + 72),
      length_remaining: int(hand + 64), max_length: int(hand + 16),
    };
  }

  function buttonState(level) {
    const listener = ptr(level + 20);
    const array = listener ? ptr(listener + 12) : 0;
    if (!array) return [];
    const count = int(array + 12);
    if (count < 0 || count > 32) throw new Error("Invalid native button array");
    return Array.from({ length: count }, (_, i) => {
      const button = ptr(array + 16 + i * 4);
      return { x: int(button + 8), y: int(button + 12), pressed: bool(button + 24) };
    });
  }

  window.unityAdapter = {
    init() {
      if (!initialization) {
        initialization = initialize().catch(cause => {
          error = String(cause && cause.message || cause);
          phase = "error";
          return false;
        });
      }
      return initialization;
    },
    transformState(base) {
      let level = 0;
      let index = null;
      let canContinue = false;
      let controls = false;
      const hands = { left: null, right: null };
      let buttons = [];
      try {
        sampleCompletion();
        if (phase === "playing") {
          level = board();
          index = int(controller + 144);
          canContinue = bool(controller + 148) && !bool(controller + 149);
          controls = bool(statics + 24);
          if (level) {
            hands.left = handState(ptr(level + 36));
            hands.right = handState(ptr(level + 40));
            buttons = buttonState(level);
          }
        }
      } catch (cause) { error = String(cause && cause.message || cause); }
      const success = completed.size === 5;
      const actionable = phase === "playing" && !!level && startVerified;
      return {
        schemaVersion: "1.0",
        gameId: GAME_ID,
        timestampMs: base.timestampMs,
        gameTimeMs: startedAt ? Math.max(0, performance.now() - startedAt) : 0,
        status: success ? "success" : actionable ? (canContinue ? "level_complete" : controls ? "playing" : "transition") : phase,
        is_actionable: actionable,
        terminal: { isTerminal: success, outcome: success ? "success" : null, reason: success ? "five_required_handshakes" : null },
        game_state: {
          task_id: taskId,
          first_level: firstLevel,
          last_level: lastLevel,
          start_verified: startVerified,
          level: index,
          level_name: level ? b().readIl2CppString(ptr(level + 12)) : null,
          controls_enabled: controls,
          awaiting_continue: canContinue,
          hands,
          buttons,
          completed_levels: [...completed].sort((a, z) => a - z),
        },
        metrics: { puzzles_completed: completed.size },
        provenance: {
          "game_state.level": "engine_hook",
          "game_state.level_name": "engine_hook",
          "game_state.controls_enabled": "engine_hook",
          "game_state.awaiting_continue": "engine_hook",
          "game_state.hands": "engine_hook",
          "game_state.buttons": "engine_hook",
          "game_state.completed_levels": "engine_hook",
          "metrics.puzzles_completed": "engine_hook",
        },
        diagnostics: { error },
      };
    },
    reset() {
      phase = "resetting";
      startVerified = false;
      completed.clear();
      if (typeof window.__resetRandomSeed === "function") window.__resetRandomSeed();
      location.reload();
      return true;
    },
  };
})();
