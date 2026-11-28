/* Bright Lancer 1.2 / Unity 2019.4.16f1.
 * IL2CPP metadata and native callbacks are engine_hook provenance.
 * Old Emscripten uses signature-relative RVAs: ii+1170, iii+3348,
 * vii+50683, viii+59122. The indices below are global WASM table indices.
 * All memory access and native calls go through the repository Unity bridge.
 */
(() => {
  'use strict';
  const GAME_ID = '491_bright-lancer';
  const TASKS = { '491_01': [2, 2], '491_02': [3, 4],
    '491_03': [4, 15], '491_04': [5, 40] };
  const taskId = new URLSearchParams(location.search).get('task') || '491_01';
  const [requestedScene, expectedEnemies] = TASKS[taskId] || [0, 0];
  const FN = { keyboard:56681, loadScene:55574, sceneInto:55576,
    sceneIndex:9236, sceneName:9235, findPlayer:2137, healthComponent:3998, goTransform:9145,
    gameObject:9142, transform:9141, positionInto:63343,
    bodyPositionInto:61527, active:9147, objectName:9185 };
  const SLOT = { gameManager:3077524, startManager:3077608,
    findPlayerMethod:3078356, healthMethod:3077976 };
  const now = () => (window.__realPerfNow || performance.now.bind(performance))();
  const delay = ms => new Promise(resolve =>
    (window.__realSetTimeout || setTimeout)(resolve, ms));
  let bridge, scratch=0, player=0, playerHealth=0, manager=0, managerClass=0;
  let initialized=false, resetting=false, recovering=false, success=false;
  let startupPromise=null, startupError=null, startedAt=0, bestProgress=0;
  let enemies=[], current=null, lastSample=0;
  const read = (type, address) => bridge.read(type,address);
  const ptr = address => read('ptr',address);
  const call = (name,args) => bridge.call(FN[name],args);
  const live = object => !!object && ptr(object+8)!==0;
  const singleton = slot => { const klass=ptr(slot);
    const statics=klass && ptr(klass+92); return statics ? ptr(statics) : 0; };
  function scene() {
    call('sceneInto',[scratch,0]);
    const handle=read('i32',scratch);
    return {index:call('sceneIndex',[handle,0]),
      name:bridge.readIl2CppString(call('sceneName',[handle,0]))};
  }
  function findPlayer() { return call('findPlayer',[ptr(SLOT.findPlayerMethod)]); }
  function health(go) { return call('healthComponent',[call('goTransform',[go,0]),ptr(SLOT.healthMethod)]); }
  function position(component,body=false) {
    if(body) call('bodyPositionInto',[ptr(component+84),scratch,0]);
    else call('positionInto',[call('transform',[component,0]),scratch,0]);
    const p={x:read('f32',scratch),y:read('f32',scratch+4)};
    if(!Number.isFinite(p.x)||!Number.isFinite(p.y)) throw Error('Invalid native position');
    return p;
  }
  const distance = (a,b) => Math.hypot(a.x-b.x,a.y-b.y);
  function listContents(m) {
    const list=ptr(m+24), count=read('i32',list+12), array=ptr(list+8);
    if(count!==expectedEnemies||!array) throw Error('Unexpected enemy roster');
    return Array.from({length:count},(_,i)=>ptr(array+16+4*i));
  }
  function resolveManager() {
    if(!managerClass) {
      const classes=bridge.findUnityClasses('EnemyManager',
        {start:1048576,end:33554432,limit:4});
      if(classes.length!==1) throw Error('EnemyManager class unresolved');
      managerClass=classes[0].address;
    }
    const matches=[], bytes=bridge.status().memoryBytes;
    // Explicit bounded windows; this runs only on a level start/retry.
    if(bytes>536870912) throw Error('Unexpected Unity heap size');
    for(let start=1048576;start<bytes;start+=33554432) {
      const candidates=bridge.findUnityInstances(managerClass,{start,
        end:Math.min(start+33554432,bytes),limit:16,
        where:[{offset:28,type:'i32',equals:expectedEnemies}]});
      for(const p of candidates) {
        if(!live(p)||ptr(p+24)<1048576||!live(ptr(p+16))) continue;
        if(read('i32',ptr(p+24)+12)!==expectedEnemies) continue;
        if(bridge.readIl2CppString(call('objectName',[p,0]))==='Enemies') matches.push(p);
      }
    }
    if(matches.length!==1) throw Error('Expected one live enemy manager, got '+matches.length);
    return matches[0];
  }
  async function waitFor(test,label) {
    const deadline=now()+45000;
    while(now()<deadline) {
      const value=test(); if(value) return value;
      await delay(30);
    }
    throw Error('Timed out waiting for '+label);
  }
  function controllablePlayer(index) {
    if(scene().index!==index) return 0;
    const p=findPlayer();
    if(!live(p)||!read('bool',p+72)||read('bool',p+80)||!ptr(p+84)) return 0;
    const hp=health(call('gameObject',[p,0]));
    return live(hp)&&read('i32',hp+16)>0&&!read('bool',hp+28) ? p : 0;
  }
  async function attachLevel() {
    player=await waitFor(()=>controllablePlayer(requestedScene),'controllable requested level');
    playerHealth=health(call('gameObject',[player,0]));
    manager=resolveManager();
    const origin=position(player,true);
    enemies=listContents(manager).map((go,index)=>{
      const hp=health(go);
      if(!live(hp)) throw Error('Missing combatant Health');
      const maximum=read('i32',hp+12), initialPosition=position(hp);
      if(maximum<1||maximum>100) throw Error('Invalid combatant health');
      const initialHealth=read('i32',hp+16)>0?read('i32',hp+16):maximum;
      return {index,go,hp,maximum,initialHealth,initialPosition,
        initialDistance:Math.max(1,distance(origin,initialPosition)),
        approach:0,damage:0,seenAlive:false,dead:false};
    });
    if(call('active',[ptr(manager+16),0])) throw Error('Arena already cleared at start');
    initialized=true; recovering=false;
    sample();
    document.querySelector('canvas')?.focus();
  }
  function sample() {
    if(!initialized||resetting||success) return;
    const level=scene();
    if(level.index!==requestedScene) return;
    if(!live(player)||!live(manager)) {
      if(!recovering) {
        recovering=true; initialized=false;
        attachLevel().catch(error=>{startupError=String(error.message||error);});
      }
      return;
    }
    const p=position(player,true);
    const remaining=read('i32',manager+28);
    if(remaining<0||remaining>expectedEnemies) throw Error('Invalid native enemy count');
    const defeated=expectedEnemies-remaining;
    let approaches=0,damage=0;
    const states=enemies.map(e=>{
      const exists=live(e.hp), active=live(e.go)&&Boolean(call('active',[e.go,0]));
      const hp=exists?read('i32',e.hp+16):0;
      // Retain an observed death even when activation and damage happened
      // between alive samples. The latch belongs to this roster, not a retry.
      e.dead=e.dead||(exists?read('bool',e.hp+28):e.seenAlive&&defeated>0);
      const dead=e.dead;
      if(exists&&active&&hp>0) e.seenAlive=true;
      // Dormant enemies have currentHealth=0 until Health.Start. That zero is
      // never damage. Health.isDead and destroyed, previously live objects are.
      if(dead) e.damage=1;
      else if(exists&&active&&hp>0) e.damage=Math.max(e.damage,1-hp/e.initialHealth);
      e.approach=Math.max(e.approach,
        Math.min(1,Math.max(0,1-distance(p,e.initialPosition)/e.initialDistance)));
      if(dead) e.approach=1;
      approaches+=e.approach; damage+=e.damage;
      return {index:e.index,initial_position:e.initialPosition,
        position:exists?position(e.hp):e.initialPosition,
        health:hp,max_health:e.maximum,active,dead:Boolean(dead)};
    });
    const exitActive=Boolean(call('active',[ptr(manager+16),0]));
    // No partial score can satisfy the task before the native clear trigger.
    const partial=(.20*approaches+.60*damage+.19*defeated)/expectedEnemies;
    success=remaining===0&&exitActive;
    bestProgress=Math.max(bestProgress,success?1:Math.min(.99,partial));
    const hp=read('i32',playerHealth+16), dead=read('bool',playerHealth+28);
    current={level:level.index-1,scene_name:level.name,player:{position:p,
      health:hp,max_health:read('i32',playerHealth+12),dead,
      can_move:read('bool',player+72)},enemies:states,
      enemies_remaining:remaining,enemies_defeated:defeated,exit_active:exitActive};
  }
  async function init() {
    if(startupPromise) return startupPromise;
    startupPromise=(async()=>{
      if(!TASKS[taskId]) throw Error('Unknown task '+taskId);
      await waitFor(()=>window.__unityBridge?.status().hasTable&&
        window.__unityBridge.status().profileGameId===GAME_ID&&
        typeof window.unityInstance?.Module?.asm?._malloc==='function','Unity runtime');
      bridge=window.__unityBridge;
      const start=await waitFor(()=>{
        const s=singleton(SLOT.startManager);
        return live(s)&&ptr(s+28)?s:0;
      },'native keyboard selection');
      scratch=window.unityInstance.Module._malloc(32);
      if(scene().index!==1) throw Error('Expected native main menu');
      if(window.__resetRandomSeed) window.__resetRandomSeed();
      call('keyboard',[start,0]);
      await waitFor(()=>controllablePlayer(2),'initial native gameplay');
      if(requestedScene!==2) call('loadScene',[requestedScene,0]);
      await attachLevel();
      startedAt=performance.now();
      return true;
    })().catch(error=>{startupError=String(error.message||error);return false;});
    return startupPromise;
  }
  window.unityAdapter={
    init,
    reset() {
      resetting=true; initialized=false;
      if(window.__resetRandomSeed) window.__resetRandomSeed();
      (window.__realSetTimeout||setTimeout)(()=>location.reload(),0);
      return true;
    },
    transformState(state) {
      const ready=initialized&&!resetting&&!!current&&!startupError;
      const dead=ready&&current.player.dead;
      state.schemaVersion='1.0';
      state.gameTimeMs=startedAt?Math.max(0,performance.now()-startedAt):0;
      state.status=startupError?'error':!ready?'loading':success?'success':dead?'fail':'playing';
      state.is_actionable=Boolean(ready);
      state.terminal={isTerminal:Boolean(success||dead),
        outcome:success?'success':dead?'fail':null};
      state.game_state={...(state.game_state||{}),...(current||{
        level:requestedScene-1,enemies_remaining:expectedEnemies,
        enemies_defeated:0,exit_active:false,player:{health:0,dead:false}}),
        task:{id:taskId,level:requestedScene-1}};
      state.metrics={combat_clear_progress:bestProgress,
        enemies_remaining:current?.enemies_remaining??expectedEnemies,
        enemies_defeated:current?.enemies_defeated??0,health:current?.player.health??0};
      state.provenance={
        'game_state.using_gamepad':'engine_hook','game_state.carried_health':'engine_hook',
        'game_state.level':'engine_hook','game_state.scene_name':'engine_hook',
        'game_state.player':'engine_hook','game_state.enemies':'engine_hook',
        'game_state.enemies_remaining':'engine_hook','game_state.enemies_defeated':'engine_hook',
        'game_state.exit_active':'engine_hook','metrics':'engine_hook'};
      if(startupError) (state.raw||={}).startup_error=startupError;
      return state;
    }
  };
  function tick() {
    try {
      if(performance.now()-lastSample>=40) {sample();lastSample=performance.now();}
    } catch(error) {startupError=String(error.message||error);}
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
  init();
})();
