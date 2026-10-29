# Third-party notices

Third-party tools remain the work of their respective authors and contributors.
This repository's entry points do not change their ownership or license. Original
license and notice files inside the distributions are retained.

The browser runtime was initially derived from
[GameWorld](https://github.com/gameworld-project/GameWorld), by Mingyu Ouyang,
Siyuan Hu, Kevin Qinghong Lin, Hwee Tou Ng and Mike Zheng Shou.
See [GameWorld: Towards Standardized and Verifiable Evaluation of Multimodal Game Agents](https://arxiv.org/abs/2604.07429).
We acknowledge its browser control, game-state and task-evaluation foundations.
This repository does not distribute the GameWorld game corpus.

Game2Player model checkpoints are fine-tuned from
[Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) and
[Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B), released by the Qwen team
under Apache-2.0.

The two games under `benchmark/playerbench/originals/` were authored by our team
and are released under Apache-2.0. Third-party game titles and source links in the
benchmark identify the research tasks. The corresponding game builds and assets
are not distributed here, and their rights remain with their respective owners.

| Component | Version | Source | License / location |
| --- | --- | --- | --- |
| Il2CppDumper | 6.7.46 | [Perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) | MIT. `tools/unity_webgl/vendor/il2cppdumper/LICENSE` |
| Mono.Cecil | 0.11.4 | [jbevain/cecil](https://github.com/jbevain/cecil/tree/0.11.4) | MIT. `tools/unity_webgl/vendor/il2cppdumper/LICENSE.Mono.Cecil.txt` |
| Ruffle | 0.4.1 | [upstream release](https://github.com/ruffle-rs/ruffle/releases/tag/v0.4.1) | MIT OR Apache-2.0. `tools/flash/vendor/LICENSE_MIT`, `tools/flash/vendor/LICENSE_APACHE`, and original README/licenses in the self-hosted ZIP |
| JPEXS FFDec | 26.3.0 | [upstream release](https://github.com/jindrapetrik/jpexs-decompiler/releases/tag/version26.3.0) | GPL-3.0-or-later. `tools/flash/vendor/LICENSE` and notices in the distribution |
| Godot RE Tools | 2.7.4 | [upstream release](https://github.com/GDRETools/gdsdecomp/releases/tag/v2.7.4) | MIT. `tools/godot/vendor/LICENSE` and notices in the distribution |
| UnityPy | 1.25.4 | [K0lb3/UnityPy](https://github.com/K0lb3/UnityPy) | MIT. installed Python dependency, license supplied in its package |
| Shrinko8 / shrinko | 1.2.7.1 | [thisismypassport/shrinko8](https://github.com/thisismypassport/shrinko8) | MIT. installed Python dependency, license supplied in its package |

The unmodified Ruffle web self-hosted ZIP is bundled for offline playback.
[provenance and reproduction instructions](tools/flash/vendor/RUFFLE.md) record
its fixed download URL and SHA-256. Its source is at the linked release and
[v0.4.1 tag](https://github.com/ruffle-rs/ruffle/tree/v0.4.1). Both upstream license
texts, package metadata and README remain inside the distribution and are copied
into each game's runtime directory.

FFDec source code for the bundled release is available from its linked release
and [version26.3.0 tag](https://github.com/jindrapetrik/jpexs-decompiler/tree/version26.3.0).
GDRETools includes Godot and other components. their original notices accompany
its distribution. Installing Python dependencies also installs their own license
metadata. Java and .NET runtimes are not redistributed by this repository.
