# Ruffle web runtime

Unmodified official self-hosted distribution: Ruffle 0.4.1.
Copyright (c) 2018 Ruffle LLC and Ruffle contributors.
License: MIT OR Apache-2.0. Both license texts are alongside this file and inside
the ZIP. The upstream README, package metadata, source maps and licenses remain
in the archive and are extracted together with the JavaScript and WASM files.

- Release: https://github.com/ruffle-rs/ruffle/releases/tag/v0.4.1
- Source: https://github.com/ruffle-rs/ruffle/tree/v0.4.1
- Archive: https://github.com/ruffle-rs/ruffle/releases/download/v0.4.1/ruffle-0.4.1-web-selfhosted.zip
- Size: 10,268,173 bytes.
- SHA-256: `d33c135beea75c83183da1938a49ee3f08c2f7a2eb2ae8f3ce43de842412aae6`.

The archive is the official release file and is used offline. From the repository
root, extract it into a new folder of your game:

```bash
python -m tools.flash ruffle-runtime games/benchmark/1_my-game/ruffle
```

Load `ruffle/ruffle.js` from the game's HTML and keep the other extracted files
beside it. The Flash skill shows the launcher pattern.

To check the bundled archive against upstream:

```bash
mkdir -p .cache
curl --fail --location \
  https://github.com/ruffle-rs/ruffle/releases/download/v0.4.1/ruffle-0.4.1-web-selfhosted.zip \
  --output .cache/ruffle-0.4.1-web-selfhosted.zip
printf 'd33c135beea75c83183da1938a49ee3f08c2f7a2eb2ae8f3ce43de842412aae6  .cache/ruffle-0.4.1-web-selfhosted.zip\n' | sha256sum --check
```
