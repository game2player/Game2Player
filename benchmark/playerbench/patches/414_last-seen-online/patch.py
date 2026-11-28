"""Insert the reference adapter's five read-only native observation hooks."""
from pathlib import Path
import sys


def replace_once(text, anchor, replacement):
    if text.count(anchor) != 1:
        raise ValueError(f"Expected one upstream anchor: {anchor!r}")
    return text.replace(anchor, replacement, 1)


def main(root):
    bundle = root / "assets/index-DJvlyFN9.js"
    text = bundle.read_text()
    # Insert hooks without replacing any original game code or state transitions.
    anchor = "return(0,m.useEffect)(()=>{o.addMessage&&o.addMessage((0,h.jsx)(Q,{name:`OTHER_WORLD_DOOR_PUZZLE_FOUR_PORTRAITS`}))},[])"
    hook = "(0,m.useEffect)(()=>{window.gameAPI&&window.gameAPI.__onSceneReady&&window.gameAPI.__onSceneReady(`garden`)},[]),"
    text = replace_once(text, anchor, "return" + hook + anchor[len("return"):])

    anchor = "(0,m.useEffect)(()=>{r.setNoiseIntensity&&r.setNoiseIntensity(.5)});let[i,a]=(0,m.useState)(0)"
    hook = "(0,m.useEffect)(()=>{window.gameAPI&&window.gameAPI.__onSceneReady&&window.gameAPI.__onSceneReady(`fountain2`)},[]),"
    text = replace_once(text, anchor, hook + anchor)

    anchor = "l(n),eh.default.isEqual(n,[`f`,`o`,`r`,`e`,`v`,`e`,`r`])"
    hook = "window.gameAPI&&window.gameAPI.__onFountainInput&&window.gameAPI.__onFountainInput(n),"
    text = replace_once(text, anchor, "l(n)," + hook + anchor[len("l(n),"):])

    anchor = "(0,m.useEffect)(()=>{l.fov=A===`landscape`?75:100,l.updateProjectionMatrix()},[l,A])"
    hooks = "(0,m.useEffect)(()=>{window.gameAPI&&window.gameAPI.__onGardenState&&window.gameAPI.__onGardenState(C)},[C]),"
    hooks += "(0,m.useEffect)(()=>{window.gameAPI&&window.gameAPI.__onSceneReady&&window.gameAPI.__onSceneReady(n)},[n]),"
    text = replace_once(text, anchor, hooks + anchor)
    bundle.write_text(text)

    entry = root / "index.html"
    text = entry.read_text()
    anchor = '    <script type="module" crossorigin src="./assets/index-DJvlyFN9.js"></script>'
    text = replace_once(text, anchor, '    <script src="./game_api.js"></script>\n' + anchor)
    text = replace_once(text, '<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>', "")
    entry.write_text(text)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
