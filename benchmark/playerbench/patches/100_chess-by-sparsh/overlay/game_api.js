// Readable state integration loaded before the compiled application bundle.
(() => {
  "use strict";

  let stockfishFactory;
  let episodeStartedAt = Date.now();
  const wrappedApi = Symbol("wrappedApi");

  function serializeStockfishSearches(engine) {
    if (!engine || engine.__searchesSerialized) return engine;

    const realAdd = engine.addMessageListener.bind(engine);
    const realPost = engine.postMessage.bind(engine);
    const listeners = [];
    const queue = [];
    let permanentListener = null;
    let pendingPosition = null;
    let active = null;

    function drain() {
      if (active || queue.length === 0) return;
      active = queue.shift();
      if (active.position) realPost(active.position);
      realPost(active.command);
    }

    realAdd((message) => {
      const bestMove = typeof message === "string" && message.startsWith("bestmove");
      const searchKind = active && active.kind;
      for (const listener of [...listeners]) {
        // Depth searches are used only for move analysis. Deliver their
        // result to the temporary analysis listener, not to the live-game
        // callback that would otherwise mistake it for Black's move.
        if (!(bestMove && searchKind === "analysis" && listener === permanentListener)) {
          listener(message);
        }
      }
      if (bestMove && active) {
        active = null;
        drain();
      }
    });

    engine.addMessageListener = (listener) => {
      if (!permanentListener) permanentListener = listener;
      listeners.push(listener);
    };
    engine.removeMessageListener = (listener) => {
      const index = listeners.indexOf(listener);
      if (index >= 0) listeners.splice(index, 1);
    };
    engine.postMessage = (command) => {
      if (typeof command !== "string") {
        realPost(command);
        return;
      }
      if (command.startsWith("position ")) {
        pendingPosition = command;
        return;
      }
      if (command.startsWith("go ")) {
        queue.push({
          position: pendingPosition,
          command,
          kind: command.startsWith("go depth ") ? "analysis" : "move",
        });
        pendingPosition = null;
        drain();
        return;
      }
      if (command === "stop") queue.length = 0;
      realPost(command);
    };
    Object.defineProperty(engine, "__searchesSerialized", { value: true });
    return engine;
  }

  Object.defineProperty(window, "Stockfish", {
    configurable: true,
    get() {
      if (!stockfishFactory) return undefined;
      return (...args) => Promise.resolve(Reflect.apply(stockfishFactory, window, args))
        .then(serializeStockfishSearches);
    },
    set(value) {
      stockfishFactory = value;
    },
  });

  let currentApi;
  Object.defineProperty(window, "gameAPI", {
    configurable: true,
    get() {
      return currentApi;
    },
    set(api) {
      if (api && !api[wrappedApi]) {
        const getState = api.getState.bind(api);
        const reset = api.reset.bind(api);
        api.getState = () => ({
          ...getState(),
          gameTimeMs: Math.max(0, Date.now() - episodeStartedAt),
        });
        api.reset = async (...args) => {
          episodeStartedAt = Date.now();
          return reset(...args);
        };
        Object.defineProperty(api, wrappedApi, { value: true });
      }
      currentApi = api;
    },
  });

  function isButton(button, label) {
    return button instanceof HTMLButtonElement && button.textContent.trim() === label;
  }

  document.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("button") : null;
    if (isButton(button, "New Game")) episodeStartedAt = Date.now();
    if (isButton(button, "Undo") && document.querySelector(".board-computer-thinking")) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  function syncUndoButton() {
    const undo = [...document.querySelectorAll("button")].find((button) => isButton(button, "Undo"));
    if (!undo) return;
    const shouldDisable = Boolean(document.querySelector(".board-computer-thinking")) ||
      !document.querySelector(".move-row");
    if (undo.disabled !== shouldDisable) undo.disabled = shouldDisable;
  }

  document.addEventListener("DOMContentLoaded", () => {
    syncUndoButton();
    new MutationObserver(syncUndoButton).observe(document.body, {
      attributes: true,
      attributeFilter: ["class"],
      childList: true,
      subtree: true,
    });
  });
})();
