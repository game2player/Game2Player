"""Apply the same task bridge to the author's unchanged Demo #10 gameplay."""
from pathlib import Path
import sys


def replace_once(root, relative, before, after):
    path = root / relative
    text = path.read_text(encoding="utf-8")
    if text.count(before) != 1:
        raise ValueError(f"Unexpected original anchor in {relative}")
    path.write_text(text.replace(before, after, 1), encoding="utf-8")


def main():
    root = Path(sys.argv[1])
    replace_once(root, "index.html", '</html>', '  <script src="game_api.js"></script>\n</html>')
    path = root / "index.html"
    text = path.read_text(encoding="utf-8")
    text = text.replace('<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>', '')
    path.write_text(text, encoding="utf-8")
    replace_once(root, "src/loaders/data.js", 'let saved = localStorage.getItem("savedGameState");',
                 'const requestedTaskStart = new URLSearchParams(window.location.search).has("task");\n'
                 'let saved = requestedTaskStart ? null : localStorage.getItem("savedGameState");')
    replace_once(root, "src/loaders/data.js", 'const useSpreadsheet = navigator.onLine && env === "web";',
                 'const useSpreadsheet = false; // Use the bundled tables so every run is self-contained.')


if __name__ == "__main__":
    main()
