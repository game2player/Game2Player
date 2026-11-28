(function installBonnieAdapter() {
  "use strict";

  const GAME_ID = "190_bonnies-bakery";
  const TASK = new URLSearchParams(location.search).get("task") || "190_01";
  const HOOK_TAG = 190190;
  const FLOWCHART_TYPE_INFO = 5083476;
  const BLOCKS_METHOD_INFO = 5159984;
  const METHOD = { name: 20020, type: 3932, findAll: 37015, execute: 20028, seed: 4234 };
  let phase = "loading", error = null, started = false, initial = null;
  let startPromise = null, live = null, heldFreshCake = false, gameTimeStart = 0;
  const metrics = { red_velvet_cakes_prepared: 0, orders_served: 0 };
  const commands = new Map();

  const bridge = () => window.__unityBridge;
  const read = (type, at) => bridge().read(type, at);
  const ptr = at => read("ptr", at);
  const text = at => at ? bridge().readIl2CppString(at) : "";
  const call = (index, args) => bridge().call(index, args);
  const list = at => {
    if (!at) return [];
    const size = read("i32", at + 12), array = ptr(at + 8);
    if (size < 0 || size > 1024 || (!array && size)) throw Error("Invalid Unity list");
    return Array.from({ length: size }, (_, i) => ptr(array + 16 + i * 4));
  };
  const array = at => {
    const size = read("i32", at + 12);
    if (size < 0 || size > 1024) throw Error("Invalid Unity array");
    return Array.from({ length: size }, (_, i) => ptr(at + 16 + i * 4));
  };
  const className = at => bridge().describeUnityClass(ptr(at)).name;
  const flowName = at => text(call(METHOD.name, [at, 0]));
  const blocks = flow => {
    const info = ptr(BLOCKS_METHOD_INFO);
    return array(call(ptr(info), [flow, info]));
  };
  function variableRoot(at) {
    for (let i = 0; i < 4; i++) {
      const ref = ptr(at + 20);
      if (!ref) return at;
      at = ref;
    }
    throw Error("Variable reference cycle");
  }
  function variableValue(variable) {
    const at = variable.at;
    if (variable.type === "BooleanVariable") return read("bool", at + 24);
    if (variable.type === "IntegerVariable") return read("i32", at + 24);
    if (variable.type === "FloatVariable") return read("f32", at + 24);
    if (variable.type === "StringVariable") return text(ptr(at + 24));
    return null;
  }
  const value = (owner, key, fallback = 0) => {
    const variable = live && live.variables.get(owner + "/" + key);
    return variable ? variableValue(variable) : fallback;
  };

  // The build's 54-byte Fungus.SetVariable.OnEnter method has one entry call
  // added to Unity's existing JS_Log_Dump import. A reserved log type carries
  // the real command pointer; other log types retain Unity's original behavior.
  // This observes executed commands, including synchronous commands that would
  // disappear between polls. No game value or branch is changed by the hook.
  function commandEvent(command) {
    if (!started) return;
    const source = commands.get(command);
    if (!source) return;
    const any = ptr(command + 40), variable = ptr(any + 8);
    if (!variable || className(variable) !== "IntegerVariable") return;
    const key = text(ptr(variable + 16));
    const op = read("i32", command + 44);
    if (key === "score" && op === 2 && source.block === "Customer Served") {
      metrics.orders_served += 1;
    }
    if (key !== "currentDish" && !/^dishSlot[123]$/.test(key)) return;
    const ref = ptr(any + 96);
    const amount = ref ? read("i32", variableRoot(ref) + 24) : read("i32", any + 100);
    if (key === "currentDish" && op === 0) {
      // Finish Prep's cake branch produces a NEW carried cake. Taking a cake
      // back off a plate does not create a new token and cannot earn it twice.
      heldFreshCake = amount === 1 && source.owner === "Bonnie" && source.block === "Finish Prep";
    }
    if (/^dishSlot[123]$/.test(key) && op === 0 && amount === 1 && heldFreshCake) {
      // Move 1/Move 2 only shift existing plates. Replace and Set Slot N insert
      // the carried dish; a Slot N flowchart can also swap it onto that plate.
      const insertion = source.owner === "Top Down Brain"
        ? /^(Set Slot [123]|Replace)$/.test(source.block)
        : /^Slot [123]$/.test(source.owner);
      if (insertion) {
        metrics.red_velvet_cakes_prepared += 1;
        heldFreshCake = false;
      }
    }
  }
  function instrumentImports(imports) {
    const env = imports && imports.a;
    if (!env || typeof env.Vh !== "function" || env.Vh.__bonnieHook) return;
    const original = env.Vh;
    const wrapped = function (pointer, type) {
      if (type !== HOOK_TAG) return original.apply(this, arguments);
      try { commandEvent(pointer); }
      catch (cause) { error = String(cause.message || cause); phase = "error"; }
    };
    wrapped.__bonnieHook = true;
    env.Vh = wrapped;
  }
  for (const name of ["instantiate", "instantiateStreaming"]) {
    if (typeof WebAssembly[name] !== "function") continue;
    const original = WebAssembly[name].bind(WebAssembly);
    WebAssembly[name] = function (source, imports) {
      instrumentImports(imports);
      return original(source, imports);
    };
  }

  function cachedFlows() {
    const klass = ptr(FLOWCHART_TYPE_INFO);
    if (!klass || (klass & 3)) return [];
    const statics = ptr(klass + 92);
    return statics ? list(ptr(statics)) : [];
  }
  function resolveDay() {
    const cached = cachedFlows();
    const bonnie = cached.find(at => flowName(at) === "Bonnie");
    if (!bonnie) return null;
    const type = call(METHOD.type, [bonnie, 0]);
    const flows = new Map(array(call(METHOD.findAll, [type, 1, 0])).map(at => [flowName(at), at]));
    if (!["Bonnie", "Top Down Brain", "Scene Brain", "Oven", "Customer", "Customer 8"].every(n => flows.has(n))) return null;
    const variables = new Map(), namedBlocks = new Map();
    commands.clear();
    for (const [owner, flow] of flows) {
      for (const at of list(ptr(flow + 68))) {
        variables.set(owner + "/" + text(ptr(at + 16)), { at, type: className(at) });
      }
      for (const block of blocks(flow)) {
        const name = text(ptr(block + 52));
        namedBlocks.set(owner + "/" + name, block);
        for (const command of list(ptr(block + 64))) {
          if (className(command) === "SetVariable") commands.set(command, { owner, block: name });
        }
      }
    }
    return { flows, variables, blocks: namedBlocks };
  }
  async function until(test, deadline, reason) {
    for (;;) {
      if (performance.now() > deadline) throw Error(reason);
      const result = test();
      if (result) return result;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  }
  async function start(options) {
    const deadline = performance.now() + 55000;
    if (!["190_01", "190_02"].includes(TASK)) throw Error("Unknown task: " + TASK);
    const menu = await until(() => {
      if (!bridge() || !bridge().status().hasUnityInstance) return null;
      return cachedFlows().find(at => flowName(at) === "Menu Brain");
    }, deadline, "Title scene did not become ready");
    if (window.__resetRandomSeed) window.__resetRandomSeed();
    const seed = Number.isFinite(options.seed) ? options.seed : 42;
    call(METHOD.seed, [seed | 0, 0]);
    const skip = blocks(menu).find(at => text(ptr(at + 52)) === "Skip Tutorial");
    if (!skip) throw Error("Native Skip Tutorial block missing");
    // Use the exact typed overload. SendMessage on ExecuteBlock has ambiguous
    // overloads in this build and is deliberately not used for task entry.
    call(METHOD.execute, [menu, skip, 0, 0, 0]);
    const skipExecuted = read("i32", skip + 88) > 0;
    live = await until(resolveDay, deadline, "Daytime scene did not load");
    await until(() => {
      const timer = live.blocks.get("Scene Brain/Start Timer");
      return timer && read("i32", timer + 88) > 0;
    }, deadline, "Cooking timer did not start");
    initial = {
      scene: "Daytime", skip_tutorial: skipExecuted,
      score: value("Bonnie", "score"), carried_dish: value("Bonnie", "currentDish"),
      plates: [1, 2, 3].map(n => value("Top Down Brain", "dishSlot" + n)), seed,
    };
    if (!initial.skip_tutorial || initial.score !== 0 || initial.carried_dish !== 0 || initial.plates.some(Boolean)) {
      throw Error("Fresh cooking start invariant failed");
    }
    heldFreshCake = false;
    metrics.red_velvet_cakes_prepared = metrics.orders_served = 0;
    gameTimeStart = performance.now();
    started = true;
    phase = "playing";
    document.querySelector("canvas").focus();
    return true;
  }
  function state(base) {
    let finished = false, gameState = { scene: phase === "playing" ? "Daytime" : phase, task: TASK };
    if (live) {
      try {
        finished = Boolean(value("Scene Brain", "levelFinish", false));
        gameState = {
          ...gameState, score: value("Bonnie", "score"),
          carried_dish: value("Bonnie", "currentDish"),
          plates: [1, 2, 3].map(n => value("Top Down Brain", "dishSlot" + n)),
          orders: { red_velvet: value("Customer Orders", "redVelvet"), pastry: value("Customer Orders", "pastry"), donut: value("Customer Orders", "donut"), bun: value("Customer Orders", "bun") },
          shift_finished: finished, initial,
        };
      } catch (cause) { error = String(cause.message || cause); phase = "error"; }
    }
    const success = TASK === "190_01" ? metrics.red_velvet_cakes_prepared >= 1 : metrics.orders_served >= 3;
    return {
      ...base, schemaVersion: "1.0", gameId: GAME_ID,
      timestampMs: Date.now(), gameTimeMs: started ? Math.max(0, performance.now() - gameTimeStart) : 0,
      status: error ? "error" : success ? "success" : finished ? "finished" : phase,
      is_actionable: started && !error,
      terminal: { isTerminal: success || finished, outcome: success ? "success" : finished ? "fail" : null, reason: success ? "task_complete" : finished ? "shift_finished" : null },
      game_state: gameState, metrics: { ...metrics },
      provenance: { game_state: "engine_hook", metrics: "engine_hook" },
      diagnostics: { error },
    };
  }
  window.unityAdapter = {
    init({ options }) {
      if (!startPromise) startPromise = start(options || {}).catch(cause => {
        error = String(cause.message || cause); phase = "error"; return false;
      });
      return startPromise;
    },
    transformState: state,
    reset() {
      started = false; phase = "resetting";
      if (window.__resetRandomSeed) window.__resetRandomSeed();
      location.reload();
      return true;
    },
  };
  // The manager injects the profile on every navigation. A reload reset must
  // re-enter Daytime even when no second explicit init() call follows it.
  Promise.resolve().then(() => {
    if (window.gameAPI && typeof window.gameAPI.init === "function") {
      const seed = window.__getRandomSeed ? window.__getRandomSeed() : 42;
      window.gameAPI.init({ seed });
    }
  });
})();
