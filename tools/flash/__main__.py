"""Extract the local Ruffle runtime or forward arguments to shared FFDec."""

import argparse
from pathlib import Path
import sys
import zipfile

from tools.engine_tools import run_backend


RUFFLE_DISTRIBUTION = Path(__file__).with_name("vendor") / "ruffle-0.4.1-web-selfhosted.zip"


def install_ruffle(directory: Path) -> Path:
    """Copy the complete official self-hosted distribution into a new directory."""
    with zipfile.ZipFile(RUFFLE_DISTRIBUTION) as archive:
        directory.mkdir(parents=True)
        archive.extractall(directory)
    return directory


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["ruffle-runtime"]:
        parser = argparse.ArgumentParser(
            prog="python -m tools.flash ruffle-runtime",
            description="Extract bundled Ruffle 0.4.1 for offline browser playback.",
        )
        parser.add_argument("directory", type=Path, help="new game-local runtime directory")
        options = parser.parse_args(args[1:])
        try:
            print(install_ruffle(options.directory))
        except (OSError, zipfile.BadZipFile) as exc:
            print(f"ruffle-runtime: {exc}", file=sys.stderr)
            return 2
        return 0
    return run_backend("ffdec", args)


if __name__ == "__main__":
    raise SystemExit(main())
