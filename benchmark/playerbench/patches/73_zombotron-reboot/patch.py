"""Adapt the pinned public Unity page for the existing offline PlayerBench wrapper."""
from pathlib import Path
import sys


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Unexpected upstream HTML anchor: {old[:70]!r}")
    return text.replace(old, new, 1)


def main(root):
    path = root / "index.html"
    text = path.read_text()
    text = replace_once(text, '<html lang="en-us">',
                        '<html data-game-id="73_zombotron-reboot" lang="en-us">')
    text = replace_once(text, '    <link rel="manifest" href="manifest.webmanifest">',
                        '    <script defer src="game_api.js"></script>')
    # The reference uses a plain local page without itch's parent-frame integration
    # or Unity's optional PWA cache. Neither participates in gameplay.
    start = text.index('      window.addEventListener("load", function () {')
    end = text.index('      var container =', start)
    text = text[:start] + text[end:]
    text = replace_once(text,
        '<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>', '')
    # Load synchronously, as in the reference, so the existing runtime bridge can
    # observe the instance; preserve the public config and loading UI.
    text = replace_once(text, '    <script>\n',
                        '    <script src="Build/Zombotron.loader.js"></script>\n    <script>\n')
    start = text.index('      var script = document.createElement("script");')
    end = text.index('      document.body.appendChild(script);', start)
    end += len('      document.body.appendChild(script);')
    text = text[:start] + '''      createUnityInstance(canvas, config, function (progress) {
        progressBarFull.style.width = 100 * progress + "%";
      }).then(function (unityInstance) {
        window.unityInstance = unityInstance;
        loadingBar.style.display = "none";
      }).catch(function (message) {
        console.error(message);
      });''' + text[end:]
    path.write_text(text)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch.py GAME_DIRECTORY")
    main(Path(sys.argv[1]))
