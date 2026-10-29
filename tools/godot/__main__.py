"""Forward GDRE command-line arguments in headless mode."""

import sys
from pathlib import Path

from tools.engine_tools import run_backend


_PATH_OPTIONS = {
    "--recover", "--extract", "--list-files", "--compile", "--decompile",
    "--pck-create", "--pck-patch", "--output", "--embed", "--pck",
    "--csharp-assembly", "--load-custom-bytecode", "--custom-decryption-script",
    "--dump-bytecode-versions", "--txt-to-bin", "--bin-to-txt",
}


def _absolute_paths(argv: list[str]) -> list[str]:
    """GDRE changes directory internally; resolve local paths before launching it."""
    result = []
    for argument in argv:
        option, separator, value = argument.partition("=")
        if separator and value and option in _PATH_OPTIONS:
            argument = f"{option}={Path(value).expanduser().resolve()}"
        elif separator and option in {"--patch-file", "--patch-translations"}:
            source, destination_separator, destination = value.partition("=")
            if source and destination_separator:
                argument = f"{option}={Path(source).expanduser().resolve()}={destination}"
        result.append(argument)
    return result


def main(argv: list[str] | None = None) -> int:
    return run_backend("gdre", _absolute_paths(list(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    raise SystemExit(main())
