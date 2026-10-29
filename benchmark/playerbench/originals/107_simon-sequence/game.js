(() => {
  "use strict";
  const LEVELS = [
    { name: "Warm Signal", description: "Repeat a two-step signal.", sequence: ["amber","teal"] },
    { name: "Rose Circuit", description: "Repeat the three-step circuit.", sequence: ["coral","violet","amber"] },
    { name: "Tide Code", description: "Four signals, one exact order.", sequence: ["teal","amber","violet","coral"] },
    { name: "Prism Relay", description: "Keep the five-step relay in memory.", sequence: ["violet","coral","teal","amber","violet"] },
    { name: "Night Pulse", description: "A six-step pulse pattern.", sequence: ["amber","coral","amber","teal","violet","coral"] },
    { name: "Aurora Test", description: "Complete the seven-step final test.", sequence: ["teal","violet","coral","amber","teal","coral","violet"] }
  ];
  const COLORS = ["amber","teal","coral","violet"];
  const params = new URLSearchParams(location.search);
  const requestedLevel = Math.max(1, Math.min(LEVELS.length, Number.parseInt(params.get("level") || "1", 10) || 1));
  const runtime = { levelIndex: requestedLevel - 1, inputIndex: 0, complete: false, failed: false, lastAction: "level_loaded" };
  const els = { name: document.getElementById("level-name"), description: document.getElementById("level-description"), sequence: document.getElementById("sequence-display"), palette: document.getElementById("palette"), step: document.getElementById("step-value"), length: document.getElementById("length-value"), status: document.getElementById("status"), restart: document.getElementById("restart-button") };
  function render() {
    const level = LEVELS[runtime.levelIndex]; document.body.dataset.phase = runtime.complete || runtime.failed ? "terminal" : "input";
    els.name.textContent = String(runtime.levelIndex + 1).padStart(2, "0") + " · " + level.name; els.description.textContent = level.description; els.step.textContent = String(runtime.inputIndex); els.length.textContent = String(level.sequence.length);
    els.sequence.replaceChildren(); level.sequence.forEach((color, index) => { const chip = document.createElement("span"); chip.className = "sequence-chip " + color; chip.dataset.index = String(index); chip.dataset.color = color; chip.textContent = String(index + 1); chip.setAttribute("aria-label", "Signal " + (index + 1) + ": " + color); els.sequence.append(chip); });
    els.palette.replaceChildren(); COLORS.forEach(color => { const button = document.createElement("button"); button.type = "button"; button.className = "signal-button " + color; button.dataset.color = color; button.textContent = color === "amber" ? "A" : color === "teal" ? "T" : color === "coral" ? "C" : "V"; button.setAttribute("aria-label", "Play " + color); button.disabled = runtime.complete || runtime.failed; button.addEventListener("click", () => choose(color)); els.palette.append(button); });
    els.status.className = runtime.complete ? "status win" : "status"; els.status.textContent = runtime.complete ? "Signal matched — challenge cleared." : runtime.failed ? "Signal missed — replay this level." : "Repeat step " + (runtime.inputIndex + 1) + " of " + level.sequence.length;
  }
  function loadLevel(index) { runtime.levelIndex = index; runtime.inputIndex = 0; runtime.complete = false; runtime.failed = false; runtime.lastAction = "level_loaded"; render(); }
  function choose(color) { if (runtime.complete || runtime.failed) return; const expected = LEVELS[runtime.levelIndex].sequence[runtime.inputIndex]; if (color !== expected) { runtime.failed = true; runtime.lastAction = "wrong_signal"; render(); return; } runtime.inputIndex += 1; runtime.lastAction = "signal_step"; if (runtime.inputIndex === LEVELS[runtime.levelIndex].sequence.length) { runtime.complete = true; runtime.lastAction = "sequence_complete"; } render(); }
  function getState() { const level = LEVELS[runtime.levelIndex]; const progress = runtime.complete ? 100 : Math.min(99, Math.round(runtime.inputIndex / level.sequence.length * 100)); return { schemaVersion: "2.0", gameId: "107_simon-sequence", timestampMs: Date.now(), gameTimeMs: 0, status: runtime.complete || runtime.failed ? "terminal" : "ready", is_actionable: !runtime.complete && !runtime.failed, terminal: { isTerminal: runtime.complete || runtime.failed, outcome: runtime.complete ? "success" : runtime.failed ? "fail" : null, reason: runtime.complete ? "sequence_complete" : runtime.failed ? "wrong_signal" : null }, game_state: { level: runtime.levelIndex + 1, level_name: level.name, phase: runtime.complete || runtime.failed ? "terminal" : "input", sequence_length: level.sequence.length, sequence: [...level.sequence], input_index: runtime.inputIndex, last_action: runtime.lastAction }, metrics: { score: runtime.inputIndex, task_progress: progress, objective_complete: runtime.complete ? 1 : 0, failed: runtime.failed ? 1 : 0 } }; }
  els.restart.addEventListener("click", () => loadLevel(runtime.levelIndex)); window.__installGameAPI({ getState, reset: () => loadLevel(runtime.levelIndex), notes: ["deterministic_sequences", "offline_runtime", "public_sequence_before_input"] }); loadLevel(runtime.levelIndex);
})();

