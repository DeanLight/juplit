"""A Jupyter kernel that outlives the CLI invocation that started it.

Agents call a CLI once per tool use, so "try a cell, look at the result, try again"
needs a kernel that survives between calls. There is no Jupyter server here: juplit
launches the kernelspec itself and records the connection in `.juplit/kernels/`, so this
works on a login node, in CI and in an ephemeral sandbox.

Two things about the launch are load-bearing, both verified:

* The kernelspec's `argv[0]` is a bare `"python"`, which inside a virtualenv resolves to
  the *system* interpreter — no ipykernel, kernel dead at launch. It has to be rewritten
  to `sys.executable`, the same substitution `jupyter_client` makes internally.
* `jupyter_client` sets `JPY_PARENT_PID` to the launching process, and ipykernel then
  kills the kernel when that process exits. Overriding it to `1` is what makes the
  kernel detached; without it the kernel does not survive to the first re-attach.
"""

import json
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

from nbformat import NotebookNode
from nbformat.v4 import new_output

from juplit.tasks import _repo_root

DEFAULT_NAME = "default"
DEFAULT_TIMEOUT = 300.0
READY_TIMEOUT = 30.0
LIVENESS_TIMEOUT = 5.0
UNIX_SOCKET_LIMIT = 90          # sun_path is ~104 bytes; leave room for the channel suffix


def kernels_dir() -> Path:
    return _repo_root() / ".juplit" / "kernels"


def session_path(name: str = DEFAULT_NAME) -> Path:
    """`.juplit/kernels/<name>.json` — pid, connection file, cwd and kernel name."""
    return kernels_dir() / f"{name}.json"


def _read_session(name: str) -> dict | None:
    path = session_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _client(session: dict, timeout: float):
    """A connected BlockingKernelClient for a recorded session. Caller stops the channels."""
    from jupyter_client import BlockingKernelClient

    client = BlockingKernelClient(connection_file=session["connection_file"])
    client.load_connection_file()
    client.start_channels()
    client.wait_for_ready(timeout=timeout)
    return client


def _connection_info(name: str, conn_file: Path) -> dict:
    """Connection details for a new kernel: unix sockets where the path is short enough."""
    info = {
        "key": uuid.uuid4().hex,
        "signature_scheme": "hmac-sha256",
        "kernel_name": name,
    }
    socket_base = kernels_dir() / f"{name}-{uuid.uuid4().hex[:8]}"
    if os.name == "posix" and len(str(socket_base)) < UNIX_SOCKET_LIMIT:
        # ipc keeps the kernel off a world-visible TCP port, which matters on a shared
        # login node; a long tmp path would blow the sun_path limit, so fall back to tcp.
        info |= {"transport": "ipc", "ip": str(socket_base)}
        ports = range(1, 6)
    else:
        info |= {"transport": "tcp", "ip": "127.0.0.1"}
        ports = _free_ports(5)
    info |= {f"{channel}_port": port
             for channel, port in zip(("shell", "iopub", "stdin", "control", "hb"), ports)}
    return info


def _free_ports(count: int) -> list[int]:
    import socket

    sockets = []
    for _ in range(count):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        sockets.append(s)
    ports = [s.getsockname()[1] for s in sockets]
    for s in sockets:
        s.close()
    return ports


def _kernel_argv(kernel: str, conn_file: Path) -> list[str]:
    from jupyter_client.kernelspec import KernelSpecManager, NoSuchKernel

    try:
        spec = KernelSpecManager().get_kernel_spec(kernel)
    except NoSuchKernel:
        raise RuntimeError(
            f"kernel {kernel!r} is not installed — `pip install ipykernel`"
        ) from None
    argv = [a.replace("{connection_file}", str(conn_file)) for a in spec.argv]
    bare = {"python", f"python{sys.version_info[0]}",
            f"python{sys.version_info[0]}.{sys.version_info[1]}"}
    if argv and argv[0] in bare:
        argv[0] = sys.executable
    return argv


def start(name: str = DEFAULT_NAME, kernel: str = "python3", cwd: Path | None = None) -> dict:
    """Launch a kernel that outlives this process. Reuses a live one of the same name."""
    if alive(name):
        return _read_session(name)

    kernels_dir().mkdir(parents=True, exist_ok=True)
    conn_file = kernels_dir() / f"{name}-connection.json"
    conn_file.write_text(json.dumps(_connection_info(name, conn_file)))
    argv = _kernel_argv(kernel, conn_file)
    log = kernels_dir() / f"{name}.log"

    with open(log, "w") as log_file:
        process = subprocess.Popen(
            argv,
            env={**os.environ, "JPY_PARENT_PID": "1"},
            start_new_session=True,
            cwd=str(cwd or _repo_root()),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

    session = {
        "name": name,
        "pid": process.pid,
        "kernel": kernel,
        "cwd": str(cwd or _repo_root()),
        "connection_file": str(conn_file),
    }
    session_path(name).write_text(json.dumps(session, indent=2))
    try:
        _client(session, READY_TIMEOUT).stop_channels()
    except Exception:
        stop(name)
        raise RuntimeError(
            f"kernel {name!r} died before it was ready — see {log}:\n{log.read_text()[-500:]}"
        ) from None
    return session


def alive(name: str = DEFAULT_NAME) -> bool:
    """True if the recorded pid exists and the kernel answers within a short timeout."""
    session = _read_session(name)
    if session is None:
        return False
    try:
        os.kill(session["pid"], 0)
    except OSError:
        return False
    try:
        _client(session, LIVENESS_TIMEOUT).stop_channels()
    except Exception:
        return False
    return True


def status() -> list[dict]:
    """One row per recorded session: name, pid, cwd, kernel, alive."""
    if not kernels_dir().exists():
        return []
    rows = []
    for path in sorted(kernels_dir().glob("*.json")):
        if path.name.endswith("-connection.json"):
            continue
        session = _read_session(path.stem)
        if session is not None:
            rows.append(session | {"alive": alive(session["name"])})
    return rows


def stop(name: str = DEFAULT_NAME) -> bool:
    """Shut a kernel down and forget it. True if there was a session to stop.

    We did not spawn it in this process, so there is no KernelManager to ask: the pid in
    the session file and the control channel are all we have.
    """
    session = _read_session(name)
    if session is None:
        return False
    try:
        client = _client(session, LIVENESS_TIMEOUT)
        client.shutdown()
        client.stop_channels()
    except Exception:
        try:
            os.kill(session["pid"], signal.SIGTERM)
        except OSError:
            pass

    for path in (session_path(name), Path(session["connection_file"])):
        path.unlink(missing_ok=True)
    for socket_file in kernels_dir().glob(f"{name}-*"):
        if not socket_file.name.endswith(".json"):
            socket_file.unlink(missing_ok=True)
    return True


def stop_all() -> list[str]:
    """Stop every recorded session. `juplit clean` calls this — nothing else reaps them."""
    return [row["name"] for row in status() if stop(row["name"])]


def execute(code: str, name: str = DEFAULT_NAME,
            timeout: float = DEFAULT_TIMEOUT) -> list[NotebookNode]:
    """Run code on the session kernel and return its outputs. Touches no file.

    A failed attempt therefore exists only in the kernel's history and in this return
    value — never in the notebook.
    """
    session = _read_session(name)
    if session is None:
        raise RuntimeError(f"no kernel {name!r} — run `juplit kernel start`")
    try:
        os.kill(session["pid"], 0)
    except OSError:
        raise RuntimeError(
            f"kernel {name!r} is gone (pid {session['pid']}) — run `juplit kernel start`"
        ) from None

    try:
        client = _client(session, LIVENESS_TIMEOUT)
    except Exception:
        raise RuntimeError(
            f"kernel {name!r} is wedged — run `juplit kernel stop --name {name}`"
        ) from None

    outputs: list[NotebookNode] = []
    msg_id = client.execute(code)
    try:
        while True:
            message = client.get_iopub_msg(timeout=timeout)
            if message["parent_header"].get("msg_id") != msg_id:
                continue
            output = _to_output(message)
            if output is not None:
                outputs.append(output)
            if (message["msg_type"] == "status"
                    and message["content"]["execution_state"] == "idle"):
                return outputs
    except Exception as exc:
        if type(exc).__name__ != "Empty":                # queue.Empty means the timeout hit
            raise
        _interrupt(session)
        raise RuntimeError(
            f"execution exceeded {timeout}s and was interrupted; "
            f"partial output: {[o.get('output_type') for o in outputs]}"
        ) from None
    finally:
        client.stop_channels()


def _interrupt(session: dict) -> None:
    """SIGINT the kernel's process group — it is its own group (start_new_session)."""
    try:
        os.killpg(session["pid"], signal.SIGINT)
    except OSError:
        pass


def _to_output(message: dict) -> NotebookNode | None:
    """Map one IOPub message to an nbformat output node, or None if it carries no output."""
    kind, content = message["msg_type"], message["content"]
    if kind == "stream":
        return new_output("stream", name=content["name"], text=content["text"])
    if kind in ("execute_result", "display_data"):
        return new_output(kind, data=content["data"], metadata=content.get("metadata", {}))
    if kind == "error":
        return new_output("error", ename=content["ename"], evalue=content["evalue"],
                          traceback=content["traceback"])
    return None
