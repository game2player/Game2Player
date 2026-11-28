"""Reproduce the existing HIT8OX instrumentation; no upstream code/assets bundled.

Uses shrinko8 to decode/re-encode the embedded cartridge. All graphics, maps,
flags, music and sounds are retained from the downloaded ROM unchanged.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError(f"Expected {count} occurrences of {old!r}")
    return text.replace(old, new)


def shrinko(work, *args):
    subprocess.run([sys.executable, "-m", "shrinko.shrinko8", *args],
                   cwd=work, check=True, capture_output=True, text=True)


def patch_cart(s):
    s = replace(s, "function _init()eH()",
                "function _init()gw_ser,gw_fk,gw_fm,gw_fv,gw_blk,gw_win=0,0,0,0,0,0 eH()")
    s = replace(s, "poke(24366,1)end function _update()",
                'poke(24366,1)gw_ac={}for i,n in ipairs(split"idle,punch1,punch1_2_idle,punch2_2_idle,kick1,kick2,enter_block,block,exit_block,hit1,hit2,hit3,dead,won_transition,won",",",false)do gw_ac[n]=i end end function _update()')
    s = replace(s, "function eE()", "function eE()gw_blk,gw_win=0,0 ")
    s = replace(s, "function n6()M,f=H,O eE()end",
                'function n6()M,f=H,O eE()gw_ser+=1\ngw_fk,gw_fm,gw_fv=k,M,f=="solo"and 4or 5\nend')
    s = replace(s, 'if d[o]=="block"then s[o]-=.5',
                'if d[o]=="block"then\nif o==1then gw_blk+=1end\ns[o]-=.5', 2)
    return replace(s, "function _update()ni=stat(31)a=time()end",
        'function _update()ni=stat(31)a=time()\n'
        'local q,g=@24465,@24466\n'
        'if @24464>0and q>=1and q<=3and g>=1and g<=3then k,H,O=q,g,"solo"poke(24464,0)n6()w,h=1,4 end\n'
        'if en and gw_win<1then gw_win=s[1]>0and 1or 2 end\n'
        'poke(24448,72,s[1]*2,s[2]*2,k,H,M,en and 1or 0,({menu=1,editor=2,selector=3,solo=4,versus=5})[f],min(u[1]*100,255),min(u[2]*100,255),min(gw_blk,255),gw_ser%256,gw_win,gw_fk,gw_fm,gw_fv)\n'
        'poke(24468,h)\n'
        'poke2(24480,e[1][1]*10,e[1][3]*10,e[2][1]*10,e[2][3]*10)poke(24488,gw_ac[d[1]]or 0,t[1],F[1],gw_ac[d[2]]or 0,t[2],F[2])\nend')


def main(root):
    js = root / "hit8ox.js"
    text = js.read_text(encoding="utf-8")
    matches = list(re.finditer(r"var _cartdat\s*=\s*\[([0-9,\s]*)\]", text))
    if len(matches) != 1:
        raise ValueError("Expected exactly one embedded cartridge bank")
    match = matches[0]
    rom = bytes(int(n) for n in match[1].split(",") if n.strip())
    if len(rom) != 32768:
        raise ValueError("Expected one 32 KiB cartridge")
    with tempfile.TemporaryDirectory(prefix="hit8ox-") as temporary:
        work = Path(temporary)
        (work / "cart.rom").write_bytes(rom)
        shrinko(work, "cart.rom", "cart.p8", "-F", "rom", "-f", "p8")
        source = work / "cart.p8"
        source.write_text(patch_cart(source.read_text(encoding="utf-8")), encoding="utf-8")
        shrinko(work, "cart.p8", "patched.rom", "-f", "rom")
        patched = (work / "patched.rom").read_bytes()
    if len(patched) != len(rom) or patched[:0x4300] != rom[:0x4300]:
        raise ValueError("Repacking changed cartridge assets")
    text = text[:match.start(1)] + ",".join(map(str, patched)) + text[match.end(1):]
    # Existing reference raises both the comparison and the printed limit.
    text = replace(text, "if((o|0)>8192){Fg(50811,6);c[i>>2]=o;c[i+4>>2]=8192;",
                   "if((o|0)>65535){Fg(50811,6);c[i>>2]=o;c[i+4>>2]=65535;")
    js.write_text(text, encoding="utf-8")
    html = root / "index.html"
    text = html.read_text(encoding="utf-8")
    text = replace(text, '<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>',
                   '<script src="game_api.js"></script>')
    text = replace(text, "</STYLE>",
        '#p8_frame_0{max-width:none !important;max-height:none !important;}\n'
        '#p8_frame{max-width:100vw !important;max-height:100vh !important;height:100vh !important;}\n'
        '#p8_menu_buttons,#p8_menu_buttons_touch{display:none !important;}\n'
        'body{overflow:hidden;background-color:#000 !important;}\n</STYLE>')
    html.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
