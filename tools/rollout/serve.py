"""Local model servers and the machine checks a rollout makes before using resources.

A model whose profile has `serve` runs as one vLLM server per GPU the rollout is
given. Only GPUs with no compute process and almost no memory in use are taken, and
only this rollout's own server processes are ever stopped.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from catalog.models._base import ModelProfile

GPU_BUSY_MIB = 1000
SERVER_READY_TIMEOUT_S = 20 * 60


def log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {message}", flush=True)


def gpu_states() -> dict[int, dict]:
    """index -> {uuid, memory_mib, processes} for every GPU nvidia-smi reports."""
    rows = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,uuid,memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    apps = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid", "--format=csv,noheader"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    busy_uuids: dict[str, int] = {}
    for line in apps:
        uuid = line.split(",")[0].strip()
        if uuid:
            busy_uuids[uuid] = busy_uuids.get(uuid, 0) + 1
    states = {}
    for line in rows:
        index, uuid, memory = (part.strip() for part in line.split(","))
        states[int(index)] = {"uuid": uuid, "memory_mib": int(memory),
                              "processes": busy_uuids.get(uuid, 0)}
    return states


def gpu_idle(state: dict) -> bool:
    return state["processes"] == 0 and state["memory_mib"] < GPU_BUSY_MIB


def require_idle_gpus(gpus: list[int], *, wait: bool, settle_s: float = 30.0) -> None:
    """Return once every GPU is idle (for `settle_s` in a row when waiting); without
    `wait`, fail at once on a busy one."""
    idle_since: float | None = None
    while True:
        states = gpu_states()
        missing = [gpu for gpu in gpus if gpu not in states]
        if missing:
            raise RuntimeError(f"no such GPU: {missing}")
        busy = {gpu: states[gpu] for gpu in gpus if not gpu_idle(states[gpu])}
        if not busy:
            if not wait:
                return
            idle_since = idle_since or time.monotonic()
            if time.monotonic() - idle_since >= settle_s:
                return
        else:
            if not wait:
                raise RuntimeError(f"GPUs in use by other processes: {busy}; pass --wait-idle to wait")
            idle_since = None
        time.sleep(5)


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


@dataclass
class Server:
    gpu: int
    port: int
    process: subprocess.Popen
    log_path: Path

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/chat/completions"

    def stop(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=60)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()


def server_command(profile: ModelProfile, *, vllm: str, port: int, weights: str | None,
                   thinking: bool) -> list[str]:
    serve = profile.serve
    if serve is None:
        raise ValueError(f"{profile.model_name} is not a locally served model")
    command = [vllm, "serve", str(Path(weights).expanduser()) if weights else serve.weights,
               "--served-model-name", profile.model,
               "--host", "127.0.0.1", "--port", str(port)]
    if serve.revision and not weights:
        command += ["--revision", serve.revision]
    command += list(serve.args)
    if thinking:
        command += list(serve.thinking_args)
    return command


def start_server(profile: ModelProfile, *, gpu: int, device: str, port: int, vllm: str,
                 weights: str | None, thinking: bool, log_dir: Path) -> Server:
    """Serve on `gpu` (an nvidia-smi index), selected for CUDA as `device`. CUDA leaves
    out a GPU that has failed and renumbers the rest, so an index alone can land on
    another card; a UUID cannot."""
    if not port_free(port):
        raise RuntimeError(f"port {port} for the GPU {gpu} server is in use")
    command = server_command(profile, vllm=vllm, port=port, weights=weights, thinking=thinking)
    vllm_bin = str(Path(vllm).resolve().parent) if os.sep in vllm else ""
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=device, TOKENIZERS_PARALLELISM="false")
    if vllm_bin:
        env["PATH"] = vllm_bin + os.pathsep + env.get("PATH", "")
    if "CUDA_HOME" not in env and Path("/usr/local/cuda/bin").is_dir():
        # Kernels some models compile at load time need nvcc.
        env["CUDA_HOME"] = "/usr/local/cuda"
        env["PATH"] = env["PATH"] + os.pathsep + "/usr/local/cuda/bin"
    log_path = log_dir / f"vllm_gpu{gpu}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"$ {' '.join(command)}\n")
        handle.flush()
        process = subprocess.Popen(command, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
    server = Server(gpu=gpu, port=port, process=process, log_path=log_path)
    log(f"GPU {gpu}: starting {profile.model_name} on port {port} (log {log_path})")
    deadline = time.monotonic() + SERVER_READY_TIMEOUT_S
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
                raise RuntimeError(f"vLLM on GPU {gpu} exited ({process.returncode}):\n{tail}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=5) as response:
                    if response.status == 200:
                        log(f"GPU {gpu}: ready")
                        return server
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(5)
    except BaseException:  # interrupted while starting: do not leave it holding the GPU
        server.stop()
        raise
    server.stop()
    raise RuntimeError(f"vLLM on GPU {gpu} was not ready after {SERVER_READY_TIMEOUT_S} s")


__all__ = [
    "Server",
    "gpu_states",
    "log",
    "port_free",
    "require_idle_gpus",
    "server_command",
    "start_server",
]
