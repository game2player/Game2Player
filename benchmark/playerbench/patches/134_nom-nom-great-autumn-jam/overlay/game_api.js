(function installNomNomAdapter() {
  "use strict";

  const GAME_ID = "134_nom-nom-great-autumn-jam";

  // The only save data this game writes is the fishing prize collection in Unity's
  // IndexedDB file system. Every task starts from a fresh game, so the store is
  // dropped before the Unity loader can mount it.
  try {
    if (window.indexedDB) window.indexedDB.deleteDatabase("/idbfs");
  } catch (_) {}

  // IL2CPP layout of this 32-bit build (Il2CppClass and MethodInfo).
  const CLASS_NAME = 8;
  const CLASS_METHODS = 76;
  const CLASS_STATIC_FIELDS = 92;
  const CLASS_METHOD_COUNT = 168; // u16
  const METHOD_POINTER = 0;
  const METHOD_NAME = 12;
  // System.Collections.Generic.List<T>: _items @8, _size @12; array data @16.
  const LIST_ITEMS = 8;
  const LIST_SIZE = 12;
  const ARRAY_DATA = 16;

  // TypeInfo slots, shared with catalog/unity/134_nom-nom-great-autumn-jam.yaml.
  const TYPE_INFO = { dayManager: 3983976, blackboard: 3983948 };

  // Field offsets read from the live Il2CppClass field tables.
  const DAY = {
    recurrentClients: 36, // List<Client>: perfect serves of the current day
    clientQueue: 40, // List<Client>: customers at the counter and in line
    yesterdayClients: 44, // List<Client>: regulars who come back the next day
    dayDuration: 76,
    time: 88,
    isDay: 92,
    ratings: 96, // List<float>: one star rating appended per finished day
    lost: 100,
  };
  const CLIENT = {
    served: 20,
    name: 32,
    typePreference: 36, // jamType: 1 Apple, 2 Pumpkin, 3 Fig
    charmPreference: 40, // jamCharm: 1 Space, 2 Nature, 3 Spooky
    tastePreference: 44, // dishTaste: 1 Sweet, 2 Sour, 3 Savory
    recurringClient: 148,
    todayRating: 152, // number of matched preferences, 0..3
  };
  const BLACKBOARD = { scene: 32, cameraManager: 24, webSite: 208, orderSentence0: 228 };
  const CAMERA = { currentSituation: 48 };
  const WEBSITE = { reviews: 32 };
  const REVIEW = { rating: 28 };

  const FRUIT = ["none", "apple", "pumpkin", "fig"];
  const CHARM = ["none", "space", "nature", "spooky"];
  const TASTE = ["none", "sweet", "sour", "savory"];
  const VIEW = ["dish_picker", "jam_shelf", "jam_maker", "cafe"];

  const START_TIMEOUT_MS = 55000;
  const params = new URLSearchParams(window.location.search);
  const requestedTask = (params.get("task") || "").trim() || null;

  let phase = "boot";
  let startupError = null;
  let startPromise = null;
  let startDetails = null;
  let resetPending = false;
  let lastError = null;
  let sceneClass = 0;
  let startTrace = null;

  // Event latches. Client objects persist for the whole run (a regular who comes
  // back is the same object), so addresses identify customers.
  const progress = {
    bestServeRating: 0,
    bestDayPerfect: 0,
    regularStage: 0,
    dayIndex: null,
    carryover: new Set(), // recurrentClients entries already present when a day began
    returning: new Map(), // client address -> day index of its return visit
  };

  const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
  const finite = (value) => (Number.isFinite(value) ? Number(value) : null);

  function bridge() {
    const api = window.__unityBridge;
    if (!api) throw new Error("Unity bridge is not installed");
    return api;
  }
  const read = (type, address) => bridge().read(type, address);

  function cString(pointer) {
    let text = "";
    for (let i = 0; i < 128; i += 1) {
      const code = read("u8", pointer + i);
      if (!code) break;
      text += String.fromCharCode(code);
    }
    return text;
  }

  function className(object) {
    const klass = object ? read("ptr", object) : 0;
    return klass ? cString(read("ptr", klass + CLASS_NAME)) : null;
  }

  function staticInstance(typeInfo) {
    const klass = read("ptr", typeInfo);
    // Until the café scene first uses the class, IL2CPP leaves an encoded metadata
    // token (an odd value) in the slot instead of the class pointer.
    if (!klass || (klass & 3) !== 0 || klass >= bridge().status().memoryBytes) return 0;
    const statics = read("ptr", klass + CLASS_STATIC_FIELDS);
    const instance = statics ? read("ptr", statics) : 0;
    return instance && read("ptr", instance) === klass ? instance : 0;
  }

  function listItems(list, limit) {
    if (!list) return [];
    const items = read("ptr", list + LIST_ITEMS);
    const size = read("i32", list + LIST_SIZE);
    if (!items || size < 0 || size > (limit || 256)) return [];
    const out = [];
    for (let i = 0; i < size; i += 1) out.push(read("ptr", items + ARRAY_DATA + i * 4));
    return out;
  }

  function floatList(list) {
    return listItems(list, 4096).map((bits) => {
      const view = new DataView(new ArrayBuffer(4));
      view.setUint32(0, bits >>> 0, true);
      return view.getFloat32(0, true);
    });
  }

  function readClient(address) {
    if (!address) return null;
    const namePointer = read("ptr", address + CLIENT.name);
    return {
      address: address,
      name: namePointer ? bridge().readIl2CppString(namePointer) : null,
      served: read("bool", address + CLIENT.served),
      recurring: read("bool", address + CLIENT.recurringClient),
      rating: finite(read("f32", address + CLIENT.todayRating)) || 0,
      fruit: FRUIT[read("i32", address + CLIENT.typePreference)] || null,
      charm: CHARM[read("i32", address + CLIENT.charmPreference)] || null,
      taste: TASTE[read("i32", address + CLIENT.tastePreference)] || null,
    };
  }

  // ---- task start: the title's PLAY button calls Scene.Play() -------------------

  function findMethod(klass, name) {
    const methods = read("ptr", klass + CLASS_METHODS);
    const count = read("u16", klass + CLASS_METHOD_COUNT);
    if (!methods || !count) return null;
    for (let i = 0; i < count; i += 1) {
      const info = read("ptr", methods + i * 4);
      if (info && cString(read("ptr", info + METHOD_NAME)) === name) {
        return { info: info, pointer: read("ptr", info + METHOD_POINTER) };
      }
    }
    return null;
  }

  function titleScene() {
    const api = bridge();
    const memory = api.status().memoryBytes;
    if (!memory) return null;
    if (!sceneClass) {
      // UnityEngine.SceneManagement.Scene shares the name; the game's class is the
      // one whose method table (filled once the title runs) holds Play().
      const found = api.findUnityClasses("Scene", { start: 1 << 16, end: memory, limit: 4 });
      const list = Array.isArray(found) ? found : (found && found.results) || [];
      const owner = list.find((klass) => {
        try {
          return findMethod(klass.address, "Play");
        } catch (_) {
          return null; // a half-initialised or unrelated class
        }
      });
      startTrace = `scene classes=${list.length} owner=${Boolean(owner)}`;
      if (!owner) return null;
      sceneClass = owner.address;
    }
    const play = findMethod(sceneClass, "Play");
    if (!play) return null;
    const found = api.findUnityInstances(sceneClass, { start: 1 << 20, end: memory, limit: 4 });
    const instances = Array.isArray(found) ? found : (found && found.results) || [];
    startTrace = `scene instances=${instances.length}`;
    if (instances.length !== 1) return null;
    const instance = typeof instances[0] === "number" ? instances[0] : instances[0].address;
    return { instance: instance, play: play };
  }

  function cafeReady() {
    const dayManager = staticInstance(TYPE_INFO.dayManager);
    const blackboard = staticInstance(TYPE_INFO.blackboard);
    if (!dayManager || !blackboard || !read("ptr", blackboard + BLACKBOARD.scene)) return null;
    return {
      dayManager: dayManager,
      isDay: read("bool", dayManager + DAY.isDay),
      time: read("f32", dayManager + DAY.time),
      finishedDays: floatList(read("ptr", dayManager + DAY.ratings)).length,
    };
  }

  async function waitForCafe(deadline, previousDayManager) {
    for (;;) {
      if (performance.now() > deadline) throw new Error("cafe_scene_not_loaded");
      await sleep(250);
      try {
        const cafe = cafeReady();
        if (cafe && cafe.dayManager !== previousDayManager) return cafe;
      } catch (_) {}
    }
  }

  function orderSentenceLoaded() {
    const blackboard = staticInstance(TYPE_INFO.blackboard);
    const pointer = blackboard ? read("ptr", blackboard + BLACKBOARD.orderSentence0) : 0;
    return Boolean(pointer && bridge().readIl2CppString(pointer));
  }

  async function startTask() {
    phase = "starting";
    const deadline = performance.now() + START_TIMEOUT_MS;
    let scene = null;
    while (!scene) {
      if (performance.now() > deadline) throw new Error("title_scene_not_found");
      try {
        if (cafeReady()) throw new Error("cafe_already_loaded");
        scene = titleScene();
      } catch (error) {
        if (String(error && error.message) === "cafe_already_loaded") throw error;
        startTrace = String(error && error.message ? error.message : error);
        scene = null;
      }
      if (!scene) await sleep(250);
    }
    if (className(scene.instance) !== "Scene") throw new Error("scene_type_mismatch");
    bridge().call(scene.play.pointer, [scene.instance, scene.play.info]);
    let cafe = await waitForCafe(deadline, 0);
    // The blackboard reads its localized dialogue lines once, when the café loads.
    // Pressing PLAY before the localization tables finish loading leaves them null
    // and every customer's dialogue stalls empty, so the café is reloaded through
    // the game's own Scene.Retry() until the lines read back.
    let reloads = 0;
    while (!orderSentenceLoaded()) {
      if (performance.now() > deadline || reloads >= 12) throw new Error("localization_not_loaded");
      await sleep(500);
      if (orderSentenceLoaded()) break;
      const blackboard = staticInstance(TYPE_INFO.blackboard);
      const cafeScene = read("ptr", blackboard + BLACKBOARD.scene);
      const retry = findMethod(read("ptr", cafeScene), "Retry");
      if (!retry || className(cafeScene) !== "Scene") throw new Error("scene_retry_missing");
      bridge().call(retry.pointer, [cafeScene, retry.info]);
      reloads += 1;
      cafe = await waitForCafe(deadline, cafe.dayManager);
    }
    // render_patch.js: the café camera's post-processing renders black on ANGLE/Vulkan.
    let patched = 0;
    while (!patched) {
      if (performance.now() > deadline) throw new Error("postprocess_patch_failed");
      try {
        patched = window.__nomnomDisablePostProcessing(bridge());
      } catch (error) {
        startTrace = String(error && error.message ? error.message : error);
      }
      if (!patched) await sleep(250);
    }
    if (!cafe.isDay || cafe.finishedDays !== 0 || !(cafe.time < 30)) {
      throw new Error(`unexpected_start day=${cafe.isDay} finished=${cafe.finishedDays} t=${cafe.time}`);
    }
    startDetails = { route: "Scene.Play", localization_reloads: reloads, day: 1, finished_days: 0, clock_s: cafe.time, postprocess_layers_disabled: patched };
    phase = "ready";
  }

  // ---- progress -------------------------------------------------------------------

  function updateProgress(day) {
    const dayIndex = day.finishedDays; // 0 during and after day 1 until it ends
    if (progress.dayIndex !== dayIndex && day.isDay) {
      progress.dayIndex = dayIndex;
      progress.carryover = new Set(day.perfectList);
    }
    for (const client of day.queue) {
      if (client.served && client.rating > progress.bestServeRating) {
        progress.bestServeRating = Math.min(3, client.rating);
      }
      // recurringClient is set by a perfect serve together with served=true; a
      // regular re-injected on a later day comes back with served cleared.
      if (day.isDay && client.recurring && !client.served && !progress.returning.has(client.address)) {
        progress.returning.set(client.address, dayIndex);
      }
    }
    for (const rating of day.reviewRatings) {
      if (rating > progress.bestServeRating) progress.bestServeRating = Math.min(3, rating);
    }
    let todayPerfect = 0;
    if (day.isDay && progress.dayIndex === dayIndex) {
      for (const address of day.perfectList) {
        if (progress.carryover.has(address)) continue;
        const client = readClient(address);
        if (!client || !client.served || client.rating < 3) continue;
        todayPerfect += 1;
        progress.bestServeRating = 3;
        if (progress.returning.get(address) === dayIndex) progress.regularStage = 3;
      }
      progress.bestDayPerfect = Math.max(progress.bestDayPerfect, todayPerfect);
    }
    if (progress.bestServeRating >= 3) progress.regularStage = Math.max(progress.regularStage, 1);
    if (progress.returning.size) progress.regularStage = Math.max(progress.regularStage, 2);
    return todayPerfect;
  }

  function readDay() {
    const dayManager = staticInstance(TYPE_INFO.dayManager);
    if (!dayManager) return null;
    const blackboard = staticInstance(TYPE_INFO.blackboard);
    const ratings = floatList(read("ptr", dayManager + DAY.ratings));
    const queue = listItems(read("ptr", dayManager + DAY.clientQueue), 64).map(readClient).filter(Boolean);
    const perfectList = listItems(read("ptr", dayManager + DAY.recurrentClients), 256);
    const returningTomorrow = listItems(read("ptr", dayManager + DAY.yesterdayClients), 256).length;
    let reviewRatings = [];
    let view = null;
    if (blackboard) {
      const webSite = read("ptr", blackboard + BLACKBOARD.webSite);
      if (webSite) {
        reviewRatings = listItems(read("ptr", webSite + WEBSITE.reviews), 512)
          .map((review) => (review ? finite(read("f32", review + REVIEW.rating)) : null))
          .filter((value) => value !== null);
      }
      const camera = read("ptr", blackboard + BLACKBOARD.cameraManager);
      if (camera) view = VIEW[read("i32", camera + CAMERA.currentSituation)] || null;
    }
    return {
      isDay: read("bool", dayManager + DAY.isDay),
      time: finite(read("f32", dayManager + DAY.time)) || 0,
      duration: finite(read("f32", dayManager + DAY.dayDuration)) || 180,
      lost: read("bool", dayManager + DAY.lost),
      ratings: ratings,
      finishedDays: ratings.length,
      queue: queue,
      perfectList: perfectList,
      returningTomorrow: returningTomorrow,
      reviewRatings: reviewRatings,
      view: view,
    };
  }

  // ---- public state ---------------------------------------------------------------

  function transformState(state) {
    try {
      let day = null;
      try {
        day = phase === "ready" ? readDay() : null;
      } catch (error) {
        lastError = String(error && error.message ? error.message : error);
      }
      const todayPerfect = day ? updateProgress(day) : 0;
      const bestDayRating = day && day.ratings.length ? Math.max(...day.ratings) : 0;
      const customer = day && day.queue.length ? day.queue[0] : null;

      let terminal = { isTerminal: false, outcome: null, reason: null };
      if (startupError) terminal = { isTerminal: true, outcome: "fail", reason: startupError };
      else if (day && day.lost) terminal = { isTerminal: true, outcome: "fail", reason: "three_bad_days" };

      let status = "loading";
      let actionable = false;
      if (resetPending) status = "transition";
      else if (terminal.isTerminal) status = "terminal";
      else if (phase === "ready" && day) {
        status = "playing";
        actionable = true;
      }

      const dayNumber = day ? day.finishedDays + (day.isDay ? 1 : 0) : 0;
      state.schemaVersion = "2.0";
      state.gameId = GAME_ID;
      state.gameTimeMs = day ? Math.round((day.finishedDays * day.duration + (day.isDay ? day.time : 0)) * 1000) : 0;
      state.status = status;
      state.is_actionable = actionable;
      state.terminal = terminal;
      state.game_state = {
        provenance: "engine_hook",
        task: requestedTask,
        phase: day ? (day.isDay ? "day" : "night") : "loading",
        day_number: dayNumber,
        days_finished: day ? day.finishedDays : 0,
        day_clock_s: day ? day.time : 0,
        day_length_s: day ? day.duration : 0,
        view: day ? day.view : null,
        customers_present: day ? day.queue.length : 0,
        customer: customer
          ? { name: customer.name, served: customer.served, rating: customer.served ? customer.rating : null, returning: customer.recurring && !customer.served }
          : null,
        day_ratings: day ? day.ratings : [],
        reviews_today: day ? day.reviewRatings.length : 0,
        regulars_tomorrow: day ? day.returningTomorrow : 0,
        lost: day ? day.lost : false,
        start: startDetails,
        // Verifier-only: what the current customer asks for (they say it in dialogue).
        verifier: {
          customer_preferences: customer
            ? { fruit: customer.fruit, charm: customer.charm, taste: customer.taste }
            : null,
        },
      };
      state.metrics = {
        best_serve_rating: progress.bestServeRating,
        best_day_perfect_reviews: progress.bestDayPerfect,
        today_perfect_reviews: todayPerfect,
        regular_customer_stage: progress.regularStage,
        best_day_rating: bestDayRating,
        days_finished: day ? day.finishedDays : 0,
        day_number: dayNumber,
      };
      state.debug = { phase: phase, startup_error: startupError, start_trace: startTrace, last_error: lastError };
      return state;
    } catch (error) {
      lastError = String(error && error.message ? error.message : error);
      return state;
    }
  }

  window.unityAdapter = {
    async init() {
      if (!startPromise) {
        startPromise = startTask().catch((error) => {
          startupError = error && error.message ? error.message : String(error);
          phase = "failed";
        });
      }
      return { ok: true, pending: phase !== "ready" };
    },
    transformState: transformState,
    async reset() {
      if (resetPending) return { ok: false, method: "reload", reason: "reset_in_progress" };
      resetPending = true;
      try {
        if (typeof window.__resetRandomSeed === "function") window.__resetRandomSeed();
      } catch (_) {}
      window.setTimeout(() => window.location.reload(), 0);
      return { ok: true, method: "reload" };
    },
  };

  let attempts = 0;
  const autoInit = () => {
    attempts += 1;
    if (window.gameAPI && typeof window.gameAPI.init === "function") {
      window.gameAPI.init({}).catch(() => {});
    } else if (attempts < 400) {
      window.setTimeout(autoInit, 50);
    }
  };
  window.setTimeout(autoInit, 0);

  document.documentElement.dataset.gameId = GAME_ID;
}());
