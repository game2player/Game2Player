// Review extract of authored helper methods only; compiled recipe is used at install.

// Added bridge methods in StateModule.as
      private function skyVisible(d:DisplayObject) : Boolean
      {
         if(!d || !d.stage)
         {
            return false;
         }
         var a:DisplayObject = d;
         while(a)
         {
            if(!a.visible || a.alpha <= 0)
            {
               return false;
            }
            a = a.parent;
         }
         var r:Rectangle = d.getBounds(stage);
         return r.width > 0 && r.height > 0 && r.intersects(new Rectangle(0,0,640,480));
      }

      private function skyFind(d:DisplayObject, kind:String, result:Array) : void
      {
         if(!skyVisible(d))
         {
            return;
         }
         if(getQualifiedClassName(d).split("::").pop() == kind)
         {
            result.push(d);
         }
         var c:DisplayObjectContainer = d as DisplayObjectContainer;
         if(c)
         {
            var i:int = 0;
            while(i < c.numChildren)
            {
               skyFind(c.getChildAt(i),kind,result);
               i++;
            }
         }
      }

      private function skyBounds(d:DisplayObject) : Object
      {
         var r:Rectangle = d.getBounds(stage).intersection(new Rectangle(0,0,640,480));
         return {
            "x":r.x,
            "y":r.y,
            "width":r.width,
            "height":r.height
         };
      }

      private function skyTargetBounds(d:DisplayObject) : Object
      {
         if(!(d is SimpleButton) || !SimpleButton(d).hitTestState)
         {
            return skyBounds(d);
         }
         var hit:DisplayObject = SimpleButton(d).hitTestState;
         var r:Rectangle = hit.getBounds(hit);
         var p:Point = d.localToGlobal(hit.transform.matrix.transformPoint(new Point(r.x,r.y)));
         var q:Point = d.localToGlobal(hit.transform.matrix.transformPoint(new Point(r.right,r.bottom)));
         return {
            "x":p.x,
            "y":p.y,
            "width":q.x - p.x,
            "height":q.y - p.y
         };
      }

      private function skyContext(d:DisplayObject, label:String) : String
      {
         if(label == "X")
         {
            return "Close character details";
         }
         if(label == "Equipment slot")
         {
            var owner:DisplayObject = d.parent;
            loop0:
            while(owner)
            {
               var _loc7_:int = 0;
               var _loc8_:* = ["equipSlot1","equipSlot2","equipSlot3","equipSlot4"];
               do
               {
                  for each(var slot:String in _loc8_)
                  {
                  }
                  owner = owner.parent;
                  continue loop0;
               }
               while(!(slot in owner && owner[slot] === d.parent));
               return "Equipment slot " + slot.substr(9);
            }
         }
         if(label == "+")
         {
            var a:DisplayObject = d.parent;
            loop2:
            while(a)
            {
               _loc7_ = 0;
               _loc8_ = ["STRAddButton","WILAddButton","AGIAddButton","WISAddButton"];
               do
               {
                  for each(var key:String in _loc8_)
                  {
                  }
                  a = a.parent;
                  continue loop2;
               }
               while(!(key in a && a[key] === d));
               return "Increase " + key.substr(0,3);
            }
         }
         return label;
      }

      private function skyWalk(d:DisplayObject, path:String, targets:Array, values:Array) : void
      {
         if(!skyVisible(d))
         {
            return;
         }
         var kind:String = getQualifiedClassName(d).split("::").pop();
         if(kind == "PlayView" || kind == "SoundView" || kind == "TransitionView" || kind == "TooltipView" && path.indexOf("tooltip/") != 0)
         {
            return;
         }
         if("skyUI" in d)
         {
            var meta:Object = d["skyUI"]();
            if(meta.kind && this.facade.hasMediator(meta.kind))
            {
               var m:Object = this.facade.retrieveMediator(meta.kind);
               if("skyUI" in m)
               {
                  meta = m["skyUI"]();
               }
            }
            var label:String = skyContext(d,String(meta.label));
            var enabled:Boolean = Boolean(meta.enabled);
            var a:DisplayObject = d;
            while(a)
            {
               if(a is InteractiveObject && a === d && !InteractiveObject(a).mouseEnabled)
               {
                  enabled = false;
               }
               if(a.parent && !a.parent.mouseChildren)
               {
                  enabled = false;
               }
               a = a.parent;
            }
            targets.push({
               "id":path,
               "label":label,
               "bounds":skyTargetBounds(d),
               "enabled":enabled,
               "selected":Boolean(meta.selected),
               "provenance":"internal"
            });
            if(label && label != "+" && kind != "MapButton" && kind != "ItemButton" && kind != "ItemEquipView")
            {
               values.push({
                  "id":path + "/label",
                  "text":String(meta.label),
                  "bounds":skyBounds(d),
                  "provenance":"internal"
               });
            }
            return;
         }
         if(d is TextField)
         {
            var t:TextField = d as TextField;
            if(t.text.replace(/\s/g,"").length > 0)
            {
               values.push({
                  "id":path,
                  "text":t.text,
                  "bounds":skyBounds(t),
                  "provenance":"internal"
               });
            }
         }
         var c:DisplayObjectContainer = d as DisplayObjectContainer;
         if(c)
         {
            var i:int = 0;
            while(i < c.numChildren)
            {
               skyWalk(c.getChildAt(i),path + "/" + i,targets,values);
               i++;
            }
         }
      }

      public function skyReadVisibleUI() : Object
      {
         if(!stage)
         {
            return null;
         }
         var targets:Array = [];
         var values:Array = [];
         var modals:Array = [];
         var tips:Array = [];
         var sound:Array = [];
         var covers:Array = [];
         var overlays:Array = [];
         var fades:Array = [];
         skyFind(stage,"TransitionView",fades);
         var fading:Boolean = fades.length > 0;
         for each(var fade:Object in fades)
         {
            if(fade.basicBackground.alpha >= 0.9999)
            {
               return {
                  "session":String(loaderInfo.parameters.gwSession),
                  "screen":this.skyReadState().screen,
                  "paused":false,
                  "fading":true,
                  "blackout":true,
                  "covered_bounds":[],
                  "overlay_bounds":[],
                  "targets":[],
                  "values":[]
               };
            }
         }
         skyFind(stage,"SubCharProfileView",modals);
         skyFind(stage,"PauseView",modals);
         var paused:Boolean = false;
         if(modals.length > 0)
         {
            var modal:DisplayObject = modals[modals.length - 1];
            paused = getQualifiedClassName(modal).split("::").pop() == "PauseView";
            skyWalk(modal,"modal",targets,values);
            covers.push(skyBounds(modal));
         }
         else
         {
            skyWalk(stage,"stage",targets,values);
         }
         skyFind(stage,"TooltipView",tips);
         var i:int = 0;
         while(i < tips.length)
         {
            skyWalk(tips[i],"tooltip/" + i,targets,values);
            overlays.push(skyBounds(tips[i]));
            i++;
         }
         skyFind(stage,"SoundView",sound);
         for each(var s:Object in sound)
         {
            targets = targets.concat(s["skyVisibleSoundTargets"]());
         }
         return {
            "session":String(loaderInfo.parameters.gwSession),
            "screen":this.skyReadState().screen,
            "paused":paused,
            "fading":fading,
            "blackout":false,
            "covered_bounds":covers,
            "overlay_bounds":overlays,
            "targets":targets,
            "values":values
         };
      }

      public function skyReadState() : Object
      {
         var c:CharProxy = this.facade.retrieveProxy(CharProxy.NAME) as CharProxy;
         var screen:String = "splash";
         if(this.facade.hasMediator("TitleMediator"))
         {
            screen = "title";
         }
         if(this.facade.hasMediator("LoungeMediator"))
         {
            screen = "lounge";
         }
         if(this.facade.hasMediator("PlayMediator"))
         {
            screen = "playing";
         }
         if(this.facade.hasMediator("SummaryMediator"))
         {
            screen = "summary";
         }
         return {
            "session":String(loaderInfo.parameters.gwSession),
            "screen":screen,
            "type":c.vo.type,
            "stage":c.vo.currentStage,
            "health":c.vo.currentHealth,
            "maxHealth":c.vo.healthPoint,
            "magic":c.vo.currentMagic,
            "maxMagic":c.vo.magicPoint,
            "spirit":c.vo.currentSpirit,
            "rank":c.vo.rank,
            "STR":c.vo.STR,
            "WIL":c.vo.WIL,
            "AGI":c.vo.AGI,
            "WIS":c.vo.WIS,
            "coins":c.vo.coins,
            "exp":c.vo.exp,
            "score":c.vo.score,
            "equips":c.vo.equips,
            "items":c.vo.items,
            "stagesUnlocked":c.vo.stagesUnlocked,
            "hasFinished":c.vo.hasFinished
         };
      }

      public function skyStart(kind:String, stageName:String) : Object
      {
         if(!this.facade.hasMediator("TitleMediator"))
         {
            throw new Error("Fresh start requires title");
         }
         if(["Warrior","Hunter","Paladin","BattleMage"].indexOf(kind) < 0 || ["STAGE_01","STAGE_02","STAGE_03"].indexOf(stageName) < 0)
         {
            throw new Error("Unknown task variant");
         }
         var c:CharProxy = this.facade.retrieveProxy(CharProxy.NAME) as CharProxy;
         c.create(kind);
         c.vo.currentStage = stageName;
         if(c.vo.stagesUnlocked.indexOf(stageName) < 0)
         {
            c.vo.stagesUnlocked.push(stageName);
         }
         c.save();
         Config.instance.newGame = false;
         this.facade.sendNotification(StateCommand.PLAY);
         return this.skyReadState();
      }

// Added bridge methods in buttons/ItemButton.as
      public function skyUI() : Object
      {
         return {
            "kind":this.type,
            "label":this.type,
            "enabled":enabled,
            "selected":false
         };
      }

// Added bridge methods in buttons/MapButton.as
      public function skyUI() : Object
      {
         return {
            "label":"Stage " + int(this.type.substr(6)).toString(),
            "enabled":!this.locked,
            "selected":this.selected
         };
      }

// Added bridge methods in com/soybeansoft/display/ComplexButton.as
      public function skyUI() : Object
      {
         return {
            "label":this._text,
            "enabled":enabled && !this._lock,
            "selected":this._select
         };
      }

// Added bridge methods in play/view/PlayObjectsMediator.as
      public function skyReadVisibleCombat() : Object
      {
         var hazards:Array = [];
         var enemiesVisible:Array = [];
         var pickupsVisible:Array = [];
         var h:Hero = this.playObjects[this.heroId] as Hero;
         var magicVisible:Boolean = false;
         for each(var o:PlayObject in this.playObjects)
         {
            var g:Object = o.skyVisibleGeometry();
            if(g)
            {
               if(o is Obstacle)
               {
                  var e:Enemy = o as Enemy;
                  g.kind = "obstacle";
                  g.damaging = h != null && !h.isInvincible && e.damage > 0;
                  hazards.push(g);
               }
               else if(o is Enemy)
               {
                  e = o as Enemy;
                  g.kind = e.enemyType;
                  g.target_bounds = g.collision;
                  g.contact_bounds = g.collision;
                  g.targetable = !e.invincible;
                  g.contact_damaging = e.kamikaze && e.damage > 0 && h != null && !h.isInvincible;
                  delete g.collision;
                  enemiesVisible.push(g);
               }
               else if(o is Bullet && Bullet(o).ownerType == PlayObjectTypeValues.ENEMY)
               {
                  var b:Bullet = o as Bullet;
                  g.kind = "hostile_projectile";
                  g.damaging = b.damage > 0 && h != null && !h.isInvincible;
                  hazards.push(g);
               }
               else if(o is Pickup)
               {
                  var p:Pickup = o as Pickup;
                  g.kind = p.pickupType;
                  g.collection_bounds = g.collision;
                  delete g.collision;
                  pickupsVisible.push(g);
               }
               if(o is MagicBullet)
               {
                  magicVisible = true;
               }
            }
         }
         var indicators:Object = h ? h.skyVisibleIndicators() : null;
         if(indicators && magicVisible)
         {
            indicators.spell_active = true;
         }
         return {
            "session":skySession,
            "visible_hazards":hazards,
            "visible_enemy_geometry":enemiesVisible,
            "visible_pickups":pickupsVisible,
            "player_indicators":indicators
         };
      }

      public function skyReadPlay() : Object
      {
         var v:Object = this.playProxy.vo;
         var h:Hero = this.playObjects[this.heroId] as Hero;
         var targets:Array = [];
         for each(var o:PlayObject in this.playObjects)
         {
            if(o is Enemy && targets.length < 64)
            {
               var e:Enemy = o as Enemy;
               targets.push({
                  "id":e.id,
                  "type":e.enemyType,
                  "hp":e.skyHealth(),
                  "x":e.position.x,
                  "y":e.position.y,
                  "invincible":e.invincible
               });
            }
         }
         return {
            "session":skySession,
            "stage":PlayFacade.getPlay().charVO.currentStage,
            "type":PlayFacade.getPlay().charVO.type,
            "score":v.score,
            "nativeRemovals":v.kills,
            "hits":v.hits,
            "shots":v.shots,
            "casts":v.casts,
            "coins":v.coins,
            "exp":v.exp,
            "isVictory":v.isVictory,
            "isEndGame":v.isEndGame,
            "isDelayToEnd":v.isDelayToEnd,
            "time":v.currentTime,
            "waves":this.levelProxy.waves.size,
            "bossPhaseIndex":this.bossPhaseIndex,
            "targets":targets,
            "heroPresent":h != null,
            "x":(h ? h.position.x : -1),
            "y":(h ? h.position.y : -1),
            "hp":(h ? h.currentHealth : 0),
            "maxHp":(h ? h.totalHealth : 0),
            "mp":(h ? h.currentMagic : 0),
            "maxMp":(h ? h.totalMagic : 0),
            "spirit":(h ? h.currentSpirit : 0),
            "spiritFraction":(h ? h.spiritPerc : 0),
            "spiritReady":(h ? h.hasEnoughSpirit : false)
         };
      }

// Added bridge methods in play/view/component/PauseView.as
      public function skyReadPause() : Object
      {
         return {
            "session":skySession,
            "open":parent != null && visible,
            "ready":parent != null && visible && Math.abs(y - 140) < 0.1
         };
      }

// Added bridge methods in play/view/playobjects/Enemy.as
      public function skyHealth() : int
      {
         return health;
      }

// Added bridge methods in play/view/playobjects/Hero.as
      public function skyVisibleIndicators() : Object
      {
         return {
            "spirit_active":this.isSpiritMode,
            "temporary_protection":this.isInvincible,
            "spell_active":this.currentSpellTime > 0,
            "provenance":"internal"
         };
      }

// Added bridge methods in play/view/playobjects/PlayObject.as
      public function skyVisibleGeometry() : Object
      {
         if(!isRenderable || !renderHelper || !renderHelper.isVisible())
         {
            return null;
         }
         var p:Point = renderHelper.getPoint();
         var r:Rectangle = renderHelper.getRect();
         var image:Rectangle = new Rectangle(p.x,p.y,r.width,r.height);
         if(!image.intersects(new Rectangle(0,0,640,480)))
         {
            return null;
         }
         var h:Rectangle = hitbox;
         return {
            "id":id,
            "position":{
               "x":position.x,
               "y":position.y
            },
            "collision":{
               "shape":"rectangle",
               "x":h.x,
               "y":h.y,
               "width":h.width,
               "height":h.height
            },
            "rendered_bounds":{
               "x":image.x,
               "y":image.y,
               "width":image.width,
               "height":image.height
            },
            "provenance":"internal"
         };
      }

// Added bridge methods in shell/view/component/SoundView.as
      public function skyVisibleSoundTargets() : Array
      {
         var result:Array = [];
         var b:Object = this.musicOnButton.visible ? this.musicOnButton : this.musicOffButton;
         var r:Rectangle = b.getBounds(stage);
         result.push({
            "id":"sound/music",
            "label":"Music",
            "enabled":true,
            "selected":this.musicOnButton.visible,
            "bounds":{
               "x":r.x,
               "y":r.y,
               "width":r.width,
               "height":r.height
            },
            "provenance":"internal"
         });
         b = this.effectOnButton.visible ? this.effectOnButton : this.effectOffButton;
         r = b.getBounds(stage);
         result.push({
            "id":"sound/effects",
            "label":"Sound effects",
            "enabled":true,
            "selected":this.effectOnButton.visible,
            "bounds":{
               "x":r.x,
               "y":r.y,
               "width":r.width,
               "height":r.height
            },
            "provenance":"internal"
         });
         return result;
      }

// Added bridge methods in shell/view/component/TransitionView.as
      public function skyReadTransition() : Object
      {
         return {
            "session":skySession,
            "active":parent != null
         };
      }

// Added bridge methods in state/view/ItemShopMediator.as
      public function skyUI() : Object
      {
         return {
            "label":this.proxy.vo.title,
            "enabled":this.purchased || this.status == CHEAP,
            "selected":false
         };
      }

// Added bridge methods in state/view/component/ItemEquipView.as
      public function skyUI() : Object
      {
         return {
            "label":"Equipment slot",
            "enabled":this.hasEventListener("click"),
            "selected":false
         };
      }
