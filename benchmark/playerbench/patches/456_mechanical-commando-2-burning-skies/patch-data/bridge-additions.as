/* Authored bridge helper bodies, exported from the reconstructed SWF with
 * JPEXS FFDec 26.3.0 for review. Not standalone compilation units.
 * Native game methods and assets are intentionally omitted.
 * Installation uses checked ABC copy/insert ranges, not this review file. */


// Additions to com/lachhhEngine/initializers/preloader/TeaGamesPreloader.as
      private function gwLoaded(event:Event) : void
      {
         if(root.loaderInfo.bytesTotal <= 0 || root.loaderInfo.bytesLoaded < root.loaderInfo.bytesTotal)
         {
            return;
         }
         removeEventListener(Event.ENTER_FRAME,this.gwLoaded);
         this._callback.DoCallback();
      }

// Additions to com/mechcomm2/Main.as
      public function gwEvent(kind:String, data:Object) : void
      {
         var ev:MC2_LevelEvent = _game.levelEventManager.GetCurrentLevelEvent() as MC2_LevelEvent;
         data.attempt = this.gwAttempt;
         data.mission = MC2_GameProgress.gameProgress.selectedLevel != null ? MC2_GameProgress.gameProgress.selectedLevel.id + 1 : 0;
         this.gwEvents.push({
            "kind":kind,
            "data":data
         });
      }

      public function gwDrainNativeEvents() : Array
      {
         var result:Array = this.gwEvents;
         this.gwEvents = [];
         return result;
      }

      public function gwDamage(actor:Mc2_Actor, amount:Number) : void
      {
         if(amount <= 0)
         {
            return;
         }
         var ev:MC2_LevelEvent = _game.levelEventManager.GetCurrentLevelEvent() as MC2_LevelEvent;
         if(ev == null || ev.boss == null)
         {
            return;
         }
         var b:Boss = ev.boss;
         var index:int = -1;
         if(actor != b)
         {
            if(!(actor is BreakablePart) || BreakablePart(actor).owner != b)
            {
               return;
            }
            index = -2;
            var i:int = 0;
            while(i < b.numBreakables)
            {
               if(b.GetBreakablePart(i) == actor)
               {
                  index = i;
               }
               i++;
            }
            if(index == -2)
            {
               return;
            }
         }
         this.gwEvent("boss_damage",{
            "part":index,
            "amount":amount,
            "budget":this.gwBossBudget(b)
         });
      }

      private function gwBossBudget(b:Boss) : Object
      {
         var parts:Array = [];
         var i:int = 0;
         while(i < b.numBreakables)
         {
            var part:BreakablePart = b.GetBreakablePart(i);
            parts.push({
               "index":i,
               "hp":part.hp,
               "maxHp":part.metaMc2Actor.maxHp,
               "dead":part.IsDead()
            });
            i++;
         }
         return {
            "hp":b.hp,
            "maxHp":b.metaMc2Actor.maxHp,
            "dead":b.IsDead(),
            "parts":parts
         };
      }

      public function gwRead() : Object
      {
         var g:MC2_Game = MC2_Game(_game);
         var o:Object = {
            "ready":this.gwReady,
            "starting":this.gwStarting,
            "attempt":this.gwAttempt,
            "gameTimeMs":this.gwClock,
            "paused":g.paused,
            "victory":g.victory,
            "gameOver":g.gameOver,
            "xp":g.ingameXP,
            "nativeKills":StatsLevel.instance.GetValue(StatsLevel.ID_KILLRANK),
            "ammo":g.ammoManager.activeFactoryObjects.length,
            "screens":[],
            "mission":0,
            "hero":null,
            "boss":null
         };
         for each(var sc:Screen in ScreenManager.instance.activeFactoryObjects)
         {
            o.screens.push(getQualifiedClassName(sc).split("::").pop());
         }
         var h:Mc2_Actor = g.GetPlayer1() as Mc2_Actor;
         if(h != null && h.metaInfo != null)
         {
            o.hero = {
               "x":h.px,
               "y":h.py,
               "hp":h.hp,
               "maxHp":h.metaMc2Actor.maxHp,
               "dead":h.IsDead()
            };
         }
         var ev:MC2_LevelEvent = g.levelEventManager.GetCurrentLevelEvent() as MC2_LevelEvent;
         if(ev != null)
         {
            o.mission = ev.levelNumber;
            o.bossDead = ev.bossDead;
            if(ev.boss != null && ev.boss.metaInfo != null)
            {
               o.boss = this.gwBossBudget(ev.boss);
            }
         }
         var save:MC2_GameProgress = MC2_GameProgress.gameProgress;
         if(save != null)
         {
            o.savedXP = save.xp;
            o.completedMissions = save.story.numMainCompleted;
            o.gear = {
               "gun":save.equippedGun,
               "shoulder":save.equippedShoulder,
               "cockpit":save.equippedCockpit,
               "wings":save.equippedLegs,
               "special":save.equippedSpecial
            };
         }
         return o;
      }

      public function gwStart(mission:int) : Object
      {
         if(mission < 1 || mission > 5)
         {
            return {"error":"invalid mission"};
         }
         this.gwRequested = mission;
         this.gwReady = false;
         this.gwStarting = true;
         this.gwSawIntro = false;
         var all:Array = [];
         for each(var obj:Object in ScreenManager.instance.activeFactoryObjects)
         {
            all.push(obj);
         }
         for each(var sc:Screen in all)
         {
            if(!(sc is IngameScreen))
            {
               ScreenManager.instance.RemoveScreen(sc);
            }
         }
         FileManager.instance.SelectNewGameProgress(0);
         MC2_GamePrefs(FileManager.instance.gamePrefs).useMouse = false;
         this.LaunchGame([AllMetaLevels.MetaMission1,AllMetaLevels.MetaMission2,AllMetaLevels.MetaMission3,AllMetaLevels.MetaMission4,AllMetaLevels.MetaMission5][mission - 1]);
         return {"requested":mission};
      }

      private function gwFrame() : void
      {
         var _loc6_:Main = this;
         var _loc7_:Number = _loc6_.poFrame + 1;
         _loc6_.poFrame = _loc7_;
         var g:MC2_Game = MC2_Game(_game);
         if(this.gwStarting)
         {
            var all:Array = [];
            for each(var obj:Object in ScreenManager.instance.activeFactoryObjects)
            {
               all.push(obj);
            }
            for each(var sc:Screen in all)
            {
               if(sc is BriefingScreen)
               {
                  this.gwSawIntro = true;
                  BriefingScreen(sc).gwDismissIntro();
               }
               if(sc is TutorialScreen)
               {
                  TutorialScreen(sc).gwDismissIntro();
               }
            }
            var s:Object = this.gwRead();
            if(this.gwSawIntro && s.mission == this.gwRequested && s.hero != null && !s.paused && s.screens.length == 1)
            {
               this.gwStarting = false;
               this.gwReady = true;
            }
         }
         if(g.isInit && !g.paused && !g.gameOver && !g.victory)
         {
            this.gwClock += 1000 / 36 * g.speedManager.GetSpeed();
         }
      }

      private function poPoint(p:Point) : Object
      {
         return {
            "x":p.x,
            "y":p.y
         };
      }

      private function poRect(r:Rectangle) : Object
      {
         if(r == null)
         {
            return null;
         }
         return {
            "x":r.x,
            "y":r.y,
            "width":r.width,
            "height":r.height
         };
      }

      private function poClock() : Object
      {
         return {
            "frame":this.poFrame,
            "game_time_ms":this.gwClock,
            "native_fps":36
         };
      }

      private function poId(a:Object) : String
      {
         var identity:Object = this.poVisibleIdentities[a];
         if(identity == null || identity.lifetime != a.poLifetime)
         {
            identity = {
               "lifetime":a.poLifetime,
               "id":++this.poNextVisibleIdentity
            };
            this.poVisibleIdentities[a] = identity;
         }
         return "entity:" + identity.id;
      }

      private function poAttached(d:DisplayObject) : Boolean
      {
         if(d == null || d.stage == null)
         {
            return false;
         }
         var current:DisplayObject = d;
         while(current != null)
         {
            if(!current.visible || current.alpha <= 0 || current.transform.colorTransform.alphaMultiplier <= 0)
            {
               return false;
            }
            current = current.parent;
         }
         return true;
      }

      private function poBounds(d:DisplayObject) : Rectangle
      {
         if(d == null || !d.visible || d.alpha <= 0 || d.transform.colorTransform.alphaMultiplier <= 0)
         {
            return null;
         }
         var result:Rectangle = null;
         var part:Rectangle = null;
         var container:DisplayObjectContainer = d as DisplayObjectContainer;
         if(container != null && container.numChildren > 0)
         {
            var i:int = 0;
            while(i < container.numChildren)
            {
               part = this.poBounds(container.getChildAt(i));
               if(part != null)
               {
                  result = result == null ? part : result.union(part);
               }
               i++;
            }
         }
         else
         {
            part = d.getBounds(stage);
            if(part.width > 0 && part.height > 0)
            {
               result = part;
            }
         }
         if(result != null && d.mask != null)
         {
            result = result.intersection(d.mask.getBounds(stage));
         }
         if(result != null && d.scrollRect != null)
         {
            var clip:Rectangle = d.scrollRect;
            var p0:Point = d.localToGlobal(new Point(clip.left,clip.top));
            var p1:Point = d.localToGlobal(new Point(clip.right,clip.bottom));
            result = result.intersection(new Rectangle(p0.x,p0.y,p1.x - p0.x,p1.y - p0.y));
         }
         return result != null && result.width > 0 && result.height > 0 ? result : null;
      }

      private function poVisibleBounds(d:DisplayObject) : Rectangle
      {
         if(!this.poAttached(d))
         {
            return null;
         }
         var r:Rectangle = this.poBounds(d);
         if(r == null || !r.intersects(new Rectangle(0,0,700,550)))
         {
            return null;
         }
         if(!this.poExposed(d,r.intersection(new Rectangle(0,0,700,550))))
         {
            return null;
         }
         return r;
      }

      private function poWithin(d:DisplayObject, owner:DisplayObject) : Boolean
      {
         while(d != null)
         {
            if(d == owner)
            {
               return true;
            }
            d = d.parent;
         }
         return false;
      }

      private function poAlphaAt(d:DisplayObject, p:Point, cache:Dictionary) : Number
      {
         var sample:Object = cache[d];
         if(sample == null)
         {
            var r:Rectangle = d.getBounds(stage).intersection(new Rectangle(0,0,700,550));
            var left:int = Math.max(0,Math.floor(r.left));
            var top:int = Math.max(0,Math.floor(r.top));
            var width:int = Math.min(700,Math.ceil(r.right)) - left;
            var height:int = Math.min(550,Math.ceil(r.bottom)) - top;
            if(width <= 0 || height <= 0)
            {
               return 0;
            }
            var bitmap:BitmapData = new BitmapData(width,height,true,0);
            sample = {
               "bitmap":bitmap,
               "left":left,
               "top":top
            };
            cache[d] = sample;
            var matrix:Matrix = d.transform.concatenatedMatrix.clone();
            matrix.tx -= left;
            matrix.ty -= top;
            var multiplier:Number = 1;
            var offset:Number = 0;
            var ancestor:DisplayObject = d;
            while(ancestor != null)
            {
               var color:ColorTransform = ancestor.transform.colorTransform;
               offset = offset * color.alphaMultiplier + color.alphaOffset;
               multiplier *= color.alphaMultiplier;
               ancestor = ancestor.parent;
            }
            bitmap.draw(d,matrix,new ColorTransform(1,1,1,multiplier,0,0,0,offset));
         }
         var x:int = Math.floor(p.x) - sample.left;
         var y:int = Math.floor(p.y) - sample.top;
         if(x < 0 || y < 0 || x >= sample.bitmap.width || y >= sample.bitmap.height)
         {
            return 0;
         }
         return sample.bitmap.getPixel32(x,y) >>> 24 & 0xFF;
      }

      private function poDisposeAlphas(cache:Dictionary) : void
      {
         for each(var sample:Object in cache)
         {
            sample.bitmap.dispose();
         }
      }

      private function poExposedAt(d:DisplayObject, p:Point, cache:Dictionary = null) : Boolean
      {
         var hits:Array;
         var hasOwn:Boolean;
         var candidate:DisplayObject;
         var transmission:Number;
         var exposedAlpha:Number;
         var i:int;
         var hit:DisplayObject;
         var alpha:Number;
         var temporary:Boolean = cache == null;
         if(temporary)
         {
            cache = new Dictionary(true);
         }
         try
         {
            hits = stage.getObjectsUnderPoint(p);
            hasOwn = false;
            for each(candidate in hits)
            {
               if(this.poAttached(candidate) && this.poWithin(candidate,d))
               {
                  hasOwn = true;
               }
            }
            if(!hasOwn)
            {
               return false;
            }
            transmission = 1;
            exposedAlpha = 0;
            i = hits.length - 1;
            while(true)
            {
               if(i < 0)
               {
                  return false;
               }
               hit = hits[i] as DisplayObject;
               if(this.poAttached(hit))
               {
                  alpha = this.poAlphaAt(hit,p,cache);
                  if(alpha > 0)
                  {
                     if(this.poWithin(hit,d))
                     {
                        exposedAlpha += transmission * alpha;
                        if(exposedAlpha >= 1)
                        {
                           return true;
                        }
                     }
                     transmission *= 1 - alpha / 255;
                     if(transmission < 1 / 255)
                     {
                        return false;
                     }
                  }
               }
               i--;
            }
         }
         finally
         {
            if(temporary)
            {
               this.poDisposeAlphas(cache);
            }
         }
      }

      private function poExposed(d:DisplayObject, r:Rectangle) : Boolean
      {
         var fractions:Array;
         var fx:Number;
         var fy:Number;
         var nx:int;
         var ny:int;
         var x:int;
         var y:int;
         var p:Point;
         var dx:Number;
         var dy:Number;
         var cache:Dictionary = new Dictionary(true);
         try
         {
            fractions = [0.5,0.1,0.3,0.7,0.9];
            var _loc4_:int = 0;
            var _loc5_:* = fractions;
            while(true)
            {
               for each(fx in _loc5_)
               {
                  for each(fy in fractions)
                  {
                     if(this.poExposedAt(d,new Point(r.x + r.width * fx,r.y + r.height * fy),cache))
                     {
                        return true;
                     }
                  }
               }
               nx = Math.max(1,Math.min(17,Math.ceil(r.width / 3)));
               ny = Math.max(1,Math.min(17,Math.ceil(r.height / 3)));
               x = 0;
               while(true)
               {
                  if(x >= nx)
                  {
                     return false;
                  }
                  y = 0;
                  while(y < ny)
                  {
                     p = new Point(r.x + r.width * (x + 0.5) / nx,r.y + r.height * (y + 0.5) / ny);
                     dx = Math.min(2,r.width / 10);
                     dy = Math.min(2,r.height / 10);
                     if(this.poExposedAt(d,p,cache) && this.poExposedAt(d,new Point(p.x - dx,p.y),cache) && this.poExposedAt(d,new Point(p.x + dx,p.y),cache) && this.poExposedAt(d,new Point(p.x,p.y - dy),cache) && this.poExposedAt(d,new Point(p.x,p.y + dy),cache))
                     {
                        return true;
                     }
                     y++;
                  }
                  x++;
               }
               break;
            }
         }
         finally
         {
            this.poDisposeAlphas(cache);
         }
      }

      private function poWorld(a:Actor, x:Number, y:Number) : Point
      {
         return _game.camera.ConvertPointToScreen(new Point(x,y),a.layer);
      }

      private function poCircle(a:Actor, x:Number, y:Number, radius:Number) : Object
      {
         var center:Point = this.poWorld(a,x,y);
         var edge:Point = this.poWorld(a,x + radius,y);
         return {
            "shape":"circle",
            "center":this.poPoint(center),
            "radius":Point.distance(center,edge),
            "provenance":"internal"
         };
      }

      private function poActorCircle(a:Mc2_Actor) : Object
      {
         if(a.circCol == null || a.IsDead())
         {
            return null;
         }
         return this.poCircle(a,a.circCol.x,a.circCol.y,a.circCol.rayon);
      }

      private function poVelocity(a:Actor) : Object
      {
         var p:Point = this.poWorld(a,a.px,a.py);
         var q:Point = this.poWorld(a,a.px + a.vx,a.py + a.vy);
         return {
            "x":q.x - p.x,
            "y":q.y - p.y,
            "units":"stage_pixels_per_native_frame",
            "includes_camera_motion":false,
            "provenance":"internal"
         };
      }

      private function poEntity(a:Actor, bounds:Rectangle) : Object
      {
         return {
            "id":this.poId(a),
            "center":this.poPoint(this.poWorld(a,a.px,a.py)),
            "sprite_origin":this.poPoint(a.animationContainer.localToGlobal(new Point())),
            "bounds":this.poRect(bounds),
            "visible_bounds":this.poRect(bounds.intersection(new Rectangle(0,0,700,550))),
            "sample_clock":this.poClock(),
            "geometry_provenance":"engine_hook"
         };
      }

      private function poClassification(a:Mc2_Actor, boss:Boss) : String
      {
         if(a is BreakablePart)
         {
            return "attached_component";
         }
         if(a == boss)
         {
            return "boss_body";
         }
         switch(a.metaMc2Actor.actorSpecificType)
         {
            case MC2_EnemyManager.MECH_3_CHOPPER:
               return "helicopter";
            case MC2_EnemyManager.MECH_7_BOMBER:
               return "bomber";
            case MC2_EnemyManager.MECH_8_FIGHTER:
               return "fighter";
            case MC2_EnemyManager.MECH_5_TANK:
               return "tank";
            case MC2_EnemyManager.MECH_6_TURRET:
               return "ground_turret";
            case MC2_EnemyManager.MECH_1_WALKER:
               return "walker";
            case MC2_EnemyManager.MECH_2_ROCKET:
               return "rocket_weapon";
            case MC2_EnemyManager.MECH_4_SPIDER:
               return "spider";
            default:
               return "hostile";
         }
      }

      private function poPickupType(a:Mc2_Item) : String
      {
         switch(a.metaInfo.instanceType)
         {
            case MC2_ActorFactory.ID_ITEM_HEALTH_PACK:
               return "health";
            case MC2_ActorFactory.ID_ITEM_SPEED_BOOST:
               return "speed";
            case MC2_ActorFactory.ID_ITEM_SHIELD_PACK:
               return "shield";
            case MC2_ActorFactory.ID_ITEM_KILL_PICKUP:
               return "bomb";
            case MC2_ActorFactory.ID_ITEM_INTEL:
               return "intel";
            case MC2_ActorFactory.ID_EXP_PICKUP:
               return "experience";
            default:
               return "collectible";
         }
      }

      private function poText(d:DisplayObject) : String
      {
         if(d == null || !d.visible || d.alpha <= 0)
         {
            return "";
         }
         if(d is TextField)
         {
            return TextField(d).text;
         }
         var container:DisplayObjectContainer = d as DisplayObjectContainer;
         var text:String = "";
         if(container != null)
         {
            var i:int = 0;
            while(i < container.numChildren)
            {
               var childText:String = this.poText(container.getChildAt(i));
               if(childText != "")
               {
                  text += (text == "" ? "" : " ") + childText;
               }
               i++;
            }
         }
         return text;
      }

      private function poButtons(d:DisplayObject, sc:Screen, result:Array) : void
      {
         if(d == null || !d.visible || d.alpha <= 0)
         {
            return;
         }
         var interactive:InteractiveObject = d as InteractiveObject;
         var container:DisplayObjectContainer = d as DisplayObjectContainer;
         if(interactive != null && d.hasEventListener(MouseEvent.MOUSE_DOWN))
         {
            var bounds:Rectangle = this.poVisibleBounds(d);
            if(bounds != null)
            {
               var clickPoint:Point = this.poButtonPoint(d,bounds.intersection(new Rectangle(0,0,700,550)));
               if(clickPoint == null)
               {
                  return;
               }
               var enabled:Boolean = interactive.mouseEnabled && sc.state != Screen.STATE_CLOSING;
               var ancestor:DisplayObjectContainer = d.parent;
               while(ancestor != null)
               {
                  if(!ancestor.mouseChildren)
                  {
                     enabled = false;
                  }
                  ancestor = ancestor.parent;
               }
               result.push({
                  "id":this.poId(sc) + ":" + d.name,
                  "screen":getQualifiedClassName(sc).split("::").pop(),
                  "name":d.name,
                  "text":this.poText(d),
                  "enabled":enabled,
                  "opening":sc.state == Screen.STATE_OPENING,
                  "bounds":this.poRect(bounds.intersection(new Rectangle(0,0,700,550))),
                  "click_point":this.poPoint(clickPoint),
                  "provenance":"engine_hook"
               });
            }
            return;
         }
         if(container != null)
         {
            var i:int = 0;
            while(i < container.numChildren)
            {
               this.poButtons(container.getChildAt(i),sc,result);
               i++;
            }
         }
      }

      private function poButtonPoint(button:DisplayObject, bounds:Rectangle) : Point
      {
         var fractions:Array = [0.5,0.25,0.75];
         loop0:
         for each(var fx:Number in fractions)
         {
            var _loc11_:int = 0;
            var _loc12_:* = fractions;
            loop1:
            while(true)
            {
               for each(var fy:Number in _loc12_)
               {
                  var p:Point = new Point(bounds.x + bounds.width * fx,bounds.y + bounds.height * fy);
                  var hits:Array = stage.getObjectsUnderPoint(p);
                  if(hits.length != 0)
                  {
                     var top:DisplayObject = hits[hits.length - 1] as DisplayObject;
                     while(top != null)
                     {
                        if(top == button)
                        {
                           break loop1;
                        }
                        top = top.parent;
                     }
                  }
               }
               continue loop0;
            }
            return p;
         }
         return null;
      }

      public function poObserveContact(x:Number, y:Number, vx:Number, vy:Number, radius:Number, damage:Number, target:int, source:Object) : void
      {
         var a:Mc2_Actor = source as Mc2_Actor;
         if(a == null || a.metaInfo == null || damage != 10 || (target & _game.teamFactory.ALLY1_TEAM) == 0)
         {
            return;
         }
         var type:int = a.metaMc2Actor.actorSpecificType;
         var dx:Number = x - int(a.px);
         var dy:Number = y - int(a.py);
         var known:Boolean = a is Mechcomm && !a.isBoss && type != MC2_EnemyManager.MECH_5_TANK && radius == 25 && dx == 0 && dy == 0;
         if(type == MC2_EnemyManager.BOSS_1 && radius == 125 && dx == 0 && dy == 0)
         {
            known = true;
         }
         if(type == MC2_EnemyManager.BOSS_2 && radius == 75 && dx == 0 && (dy == -25 || dy == -125 || dy == -200))
         {
            known = true;
         }
         if(type == MC2_EnemyManager.BOSS_3 && radius == 150 && (dx == 0 && dy == 0 || dx == -200 && dy == -75))
         {
            known = true;
         }
         if(type == MC2_EnemyManager.BOSS_4 && radius == 75 && dx == 0 && dy == 0)
         {
            known = true;
         }
         if(type == MC2_EnemyManager.BOSS_5 && radius == 150 && dx == 0 && dy == 0)
         {
            known = true;
         }
         if(!known)
         {
            return;
         }
         var slot:int = this.poContactCounts[a] == null ? 0 : int(this.poContactCounts[a]);
         this.poContactCounts[a] = slot + 1;
         this.poContacts.push({
            "owner":a,
            "lifetime":a.poLifetime,
            "slot":slot,
            "x":x,
            "y":y,
            "vx":vx,
            "vy":vy,
            "radius":radius
         });
      }

      public function poObserve() : Object
      {
         var result:Object = {
            "player_geometry":null,
            "visible_targets":null,
            "visible_hazards":null,
            "visible_pickups":null,
            "weapon_indicators":null,
            "visible_ui_targets":null
         };
         if(_game == null || ScreenManager.instance == null)
         {
            return result;
         }
         var g:MC2_Game = MC2_Game(_game);
         var ui:Array = [];
         for each(var sc:Screen in ScreenManager.instance.activeFactoryObjects)
         {
            if(sc.visual != null)
            {
               this.poButtons(sc.visual,sc,ui);
            }
         }
         result.visible_ui_targets = ui;
         var h:Mc2_Actor = g.GetPlayer1() as Mc2_Actor;
         var livePlayer:Boolean = h != null && h.metaInfo != null && h.layer != null;
         if(!livePlayer && !this.gwReady)
         {
            return result;
         }
         var r:Rectangle = livePlayer ? this.poVisibleBounds(h.animationContainer) : null;
         var speed:Number = livePlayer && h is Plane && Plane(h).wings != null ? Plane(h).wings.metaWings.speed : 0;
         if(r != null)
         {
            var hero:Object = this.poEntity(h,r);
            hero.destroyed = h.IsDead();
            hero.collision = this.poActorCircle(h);
            hero.velocity = this.poVelocity(h);
            var origin:Point = this.poWorld(h,0,0);
            var edge:Point = this.poWorld(h,speed,0);
            hero.movement_speed = {
               "x":Math.abs(edge.x - origin.x),
               "y":Math.abs(this.poWorld(h,0,speed).y - origin.y),
               "units":"stage_pixels_per_native_frame",
               "native_fps":36,
               "diagonal_axes_independent":true,
               "provenance":"internal"
            };
            var low:Point = this.poWorld(h,g.camera.GetXLeftBorder(h.layer) + 40,g.camera.GetYUpBorder(h.layer) + 40);
            var high:Point = this.poWorld(h,g.camera.GetXRightBorder(h.layer) - 40,g.camera.GetYDownBorder(h.layer) - 40);
            hero.movement_limits = h.mustRespectCamBounds ? {
               "min_x":low.x,
               "max_x":high.x,
               "min_y":low.y,
               "max_y":high.y,
               "max_step_overshoot_x":hero.movement_speed.x,
               "max_step_overshoot_y":hero.movement_speed.y,
               "provenance":"internal"
            } : null;
            result.player_geometry = hero;
         }
         if(g.ingameScreen != null && this.poAttached(g.ingameScreen.visual))
         {
            var primary:DisplayObject = g.ingameScreen.visual.getChildByName("gunCooldown");
            var secondary:DisplayObject = g.ingameScreen.visual.getChildByName("shoulderCooldown");
            if(primary != null && secondary != null && this.poAttached(primary) && this.poAttached(secondary))
            {
               result.weapon_indicators = {
                  "primary":{
                     "value":primary.scaleX,
                     "min":0,
                     "max":1
                  },
                  "secondary":{
                     "value":secondary.scaleX,
                     "min":0,
                     "max":1
                  },
                  "sample_clock":this.poClock(),
                  "provenance":"engine_hook"
               };
            }
         }
         var ev:MC2_LevelEvent = g.levelEventManager.GetCurrentLevelEvent() as MC2_LevelEvent;
         var boss:Boss = ev != null ? ev.boss : null;
         var targets:Array = [];
         var pickups:Array = [];
         var hazards:Array = [];
         var seen:Dictionary = new Dictionary(true);
         var actors:Array = [];
         for each(var a:Mc2_Actor in g.mc2ActorManager.activeFactoryObjects)
         {
            actors.push(a);
         }
         if(boss != null && boss.metaInfo != null)
         {
            actors.push(boss);
            var j:int = 0;
            while(j < boss.numBreakables)
            {
               actors.push(boss.GetBreakablePart(j));
               j++;
            }
         }
         for each(a in actors)
         {
            if(!(a == null || a == h || a.metaInfo == null || a.layer == null || seen[a]))
            {
               seen[a] = true;
               r = this.poVisibleBounds(a.animationContainer);
               if(r != null)
               {
                  var entity:Object = this.poEntity(a,r);
                  entity.collision = this.poActorCircle(a);
                  if(a is Mc2_Item)
                  {
                     entity.type = this.poPickupType(Mc2_Item(a));
                     entity.provenance = "internal";
                     pickups.push(entity);
                  }
                  else if((a.metaMc2Actor.team & g.teamFactory.ENEMY1_TEAM) != 0)
                  {
                     entity.classification = this.poClassification(a,boss);
                     entity.destroyed = a.IsDead();
                     entity.vulnerability = null;
                     entity.provenance = "internal";
                     entity.parent_id = null;
                     entity.component_index = null;
                     if(a is BreakablePart && BreakablePart(a).owner != null)
                     {
                        var owner:Mc2_Actor = BreakablePart(a).owner;
                        entity.parent_id = this.poId(owner);
                        if(owner is Mechcomm)
                        {
                           j = 0;
                           while(j < Mechcomm(owner).numBreakables)
                           {
                              if(Mechcomm(owner).GetBreakablePart(j) == a)
                              {
                                 entity.component_index = j;
                              }
                              j++;
                           }
                        }
                     }
                     targets.push(entity);
                  }
               }
            }
         }
         for each(var ammo:Ammo in g.ammoManager.activeFactoryObjects)
         {
            if(!(ammo.metaInfo == null || ammo.layer == null || ammo.dmg <= 0 || (ammo.GetTargetTeam() & g.teamFactory.ALLY1_TEAM) == 0))
            {
               r = this.poVisibleBounds(ammo.animationContainer);
               var visualOwner:Mc2_Actor = ammo.owner as Mc2_Actor;
               var melee:Boolean = ammo is AmmoMelee;
               if(!melee)
               {
                  if(r != null)
                  {
                     var hazard:Object = this.poEntity(ammo,r);
                     var className:String = getQualifiedClassName(ammo).split("::").pop();
                     var beam:Boolean = className.indexOf("Lazer") >= 0 || className.indexOf("Beam") >= 0;
                     hazard.classification = melee ? "contact" : (beam ? "beam" : "projectile");
                     hazard.owner_id = visualOwner != null && this.poVisibleBounds(visualOwner.animationContainer) != null ? this.poId(visualOwner) : null;
                     hazard.velocity = beam || melee ? null : this.poVelocity(ammo);
                     hazard.collision = beam ? null : this.poCircle(ammo,ammo.px,ammo.py,ammo.rayon);
                     hazard.provenance = "internal";
                     hazards.push(hazard);
                  }
               }
            }
         }
         for each(var area:Object in this.poContacts)
         {
            var contactOwner:Mc2_Actor = area.owner as Mc2_Actor;
            if(!(contactOwner == null || contactOwner.metaInfo == null || contactOwner.poLifetime != area.lifetime || contactOwner.IsDead()))
            {
               r = this.poVisibleBounds(contactOwner.animationContainer);
               if(r != null)
               {
                  var circle:Object = this.poCircle(contactOwner,area.x,area.y,area.radius);
                  var end:Point = this.poWorld(contactOwner,area.x + area.vx,area.y + area.vy);
                  var region:Rectangle = new Rectangle(Math.min(circle.center.x,end.x) - circle.radius,Math.min(circle.center.y,end.y) - circle.radius,Math.abs(end.x - circle.center.x) + 2 * circle.radius,Math.abs(end.y - circle.center.y) + 2 * circle.radius);
                  if(region.intersects(new Rectangle(0,0,700,550)))
                  {
                     circle.shape = "swept_circle";
                     circle.end = this.poPoint(end);
                     hazards.push({
                        "id":this.poId(contactOwner) + ":contact:" + area.slot,
                        "owner_id":this.poId(contactOwner),
                        "classification":"contact",
                        "center":circle.center,
                        "collision":circle,
                        "velocity":this.poVelocity(contactOwner),
                        "bounds":this.poRect(region),
                        "visible_bounds":this.poRect(region.intersection(r).intersection(new Rectangle(0,0,700,550))),
                        "sample_clock":this.poClock(),
                        "provenance":"internal",
                        "geometry_provenance":"internal"
                     });
                  }
               }
            }
         }
         result.visible_targets = targets;
         result.visible_hazards = hazards;
         result.visible_pickups = pickups;
         return result;
      }

// Additions to com/mechcomm2/screens/BriefingScreen.as
      public function gwDismissIntro() : void
      {
         if(state != STATE_IDLE || this._currentConversation == null)
         {
            return;
         }
         while(this._currentConversation.numPhrase > 0)
         {
            this.NextPhrase();
         }
      }

// Additions to com/mechcomm2/screens/TutorialScreen.as
      public function gwDismissIntro() : void
      {
         if(state == STATE_IDLE)
         {
            this.Skip();
         }
      }
