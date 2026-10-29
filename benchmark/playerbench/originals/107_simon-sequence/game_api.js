(() => {
  "use strict";
  window.__installGameAPI = ({ getState, reset, notes = [] }) => {
    if (typeof getState !== "function" || typeof reset !== "function") throw new TypeError("gameAPI requires getState and reset callbacks");
    window.gameAPI = { version: "2.0", capabilities: { supports_seed: false, supports_level_select: true, supports_difficulty: false, supports_inplace_reset: true, supports_reload_reset: true, supports_pause_detection: false, supports_menu_detection: false, provides_actionable_flag: true }, async init() { return { ok: true, notes }; }, getState, async reset() { reset(); return { ok: true, method: "inplace_reset" }; } };
  };
})();

