// Review extract: authored helper methods added to the native root class.
// Fields added there: bridgeFade, bridgeLevel, bridgeGeometry, policyIds, policyNextId.
      private function bridgePublish(e:Event) : void
      {
         if(ExternalInterface.available)
         {
            ExternalInterface.call("__giveUpReceive",giveUpState());
         }
      }
      
      private function bridgeRect(child:DisplayObject, level:Level) : Object
      {
         var b:Rectangle = child.getBounds(level);
         return {
            "x":b.x,
            "y":b.y,
            "width":b.width,
            "height":b.height
         };
      }
      
      public function giveUpState() : Object
      {
         var o:Object = {
            "loaded":this.§_-8k§ != null,
            "preloaderReady":this.§_-3e§ != null && this.§_-3e§.progressInt == 100,
            "screen":"preloader",
            "lastExitIndex":§_-8t§.bridgeLastExit,
            "exits":§_-8t§.bridgeExits,
            "won":§_-8t§.bridgeWon
         };
         if(!this.§_-8k§)
         {
            return o;
         }
         o.screen = this.§_-8k§.§_-5y§;
         o.menuReady = this.§_-8k§.mainMenu != null && !this.§_-8k§.mainMenu.§_-7M§.isPlaying;
         var g:§_-8t§ = this.§_-8k§.§_-AU§;
         if(!g || !g.player || !g.§_-AX§)
         {
            return o;
         }
         var l:Level = g.§_-AX§;
         o.floorIndex = g.§_-6p§;
         o.deaths = g.totalDeaths;
         o.ticks = g.§_-B1§;
         o.paused = g.§_-26§;
         o.x = g.player.x;
         o.y = g.player.y;
         o.jumpCount = g.player.§_-A0§;
         o.loadedAssets = l.loadedAssets && l.§_-AV§ != null && l.§_-2F§ != null && l.§_-9o§ != null;
         o.levelY = l.y;
         if(!o.loadedAssets)
         {
            return o;
         }
         bridgeFade = g.§_-AH§.§_-AI§;
         o.fadeReady = bridgeFade != null && bridgeFade.currentFrame >= 16;
         o.fadeFrame = bridgeFade ? bridgeFade.currentFrame : 0;
         o.body = bridgeRect(g.player.hitA,l);
         o.entrance = {
            "x":l.§_-AV§.x,
            "y":l.§_-AV§.y
         };
         o.exit = bridgeRect(l.§_-2F§.hitA,l);
         return o;
      }
      
      public function giveUpGeometry() : Object
      {
         var g:§_-8t§ = this.§_-8k§ ? this.§_-8k§.§_-AU§ : null;
         if(!g || !g.§_-AX§ || !g.§_-AX§.loadedAssets || !g.§_-AX§.§_-2F§ || !g.§_-AX§.§_-AV§)
         {
            return null;
         }
         var l:Level = g.§_-AX§;
         if(bridgeLevel == l)
         {
            return bridgeGeometry;
         }
         var body:Rectangle = g.player.hitA.getBounds(l);
         var o:Object = {
            "floorIndex":g.§_-6p§,
            "width":900,
            "height":400,
            "solids":[],
            "entrance":{
               "x":l.§_-AV§.x + body.x + body.width / 2 - g.player.x,
               "y":l.§_-AV§.y + body.y + body.height / 2 - g.player.y
            },
            "exit":bridgeRect(l.§_-2F§.hitA,l)
         };
         var i:int = 0;
         while(i < l.tiles.length)
         {
            o.solids.push(bridgeRect(l.tiles[i],l));
            i++;
         }
         i = 0;
         while(i < l.§_-8D§.length)
         {
            o.solids.push(bridgeRect(l.§_-8D§[i],l));
            i++;
         }
         i = 0;
         while(i < l.fallingBlocks.length)
         {
            o.solids.push(bridgeRect(l.fallingBlocks[i],l));
            i++;
         }
         bridgeLevel = l;
         bridgeGeometry = o;
         return o;
      }
      
      public function giveUpNative(command:String, floor:int = 0) : Boolean
      {
         if(command == "boot")
         {
            if(!this.§_-8k§ && this.§_-3e§)
            {
               this.§_-3e§.§_-8B§();
            }
            return this.§_-8k§ != null;
         }
         if(!this.§_-8k§)
         {
            return false;
         }
         if(command == "menu")
         {
            this.§_-8k§.§_-5D§("mainMenu");
            return true;
         }
         if(command == "play" && floor >= 0 && floor <= 35 && floor % 5 == 0 && this.§_-8k§.mainMenu)
         {
            §_-8t§.configuredStartFloor = floor;
            this.§_-8k§.mainMenu.§_-6E§ = "normalGame";
            this.§_-8k§.mainMenu.§_-7M§.gotoAndPlay(2);
            stage.focus = stage;
            return true;
         }
         return false;
      }
      
      private function policyId(d:DisplayObject) : String
      {
         if(!policyIds[d])
         {
            policyIds[d] = "object-" + ++policyNextId;
         }
         return policyIds[d];
      }
      
      private function policyShown(d:DisplayObject, l:Level) : Boolean
      {
         if(!d)
         {
            return false;
         }
         var node:DisplayObject = d;
         while(node && node != l)
         {
            if(!node.visible || node.alpha <= 0)
            {
               return false;
            }
            node = node.parent;
         }
         if(node != l || !l.visible || l.alpha <= 0)
         {
            return false;
         }
         var b:Rectangle = d.getBounds(l);
         return b.width > 0 && b.height > 0 && b.right > 0 && b.left < 900 && b.bottom > 0 && b.top < 400;
      }
      
      private function policyTerrain(out:Array, t:§_-W§, kind:String, l:Level) : void
      {
         if(!policyShown(t,l))
         {
            return;
         }
         var occupied:Boolean = l.getChildByName("tile" + t.§_-5B§ + "x" + t.§_-7N§) == t;
         var size:Number = l.player.tileSize;
         out.push({
            "id":policyId(t),
            "kind":kind,
            "bounds":{
               "x":t.x,
               "y":t.y,
               "width":size,
               "height":size
            },
            "gridBounds":(occupied ? {
               "x":t.§_-5B§ * size,
               "y":t.§_-7N§ * size,
               "width":size,
               "height":size
            } : null),
            "collidable":occupied && (t.type == 1 || t.type == 2),
            "collisionType":t.type
         });
      }
      
      private function policyHazard(out:Array, owner:DisplayObject, art:DisplayObject, shape:DisplayObject, kind:String, target:String, lethal:Boolean, l:Level) : void
      {
         if(!policyShown(art,l))
         {
            return;
         }
         out.push({
            "id":policyId(owner),
            "kind":kind,
            "bounds":bridgeRect(art,l),
            "lethalBounds":(shape ? bridgeRect(shape,l) : null),
            "target":target,
            "lethal":lethal
         });
      }
      
      public function giveUpPolicySnapshot() : Object
      {
         var g:§_-8t§ = this.§_-8k§ ? this.§_-8k§.§_-AU§ : null;
         if(!g || this.§_-8k§.§_-5y§ != "normalGame" || !g.player || !g.§_-AX§)
         {
            return null;
         }
         var l:Level = g.§_-AX§;
         if(!l.loadedAssets || !l.§_-AV§ || !l.§_-2F§ || !g.§_-AH§.§_-AI§ || g.§_-AH§.§_-AI§.currentFrame < 16 || Math.abs(l.y) >= 1)
         {
            return null;
         }
         var p:§_-6x§ = g.player;
         var terrain:Array = [];
         var hazards:Array = [];
         var i:int = 0;
         while(i < l.tiles.length)
         {
            policyTerrain(terrain,l.tiles[i],"solid",l);
            i++;
         }
         i = 0;
         while(i < l.§_-8D§.length)
         {
            if(l.§_-8D§[i].currentFrame > 1)
            {
               policyTerrain(terrain,l.§_-8D§[i],"revealed",l);
            }
            i++;
         }
         i = 0;
         while(i < l.fallingBlocks.length)
         {
            policyTerrain(terrain,l.fallingBlocks[i],"falling",l);
            i++;
         }
         i = 0;
         while(i < l.§_-2L§.length)
         {
            var d:* = l.§_-2L§[i];
            policyHazard(hazards,d,d,d.hitA,"spike","spikes",true,l);
            i++;
         }
         var sawCollision:Boolean = l.§_-37§ && l.§_-A4§.alpha <= 0.5;
         i = 0;
         while(i < l.§_-AO§.length)
         {
            d = l.§_-AO§[i];
            if(policyShown(d,l))
            {
               hazards.push({
                  "id":policyId(d),
                  "kind":"saw",
                  "bounds":bridgeRect(d,l),
                  "circle":{
                     "x":d.x,
                     "y":d.y,
                     "radius":d.§_-5w§
                  },
                  "target":"saw_point",
                  "lethal":sawCollision
               });
            }
            i++;
         }
         i = 0;
         while(i < l.§_-3P§.length)
         {
            d = l.§_-3P§[i];
            policyHazard(hazards,d,d.§_-8m§,d.hitA,"ground_saw","body",sawCollision,l);
            i++;
         }
         var groups:Array = [l.§_-7K§,l.movingBuzzsaws2,l.movingBuzzsaws3,l.movingBuzzsaws4,l.movingBuzzsaws5,l.movingBuzzsaws6];
         for each(var group in groups)
         {
            i = 0;
            while(i < group.length)
            {
               d = group[i];
               policyHazard(hazards,d,d.mbs.inside,d.mbs.hitA,"moving_saw","body",sawCollision,l);
               i++;
            }
         }
         i = 0;
         while(i < l.§_-5X§.length)
         {
            d = l.§_-5X§[i];
            policyHazard(hazards,d,d.§_-B3§,d.§_-B3§.hitA,"falling_spike","spikes",true,l);
            i++;
         }
         i = 0;
         while(i < l.§_-Ag§.length)
         {
            d = l.§_-Ag§[i];
            policyHazard(hazards,d,d.§_-8N§,null,"laser_cannon","none",false,l);
            if(policyShown(d.§_-AR§,l))
            {
               hazards.push({
                  "id":policyId(d) + "/beam",
                  "kind":"laser",
                  "bounds":bridgeRect(d.§_-AR§,l),
                  "segment":{
                     "x1":d.x,
                     "y1":d.y,
                     "x2":d.x + d.§_-4C§.x,
                     "y2":d.y + d.§_-4C§.y
                  },
                  "sampleStep":{
                     "x":d.§_-7D§,
                     "y":d.§_-8s§
                  },
                  "target":"projectiles_and_lasers",
                  "lethal":true
               });
            }
            i++;
         }
         i = 0;
         while(i < l.§_-k§.length)
         {
            d = l.§_-k§[i];
            if(policyShown(d.§_-8N§,l))
            {
               hazards.push({
                  "id":policyId(d),
                  "kind":"rocket_cannon",
                  "bounds":bridgeRect(d.§_-8N§,l),
                  "target":"none",
                  "lethal":false,
                  "warningVisible":policyShown(d.§_-8N§.beam,l)
               });
            }
            i++;
         }
         i = 0;
         while(i < l.§_-7O§.length)
         {
            d = l.§_-7O§[i];
            policyHazard(hazards,d,d,d,"projectile","projectiles_and_lasers",l.§_-37§,l);
            i++;
         }
         return {
            "terrain":terrain,
            "hazards":hazards,
            "contact":{
               "body":bridgeRect(p.hitA,l),
               "spikes":bridgeRect(p.§_-Ax§,l),
               "projectilesAndLasers":bridgeRect(p.damageHitA2,l),
               "sawPoint":{
                  "x":p.x,
                  "y":p.y
               },
               "nativeGrounded":p.§_-7T§,
               "verticalPhase":p.§_-6G§,
               "jumpCount":p.§_-A0§,
               "jumpHeld":p.§_-a§
            }
         };
      }

// Native game class additions:
// configuredStartFloor initializes the existing floor index.
// init clears bridgeLastExit, bridgeExits and bridgeWon.
// The existing exit increment publishes floorExit.
// After the existing beatGame call, bridgeWon is latched and gameComplete is published.
