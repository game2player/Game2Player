
// Minimal external adapter bridge for the captured Build 40 permutation.
(function(){
 var slots={"startMap":{get:function(){return suf},set:function(f){suf=f}},
"runWave":{get:function(){return Cvf},set:function(f){Cvf=f}},
"schedule":{get:function(){return eKg},set:function(f){eKg=f}},
"addEntity":{get:function(){return dGf},set:function(f){dGf=f}},
"enemyDeath":{get:function(){return BLf},set:function(f){BLf=f}},
"tutorialMove":{get:function(){return XNf},set:function(f){XNf=f}},
"itemAdd":{get:function(){return INf},set:function(f){INf=f}},
"logicUpdate":{get:function(){return Dvf},set:function(f){Dvf=f}},
"resetGame":{get:function(){return Bvf},set:function(f){Bvf=f}}};
 $wnd.__mindustryAttach({
  hook:function(name,wrap){var s=slots[name];if(!s)throw Error('Unknown hook '+name);s.set(wrap(s.get()))},
  ready:function(){return !!(lsf&&Psf&&Usf&&Ssf&&Ssf.k&&Ssf.t&&jc&&jc.a)},
  state:function(){return Psf}, world:function(){return Usf}, player:function(){return Dsf},
  tutorial:function(){return lsf.k}, stages:function(){return IPf()},
  enemyGroup:function(){return rsf},
  loading:function(){return !!Ssf.t.a.Sb},
  mode:function(){return Psf.i.p},
  deltaMs:function(){return jc.d*1000},
  nativeStart:function(name,mode){
   CNf();eNf();wvf();
   var map=IXf(Usf.c,name);if(!map)throw Error('Unknown map '+name);
   Psf.i=mode==='freebuild'?zNf:BNf;Psf.a=cNf;
   ovf(Psf,tvf);YNf(lsf.k);muf(lsf,map);
  },
  seed:function(n){crh();r$();D5(q$,n);D5(arh,n+1);D5(_qh,n+2)},
  pause:function(on){wvf();if(Psf.j!==tvf)ovf(Psf,on?uvf:vvf)}
 });
})();

