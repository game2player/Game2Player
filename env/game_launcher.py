"""Automatically start a local HTTP server for game folders."""

from __future__ import annotations

import json
import logging
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.parse import unquote, urlsplit

LOGGER = logging.getLogger(__name__)


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class _BoundedReader:
    """Limit a file-like object's reads to one HTTP byte range."""

    def __init__(self, raw: object, remaining: int) -> None:
        self.raw = raw
        self.remaining = remaining

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        if size < 0 or size > self.remaining:
            size = self.remaining
        data = self.raw.read(size)
        self.remaining -= len(data)
        return data

    def close(self) -> None:
        self.raw.close()


class _BundleStore:
    """Read static files directly from one or more ZIP shards."""

    def __init__(self, archives: list[Path]) -> None:
        self.archives: list[zipfile.ZipFile] = []
        self.members: dict[
            str, tuple[zipfile.ZipFile, zipfile.ZipInfo]
        ] = {}
        try:
            for archive_path in archives:
                archive = zipfile.ZipFile(archive_path, "r")
                self.archives.append(archive)
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    raw_name = info.filename.replace("\\", "/").lstrip("/")
                    path = PurePosixPath(raw_name)
                    if path.is_absolute() or ".." in path.parts:
                        raise ValueError(
                            f"Unsafe path in game bundle {archive_path}: "
                            f"{info.filename}"
                        )
                    member_path = f"/{path.as_posix()}"
                    if member_path in self.members:
                        raise ValueError(
                            f"Duplicate game-bundle member: {member_path}"
                        )
                    self.members[member_path] = (archive, info)
        except Exception:
            self.close()
            raise

    def lookup(
        self, request_path: str
    ) -> tuple[zipfile.ZipFile, zipfile.ZipInfo] | None:
        return self.members.get(request_path)

    def close(self) -> None:
        for archive in self.archives:
            archive.close()
        self.archives.clear()
        self.members.clear()


class _QuietHTTPRequestHandler(SimpleHTTPRequestHandler):
    """Static handler with optional per-game MIME metadata.

    HTML5 game exports frequently use compound extensions such as ``.wasm.br``
    and ``.data.unityweb``.  ``SimpleHTTPRequestHandler`` serves those as
    generic binary data, which is enough for many games but breaks some WebGL
    loaders. A game directory can provide ``_headers.json`` with URL-path keys
    and response-header mappings; only safe representation headers are applied.
    """

    def __init__(
        self,
        *args: object,
        directory: str | None = None,
        header_map: dict[str, dict[str, str]] | None = None,
        bundle_store: _BundleStore | None = None,
        **kwargs: object,
    ) -> None:
        self._served_directory = Path(directory).resolve() if directory else None
        self._served_header_map = header_map or {}
        self._served_bundle_store = bundle_store
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def copyfile(self, source: object, outputfile: object) -> None:
        try:
            super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError):
            # Browsers routinely cancel speculative/range requests once an
            # engine has enough data. This is expected and should not emit a
            # server traceback during validation or recording.
            return

    def _metadata_for_request(self) -> dict[str, str]:
        request_path = unquote(urlsplit(self.path).path)
        value = self._served_header_map.get(request_path)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _parse_byte_range(value: str, size: int) -> tuple[int, int]:
        if not value.startswith("bytes=") or "," in value:
            raise ValueError("Only a single bytes range is supported")
        start_text, separator, end_text = value[6:].partition("-")
        if not separator:
            raise ValueError("Malformed byte range")
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
        else:
            suffix_size = int(end_text)
            if suffix_size <= 0:
                raise ValueError("Invalid suffix byte range")
            start = max(0, size - suffix_size)
            end = size - 1
        end = min(end, size - 1)
        if start < 0 or start >= size or end < start:
            raise ValueError("Unsatisfiable byte range")
        return start, end

    def send_head(self) -> object | None:
        filesystem_path = Path(self.translate_path(self.path))
        bundle_store = self._served_bundle_store
        if filesystem_path.exists() or bundle_store is None:
            return super().send_head()

        request_path = unquote(urlsplit(self.path).path)
        if request_path.endswith("/"):
            request_path += "index.html"
        path = PurePosixPath(request_path.lstrip("/"))
        if path.is_absolute() or ".." in path.parts:
            return super().send_head()
        member_path = f"/{path.as_posix()}"
        entry = bundle_store.lookup(member_path)
        if entry is None:
            return super().send_head()

        archive, info = entry
        size = info.file_size
        range_value = self.headers.get("Range")
        byte_range: tuple[int, int] | None = None
        if range_value:
            try:
                byte_range = self._parse_byte_range(range_value, size)
            except (TypeError, ValueError):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return None

        try:
            source = archive.open(info, "r")
            if byte_range:
                source.seek(byte_range[0])
        except (KeyError, OSError, zipfile.BadZipFile):
            self.send_error(500, "Unable to read packaged game asset")
            return None

        status = 206 if byte_range else 200
        response_size = (
            byte_range[1] - byte_range[0] + 1
            if byte_range
            else size
        )
        virtual_path = (
            self._served_directory / Path(*path.parts)
            if self._served_directory is not None
            else Path(*path.parts)
        )
        self.send_response(status)
        self.send_header("Content-Type", self.guess_type(str(virtual_path)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(response_size))
        if byte_range:
            self.send_header(
                "Content-Range",
                f"bytes {byte_range[0]}-{byte_range[1]}/{size}",
            )
        self.end_headers()
        if byte_range:
            return _BoundedReader(source, response_size)
        return source

    def guess_type(self, path: str) -> str:
        metadata: dict[str, str] = {}
        if self._served_directory is not None:
            try:
                relative = Path(path).resolve().relative_to(self._served_directory)
                metadata = self._served_header_map.get(f"/{relative.as_posix()}", {})
            except ValueError:
                metadata = {}
        content_type = metadata.get("Content-Type") if isinstance(metadata, dict) else None
        return content_type or super().guess_type(path)

    def end_headers(self) -> None:
        # Content-Type is supplied through guess_type. Content-Encoding is
        # intentionally excluded: captured browser response bodies are already
        # decoded, even when the upstream URL ended in .br/.gz.
        for name, value in self._metadata_for_request().items():
            if name.lower() in {"content-type", "content-length", "content-encoding"}:
                continue
            if isinstance(value, str) and "\n" not in value and "\r" not in value:
                self.send_header(name, value)
        super().end_headers()


def append_url_suffix(base_url: str, suffix: str | None) -> str:
    """Append a task URL suffix.

    Catalog tasks should use explicit `?query=value` or `#fragment` forms.
    Bare query strings are still tolerated for ad hoc CLI overrides.
    """
    clean_suffix = str(suffix or "").strip()
    if not clean_suffix:
        return base_url

    if clean_suffix.startswith("#"):
        return f"{base_url}{clean_suffix}"

    if clean_suffix.startswith(("?", "&")):
        clean_suffix = clean_suffix[1:]

    if "=" in clean_suffix:
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}{clean_suffix}"

    return f"{base_url}{clean_suffix}"


class GameLauncher:
    """Manages HTTP server for serving game files."""

    DEFAULT_HTML = "index.html"
    _REPO_ROOT = Path(__file__).resolve().parents[1]
    DEFAULT_BASE_DIR = _REPO_ROOT / "games" / "benchmark"

    def __init__(
        self,
        game_name: str,
        port: Optional[int] = None,
        base_dir: Path | str | None = None,
        html_file: Optional[str] = None,
    ):
        """Initialize a launcher for one local game directory."""
        self.game_name = game_name
        # Without a port the OS picks a free one at start, so concurrent games never
        # collide; callers that need a stable origin pass one explicitly.
        self.port = port
        self.html_file = html_file or self.DEFAULT_HTML
        self.server: _ReusableThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.bundle_store: _BundleStore | None = None

        self.game_dir = self.resolve_game_directory(game_name, base_dir=base_dir)
        self.base_dir = Path(base_dir) if base_dir is not None else self.DEFAULT_BASE_DIR

    @classmethod
    def resolve_game_directory(
        cls,
        game_name: str,
        base_dir: Path | str | None = None,
    ) -> Path:
        """Resolve a game folder from an explicit base directory or the runtime root."""
        root = Path(base_dir) if base_dir is not None else cls.DEFAULT_BASE_DIR
        return root / game_name

    def start(self) -> str:
        """
        Start the HTTP server for the game.

        Returns:
            URL to access the game (e.g., "http://127.0.0.1:8101/index.html")
        """
        if not self.game_dir.exists():
            raise FileNotFoundError(
                f"Game directory not found: {self.game_dir}. Base dir: {self.base_dir}"
            )

        LOGGER.info("Starting local game server: %s (port=%s)", self.game_name, self.port or "any")

        header_map: dict[str, dict[str, str]] = {}
        headers_file = self.game_dir / "_headers.json"
        if headers_file.is_file():
            try:
                raw_headers = json.loads(headers_file.read_text(encoding="utf-8"))
                if isinstance(raw_headers, dict):
                    header_map = {
                        str(path): {
                            str(name): str(value)
                            for name, value in values.items()
                        }
                        for path, values in raw_headers.items()
                        if isinstance(values, dict)
                    }
            except (OSError, ValueError, TypeError):
                LOGGER.warning("Ignoring invalid game header metadata: %s", headers_file)

        bundle_archives = sorted(self.game_dir.glob("_bundle_*.zip"))
        if bundle_archives:
            try:
                self.bundle_store = _BundleStore(bundle_archives)
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                raise RuntimeError(
                    f"Unable to open packaged game assets in {self.game_dir}"
                ) from exc

        handler = partial(
            _QuietHTTPRequestHandler,
            directory=str(self.game_dir),
            header_map=header_map,
            bundle_store=self.bundle_store,
        )
        try:
            self.server = _ReusableThreadingHTTPServer(
                ("127.0.0.1", int(self.port or 0)),
                handler,
            )
        except OSError as exc:
            if self.bundle_store:
                self.bundle_store.close()
                self.bundle_store = None
            raise RuntimeError(
                f"Port {self.port} is already in use. Pass a different --port."
            ) from exc
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name=f"game-server-{self.port}",
            daemon=True,
        )
        self.thread.start()

        url = f"http://127.0.0.1:{self.port}/{self.html_file}"
        LOGGER.info("Game server started at %s", url)

        return url

    def stop(self) -> None:
        """Stop the HTTP server."""
        if not self.server:
            return
        LOGGER.info("Stopping local game server for %s", self.game_name)
        self.server.shutdown()
        if self.thread:
            self.thread.join(timeout=5)
        self.server.server_close()
        if self.bundle_store:
            self.bundle_store.close()
            self.bundle_store = None
        self.server = None
        self.thread = None
