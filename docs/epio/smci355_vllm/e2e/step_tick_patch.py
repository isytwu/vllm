#!/usr/bin/env python3
"""step_tick_patch.py: run INSIDE the container before vLLM starts (e2e/serve.sh does when YT_STEP_TICK_PORT is set).

GPUModelRunner.execute_model then sends one UDP datagram to <host>:<YT_STEP_TICK_PORT + its GPU index> for every host
of YT_STEP_TICK_HOSTS (127.0.0.1) as it starts each step (serve.sh: DP rank 0's workers only, the DP ranks step
together): the start of the decode's EP-free stretch between steps (the CPU enters a step ~3 ms after the
previous step's last MoE combine and ~6 ms before the next dispatch; e1x_7112's profiles). mb/io_inject.py
--gate-port moves KV only then. Without YT_STEP_TICK_PORT in a worker's environment the call returns at once.
Idempotent: patches the image's vllm/v1/worker/gpu_model_runner.py in the job's container once.
"""
import importlib.util
import os
import sys

MARK = "# yt-step-tick"
# Worker.execute_model (vllm/v1/worker/gpu_worker.py): every step passes there whichever model runner the worker uses
# (K3.1 runs the V2 one, vllm/v1/worker/gpu/model_runner.py), and the profiler's execute_context range starts there
SIG = """    def execute_model(
        self, scheduler_output: "SchedulerOutput"
    ) -> ModelRunnerOutput | AsyncModelRunnerOutput | None:
"""
NEW = SIG + """        _yt_step_tick(self.local_rank)  """ + MARK + "\n"
HELPER = '''


''' + MARK + ''': e2e/step_tick_patch.py
import os as _yt_os  # noqa: E402
import socket as _yt_socket  # noqa: E402

_YT_TICK_PORT = int(_yt_os.environ.get("YT_STEP_TICK_PORT") or 0)
_YT_TICK_HOSTS = [h for h in (_yt_os.environ.get("YT_STEP_TICK_HOSTS") or "127.0.0.1").split(",") if h]
_YT_TICK_SOCK = None


def _yt_step_tick(local_rank):
    global _YT_TICK_SOCK
    if not _YT_TICK_PORT:
        return
    if _YT_TICK_SOCK is None:
        _YT_TICK_SOCK = _yt_socket.socket(_yt_socket.AF_INET, _yt_socket.SOCK_DGRAM)
        _YT_TICK_SOCK.setblocking(False)
    for h in _YT_TICK_HOSTS:
        try:
            _YT_TICK_SOCK.sendto(b"t", (h, _YT_TICK_PORT + int(local_rank or 0)))
        except OSError:
            pass
'''


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        importlib.util.find_spec("vllm").submodule_search_locations[0], "v1", "worker", "gpu_worker.py")
    src = open(path).read()
    if MARK in src:
        print(f"step tick: {path} already patched")
        return
    if src.count(SIG) != 1:
        sys.exit(f"step tick: execute_model's start not found exactly once in {path}")
    open(path, "w").write(src.replace(SIG, NEW, 1) + HELPER)
    print(f"step tick: patched {path}")


if __name__ == "__main__":
    main()
