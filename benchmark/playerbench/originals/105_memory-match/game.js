(() => {
  "use strict";

  const GAME_ID = "105_memory-match";
  const SYMBOLS = {
    sun: { id: "sun", glyph: "☀", name: "Sun" },
    moon: { id: "moon", glyph: "☾", name: "Moon" },
    star: { id: "star", glyph: "✦", name: "Star" },
    leaf: { id: "leaf", glyph: "❧", name: "Leaf" },
    flower: { id: "flower", glyph: "✿", name: "Flower" },
    mushroom: { id: "mushroom", glyph: "♠", name: "Mushroom" },
    butterfly: { id: "butterfly", glyph: "♫", name: "Butterfly" },
    wave: { id: "wave", glyph: "≈", name: "Wave" },
    shell: { id: "shell", glyph: "◖", name: "Shell" },
    fish: { id: "fish", glyph: "◉", name: "Fish" },
    coral: { id: "coral", glyph: "♧", name: "Coral" },
    anchor: { id: "anchor", glyph: "⚓", name: "Anchor" },
    pearl: { id: "pearl", glyph: "●", name: "Pearl" },
    planet: { id: "planet", glyph: "◌", name: "Planet" },
    comet: { id: "comet", glyph: "☄", name: "Comet" },
    telescope: { id: "telescope", glyph: "⌕", name: "Telescope" },
    satellite: { id: "satellite", glyph: "◇", name: "Satellite" },
    rocket: { id: "rocket", glyph: "⬙", name: "Rocket" },
    eclipse: { id: "eclipse", glyph: "◐", name: "Eclipse" },
    flame: { id: "flame", glyph: "♨", name: "Flame" },
    droplet: { id: "droplet", glyph: "♢", name: "Droplet" },
    bolt: { id: "bolt", glyph: "ϟ", name: "Bolt" },
    snowflake: { id: "snowflake", glyph: "❄", name: "Snowflake" },
    wind: { id: "wind", glyph: "≋", name: "Wind" },
    mountain: { id: "mountain", glyph: "△", name: "Mountain" },
    crystal: { id: "crystal", glyph: "◇", name: "Crystal" },
    seed: { id: "seed", glyph: "♧", name: "Seed" },
    mask: { id: "mask", glyph: "◒", name: "Mask" },
    lantern: { id: "lantern", glyph: "⌂", name: "Lantern" },
    crown: { id: "crown", glyph: "♛", name: "Crown" },
    ticket: { id: "ticket", glyph: "▱", name: "Ticket" },
    drum: { id: "drum", glyph: "◍", name: "Drum" },
    balloon: { id: "balloon", glyph: "○", name: "Balloon" },
    confetti: { id: "confetti", glyph: "✧", name: "Confetti" },
    ribbon: { id: "ribbon", glyph: "∞", name: "Ribbon" },
  };

  const LEVELS = [
    {
      name: "Dawn Sprint", description: "A small sunrise set to warm up your recall.", theme: "First light",
      columns: 3, cards: ["sun", "moon", "star", "moon", "star", "sun"],
    },
    {
      name: "Garden Grid", description: "A bright garden where every shape has a twin.", theme: "Botanical archive",
      columns: 4, cards: ["leaf", "flower", "mushroom", "butterfly", "mushroom", "leaf", "butterfly", "flower"],
    },
    {
      name: "Tidal Signals", description: "Read the rhythm of a six-pair tide chart.", theme: "Coastal frequency",
      columns: 4, cards: ["wave", "shell", "fish", "coral", "anchor", "pearl", "coral", "wave", "pearl", "anchor", "shell", "fish"],
    },
    {
      name: "Night Observatory", description: "Map the sky before the constellation fades.", theme: "Astral survey",
      columns: 4, cards: ["star", "planet", "comet", "moon", "telescope", "satellite", "rocket", "eclipse", "moon", "rocket", "star", "comet", "eclipse", "planet", "telescope", "satellite"],
    },
    {
      name: "Elemental Relay", description: "Eight forces, one focused chain of memory.", theme: "Elemental vault",
      columns: 4, cards: ["flame", "droplet", "bolt", "snowflake", "wind", "mountain", "crystal", "seed", "snowflake", "crystal", "seed", "flame", "mountain", "droplet", "wind", "bolt"],
    },
    {
      name: "Festival Vault", description: "The final collection hides its pairs in a parade.", theme: "Midnight festival",
      columns: 4, cards: ["mask", "lantern", "crown", "ticket", "drum", "balloon", "confetti", "ribbon", "crown", "ribbon", "mask", "balloon", "ticket", "lantern", "confetti", "drum"],
    },
  ];

  const els = {
    app: document.getElementById("app"), board: document.getElementById("board"), picker: document.getElementById("level-picker"),
    fileNumber: document.getElementById("file-number"), levelName: document.getElementById("level-name"), levelDescription: document.getElementById("level-description"),
    pairs: document.getElementById("pairs-value"), moves: document.getElementById("moves-value"), misses: document.getElementById("misses-value"),
    progressBar: document.getElementById("progress-bar"), progressValue: document.getElementById("progress-value"), streak: document.getElementById("streak-value"),
    phaseTitle: document.getElementById("phase-title"), phaseBadge: document.getElementById("phase-badge"), statusCopy: document.getElementById("status-copy"), themeCopy: document.getElementById("theme-copy"),
    restart: document.getElementById("restart-button"), completeCard: document.getElementById("complete-card"), completeSummary: document.getElementById("complete-summary"), replay: document.getElementById("replay-button"),
  };

  function requestedLevelFromUrl() {
    const raw = Number(new URLSearchParams(location.search).get("level") || 1);
    return Number.isInteger(raw) && raw >= 1 && raw <= LEVELS.length ? raw : 1;
  }

  const taskRequestedLevel = new URLSearchParams(location.search).has("level") ? requestedLevelFromUrl() : null;
  const runtime = {
    levelIndex: 0, requestedLevel: taskRequestedLevel || 1, cards: [], phase: "select-first", pendingFirst: null, mismatch: [],
    matchedPairs: 0, moves: 0, mismatches: 0, streak: 0, bestScore: 0, restarts: 0, completed: false, completedLevels: new Set(),
    lastAction: "booting", startedAt: Date.now(),
  };

  function currentLevel() { return LEVELS[runtime.levelIndex]; }

  function createCards(level) {
    return level.cards.map((symbolId, index) => ({ index, symbolId, state: "hidden" }));
  }

  function requestedLevelMatches() { return runtime.requestedLevel === runtime.levelIndex + 1; }
  function score() { return runtime.matchedPairs * 100; }
  function progress() { return runtime.completed ? 100 : Math.round((runtime.matchedPairs / currentLevel().cards.length * 2) * 100); }
  function isComplete() { return runtime.matchedPairs === currentLevel().cards.length / 2; }

  function setDataset() {
    const level = currentLevel();
    els.app.dataset.ready = "true";
    els.app.dataset.actionable = String(!runtime.completed);
    els.app.dataset.terminal = String(runtime.completed && requestedLevelMatches());
    els.app.dataset.phase = runtime.phase;
    els.app.dataset.level = String(runtime.levelIndex + 1);
    els.app.dataset.cardCount = String(level.cards.length);
    els.app.dataset.lastAction = runtime.lastAction;
    els.app.dataset.totalPairs = String(level.cards.length / 2);
    els.app.dataset.matchedPairs = String(runtime.matchedPairs);
    els.app.dataset.moves = String(runtime.moves);
    els.app.dataset.mismatches = String(runtime.mismatches);
    els.app.dataset.score = String(score());
    els.app.dataset.progress = String(progress());
  }

  function phaseCopy() {
    if (runtime.completed) return ["Board complete", "COMPLETE", "All pairs are remembered."];
    if (runtime.phase === "select-second") return ["Find its twin", "REMEMBER", "One card is waiting for a match."];
    if (runtime.phase === "mismatch") return ["Hide the mismatch", "RESET", "Tap either revealed card to hide both."];
    return ["Select a card", "READY", "Every symbol has one twin."];
  }

  function renderCard(card) {
    const symbol = SYMBOLS[card.symbolId];
    const button = document.createElement("button");
    const visible = card.state !== "hidden";
    button.type = "button";
    button.className = `memory-card is-${card.state}`;
    button.dataset.index = String(card.index);
    button.disabled = runtime.completed || (runtime.phase !== "mismatch" && card.state === "matched");
    button.setAttribute("role", "gridcell");
    if (visible) {
      button.dataset.value = symbol.id;
      button.setAttribute("aria-label", `${symbol.name} card${card.state === "matched" ? ", matched" : ", revealed"}`);
      button.innerHTML = `<span class="card-back"><span class="card-symbol" aria-hidden="true">${symbol.glyph}</span><span class="card-name">${symbol.name}</span></span>`;
    } else {
      button.setAttribute("aria-label", "Face-down memory card");
      button.innerHTML = `<span class="card-front"><span aria-hidden="true">?</span></span>`;
    }
    button.addEventListener("click", () => handleCardClick(card.index));
    return button;
  }

  function render() {
    const level = currentLevel();
    const [title, badge, copy] = phaseCopy();
    const percentage = progress();
    els.fileNumber.textContent = String(runtime.levelIndex + 1).padStart(2, "0");
    els.levelName.textContent = level.name;
    els.levelDescription.textContent = level.description;
    els.pairs.textContent = `${runtime.matchedPairs} / ${level.cards.length / 2}`;
    els.moves.textContent = String(runtime.moves);
    els.misses.textContent = String(runtime.mismatches);
    els.progressBar.style.width = `${percentage}%`;
    els.progressValue.textContent = `${percentage}%`;
    els.streak.textContent = String(runtime.streak);
    els.phaseTitle.textContent = title;
    els.phaseBadge.textContent = badge;
    els.statusCopy.textContent = copy;
    els.themeCopy.textContent = level.theme;
    els.board.style.setProperty("--columns", String(level.columns));
    els.board.setAttribute("aria-label", `${level.name} memory board with ${level.cards.length} cards`);
    els.board.replaceChildren(...runtime.cards.map(renderCard));
    els.completeCard.hidden = !runtime.completed;
    if (runtime.completed) {
      els.completeSummary.textContent = `Solved in ${runtime.moves} turns with ${runtime.mismatches} mismatch${runtime.mismatches === 1 ? "" : "es"}.`;
    }
    [...els.picker.children].forEach((button, index) => {
      button.classList.toggle("active", index === runtime.levelIndex);
      button.classList.toggle("complete", runtime.completedLevels.has(index));
    });
    setDataset();
  }

  function loadLevel(index, updateUrl = false, countRestart = false) {
    const safeIndex = Math.max(0, Math.min(LEVELS.length - 1, index));
    runtime.levelIndex = safeIndex;
    if (taskRequestedLevel === null) runtime.requestedLevel = safeIndex + 1;
    runtime.cards = createCards(currentLevel()); runtime.phase = "select-first"; runtime.pendingFirst = null; runtime.mismatch = [];
    runtime.matchedPairs = 0; runtime.moves = 0; runtime.mismatches = 0; runtime.streak = 0; runtime.bestScore = 0; runtime.completed = false;
    runtime.restarts = countRestart ? runtime.restarts + 1 : 0; runtime.startedAt = Date.now(); runtime.lastAction = countRestart ? "restart" : "level_loaded";
    if (updateUrl && taskRequestedLevel === null) {
      const url = new URL(location.href); url.searchParams.set("level", String(safeIndex + 1)); history.replaceState(null, "", url);
    }
    els.completeCard.hidden = true;
    render();
  }

  function clearMismatch() {
    runtime.mismatch.forEach((index) => { if (runtime.cards[index].state === "revealed") runtime.cards[index].state = "hidden"; });
    runtime.mismatch = []; runtime.phase = "select-first"; runtime.lastAction = "mismatch_cleared"; render();
  }

  function handleCardClick(index) {
    if (runtime.completed) return false;
    if (runtime.phase === "mismatch") {
      if (runtime.mismatch.includes(index)) clearMismatch();
      return false;
    }
    const card = runtime.cards[index];
    if (!card || card.state === "matched" || card.state === "revealed") return false;
    if (runtime.pendingFirst === null) {
      card.state = "revealed"; runtime.pendingFirst = index; runtime.phase = "select-second"; runtime.lastAction = "card_revealed"; render(); return true;
    }
    const first = runtime.cards[runtime.pendingFirst];
    card.state = "revealed"; runtime.moves += 1; runtime.pendingFirst = null;
    if (first.symbolId === card.symbolId) {
      first.state = "matched"; card.state = "matched"; runtime.matchedPairs += 1; runtime.streak += 1; runtime.bestScore = Math.max(runtime.bestScore, score());
      runtime.phase = "select-first"; runtime.lastAction = "pair_matched";
      if (isComplete()) {
        runtime.completed = true; runtime.phase = "complete"; runtime.completedLevels.add(runtime.levelIndex); runtime.lastAction = "board_complete";
      }
    } else {
      runtime.mismatches += 1; runtime.streak = 0; runtime.mismatch = [first.index, card.index]; runtime.phase = "mismatch"; runtime.lastAction = "mismatch";
    }
    render(); return true;
  }

  function restart() { loadLevel(runtime.levelIndex, false, true); }

  function createPicker() {
    els.picker.replaceChildren();
    LEVELS.forEach((level, index) => {
      const button = document.createElement("button"); button.type = "button"; button.textContent = String(index + 1).padStart(2, "0"); button.title = level.name;
      button.disabled = taskRequestedLevel !== null && index + 1 !== taskRequestedLevel;
      button.addEventListener("click", () => loadLevel(index, true)); els.picker.appendChild(button);
    });
  }

  function getState() {
    const level = currentLevel();
    const visibleCards = runtime.cards.map((card) => ({
      index: card.index, state: card.state, value: card.state === "hidden" ? null : SYMBOLS[card.symbolId].id,
    }));
    const terminal = runtime.completed && requestedLevelMatches();
    return {
      schemaVersion: "2.0", gameId: GAME_ID, seed: null, timestampMs: Date.now(), gameTimeMs: Date.now() - runtime.startedAt,
      status: terminal ? "terminal" : "ready", is_actionable: !runtime.completed,
      terminal: { isTerminal: terminal, outcome: terminal ? "success" : null, reason: terminal ? "all_pairs_found" : null },
      game_state: {
        level: runtime.levelIndex + 1, requested_level: runtime.requestedLevel, requested_level_match: requestedLevelMatches(), level_name: level.name,
        theme: level.theme, phase: runtime.phase, cards: visibleCards, pending_first: runtime.pendingFirst, mismatch_cards: [...runtime.mismatch],
        last_action: runtime.lastAction, visible_card_count: runtime.cards.filter((card) => card.state !== "hidden").length,
        hidden_card_count: runtime.cards.filter((card) => card.state === "hidden").length, total_cards: runtime.cards.length,
      },
      metrics: {
        task_progress: terminal ? 100 : progress(), matched_pairs: runtime.matchedPairs, total_pairs: level.cards.length / 2, score: score(),
        best_score: runtime.bestScore, moves: runtime.moves, mismatches: runtime.mismatches, streak: runtime.streak,
        perfect: terminal && runtime.mismatches === 0 ? 1 : 0, elapsed_ms: Date.now() - runtime.startedAt, restarts: runtime.restarts,
      },
    };
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "r" || event.key === "R") { event.preventDefault(); restart(); }
  });
  els.restart.addEventListener("click", restart); els.replay.addEventListener("click", restart);

  window.__installGameAPI({
    getState,
    reset() {
      if (typeof window.__resetRandomSeed === "function") window.__resetRandomSeed();
      loadLevel(runtime.requestedLevel - 1);
    },
    notes: ["deterministic_url_levels", "dom_card_state", "offline_runtime", "hidden_values_concealed"],
  });

  createPicker(); loadLevel(requestedLevelFromUrl() - 1);
})();
