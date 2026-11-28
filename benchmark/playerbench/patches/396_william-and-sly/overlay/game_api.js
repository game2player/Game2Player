/* William and Sly: original Flash 10 / AS3 game, running in local Ruffle.
 * Main.gwRead exports direct native values (internal provenance). Its frame hook
 * publishes after GameLoop's real pickups, activations and combat. The SWF patch
 * only adds this bridge, guards a null offline medal list and omits telemetry.
 * All verifier arithmetic lives here, separately from __policyObservation.
 */
(function () {
  "use strict";
  const GAME_ID = "396_william-and-sly";
  const TASKS = {
    "396_01": {field:"mushrooms_collected", target:15, native:"mushrooms", nativeTarget:15},
    "396_02": {field:"mushrooms_collected", target:60, native:"mushrooms", nativeTarget:60},
    "396_03": {field:"runestone_progress", target:3, native:"runes", nativeTarget:4},
    "396_04": {field:"chest_progress", target:4, native:"chests", nativeTarget:4},
    "396_05": {field:"bridge_progress", target:3, native:"switches", nativeTarget:3},
    "396_06": {field:"darkling_progress", target:5, native:"darklingsKilled", nativeTarget:5}
  };
  // Authored 60px collision tiles from the original Main.LoadMap("Main").
  // Native gwRead checks the LIVE tile value before including a target. These
  // positions neither insert objects nor alter any gameplay or saved progress.
  const POINTS = [[21,132,1],[21,189,4],[14,698,6],[14,129,9],[40,248,10],[20,164,13],[14,202,16],[21,658,16],[20,697,18],[20,97,19],[21,46,20],[21,130,22],[23,380,35],[20,561,35],[21,412,36],[14,596,36],[23,338,37],[23,310,39],[14,412,39],[20,193,40],[21,874,40],[20,232,41],[23,568,42],[20,334,44],[14,974,44],[20,846,46],[23,265,48],[14,877,49],[23,605,50],[20,828,50],[14,284,51],[20,887,51],[21,771,52],[31,610,53],[21,549,54],[14,802,54],[25,341,55],[20,753,56],[20,410,59],[20,349,62],[14,344,63],[20,647,69],[20,452,72],[14,544,73],[20,522,80],[14,462,85],[23,466,111]];
  const taskId = new URLSearchParams(location.search).get("task") || "396_01";
  const task = TASKS[taskId];
  const realNow = window.__realDateNow || Date.now.bind(Date);
  const realTimeout = window.__realSetTimeout || window.setTimeout.bind(window);
  let player = null, raw = null, bootPromise = null, error = null;
  let ready = false, acceptingFrames = false, episode = 0, clockOrigin = 0;
  let progress = {}, stages = {};

  const finite = v => typeof v === "number" && Number.isFinite(v);
  const number = v => finite(v) ? v : 0;
  const clamp = v => Math.max(0, Math.min(1, v));
  const distance = (a,b) => Math.hypot(a.x-b.x,a.y-b.y);
  function call(name, ...args) { return player.ruffle().callExternalInterface(name, ...args); }
  function read() { try { return player ? call("gwRead") : null; } catch (_) { return null; } }
  function targets(v, types) {
    return (Array.isArray(v.targets) ? v.targets : []).filter(t => types.includes(t.type));
  }
  function nearest(point, list) {
    return list.length ? Math.min(...list.map(t => distance(point,t))) : null;
  }
  function stage(name, count, v, groups) {
    if (!stages[name] || stages[name].count !== count) {
      stages[name] = {count, baselines:groups.map(g => nearest(v,g))};
    }
    return stages[name];
  }
  function closure(v, list, baseline) {
    const current = nearest(v,list);
    // Zero initial distance already means arrival; the native event alone
    // supplies the next whole unit. Missing targets never imply completion.
    return current !== null && baseline !== null && baseline > 0 ? clamp(1-current/baseline) : 0;
  }
  function remember(field, candidate) {
    if (finite(candidate)) progress[field] = Math.max(progress[field] || 0,candidate);
  }
  function consume(v) {
    if (!v || v.hookVersion !== 1) return;
    raw = v;
    if (!acceptingFrames || !v.mode || !finite(v.x) || !finite(v.y)) return;
    const runes = targets(v,[14]);
    const keys = targets(v,[20]);
    const chests = targets(v,[21,23]);
    const switches = targets(v,[25,31,40]);
    const darklings = Array.isArray(v.darklings) ? v.darklings : [];
    const fairyPart = Math.min(5,Math.max(0,number(v.fairyflies)))/5;

    remember("mushrooms_collected",number(v.mushrooms));

    const activated = Math.max(0,number(v.runes)-1);
    const r = stage("runes",activated,v,[runes]);
    remember("runestone_progress",activated + 0.4*fairyPart +
      (fairyPart === 1 ? 0.59*closure(v,runes,r.baselines[0]) : 0));

    const opened = number(v.chests);
    const c = stage("chests",opened,v,[keys,chests]);
    const chestPart = v.keys > 0 ? 0.4+0.59*closure(v,chests,c.baselines[1]) :
      0.39*closure(v,keys,c.baselines[0]);
    remember("chest_progress",opened+chestPart);

    const switched = number(v.switches);
    const b = stage("bridges",switched,v,[switches]);
    remember("bridge_progress",switched+0.99*closure(v,switches,b.baselines[0]));

    const killed = number(v.darklingsKilled);
    const d = stage("darklings",killed,v,[runes,darklings]);
    const darkPart = v.charge > 0 ? 0.5+0.49*closure(v,darklings,d.baselines[1]) :
      0.4*fairyPart+(fairyPart === 1 ? 0.09*closure(v,runes,d.baselines[0]) : 0);
    remember("darkling_progress",killed+darkPart);
  }
  // Never call back into Ruffle while its ExternalInterface callback is active.
  window.gwNativeState = v => consume(v);

  async function until(predicate, label, timeoutMs=25000) {
    const deadline = realNow()+timeoutMs;
    while (realNow()<deadline) {
      if (predicate()) return;
      await new Promise(resolve => realTimeout(resolve,25));
    }
    throw new Error("Timed out waiting for "+label);
  }
  function focusGame() {
    const target = player?.shadowRoot?.querySelector("#container") || player;
    if (target) { target.tabIndex=0; target.focus({preventScroll:true}); }
  }
  async function startFresh() {
    ready=acceptingFrames=false;
    raw=error=null;
    stages={};
    progress={mushrooms_collected:0,runestone_progress:0,chest_progress:0,bridge_progress:0,darkling_progress:0};
    episode++;
    clockOrigin=0;
    if (window.__resumeGame) window.__resumeGame();
    if (window.__resetRandomSeed) window.__resetRandomSeed();
    const loading=document.getElementById("loading");
    loading.hidden=false;
    loading.textContent="Loading William and Sly…";
    try {
      if (!task) throw new Error("Unknown task: "+taskId);
      // A fresh player avoids Ruffle retaining keyboard focus for an old movie.
      document.getElementById("stage").replaceChildren();
      player=window.RufflePlayer.newest().createPlayer();
      player.id="game-player";
      document.getElementById("stage").appendChild(player);
      window.__gwBootStage="loading swf";
      await player.ruffle().load({url:"game.swf",allowScriptAccess:true,autoplay:"on",
        unmuteOverlay:"hidden",splashScreen:false});
      window.__gwBootStage="waiting swf";
      await until(() => { const v=read(); return v?.framesLoaded===v?.framesTotal && v?.framesTotal===7; },"complete original SWF");
      // Invoke the original preloader's named startMovie(), then let the native
      // sponsorship timeline construct frame7 and call Startup in normal order.
      window.__gwBootStage="boot";
      if (call("gwBoot")!==true) throw new Error("Native boot refused");
      window.__gwBootStage="waiting native";
      await until(() => read()?.ready===true,"native initialization");
      window.__gwBootStage="start";
      if (call("gwStart",POINTS)!==true) throw new Error("Native new game refused");
      window.__gwBootStage="waiting dialogue";
      await until(() => {
        const v=read();
        return v?.mode===true && v.williamAnimation==="Talk" && v.phrase===0 &&
          v.cineRight===0 && v.movementEnabled===false && v.faderFrame===48 &&
          v.mushrooms===0 && v.runes===1 && v.chests===0 && v.switches===0 &&
          v.darklingsKilled===0 && v.fairyflies===0 && v.keys===0 && v.charge<0 &&
          v.x>3100 && v.x<3250 && v.y>800 && v.y<850;
      },"clean opening conversation");
      const initial=read();
      clockOrigin=number(initial.ticks);
      acceptingFrames=true;
      consume(initial);
      focusGame();
      ready=true;
      loading.hidden=true;
      return getState();
    } catch (failure) {
      error=String(failure);
      loading.textContent=error;
      throw failure;
    }
  }
  function init() { if (!bootPromise) bootPromise=startFresh(); return bootPromise; }
  async function reset() {
    if (bootPromise) await bootPromise.catch(() => {});
    bootPromise=startFresh();
    return bootPromise;
  }
  function getState() {
    consume(read());
    // Native completion is required as well as the metric: prerequisite fractions
    // are strictly below one whole event and cannot satisfy an objective early.
    const complete=!!task && number(raw?.[task.native])>=task.nativeTarget && progress[task.field]>=task.target;
    const state={
      schemaVersion:"1.0.0",gameId:GAME_ID,timestampMs:Date.now(),
      gameTimeMs:Math.max(0,number(raw?.ticks)-clockOrigin)*1000/30,
      status:error ? "error" : !ready ? "loading" : complete ? "won" :
        !raw?.mode ? "menu" : raw?.movementEnabled ? "playing" : "dialogue",
      is_actionable:ready && !error && raw?.mode===true,
      terminal:{isTerminal:complete,outcome:complete ? "success" : null},
      game_state:{task_id:taskId,episode,task_complete:complete,
        player:{x:number(raw?.x),y:number(raw?.y),speed:number(raw?.speed),fall:number(raw?.fall),
          grounded:raw?.grounded===true,wings:raw?.wings===true},
        movement_enabled:raw?.movementEnabled===true,dialogue_phrase:number(raw?.phrase),
        mushrooms:number(raw?.mushrooms),runestones_active:number(raw?.runes),
        chests_opened:number(raw?.chests),bridge_switches:number(raw?.switches),
        darklings_banished:number(raw?.darklingsKilled),fairyflies:number(raw?.fairyflies),
        keys:number(raw?.keys),white_magic_charge:Math.max(0,number(raw?.charge)),
        status_panel_open:raw?.hudVisible===true,map_available:raw?.mapAvailable===true,
        boss_mode:raw?.bossMode===true},
      metrics:{...progress},
      provenance:{gameTimeMs:"engine_hook","game_state.player":"internal",
        "game_state.movement_enabled":"internal","game_state.dialogue_phrase":"internal",
        "game_state.mushrooms":"internal","game_state.runestones_active":"internal",
        "game_state.chests_opened":"internal","game_state.bridge_switches":"internal",
        "game_state.darklings_banished":"internal","game_state.fairyflies":"internal",
        "game_state.keys":"internal","game_state.white_magic_charge":"internal",
        "game_state.status_panel_open":"internal","game_state.map_available":"internal",
        "game_state.boss_mode":"internal","game_state.task_complete":"internal",
        "metrics.mushrooms_collected":"internal","metrics.runestone_progress":"internal",
        "metrics.chest_progress":"internal","metrics.bridge_progress":"internal",
        "metrics.darkling_progress":"internal"}
    };
    return state;
  }
  window.gameAPI={init,reset,getState};
  // Independent observation channel. Never reads gwRead, verifier targets,
  // progress, saves, or gameAPI. The read-only AS3 gwPolicyScene callback uses
  // the current native display list/bitmap alpha to reject covered content.
  // All coordinates and entity values have internal provenance. Visibility
  // is a native render-data gate, not a screenshot/pixel estimate of state.
  //
  // revealed_terrain is intentionally NOT PROVIDED. The game has no discovery
  // flag, and the same foreground art covers solid and passable collision cells.
  // Even positive-only collider outlines can disclose secret openings through
  // gaps in the output. The collision map is not a legitimate policy channel.
  // Objects require a recognizable current icon; opened chests/on switches may
  // remain, while removed pickups disappear. Bounds describe visible icon pixels.
  // Fairy ids refer only to already spawned, free, visible particles. Darklings
  // require an emerged body; buried mud/eye peeks are intentionally omitted.
  function getPolicyState() {
    const empty = () => ({world_view:null, revealed_objects:null,
      visible_free_fairyflies:null, revealed_darkling_status:null});
    if (!ready || !player) return empty();
    let scene;
    try { scene = call("gwPolicyScene"); } catch (_) { return empty(); }
    if (!scene || !scene.world_view) return empty();
    const native = value => ({...value, provenance:"internal"});
    return {
      world_view:native(scene.world_view),
      revealed_objects:scene.objects.map(native),
      visible_free_fairyflies:scene.flies.map(native),
      revealed_darkling_status:scene.darklings.map(native)
    };
  }
  window.__policyObservation = {getState:getPolicyState};
  if (document.readyState==="loading") document.addEventListener("DOMContentLoaded",()=>init().catch(()=>{}),{once:true});
  else init().catch(()=>{});
})();
