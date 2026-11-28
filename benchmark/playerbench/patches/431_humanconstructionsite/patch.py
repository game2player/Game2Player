"""Load the existing external task adapter before the native RPG Maker entrypoint."""
from pathlib import Path
import sys

root = Path(sys.argv[1])
entry = root / 'index.html'
data = entry.read_bytes()
anchor = b'        <script type="text/javascript" src="js/main.js"></script>'
if data.count(anchor) != 1 or b'src="game_api.js"' in data:
    raise ValueError('Unexpected original entrypoint')
newline = b'\r\n' if b'\r\n' in data else b'\n'
injected = b'        <script type="text/javascript" src="game_api.js"></script>' + newline
entry.write_bytes(data.replace(anchor, injected + anchor))

# itch.io appends this hosting telemetry script; it is unnecessary offline.
data = entry.read_bytes()
telemetry = b'<script defer src="https://static.itch.io/htmlgame.js" type="text/javascript"></script>'
if data.count(telemetry) != 1:
    raise ValueError('Unexpected itch.io hosting script')
entry.write_bytes(data.replace(telemetry + newline, b'').replace(telemetry, b''))

# Preserve the reference's Chromium/software WebGL compatibility adjustment.
pixi = root / 'js/libs/pixi.js'
data = pixi.read_bytes()
anchor = b'var contextOptions = { stencil: true, failIfMajorPerformanceCaveat: true };'
replacement = b'var contextOptions = { stencil: true, failIfMajorPerformanceCaveat: false };'
if data.count(anchor) != 1:
    raise ValueError('Unexpected Pixi WebGL support check')
pixi.write_bytes(data.replace(anchor, replacement))
