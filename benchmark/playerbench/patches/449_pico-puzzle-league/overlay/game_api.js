(function(){
 'use strict';
 window.pico8_gpio=Array(128).fill(0);
 const id=new URLSearchParams(location.search).get('task')||'449_01';
 const targets={'449_01':3,'449_02':12,'449_03':30};
 let ready=false,pending=null,error=null;
 const pin=i=>Number(window.pico8_gpio[i])||0;
 function waitFor(test){return new Promise((resolve,reject)=>{const end=performance.now()+45000;function tick(){if(test())resolve();else if(performance.now()>end)reject(new Error('Native cartridge startup timed out'));else setTimeout(tick,20);}tick();});}
 async function boot(){
  await waitFor(()=>typeof window.p8_run_cart==='function');
  if(!window.p8_is_running)window.p8_run_cart();
  await waitFor(()=>pin(0)===77);
  const seed=typeof window.__getRandomSeed==='function'?Number(window.__getRandomSeed())>>>0:42;
  window.pico8_gpio[120]=seed&255;window.pico8_gpio[121]=(seed>>>8)&255;window.pico8_gpio[127]=1;
  await waitFor(()=>pin(1)===1);
  ready=true;document.getElementById('canvas').focus();return getState();
 }
 function init(){if(!pending)pending=boot().catch(e=>{error=String(e);throw e;});return pending;}
 function getState(){
  const cleared=pin(2)+256*pin(3),phase=pin(1),won=ready&&cleared>=(targets[id]||3),failed=ready&&phase===2&&!won;
  return {schemaVersion:'1.0',gameId:'449_pico-puzzle-league',timestampMs:Date.now(),gameTimeMs:(pin(4)+256*pin(5))*1000/30,
   status:error?'error':won?'success':failed?'fail':ready?'playing':'loading',is_actionable:ready&&!error,
   terminal:{isTerminal:won||failed,outcome:won?'success':failed?'fail':null},
   game_state:{task_id:id,task_start_matches:ready,blocks_cleared:cleared,swaps:pin(9)+256*pin(10),native_phase:phase,
    progress_provenance:{blocks_cleared:'engine_hook',swaps:'engine_hook',native_phase:'engine_hook'}},metrics:{blocks_cleared:cleared,start_error:error}};
 }
 window.gameAPI={init,getState,reset:function(){location.reload();}};
 window.addEventListener('DOMContentLoaded',()=>{init().catch(()=>{});});
})();
