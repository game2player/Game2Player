# Workflow

## Confirm the runtime and package

Confirm the page contains the PICO-8 player lifecycle such as `p8_run_cart`,
`p8_is_running`, `pico8_state`, or `pico8_gpio`; controls and 128×128 rendering
alone are not proof. Capture the real HTML export, cart payload, player JavaScript,
audio, and any dynamic assets. Validate from a cold browser with external requests
blocked.

Start through the player's existing entry point. Hide only shell controls that
obstruct automation; do not bypass cartridge gameplay. Running state plus an
advancing `pico8_state.frame_number` is stronger readiness evidence than a timeout.

## Expose semantic state through GPIO

PICO-8 GPIO indices 0–127 correspond to cartridge memory at `0x5f80` onward.
Readable cart source may publish values with `poke`, `poke2`, or `poke4`; packed
multi-byte writes occupy consecutive pins. If instrumentation is necessary, patch
the smallest stable source point where the game already updates the semantic value.

For each field:

- record the pin, width, signedness or packing rule;
- locate the cartridge-side write, not just the JavaScript-side read;
- cause one controlled event and show the expected before/after pin values;
- distinguish current episode state from persistent bests accumulated by the
  adapter.

Useful fields include room/level, score, objective counters, health, inventory,
death, and win. Do not export the entire GPIO bank or call unchanged zeros state.

## Patch cart code

An export stores every cart as a 32 KiB ROM in `var _cartdat=[...]`, named in order by
`var _cartname=[...]`; the code inside each ROM is compressed. Edit it through the
shared tool:

```bash
python -m tools.pico8 unpack games/benchmark/<game_id>/<player>.js <cache>/carts
# edit <cache>/carts/<cart>.p8
python -m tools.pico8 pack games/benchmark/<game_id>/<player>.js <cache>/carts
python -m tools.pico8 check <player>.js --against <player before the edit>.js
```

A player inside a bundle reads as `<game_dir>/_bundle_001.zip:<player>.js`; pack it
with `--out`. `pack` re-encodes only the edited carts and writes nothing when a check
fails:

- a cart must stay within the player's token limit and 15616 bytes of compressed code.
  The token limit (8192) is one comparison in the exported player, not a property of
  the cart, and only charges memory: `python -m tools.pico8 lift-token-limit
  <player>.js` raises it to 65535. Lift it
  rather than deleting game code to make room; the compressed size then is the real
  limit, since that is the cart's code space.
- a name the code still reads must keep its definition. Carts often create objects
  from string names (`[[c="name"]]`), so deleting such a block while a use survives
  elsewhere loads fine and crashes at the first action that reaches the use. When a
  removal is intended, check every use, then pass `--allow-undefined NAME`.

After packing, replay an action that reaches each edited code path: a cart that boots
and accepts input has not shown that its edited functions run.

## Reset and task starts

Reload is the safe default reset. Clear only cartridge-owned persistence when a
clean episode requires it. A task-specific start may use an existing level select,
documented cart flag, or narrowly reviewed source hook, but it must confirm the
requested room/mode after startup and must not pre-complete the objective.

## Evaluate

With networking blocked, verify cart startup, advancing frames, real input, a
corresponding GPIO transition, and two resets. Apply the Builder's common task
contract rules. A JavaScript adapter that initializes `pico8_gpio` or reads pins
which the cartridge never writes is unsupported evidence.
