// Review source for the compiled bridge represented by abc-insert.bin.
// These declarations/methods were added to Main; no original game routines.
      private var gwPoints:Array = [];
      private var gwTicks:uint = 0;
      private var gwPolicyIds:Dictionary = new Dictionary(true);
      private var gwPolicyNextId:uint = 0;
      private var gwPolicyCover:BitmapData;

      public function gwBoot() : Boolean
      {
         var i:int = 0;
         while(i < numChildren)
         {
            var child:Object = getChildAt(i);
            if("startMovie" in child)
            {
               child.startMovie();
               return true;
            }
            i++;
         }
         return SharedObj != null;
      }
      
      public function gwStart(points:Array) : Boolean
      {
         if(SharedObj == null)
         {
            return false;
         }
         gwPoints = points;
         NewGameHelper();
         gwTicks = 0;
         return true;
      }
      
      public function gwRead() : Object
      {
         var targets:Array = [];
         var darklings:Array = [];
         if(EnableGameMode && SharedObj != null)
         {
            var i:int = 0;
            while(i < gwPoints.length)
            {
               var p:Array = gwPoints[i];
               if(Number(TileMap.HitTest(int(p[1]),int(p[2]))) == Number(p[0]))
               {
                  targets.push({
                     "type":p[0],
                     "x":int(p[1]) * 60 + 30,
                     "y":int(p[2]) * 60 + 30,
                     "id":int(p[1]) + "," + int(p[2])
                  });
               }
               i++;
            }
            i = 0;
            while(i < VerticalVec.length)
            {
               var v:Vertical = VerticalVec[i];
               if(v.Type == "Dark")
               {
                  darklings.push({
                     "x":v.x,
                     "y":v.y
                  });
               }
               i++;
            }
         }
         return {
            "hookVersion":1,
            "framesLoaded":framesLoaded,
            "framesTotal":totalFrames,
            "frame":currentFrame,
            "ready":SharedObj != null,
            "ticks":gwTicks,
            "mode":EnableGameMode,
            "movementEnabled":EnableKeys,
            "cineRight":CineRight,
            "cineLeft":CineLeft,
            "phrase":WilliamPhrase,
            "williamAnimation":(Cam == null ? "" : Cam.William.Ani),
            "mushrooms":Mushrooms,
            "runes":RunesActive,
            "switches":SwitchesActive,
            "chests":BoxesActive,
            "darklingsKilled":DarklingsKilled,
            "fairyflies":FairyArray.length,
            "keys":KeyArray.length,
            "x":(Hero == null ? 0 : Hero.x),
            "y":(Hero == null ? 0 : Hero.y),
            "speed":(Hero == null ? 0 : Hero.Speed),
            "fall":(Hero == null ? 0 : Hero.Fall),
            "grounded":(Hero == null ? false : Hero.Grounded),
            "wings":(Hero == null ? false : Hero.Wings),
            "charge":DarkKillerCharge,
            "hudVisible":(HUD == null ? false : HUD.visible),
            "mapAvailable":(HUD == null ? false : HUD.MapHolder.visible),
            "bossMode":BossMode,
            "bossDeath":BossDeath,
            "faderFrame":(Fader == null ? 0 : Fader.currentFrame),
            "targets":targets,
            "darklings":darklings
         };
      }
      
      private function gwPolicyDraw(out:BitmapData, node:DisplayObject) : void
      {
         if(node.visible && node.alpha > 0)
         {
            out.draw(node,node.transform.concatenatedMatrix,node.transform.concatenatedColorTransform,null,out.rect,false);
         }
      }
      
      private function gwPolicyAbove(node:DisplayObject, out:BitmapData) : void
      {
         out.fillRect(out.rect,0);
         var current:DisplayObject = node;
         while(current != this && current.parent != null)
         {
            var parent:DisplayObjectContainer = current.parent;
            var i:int = parent.getChildIndex(current) + 1;
            while(i < parent.numChildren)
            {
               gwPolicyDraw(out,parent.getChildAt(i));
               i++;
            }
            current = parent;
         }
      }
      
      private function gwPolicyInside(x:Number, y:Number) : Boolean
      {
         return x >= 0 && y >= 0 && x < StageWidth && y < StageHeight;
      }
      
      private function gwPolicyAlpha(data:BitmapData, x:Number, y:Number) : uint
      {
         return data.getPixel32(int(x),int(y)) >>> 24;
      }
      
      private function gwPolicyId(value:Object, prefix:String) : String
      {
         if(gwPolicyIds[value] == null)
         {
            gwPolicyIds[value] = prefix + String(++gwPolicyNextId);
         }
         return String(gwPolicyIds[value]);
      }
      
      private function gwPolicyTileBounds(tile:Tile) : Object
      {
         var total:int = 0;
         var seen:int = 0;
         var left:int = 60;
         var top:int = 60;
         var right:int = -1;
         var bottom:int = -1;
         var px:int = 0;
         while(px < 60)
         {
            var py:int = 0;
            while(py < 60)
            {
               if(tile.Gfx.bitmapData.getPixel32(px,py) >>> 24 >= 192)
               {
                  total++;
                  var sx:Number = tile.Gfx.x + Cam.x + px;
                  var sy:Number = tile.Gfx.y + Cam.y + py;
                  if(!(!gwPolicyInside(sx,sy) || gwPolicyAlpha(gwPolicyCover,sx,sy) > 32))
                  {
                     seen++;
                     left = Math.min(left,px);
                     top = Math.min(top,py);
                     right = Math.max(right,px);
                     bottom = Math.max(bottom,py);
                  }
               }
               py++;
            }
            px++;
         }
         if(seen < 8 || seen < total * (tile.Type == "1410" ? 0.1 : 0.25))
         {
            return null;
         }
         return {
            "x":tile.Gfx.x + left,
            "y":tile.Gfx.y + top,
            "width":right - left + 1,
            "height":bottom - top + 1
         };
      }
      
      public function gwPolicyScene() : Object
      {
         if(!EnableGameMode || SharedObj == null || Hero == null || !Cam.visible)
         {
            return null;
         }
         if(gwPolicyCover == null)
         {
            gwPolicyCover = new BitmapData(StageWidth,StageHeight,true,0);
         }
         var objects:Array = [];
         var flies:Array = [];
         var dark:Array = [];
         gwPolicyAbove(Cam.BackShrubTiles,gwPolicyCover);
         var i:int = 0;
         while(i < GridWidth)
         {
            var j:int = 0;
            while(j < GridHeight)
            {
               var tile:Tile = BackShrubTileVec[i][j];
               var x:Number = tile.Gfx.x;
               var y:Number = tile.Gfx.y;
               if(!(x + Cam.x + 60 < 0 || x + Cam.x >= StageWidth || y + Cam.y + 60 < 0 || y + Cam.y >= StageHeight))
               {
                  var type:int = int(TileMap.HitTest(tile.GridX,tile.GridY));
                  var code:String = tile.Type;
                  var kind:String = "";
                  var state:String = "";
                  if(type == 7 && code == "0310")
                  {
                     kind = "mushroom";
                     state = "uncollected";
                  }
                  else if(type == 20 && code == "1410")
                  {
                     kind = "key";
                     state = "uncollected";
                  }
                  else if((type == 21 || type == 23) && code == "1204")
                  {
                     kind = "chest";
                     state = "locked";
                  }
                  else if(code == "1304")
                  {
                     kind = "chest";
                     state = "opened";
                  }
                  else if(type == 14 && code == "0610")
                  {
                     kind = "runestone";
                     state = "unlit";
                  }
                  else if(type == 22 && code == "0710")
                  {
                     kind = "runestone";
                     state = "lit";
                  }
                  else if((type == 25 || type == 31 || type == 40) && code == "1205")
                  {
                     kind = "bridge_switch";
                     state = "off";
                  }
                  else if(code == "1305")
                  {
                     kind = "bridge_switch";
                     state = "on";
                  }
                  if(kind != "")
                  {
                     var bounds:Object = gwPolicyTileBounds(tile);
                     if(bounds != null)
                     {
                        objects.push({
                           "id":"object:" + tile.GridX + "," + tile.GridY,
                           "kind":kind,
                           "state":state,
                           "x":x + 30,
                           "y":y + 30,
                           "bounds":bounds
                        });
                     }
                  }
               }
               j++;
            }
            i++;
         }
         gwPolicyAbove(Cam.ParticleLayer,gwPolicyCover);
         var p:Particle = PTCSystem.First;
         while(p != null)
         {
            if(p.Type == "Fairy")
            {
               x = p.XThrust + p.XPos;
               y = p.YThrust + p.YPos;
               var sx:Number = x + Cam.x;
               var sy:Number = y + Cam.y;
               if(gwPolicyInside(sx,sy) && gwPolicyAlpha(gwPolicyCover,sx,sy) <= 32 && PTCBitmap.getPixel32(int(sx),int(sy)) == 4294967295)
               {
                  flies.push({
                     "id":gwPolicyId(p,"fairy:"),
                     "x":x,
                     "y":y
                  });
               }
            }
            p = p.Next;
         }
         i = 0;
         while(i < VerticalVec.length)
         {
            var v:Vertical = VerticalVec[i];
            if(!(v.Type != "Dark" || !v.visible))
            {
               var frame:int = v.Graphic.currentFrame;
               if(frame >= 47 && frame <= 66 || frame >= 74 && frame <= 89)
               {
                  var r:Rectangle = v.getBounds(this);
                  if(!(r.right < 0 || r.left >= StageWidth || r.bottom < 0 || r.top >= StageHeight))
                  {
                     gwPolicyAbove(v,gwPolicyCover);
                     var seen:int = 0;
                     var total:int = 0;
                     var px:int = Math.floor(r.left);
                     while(px < r.right)
                     {
                        var py:int = Math.floor(r.top);
                        while(py < r.bottom)
                        {
                           if(v.hitTestPoint(px,py,true))
                           {
                              total++;
                              if(gwPolicyInside(px,py) && gwPolicyAlpha(gwPolicyCover,px,py) <= 32)
                              {
                                 seen++;
                              }
                           }
                           py += 2;
                        }
                        px += 2;
                     }
                     if(!(seen < 12 || seen < total * 0.25))
                     {
                        dark.push({
                           "id":gwPolicyId(v,"darkling:"),
                           "x":v.x,
                           "y":v.y,
                           "animation":v.Graphic.currentLabel,
                           "attack":frame >= 74,
                           "bounds":{
                              "x":r.x - Cam.x,
                              "y":r.y - Cam.y,
                              "width":r.width,
                              "height":r.height
                           }
                        });
                     }
                  }
               }
            }
            i++;
         }
         return {
            "world_view":{
               "x":-Cam.x,
               "y":-Cam.y,
               "width":StageWidth,
               "height":StageHeight
            },
            "objects":objects,
            "flies":flies,
            "darklings":dark
         };
      }
      
