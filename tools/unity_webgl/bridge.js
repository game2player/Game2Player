(function installUnityBridge() {
  "use strict";

  if (window.__unityBridge && window.__unityBridge.bridgeVersion) return;

  const originalInstantiate = WebAssembly.instantiate.bind(WebAssembly);
  const originalInstantiateStreaming = WebAssembly.instantiateStreaming
    ? WebAssembly.instantiateStreaming.bind(WebAssembly)
    : null;
  const runtime = {
    instance: null,
    module: null,
    memory: null,
    table: null,
    imports: null,
    exports: null,
    unityInstance: null,
    unityModule: null,
    profile: null,
    configuredAt: 0,
    captures: 0,
    captureScore: -1,
    runtimeLocked: false,
    errors: [],
  };
  const dangerousPathParts = new Set(["__proto__", "prototype", "constructor"]);
  const reservedTopLevelFields = new Set([
    "schemaVersion",
    "gameId",
    "timestampMs",
    "gameTimeMs",
    "status",
    "is_actionable",
    "terminal",
    "raw",
  ]);

  function rememberError(error) {
    const message = error && error.message ? error.message : String(error);
    runtime.errors.push(message);
    if (runtime.errors.length > 20) runtime.errors.shift();
  }

  function isMemory(value) {
    try { return value instanceof WebAssembly.Memory; } catch (_) { return false; }
  }

  function isTable(value) {
    try { return value instanceof WebAssembly.Table; } catch (_) { return false; }
  }

  function rememberRuntimeValue(found, value) {
    if (isMemory(value)) {
      if (!found.memory || value.buffer.byteLength > found.memory.buffer.byteLength) {
        found.memory = value;
      }
      return true;
    }
    if (isTable(value)) {
      if (!found.table || value.length > found.table.length) found.table = value;
      return true;
    }
    return false;
  }

  function scanRuntimeValues(value, depth, seen, found) {
    if (value == null || depth > 1) return;
    if (rememberRuntimeValue(found, value)) return;
    if (typeof value !== "object" || seen.has(value)) return;
    seen.add(value);
    let entries;
    try { entries = Object.entries(value); } catch (_) { return; }
    for (const [, child] of entries) {
      if (rememberRuntimeValue(found, child)) continue;
      if (depth < 1 && child && typeof child === "object") {
        scanRuntimeValues(child, depth + 1, seen, found);
      }
    }
  }

  function runtimeCandidateScore(found) {
    const memoryPages = found.memory ? found.memory.buffer.byteLength / 65536 : 0;
    const tableEntries = found.table ? found.table.length : 0;
    return memoryPages + tableEntries * 16;
  }

  function captureWasm(result, imports) {
    try {
      const instance = result && result.instance instanceof WebAssembly.Instance
        ? result.instance
        : result instanceof WebAssembly.Instance ? result : null;
      const module = result && result.module instanceof WebAssembly.Module ? result.module : null;
      const found = { memory: null, table: null };
      scanRuntimeValues(imports, 0, new Set(), found);
      if (instance) scanRuntimeValues(instance.exports, 0, new Set(), found);
      const score = runtimeCandidateScore(found);
      const currentComplete = Boolean(runtime.memory && runtime.table);
      const candidateComplete = Boolean(found.memory && found.table);
      if (
        !runtime.runtimeLocked
        && (!currentComplete || candidateComplete)
        && score > runtime.captureScore
      ) {
        if (instance) {
          runtime.instance = instance;
          runtime.exports = instance.exports;
        }
        if (module) runtime.module = module;
        if (imports) runtime.imports = imports;
        if (found.memory) runtime.memory = found.memory;
        if (found.table) runtime.table = found.table;
        runtime.captureScore = score;
      }
      runtime.captures += 1;
    } catch (error) {
      rememberError(error);
    }
    return result;
  }

  WebAssembly.instantiate = async function bridgeInstantiate(source, imports) {
    const result = await originalInstantiate(source, imports);
    return captureWasm(result, imports);
  };

  if (originalInstantiateStreaming) {
    WebAssembly.instantiateStreaming = async function bridgeInstantiateStreaming(source, imports) {
      const result = await originalInstantiateStreaming(source, imports);
      return captureWasm(result, imports);
    };
  }

  function captureUnityInstance(instance) {
    if (!instance) return instance;
    runtime.unityInstance = instance;
    runtime.unityModule = instance.Module || instance.module || runtime.unityModule;
    // Emscripten's Module has thousands of properties and large cyclic object
    // graphs.  Never recursively enumerate it; WASM imports/exports have
    // already supplied memory and table, and only these direct aliases matter.
    const module = runtime.unityModule;
    if (module) {
      if (isMemory(module.wasmMemory)) runtime.memory = module.wasmMemory;
      if (isTable(module.wasmTable)) runtime.table = module.wasmTable;
      if (isTable(module.__indirect_function_table)) runtime.table = module.__indirect_function_table;
    }
    if (runtime.memory && runtime.table) runtime.runtimeLocked = true;
    return instance;
  }

  function wrappedUnityFactory(factory) {
    if (typeof factory !== "function" || factory.__bridgeWrapped) return factory;
    const wrapped = function unityFactory(...args) {
      const result = factory.apply(this, args);
      return result instanceof Promise
        ? result.then(captureUnityInstance)
        : captureUnityInstance(result);
    };
    Object.defineProperty(wrapped, "__bridgeWrapped", { value: true });
    return wrapped;
  }

  function wrapCreateUnityInstance() {
    const current = window.createUnityInstance;
    if (typeof current !== "function" || current.__bridgeWrapped) return;
    const wrapped = wrappedUnityFactory(current);
    try { window.createUnityInstance = wrapped; } catch (_) {}
  }

  // Modern templates append the loader script and invoke createUnityInstance
  // from its load handler in the same task.  Register our earlier load listener
  // so the factory is wrapped before the template's handler runs.
  function hookScriptInsertion(name) {
    const original = Node.prototype[name];
    if (typeof original !== "function") return;
    Node.prototype[name] = function bridgeInsertScript(node, ...args) {
      try {
        if (node instanceof HTMLScriptElement) {
          node.addEventListener("load", () => {
            wrapCreateUnityInstance();
          });
        }
      } catch (_) {}
      return original.call(this, node, ...args);
    };
  }
  hookScriptInsertion("appendChild");
  hookScriptInsertion("insertBefore");
  document.addEventListener("load", (event) => {
    if (event.target instanceof HTMLScriptElement) {
      wrapCreateUnityInstance();
    }
  }, true);

  if (!("createUnityInstance" in window)) {
    let createUnityValue;
    try {
      Object.defineProperty(window, "createUnityInstance", {
        configurable: true,
        enumerable: true,
        get() { return createUnityValue; },
        set(value) { createUnityValue = wrappedUnityFactory(value); },
      });
    } catch (_) {}
  }

  function refreshRuntimeReferences() {
    wrapCreateUnityInstance();
    if (window.unityInstance) captureUnityInstance(window.unityInstance);
    if (window.gameInstance) captureUnityInstance(window.gameInstance);
    if (runtime.unityInstance) captureUnityInstance(runtime.unityInstance);
    if (
      window.Module
      && (!runtime.runtimeLocked || window.Module === runtime.unityModule)
    ) {
      runtime.unityModule = window.Module;
      if (isMemory(window.Module.wasmMemory)) runtime.memory = window.Module.wasmMemory;
      if (isTable(window.Module.wasmTable)) runtime.table = window.Module.wasmTable;
    }
    const loader = window.UnityLoader;
    if (loader && loader.instances) {
      try {
        for (const instance of Object.values(loader.instances)) captureUnityInstance(instance);
      } catch (_) {}
    }
  }

  function dataView() {
    if (!runtime.memory) refreshRuntimeReferences();
    if (!runtime.memory || !(runtime.memory.buffer instanceof ArrayBuffer)) {
      throw new Error("Unity WebAssembly memory has not been captured");
    }
    return new DataView(runtime.memory.buffer);
  }

  function checkedAddress(address, size) {
    if (!Number.isInteger(address) || address <= 0) throw new Error(`Invalid address: ${address}`);
    const view = dataView();
    if (!Number.isInteger(size) || size < 0 || address + size > view.byteLength) {
      throw new Error(`Out-of-bounds memory read at ${address} (${size} bytes)`);
    }
    return view;
  }

  const readers = {
    u8: (address) => checkedAddress(address, 1).getUint8(address),
    i8: (address) => checkedAddress(address, 1).getInt8(address),
    u16: (address) => checkedAddress(address, 2).getUint16(address, true),
    i16: (address) => checkedAddress(address, 2).getInt16(address, true),
    u32: (address) => checkedAddress(address, 4).getUint32(address, true),
    i32: (address) => checkedAddress(address, 4).getInt32(address, true),
    f32: (address) => checkedAddress(address, 4).getFloat32(address, true),
    f64: (address) => checkedAddress(address, 8).getFloat64(address, true),
  };
  readers.ptr = readers.u32;
  readers.bool = (address) => readers.u8(address) !== 0;

  function readIl2CppString(pointer, maxLength) {
    if (!pointer) return null;
    const length = readers.i32(pointer + 8);
    const limit = Number.isInteger(maxLength) ? maxLength : 16384;
    if (length < 0 || length > limit) throw new Error(`Invalid IL2CPP string length: ${length}`);
    const view = checkedAddress(pointer + 12, length * 2);
    let value = "";
    const chunk = 2048;
    for (let start = 0; start < length; start += chunk) {
      const codes = [];
      const end = Math.min(length, start + chunk);
      for (let index = start; index < end; index += 1) {
        codes.push(view.getUint16(pointer + 12 + index * 2, true));
      }
      value += String.fromCharCode(...codes);
    }
    return value;
  }

  function integerOption(options, name, fallback, minimum, maximum) {
    const value = options && options[name] !== undefined ? options[name] : fallback;
    if (!Number.isInteger(value) || value < minimum || value > maximum) {
      throw new Error(`${name} must be an integer from ${minimum} to ${maximum}`);
    }
    return value;
  }

  function scanBounds(options, defaultAlignment) {
    const view = dataView();
    const bytes = view.byteLength;
    const alignment = integerOption(options, "alignment", defaultAlignment, 4, 64);
    if ((alignment & (alignment - 1)) !== 0) {
      throw new Error("alignment must be a power of two");
    }
    const defaultStart = bytes > 1024 * 1024 ? 1024 * 1024 : 4;
    const start = integerOption(options, "start", defaultStart, 4, bytes);
    // Scan the full heap by default; a caller may supply a smaller explicit range.
    const end = integerOption(options, "end", bytes, 4, bytes);
    if (start >= end) throw new Error("scan start must be smaller than end");
    return {
      view,
      start: Math.ceil(start / alignment) * alignment,
      end,
      alignment,
      heapBytes: bytes,
      limit: integerOption(options, "limit", 64, 1, 512),
    };
  }

  // A scan that finds nothing looks exactly like a class that is not there, and the
  // default window is only 32 MiB of a heap that starts near 9 MiB and grows toward
  // 2 GiB, so "not found" has usually meant "not looked at". Attach what was actually
  // covered to the returned array: `found.scan.truncated` distinguishes the two, and
  // `found.scan.hitLimit` catches the other silent stop. Non-enumerable, so existing
  // callers that iterate or read .length are unaffected.
  function withScanCoverage(results, scan) {
    Object.defineProperty(results, "scan", {
      value: Object.freeze({
        start: scan.start,
        end: scan.end,
        alignment: scan.alignment,
        heapBytes: scan.heapBytes,
        coveredBytes: Math.max(0, scan.end - scan.start),
        truncated: scan.end < scan.heapBytes,
        hitLimit: results.length >= scan.limit,
      }),
      enumerable: false,
    });
    return results;
  }

  function readCStringFromView(view, pointer, maxLength) {
    if (!Number.isInteger(pointer) || pointer <= 0 || pointer >= view.byteLength) return null;
    const available = Math.min(maxLength, view.byteLength - pointer);
    let length = 0;
    while (length < available && view.getUint8(pointer + length) !== 0) length += 1;
    if (length === 0 || length === available) return null;
    try {
      return new TextDecoder("utf-8", { fatal: true }).decode(
        new Uint8Array(view.buffer, pointer, length)
      );
    } catch (_) {
      return null;
    }
  }

  function classLayout(options) {
    return {
      nameOffset: integerOption(options, "nameOffset", 8, 0, 1024),
      selfOffset: integerOption(options, "selfOffset", 60, 0, 1024),
      staticFieldsOffset: integerOption(options, "staticFieldsOffset", 92, 0, 1024),
      maxNameLength: integerOption(options, "maxNameLength", 128, 1, 1024),
    };
  }

  function describeUnityClass(classPointer, options) {
    const view = dataView();
    const layout = classLayout(options || {});
    checkedAddress(classPointer, Math.max(
      layout.nameOffset,
      layout.selfOffset,
      layout.staticFieldsOffset
    ) + 4);
    const namePointer = view.getUint32(classPointer + layout.nameOffset, true);
    return {
      address: classPointer,
      name: readCStringFromView(view, namePointer, layout.maxNameLength),
      self: view.getUint32(classPointer + layout.selfOffset, true),
      staticFields: view.getUint32(classPointer + layout.staticFieldsOffset, true),
    };
  }

  function findUnityClasses(name, options) {
    if (typeof name !== "string" || !name || name.length > 128) {
      throw new Error("Unity class name must contain 1 to 128 characters");
    }
    const settings = options || {};
    const scan = scanBounds(settings, 4);
    const layout = classLayout(settings);
    const results = [];
    const requiredBytes = Math.max(layout.nameOffset, layout.selfOffset) + 4;
    for (
      let address = scan.start;
      address + requiredBytes <= scan.end && results.length < scan.limit;
      address += scan.alignment
    ) {
      if (scan.view.getUint32(address + layout.selfOffset, true) !== address) continue;
      const namePointer = scan.view.getUint32(address + layout.nameOffset, true);
      if (readCStringFromView(scan.view, namePointer, layout.maxNameLength) !== name) continue;
      results.push(describeUnityClass(address, layout));
    }
    return withScanCoverage(results, scan);
  }

  function valueFromView(view, type, address) {
    const widths = { u8: 1, i8: 1, bool: 1, u16: 2, i16: 2, u32: 4, i32: 4, ptr: 4, f32: 4, f64: 8 };
    const width = widths[type];
    if (!width || address <= 0 || address + width > view.byteLength) {
      throw new Error(`Invalid ${type} filter read at ${address}`);
    }
    if (type === "u8") return view.getUint8(address);
    if (type === "i8") return view.getInt8(address);
    if (type === "bool") return view.getUint8(address) !== 0;
    if (type === "u16") return view.getUint16(address, true);
    if (type === "i16") return view.getInt16(address, true);
    if (type === "u32" || type === "ptr") return view.getUint32(address, true);
    if (type === "i32") return view.getInt32(address, true);
    if (type === "f32") return view.getFloat32(address, true);
    return view.getFloat64(address, true);
  }

  function instanceFilters(options) {
    const raw = options && options.where !== undefined ? options.where : [];
    const types = new Set(["u8", "i8", "bool", "u16", "i16", "u32", "i32", "ptr", "f32", "f64"]);
    if (!Array.isArray(raw) || raw.length > 16) {
      throw new Error("where must be an array of at most 16 field filters");
    }
    return raw.map((filter, index) => {
      if (!filter || typeof filter !== "object" || !("equals" in filter)) {
        throw new Error(`where[${index}] must contain offset, type, and equals`);
      }
      const type = String(filter.type || "");
      if (!types.has(type)) throw new Error(`where[${index}].type is unsupported`);
      return {
        offset: integerOption(filter, "offset", -1, 0, 65535),
        type,
        equals: filter.equals,
      };
    });
  }

  function matchesUnityInstance(view, address, classPointer, options, filters) {
    if (!Number.isInteger(address) || address <= 0 || address + 12 > view.byteLength) return false;
    if (
      options.excludeClassMetadata !== false
      && address >= classPointer
      && address < classPointer + 512
    ) return false;
    if (view.getUint32(address, true) !== classPointer) return false;
    if (options.monitorZero !== false && view.getUint32(address + 4, true) !== 0) return false;
    if (options.validateCachedPointer !== false) {
      const cached = view.getUint32(address + 8, true);
      if (cached && (cached >= view.byteLength || cached % 4 !== 0)) return false;
      if (cached < options.cachedPointerMin || cached >= options.cachedPointerMax) return false;
    }
    try {
      return filters.every(
        (filter) => Object.is(
          valueFromView(view, filter.type, address + filter.offset),
          filter.equals
        )
      );
    } catch (_) {
      return false;
    }
  }

  function normalizedInstanceOptions(options, bytes) {
    const settings = { ...(options || {}) };
    settings.cachedPointerMin = integerOption(settings, "cachedPointerMin", 0, 0, bytes);
    settings.cachedPointerMax = integerOption(
      settings,
      "cachedPointerMax",
      bytes,
      1,
      bytes
    );
    if (settings.cachedPointerMin >= settings.cachedPointerMax) {
      throw new Error("cachedPointerMin must be smaller than cachedPointerMax");
    }
    return settings;
  }

  function findUnityInstances(classPointer, options) {
    const settings = normalizedInstanceOptions(options, dataView().byteLength);
    const scan = scanBounds(settings, 8);
    checkedAddress(classPointer, 4);
    const filters = instanceFilters(settings);
    const results = [];
    for (
      let address = scan.start;
      address + 12 <= scan.end && results.length < scan.limit;
      address += scan.alignment
    ) {
      // Almost every heap word is not an instance of this class. Reject it before
      // calling the full validator; keep all checks for the remaining candidates.
      if (scan.view.getUint32(address, true) !== classPointer) continue;
      if (matchesUnityInstance(scan.view, address, classPointer, settings, filters)) {
        results.push(address);
      }
    }
    return withScanCoverage(results, scan);
  }

  function isUnityInstance(address, classPointer, options) {
    const view = dataView();
    const settings = normalizedInstanceOptions(options, view.byteLength);
    return matchesUnityInstance(
      view,
      address,
      classPointer,
      settings,
      instanceFilters(settings)
    );
  }

  function resolveAddress(spec, context) {
    if (!spec || typeof spec !== "object") throw new Error("Address descriptor is missing");
    const cacheKey = context && context.addresses ? JSON.stringify(spec) : null;
    if (cacheKey !== null && context.addresses.has(cacheKey)) {
      return context.addresses.get(cacheKey);
    }
    let resolved;
    if (spec.kind === "absolute") resolved = spec.address;
    if (spec.kind === "static_fields") {
      const classPointer = readers.ptr(spec.type_info);
      if (!classPointer) throw new Error(`TypeInfo at ${spec.type_info} is null`);
      const profileOffset = runtime.profile && runtime.profile.runtime
        ? runtime.profile.runtime.class_static_fields_offset
        : undefined;
      const offset = spec.class_static_fields_offset ?? profileOffset ?? 92;
      const fieldsPointer = readers.ptr(classPointer + offset);
      if (!fieldsPointer) throw new Error(`Static fields for TypeInfo ${spec.type_info} are null`);
      resolved = fieldsPointer;
    }
    if (spec.kind === "method") {
      resolved = Number(invokeMethod({ ...spec, return: "ptr" }, context)) >>> 0;
      if (!resolved) throw new Error(`Address method at table index ${spec.table_index} returned null`);
      if (spec.expected_type_info) {
        const expectedClass = readers.ptr(spec.expected_type_info);
        if (!expectedClass) throw new Error(`Expected TypeInfo at ${spec.expected_type_info} is null`);
        const actualClass = readers.ptr(resolved);
        if (actualClass !== expectedClass) {
          throw new Error(
            `Method result type mismatch at ${resolved}: expected class ${expectedClass}, got ${actualClass}`
          );
        }
      }
    }
    if (spec.kind === "chain") {
      let current = resolveAddress(spec.base, context);
      for (const step of spec.steps || []) {
        current += step.offset || 0;
        if (step.dereference) {
          current = readers.ptr(current);
          if (!current) throw new Error("Null pointer while resolving address chain");
          if (step.expected_type_info) {
            const expectedClass = readers.ptr(step.expected_type_info);
            if (!expectedClass) {
              throw new Error(`Expected TypeInfo at ${step.expected_type_info} is null`);
            }
            const actualClass = readers.ptr(current);
            if (actualClass !== expectedClass) {
              throw new Error(
                `Object type mismatch at ${current}: expected class ${expectedClass}, got ${actualClass}`
              );
            }
          }
        }
      }
      resolved = current;
    }
    if (!Number.isInteger(resolved) || resolved <= 0) {
      throw new Error(`Unsupported or invalid address kind: ${spec.kind}`);
    }
    if (cacheKey !== null) context.addresses.set(cacheKey, resolved);
    return resolved;
  }

  function tableFunction(index) {
    if (!runtime.table) refreshRuntimeReferences();
    if (!runtime.table || typeof runtime.table.get !== "function") {
      throw new Error("Unity WebAssembly function table has not been captured");
    }
    const fn = runtime.table.get(index);
    if (typeof fn !== "function") throw new Error(`WASM table entry ${index} is not callable`);
    return fn;
  }

  function invokeMethod(source, context) {
    if (!runtime.profile || !runtime.profile.runtime || runtime.profile.runtime.allow_method_calls !== true) {
      throw new Error("WASM method calls are disabled by the Unity profile");
    }
    const args = Array.isArray(source.args) ? [...source.args] : [];
    if (source.this) args.unshift(resolveAddress(source.this, context));
    if (source.append_method_info !== false) args.push(0);
    const value = tableFunction(source.table_index)(...args);
    if (source.return === "bool") return Boolean(value);
    if (source.return === "ptr") return Number(value) >>> 0;
    return value;
  }

  function unityFS() {
    refreshRuntimeReferences();
    const modules = [
      runtime.unityModule,
      runtime.unityInstance && runtime.unityInstance.Module,
      window.Module,
    ];
    for (const module of modules) {
      if (module && module.FS && typeof module.FS.readFile === "function") return module.FS;
    }
    return null;
  }

  function readVirtualFile(path) {
    if (typeof path !== "string" || !path.startsWith("/") || path.split("/").includes("..")) {
      throw new Error("Virtual filesystem path must be absolute and traversal-free");
    }
    const fs = unityFS();
    if (!fs) throw new Error("Unity virtual filesystem is unavailable");
    const value = fs.readFile(path, { encoding: "utf8" });
    if (typeof value === "string") return value;
    return new TextDecoder("utf-8").decode(value);
  }

  function listVirtualFiles(path) {
    const target = path || "/";
    if (typeof target !== "string" || !target.startsWith("/") || target.split("/").includes("..")) {
      throw new Error("Virtual filesystem path must be absolute and traversal-free");
    }
    const fs = unityFS();
    if (!fs || typeof fs.readdir !== "function") {
      throw new Error("Unity virtual filesystem directory listing is unavailable");
    }
    return fs.readdir(target)
      .filter((name) => name !== "." && name !== "..")
      .map((name) => {
        const child = target === "/" ? `/${name}` : `${target.replace(/\/$/, "")}/${name}`;
        let directory = null;
        try {
          const stat = fs.stat(child);
          if (typeof fs.isDir === "function") directory = Boolean(fs.isDir(stat.mode));
        } catch (_) {}
        return { name, path: child, directory };
      });
  }

  function dottedValue(value, path) {
    if (!path) return value;
    let current = value;
    for (const key of path.split(".")) {
      if (dangerousPathParts.has(key)) throw new Error(`Unsafe state path segment: ${key}`);
      if (current == null) return null;
      if (!Object.prototype.hasOwnProperty.call(current, key)) return null;
      current = current[key];
    }
    return current === undefined ? null : current;
  }

  function hasDottedPath(value, path) {
    if (!path) return true;
    let current = value;
    for (const key of path.split(".")) {
      if (dangerousPathParts.has(key) || current == null) return false;
      if (!Object.prototype.hasOwnProperty.call(current, key)) return false;
      current = current[key];
    }
    return true;
  }

  function readSource(source, context) {
    if (source.kind === "constant") return source.value;
    if (source.kind === "method") return invokeMethod(source, context);
    if (source.kind === "fs_json") {
      if (!context.files.has(source.path)) {
        context.files.set(source.path, JSON.parse(readVirtualFile(source.path)));
      }
      return dottedValue(context.files.get(source.path), source.json_path || "");
    }
    const address = resolveAddress(source.address, context);
    if (source.type === "string") {
      return readIl2CppString(readers.ptr(address), source.max_length);
    }
    const reader = readers[source.type];
    if (!reader) throw new Error(`Unsupported memory type: ${source.type}`);
    return reader(address);
  }

  function setDotted(target, path, value) {
    const parts = path.split(".");
    if (!parts.length || parts.some((part) => !part || dangerousPathParts.has(part))) {
      throw new Error(`Unsafe state field path: ${path}`);
    }
    if (reservedTopLevelFields.has(parts[0])) {
      throw new Error(`Reserved state field path: ${path}`);
    }
    let current = target;
    for (let index = 0; index < parts.length - 1; index += 1) {
      const part = parts[index];
      if (
        !Object.prototype.hasOwnProperty.call(current, part)
        || !current[part]
        || typeof current[part] !== "object"
      ) {
        current[part] = Object.create(null);
      }
      current = current[part];
    }
    current[parts[parts.length - 1]] = value;
  }

  function stateValue(state, path) {
    return dottedValue(state, path);
  }

  function mappedValue(mapping, value, fallback) {
    if (mapping && Object.prototype.hasOwnProperty.call(mapping, String(value))) {
      return mapping[String(value)];
    }
    return fallback;
  }

  function predicateResult(predicate, state, invalidFields) {
    if (predicate.all) {
      let unknown = false;
      for (const child of predicate.all) {
        const result = predicateResult(child, state, invalidFields);
        if (result === false) return false;
        if (result === null) unknown = true;
      }
      return unknown ? null : true;
    }
    if (predicate.any) {
      let unknown = false;
      for (const child of predicate.any) {
        const result = predicateResult(child, state, invalidFields);
        if (result === true) return true;
        if (result === null) unknown = true;
      }
      return unknown ? null : false;
    }
    if (predicate.not) {
      const result = predicateResult(predicate.not, state, invalidFields);
      return result === null ? null : !result;
    }
    if (invalidFields.has(predicate.field)) return null;
    if (!hasDottedPath(state, predicate.field)) return null;
    const actual = stateValue(state, predicate.field);
    if (Object.prototype.hasOwnProperty.call(predicate, "equals")) {
      return actual === predicate.equals;
    }
    if (Object.prototype.hasOwnProperty.call(predicate, "not_equals")) {
      return actual !== predicate.not_equals;
    }
    if (Object.prototype.hasOwnProperty.call(predicate, "lt")) {
      return typeof actual === "number" && actual < predicate.lt;
    }
    if (Object.prototype.hasOwnProperty.call(predicate, "lte")) {
      return typeof actual === "number" && actual <= predicate.lte;
    }
    if (Object.prototype.hasOwnProperty.call(predicate, "gt")) {
      return typeof actual === "number" && actual > predicate.gt;
    }
    if (Object.prototype.hasOwnProperty.call(predicate, "gte")) {
      return typeof actual === "number" && actual >= predicate.gte;
    }
    if (Object.prototype.hasOwnProperty.call(predicate, "in")) {
      return predicate.in.includes(actual);
    }
    if (Object.prototype.hasOwnProperty.call(predicate, "not_in")) {
      return !predicate.not_in.includes(actual);
    }
    if (Object.prototype.hasOwnProperty.call(predicate, "is_null")) {
      return (actual === null || actual === undefined) === predicate.is_null;
    }
    if (Object.prototype.hasOwnProperty.call(predicate, "truthy")) {
      return Boolean(actual) === predicate.truthy;
    }
    return false;
  }

  function terminalResult(spec, state, invalidFields) {
    if (Array.isArray(spec.rules)) {
      for (const rule of spec.rules) {
        if (predicateResult(rule.when, state, invalidFields) === true) {
          return {
            isTerminal: true,
            outcome: rule.outcome || null,
            reason: rule.reason || null,
          };
        }
      }
      return null;
    }
    return null;
  }

  function getState() {
    const profile = runtime.profile;
    if (!profile) return null;
    refreshRuntimeReferences();
    const now = performance.now();
    const result = {
      schemaVersion: 2,
      gameId: profile.game_id,
      timestampMs: Date.now(),
      gameTimeMs: Math.max(0, Math.round(now - runtime.configuredAt)),
    };
    const context = { files: new Map(), addresses: new Map() };
    const failures = Object.create(null);
    for (const [path, source] of Object.entries(profile.state.fields || {})) {
      try {
        setDotted(result, path, readSource(source, context));
      } catch (error) {
        setDotted(result, path, null);
        failures[path] = error && error.message ? error.message : String(error);
      }
    }

    const statusSpec = profile.state.status || {};
    const statusRaw = statusSpec.field ? stateValue(result, statusSpec.field) : null;
    result.status = mappedValue(statusSpec.mapping, statusRaw, statusSpec.default || "unknown");
    result.is_actionable = (profile.state.actionable_statuses || []).includes(result.status);
    const invalidFields = new Set(Object.keys(failures));
    if (statusSpec.field && invalidFields.has(statusSpec.field)) {
      invalidFields.add("status");
      invalidFields.add("is_actionable");
    }

    const terminalSpec = profile.state.terminal;
    let terminal = { isTerminal: false, outcome: null, reason: null };
    if (terminalSpec) {
      terminal = terminalResult(terminalSpec, result, invalidFields) || terminal;
    }
    result.terminal = terminal;
    if (profile.runtime && profile.runtime.include_diagnostics) {
      result.raw = {
        unity_bridge: status(),
        field_errors: failures,
      };
    }
    return result;
  }

  function sendMessage(target, method, value) {
    refreshRuntimeReferences();
    const candidates = [
      runtime.unityInstance,
      window.unityInstance,
      window.gameInstance,
      runtime.unityModule,
      window.Module,
    ];
    for (const candidate of candidates) {
      if (candidate && typeof candidate.SendMessage === "function") {
        candidate.SendMessage(target, method, value === undefined ? "" : value);
        return true;
      }
    }
    throw new Error("Unity SendMessage is unavailable");
  }

  async function runAction(action) {
    if (!action) return false;
    if (action.kind === "reload") {
      window.location.reload();
      return true;
    }
    if (action.kind === "send_message") {
      return sendMessage(action.target, action.method, action.value);
    }
    if (action.kind === "method") {
      invokeMethod(action);
      return true;
    }
    if (action.kind === "sequence") {
      for (const child of action.actions || []) await runAction(child);
      return true;
    }
    throw new Error(`Unsupported Unity action kind: ${action.kind}`);
  }

  function status() {
    refreshRuntimeReferences();
    return {
      bridgeVersion: 1,
      capturedInstances: runtime.captures,
      runtimeLocked: runtime.runtimeLocked,
      captureScore: runtime.captureScore,
      hasInstance: Boolean(runtime.instance),
      hasMemory: Boolean(runtime.memory),
      memoryBytes: runtime.memory ? runtime.memory.buffer.byteLength : 0,
      hasTable: Boolean(runtime.table),
      tableLength: runtime.table ? runtime.table.length : 0,
      hasUnityInstance: Boolean(runtime.unityInstance),
      hasSendMessage: [runtime.unityInstance, runtime.unityModule, window.Module]
        .some((item) => item && typeof item.SendMessage === "function"),
      hasFS: Boolean(unityFS()),
      profileGameId: runtime.profile ? runtime.profile.game_id : null,
      staleStaticRoots: [...(runtime.staleRoots || [])],
      recentErrors: [...runtime.errors],
    };
  }

  function validateConfiguredProfile(profile) {
    if (
      !profile
      || profile.schema_version !== 1
      || typeof profile.game_id !== "string"
      || !profile.game_id
      || !profile.state
      || !profile.state.fields
      || typeof profile.state.fields !== "object"
    ) {
      throw new Error("Invalid Unity profile");
    }
    for (const path of Object.keys(profile.state.fields)) {
      const parts = path.split(".");
      if (
        !parts.length
        || parts.some((part) => !part || dangerousPathParts.has(part))
        || reservedTopLevelFields.has(parts[0])
      ) {
        throw new Error(`Unsafe or reserved state field path: ${path}`);
      }
    }
  }

  function gameAdapter() {
    const adapter = window.unityAdapter;
    return adapter && typeof adapter === "object" ? adapter : null;
  }

  function adaptedState() {
    const state = getState();
    const adapter = gameAdapter();
    if (!state || !adapter || typeof adapter.transformState !== "function") return state;
    try {
      return adapter.transformState(state, { bridge, profile: runtime.profile }) || state;
    } catch (error) {
      rememberError(error);
      return state;
    }
  }

  // A profile pins absolute addresses into a build that is not itself versioned, so a
  // rebuilt game leaves `type_info` pointing at whatever now lives there. Both existing
  // checks only reject null, and a stale address usually is not null: it reads as
  // plausible garbage forever. A class descriptor stores its own address at
  // +selfOffset, which is the same invariant findUnityClasses uses to recognise one,
  // so checking it once at configure time separates "this profile still fits this
  // build" from "these numbers are meaningless". Reported, not thrown: a false
  // negative here must not take down a game whose reads are fine.
  function auditStaticRoots(profile) {
    const layout = classLayout((profile && profile.runtime) || {});
    const findings = [];
    const walk = (node, path) => {
      if (Array.isArray(node)) return node.forEach((item, i) => walk(item, `${path}[${i}]`));
      if (!node || typeof node !== "object") return;
      if (node.kind === "static_fields" && Number.isInteger(node.type_info)) {
        let detail = null;
        try {
          const classPointer = readers.ptr(node.type_info);
          if (!classPointer) detail = "TypeInfo slot is null";
          else if (readers.ptr(classPointer + layout.selfOffset) !== classPointer) {
            detail = `class at ${classPointer} does not self-reference at +${layout.selfOffset}`;
          }
        } catch (error) {
          detail = String((error && error.message) || error);
        }
        if (detail) findings.push({ path, type_info: node.type_info, detail });
      }
      Object.entries(node).forEach(([key, child]) => walk(child, `${path}.${key}`));
    };
    walk((profile && profile.state && profile.state.fields) || {}, "state.fields");
    return findings;
  }

  const bridge = {
    bridgeVersion: 1,
    configure(profile) {
      validateConfiguredProfile(profile);
      runtime.profile = profile;
      runtime.configuredAt = performance.now();
      runtime.staleRoots = auditStaticRoots(profile);
      window.gameAPI = {
        schemaVersion: 2,
        capabilities: profile.capabilities || {},
        async init(options) {
          const adapter = gameAdapter();
          return adapter && typeof adapter.init === "function"
            ? adapter.init({ bridge, profile, options: options || {} })
            : true;
        },
        getState: adaptedState,
        async reset(options) {
          const adapter = gameAdapter();
          return adapter && typeof adapter.reset === "function"
            ? adapter.reset({ bridge, profile, options: options || {} })
            : runAction(profile.actions && profile.actions.reset);
        },
      };
      return status();
    },
    status,
    getState,
    resolveAddress,
    read(type, address) {
      if (type === "string") return readIl2CppString(address);
      const reader = readers[type];
      if (!reader) throw new Error(`Unsupported memory type: ${type}`);
      return reader(address);
    },
    readIl2CppString,
    describeUnityClass,
    findUnityClasses,
    findUnityInstances,
    isUnityInstance,
    readVirtualFile,
    listVirtualFiles,
    tableFunction,
    // Deliberately not behind allow_method_calls. That flag governs whether a
    // *profile* -- a declarative data file -- may resolve a field through a WASM
    // call; this is the primitive the game's own hand-written adapter uses, and that
    // adapter already runs in the page and could reach runtime.table directly.
    call(tableIndex, args) { return tableFunction(tableIndex)(...(args || [])); },
    sendMessage,
    captureUnityInstance,
  };

  Object.defineProperty(window, "__unityBridge", {
    configurable: false,
    enumerable: false,
    writable: false,
    value: bridge,
  });
  refreshRuntimeReferences();
})();
