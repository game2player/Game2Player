// Rendering patch, kept apart from game_api.js.
//
// The café camera carries a Unity PostProcessLayer (post-processing stack v2). Its
// shaders do not compile on Chromium's ANGLE/Vulkan backend ("ERROR: Shader" in the
// console), and the camera then presents a black frame: only the overlay UI is drawn.
// With software WebGL the effect works. Turning the layer off renders the scene on
// every backend, at the cost of the subtle blur/colour effect, so players see the
// same picture whichever renderer the runtime uses.
(function installNomNomRenderPatch() {
  "use strict";

  const CLASS_METHODS = 76;
  const CLASS_METHOD_COUNT = 168; // u16
  const METHOD_NAME = 12;
  const OBJECT_CACHED_PTR = 8; // UnityEngine.Object.m_CachedPtr, 0 once destroyed

  let layerClass = 0;
  let setEnabled = null;
  let getEnabled = null;

  function cString(api, pointer) {
    let text = "";
    for (let i = 0; i < 128; i += 1) {
      const code = api.read("u8", pointer + i);
      if (!code) break;
      text += String.fromCharCode(code);
    }
    return text;
  }

  function method(api, klass, name) {
    const methods = api.read("ptr", klass + CLASS_METHODS);
    const count = api.read("u16", klass + CLASS_METHOD_COUNT);
    for (let i = 0; methods && i < count; i += 1) {
      const info = api.read("ptr", methods + i * 4);
      if (info && cString(api, api.read("ptr", info + METHOD_NAME)) === name) {
        return { pointer: api.read("ptr", info), info: info };
      }
    }
    return null;
  }

  function classes(api, name) {
    const memory = api.status().memoryBytes;
    const found = api.findUnityClasses(name, { start: 1 << 16, end: memory, limit: 4 });
    return (Array.isArray(found) ? found : found.results || []).map((item) => item.address);
  }

  // Returns the number of live PostProcessLayer components now disabled.
  window.__nomnomDisablePostProcessing = function disablePostProcessing(api) {
    if (!layerClass) layerClass = classes(api, "PostProcessLayer")[0] || 0;
    if (!setEnabled || !getEnabled) {
      for (const klass of classes(api, "Behaviour")) {
        try {
          setEnabled = method(api, klass, "set_enabled");
          getEnabled = method(api, klass, "get_enabled");
        } catch (_) {
          setEnabled = getEnabled = null;
        }
        if (setEnabled && getEnabled) break;
      }
    }
    if (!layerClass || !setEnabled || !getEnabled) return 0;
    const memory = api.status().memoryBytes;
    const found = api.findUnityInstances(layerClass, { start: 1 << 20, end: memory, limit: 8 });
    let disabled = 0;
    for (const item of Array.isArray(found) ? found : found.results || []) {
      const layer = typeof item === "number" ? item : item.address;
      if (!api.read("ptr", layer + OBJECT_CACHED_PTR)) continue; // destroyed component
      if (api.call(getEnabled.pointer, [layer, getEnabled.info])) {
        api.call(setEnabled.pointer, [layer, 0, setEnabled.info]);
      }
      if (!api.call(getEnabled.pointer, [layer, getEnabled.info])) disabled += 1;
    }
    return disabled;
  };
}());
