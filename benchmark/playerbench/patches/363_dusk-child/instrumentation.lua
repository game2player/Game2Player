
--gw: environment instrumentation.
--state is published on gpio (0x5f80+pin) every
--update; pin 120 takes a start command
--(1 fresh game, 2 awakened at the tablet),
--pin 121 its nonce, echoed on pin 122.
function gw_put(i,v)
 poke(0x5f80+i,v)
end
function gw_put2(i,v)
 v=max(0,flr(v))
 gw_put(i,v%256)
 gw_put(i+1,flr(v/256)%256)
end
function gw_bits(t)
 local v,b=0,1
 for i=1,count(t) do
  if (t[i]) v+=b
  b*=2
 end
 return v
end

function gw_clear()
 gw_f=0 gw_vis={} gw_nvis=0
 gw_deaths=0 gw_wasdead=false
 gw_l={false,false,false,false,
       false,false,false,false}
 gw_m={false,false}
end
gw_clear()

function gw_reset(c)
 reload(0,0,0x4300)
 gw_vars()
 actors={} parts={}
 inv=0 aniframe=1 anitime=0 time=0
 respawning=false checkpointing=false
 _init()
 --same as pressing a button on the title
 addtext(" ")
 addtext(" ")
 addtext("you finally arrive...")
 addtext("why were you drawn here?")
 sfx(1)
 fadeout=16
 if c==2 then
  --the moment the tablet awakens you:
  --west keystone set, power granted
  wopen=true cansuper=true
  for o in all(actors) do
   if o.t==10 and not o.east then
    o.a=1
   end
   if o.t==9 and o.x==792 and o.y==80 then
    o.x=9999
   end
   if o.t==11 then
    o.a=12 o.b=0 o.c=0
   end
  end
  --feet just above the floor, where the cutscene leaves you
  px=322 py=439
  --west temple mirror
  cx=224 cy=424
  checkwopen=true
  checkpointing=true
 end
 gw_clear()
end

function gw_pub()
 gw_f+=1
 if (dead and not gw_wasdead) gw_deaths+=1
 gw_wasdead=dead
 local rx,ry=flr(px/128),flr(py/112)
 local m=2
 if (cutscene) m=4
 if (gametitle) m=0
 if (fadeout>=0) m=1
 if (dead) m=3
 if (gameover) m=5
 if not gametitle and rx>=0 and rx<8 and ry>=0 and ry<4 then
  local r=ry*8+rx+1
  if not gw_vis[r] then
   gw_vis[r]=true
   gw_nvis+=1
  end
 end
 local it=0
 if (inv!=0) it=actors[inv].t
 if (it==3) gw_l[1]=true
 if (it==9) gw_l[2]=true
 if (it==4) gw_l[3]=true
 local nw,dmax,tab,door,ni=0,0,0,0,0
 for o in all(actors) do
  if (o.t==7 and o.a==0) nw+=1
  if o.t==6 and o.b==0 then
   for s in all(actors) do
    if s.t==7 and s.a==0 and s.woff==o.woff and s.yoff==o.yoff then
     dmax=max(dmax,o.a)
     if (o.a>=16) gw_l[5]=true
    end
   end
  end
  if (o.t==11) tab=o.a
  if (o.t==12) door=o.a
  if ni<8 and (o.t==3 or o.t==4 or o.t==9) then
   local p=32+ni*5
   gw_put2(p,o.x) gw_put2(p+2,o.y)
   local f=o.t
   if (o.a==1) f+=32
   if (o.c>0) f+=64
   gw_put(p+4,f)
   ni+=1
  end
 end
 if (nw>0) gw_l[4]=true
 if (wopen or eopen) gw_l[6]=true
 if (wlit) gw_l[7]=true
 if (elit) gw_l[8]=true
 if (wopen) gw_m[1]=true
 if (eopen) gw_m[2]=true
 gw_put(0,68) gw_put(1,1)
 gw_put2(2,gw_f)
 gw_put(4,m)
 gw_put(5,rx) gw_put(6,ry)
 gw_put2(7,px) gw_put2(9,py)
 gw_put(11,gw_bits({wopen,wlit,eopen,elit,cansuper,super,dead,gameover}))
 gw_put(12,it)
 gw_put(13,gw_bits({cutscene,cring,pg==1}))
 gw_put(14,gw_nvis)
 for k=0,3 do
  local v,b=0,1
  for j=1,8 do
   if (gw_vis[k*8+j]) v+=b
   b*=2
  end
  gw_put(15+k,v)
 end
 gw_put(19,tab)
 gw_put(20,door)
 gw_put(21,min(gw_deaths,255))
 gw_put(22,gw_bits(gw_l))
 gw_put(23,gw_bits({gw_m[1],gw_m[2],cansuper,gameover}))
 gw_put(24,nw)
 gw_put(25,dmax)
 gw_put(31,ni)
end

gw_upd=_update
function _update()
 local c=peek(0x5ff8)
 if c==1 or c==2 then
  gw_reset(c)
  poke(0x5ff8,0)
  poke(0x5ffa,peek(0x5ff9))
 end
 gw_upd()
 gw_pub()
end
