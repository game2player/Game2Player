
if(reflexiveAPI == undefined)
{
   reflexiveAPI = new Object();
   reflexiveAPI.submitScore = function(v)
   {
      return v;
   };
}
function gwGetState()
{
   var g = _root.__gwGame;
   var live = 0;
   var d = 0;
   var e = 0;
   var px = -1;
   var py = -1;
   var ground = 0;
   var inv = 0;
   var p = 0;
   if(_root.__gwLive == true)
   {
      live = 1;
   }
   if(g != undefined && g.MAN != undefined)
   {
      if(g.dead == true)
      {
         d = 1;
      }
      if(g.eolSwitch == true)
      {
         e = 1;
      }
      px = int(g.MAN._x);
      py = int(g.MAN._y);
      if(g.jumpActive < 2)
      {
         ground = 1;
      }
      inv = g.inversion;
      if(g.gamePaused == true)
      {
         p = 1;
      }
   }
   return _root._currentframe + "|" + _root.LevelNo + "|" + _root.playTime + "|" + d + "|" + e + "|" + px + "|" + py + "|" + ground + "|" + inv + "|" + live + "|" + p + "|" + _root.__gwLevelId;
}
function gwStartLevel(n)
{
   var v = n;
   if(typeof v != "number" && typeof v != "string")
   {
      v = v[0];
   }
   _root.editorActive = false;
   _root.LevelNo = Number(v);
   _root.playTime = 0;
   _root.gotoAndStop("BLANK");
   return _root.LevelNo + "";
}
flash.external.ExternalInterface.addCallback("gwGetState",null,gwGetState);
flash.external.ExternalInterface.addCallback("gwStartLevel",null,gwStartLevel);
var gwUrl = _root._url;
var gwIdx = gwUrl.indexOf("level=");
if(gwIdx >= 0)
{
   var gwLvl = int(Number(gwUrl.substring(gwIdx + 6)));
   if(gwLvl >= 0 && gwLvl < 19)
   {
      _root.LevelNo = gwLvl;
      _root.playTime = 0;
      _root.gotoAndStop("BLANK");
   }
}
