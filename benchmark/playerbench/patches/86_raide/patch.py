"""Apply the reference adapter's small textual injections to the original export."""
from pathlib import Path
import sys


def replace_once(text, anchor, replacement):
    if text.count(anchor) != 1:
        raise ValueError(f"Expected one patch anchor: {anchor[:90]!r}")
    return text.replace(anchor, replacement, 1)


def main(root):
    here = Path(__file__).resolve().parent
    bundle = root / "html5game/PlantsPlantsPlants.js"
    text = bundle.read_text(encoding="utf-8-sig")
    anchor = "function gml_Object_oGameReturn_Create_0(_inst,_other){"
    text = replace_once(text, anchor, anchor + "instance_destroy(_inst);return;")
    # Reuse the original initial progress array as the fallback.
    anchor = "_inst.gmlLEVEL_CUR=0;_inst.gmlLEVEL=0;_inst.gmlLEVELS=__yy_gml_array_create("
    prefix = "(window.__gwStartLevels&&window.__gwStartLevels.length===9)?window.__gwStartLevels.slice(0):"
    text = replace_once(text, anchor, anchor + prefix)
    begin = text.index(anchor) + len(anchor) + len(prefix)
    end = text.index(");", begin)
    empty_levels = text[begin:end]
    anchor = "gml_Script_scr_LOAD_init(_inst,_other);_inst.gmlTOOL_STATE_old="
    text = replace_once(text, anchor,
        "gml_Script_scr_LOAD_init(_inst,_other);"
        "_inst.gmlSTAGE=0;_inst.gmlLEVEL=0;_inst.gmlLEVEL_CUR=0;"
        "_inst.gmlRUSH_SCORE=0;_inst.gmltutorial=false;"
        "_inst.gmlLEVELS=__yy_gml_array_create(" + prefix + empty_levels + ");"
        "_inst.gmlTOOL_STATE_old=")
    anchor = "randomize();room_goto_next();}function gml_Object_oGame_Alarm_0"
    text = replace_once(text, anchor,
        "randomize();" + (here / "boot.js").read_text().strip() +
        "}function gml_Object_oGame_Alarm_0")
    anchor = "function gml_Object_oGame_Step_0(_inst,_other){"
    text = replace_once(text, anchor, anchor + (here / "step.js").read_text().strip())
    bundle.write_text(text, encoding="utf-8")

    index = root / "index.html"
    text = index.read_text()
    text = replace_once(text, '<html lang="en">', '<html data-game-id="86_raide" lang="en">')
    text = replace_once(text, "            :-webkit-full-screen #canvas {",
        "            #canvas {\n"
        "                width: min(100vw, 100vh) !important;\n"
        "                height: min(100vw, 100vh) !important;\n"
        "            }\n            :-webkit-full-screen #canvas {")
    text = replace_once(text, "</head>", '<script defer src="game_api.js"></script>\n</head>')
    # Itch's host-page integration has no role in the standalone offline game.
    text = replace_once(text,
        '<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>', "")
    index.write_text(text)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
