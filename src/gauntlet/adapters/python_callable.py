"""Parent-enforced subprocess adapter for injected Python callables."""

from __future__ import annotations

import copy
import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, NoReturn, TypeAlias, cast

from pydantic import JsonValue

from gauntlet.adapters.protocol import (
    MAX_MESSAGE_BYTES,
    JsonObject,
    ProtocolMessageError,
    decode_reply,
    encode_message,
    request_message,
)
from gauntlet.adapters.tools import ToolRegistry

_TERMINATION_GRACE_SECONDS = 1.0
_STDERR_CAPTURE_BYTES = 1024 * 1024


class AdapterError(RuntimeError):
    """Base error for subprocess adapter failures."""


class AdapterClosedError(AdapterError):
    """Raised when an operation is attempted after adapter cleanup."""


class AdapterTimeoutError(AdapterError):
    """Raised after the parent terminates a child that missed its deadline."""

    def __init__(self, operation: str, timeout_seconds: float, stderr: str) -> None:
        super().__init__(
            f"Adapter operation {operation!r} exceeded its {timeout_seconds:g}-second deadline"
        )
        self.operation = operation
        self.timeout_seconds = timeout_seconds
        self.stderr = stderr


class AdapterProcessError(AdapterError):
    """Raised when the adapter child exits or its pipes fail."""

    def __init__(self, message: str, *, stderr: str) -> None:
        super().__init__(message)
        self.stderr = stderr


class AdapterProtocolError(AdapterError):
    """Raised when the child violates the JSONL protocol."""


class AdapterChildError(AdapterError):
    """A structured application or operation error returned by the child."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: JsonObject,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details
        self.retryable = retryable


@dataclass(frozen=True)
class _StreamEof:
    pass


@dataclass(frozen=True)
class _StreamFailure:
    message: str


_StdoutItem: TypeAlias = bytes | _StreamEof | _StreamFailure


class PythonCallableAdapter:
    """Run a ``run(payload, *, tools)`` callable in a persistent child process."""

    isolation_level = "subprocess"

    def __init__(
        self,
        target: str,
        *,
        project_root: Path,
        timeout_seconds: float = 10.0,
        tool_sequence: Sequence[JsonObject] | None = None,
        seed: int | None = None,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
    ) -> None:
        if not isinstance(target, str) or not target.strip():
            raise ValueError("target must be a non-blank 'module:callable' string")
        root = Path(project_root).resolve()
        if not root.is_dir():
            raise ValueError(f"project_root is not a directory: {root}")
        if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds):
            raise ValueError("timeout_seconds must be a finite positive number")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        if isinstance(seed, bool) or (seed is not None and not isinstance(seed, int)):
            raise ValueError("seed must be an integer or null")
        if (
            isinstance(max_message_bytes, bool)
            or not isinstance(max_message_bytes, int)
            or max_message_bytes <= 0
        ):
            raise ValueError("max_message_bytes must be a positive integer")

        sequence = list(tool_sequence or [])
        ToolRegistry(sequence)
        self._target = target
        self._project_root = root
        self._timeout_seconds = float(timeout_seconds)
        self._tool_sequence = copy.deepcopy(sequence)
        self._seed = seed
        self._max_message_bytes = max_message_bytes
        self._temp_directory = tempfile.TemporaryDirectory(
            prefix=".gauntlet-adapter-", dir=self._project_root
        )

        self._process: subprocess.Popen[bytes] | None = None
        self._stdout_queue: queue.Queue[_StdoutItem] = queue.Queue()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_buffer = bytearray()
        self._stderr_truncated = False
        self._stderr_lock = threading.Lock()
        self._request_lock = threading.RLock()
        self._correlation_counter = 0
        self._requires_reset = False
        self._closed = False

    def __enter__(self) -> PythonCallableAdapter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def is_running(self) -> bool:
        """Whether the current child exists and has not exited."""

        process = self._process
        return process is not None and process.poll() is None

    @property
    def stderr(self) -> str:
        """Return bounded diagnostics captured from the current child."""

        with self._stderr_lock:
            value = bytes(self._stderr_buffer).decode("utf-8", errors="replace")
            truncated = self._stderr_truncated
        if truncated:
            value += "\n[GAUNTLET stderr capture truncated]"
        return value

    def reset(
        self,
        *,
        tool_sequence: Sequence[JsonObject] | None = None,
        seed: int | None = None,
    ) -> None:
        """Start a fresh child and reset its deterministic fixture state."""

        with self._request_lock:
            self._ensure_open()
            if tool_sequence is not None:
                sequence = list(tool_sequence)
                ToolRegistry(sequence)
                self._tool_sequence = copy.deepcopy(sequence)
            if seed is not None:
                if isinstance(seed, bool) or not isinstance(seed, int):
                    raise ValueError("seed must be an integer")
                self._seed = seed
            self._terminate_child()
            self._requires_reset = True
            try:
                self._start_child()
                self._send_reset()
            except AdapterError:
                self._terminate_child()
                raise
            self._requires_reset = False

    def invoke(self, payload: Mapping[str, JsonValue]) -> JsonObject:
        """Invoke the callable with one JSON object under a parent deadline."""

        if not isinstance(payload, Mapping):
            raise ValueError("invoke payload must be a JSON object")
        with self._request_lock:
            self._ensure_ready()
            return self._request("invoke", copy.deepcopy(dict(payload)))

    def trace(self) -> list[JsonObject]:
        """Fetch the deterministic trace captured in the child."""

        with self._request_lock:
            self._ensure_ready()
            payload = self._request("trace", {})
        events = payload.get("events")
        if not isinstance(events, list) or not all(isinstance(item, dict) for item in events):
            self._protocol_failure("trace response must contain an events object list")
        return copy.deepcopy(cast(list[JsonObject], events))

    def usage(self) -> JsonObject:
        """Fetch counters observed by the child without estimating tokens or cost."""

        with self._request_lock:
            self._ensure_ready()
            return self._request("usage", {})

    def close(self) -> None:
        """Terminate and reap the child, close pipes, and remove adapter-owned temp data."""

        with self._request_lock:
            if self._closed:
                return
            self._terminate_child()
            self._closed = True
            self._temp_directory.cleanup()

    def _ensure_open(self) -> None:
        if self._closed:
            raise AdapterClosedError("Adapter is closed")

    def _ensure_ready(self) -> None:
        self._ensure_open()
        if self._requires_reset:
            raise AdapterProcessError(
                "Adapter must be reset after a process or protocol failure", stderr=self.stderr
            )
        if self._process is None:
            try:
                self._start_child()
                self._send_reset()
            except AdapterError:
                self._requires_reset = True
                self._terminate_child()
                raise
        elif self._process.poll() is not None:
            return_code = self._process.returncode
            self._requires_reset = True
            self._terminate_child()
            raise AdapterProcessError(
                f"Adapter child exited with code {return_code}; reset is required",
                stderr=self.stderr,
            )

    def _start_child(self) -> None:
        self._stdout_queue = queue.Queue()
        with self._stderr_lock:
            self._stderr_buffer = bytearray()
            self._stderr_truncated = False

        # Safe-path/no-user-site preserve our controlled PYTHON* settings; -I would ignore them.
        command = [
            str(Path(sys.executable).resolve()),
            "-P",
            "-s",
            "-m",
            "gauntlet.adapters._worker",
            "--target",
            self._target,
            "--project-root",
            str(self._project_root),
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=self._temp_directory.name,
                env=self._child_environment(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                bufsize=0,
            )
        except OSError as exc:
            self._requires_reset = True
            raise AdapterProcessError(
                f"Unable to start adapter child: {exc}", stderr=self.stderr
            ) from exc
        assert process.stdout is not None
        assert process.stderr is not None
        self._process = process
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(process.stdout, self._stdout_queue),
            name="gauntlet-adapter-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(process.stderr,),
            name="gauntlet-adapter-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _child_environment(self) -> dict[str, str]:
        environment: dict[str, str] = {}
        for name in ("SystemRoot", "WINDIR", "LANG", "LC_ALL", "TZ"):
            value = os.environ.get(name)
            if value:
                environment[name] = value
        temp_path = self._temp_directory.name
        environment.update(
            {
                "HOME": temp_path,
                "USERPROFILE": temp_path,
                "TEMP": temp_path,
                "TMP": temp_path,
                "TMPDIR": temp_path,
                "PYTHONHASHSEED": self._python_hash_seed(),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1",
                "PYTHONUTF8": "1",
            }
        )
        return environment

    def _python_hash_seed(self) -> str:
        seed = self._seed
        if seed is None or seed < 0 or seed > 4_294_967_295:
            return "0"
        return str(seed)

    def _send_reset(self) -> None:
        self._request(
            "reset",
            {
                "seed": self._seed,
                "tool_sequence": cast(JsonValue, copy.deepcopy(self._tool_sequence)),
            },
        )

    def _request(self, operation: str, payload: JsonObject) -> JsonObject:
        process = self._process
        if process is None or process.stdin is None:
            raise AdapterProcessError("Adapter child is not running", stderr=self.stderr)
        if process.poll() is not None:
            return_code = process.returncode
            self._requires_reset = True
            self._terminate_child()
            raise AdapterProcessError(
                f"Adapter child exited with code {return_code}; reset is required",
                stderr=self.stderr,
            )

        self._correlation_counter += 1
        correlation_id = str(self._correlation_counter)
        try:
            encoded = encode_message(
                request_message(operation, correlation_id, payload),
                max_bytes=self._max_message_bytes,
            )
            process.stdin.write(encoded)
            process.stdin.flush()
        except ProtocolMessageError as exc:
            raise AdapterProtocolError(str(exc)) from exc
        except (BrokenPipeError, OSError) as exc:
            self._requires_reset = True
            self._terminate_child()
            raise AdapterProcessError(
                f"Unable to write {operation!r} request to adapter child: {exc}",
                stderr=self.stderr,
            ) from exc

        try:
            item = self._stdout_queue.get(timeout=self._timeout_seconds)
        except queue.Empty as exc:
            self._requires_reset = True
            self._terminate_child()
            raise AdapterTimeoutError(operation, self._timeout_seconds, self.stderr) from exc

        if isinstance(item, _StreamEof):
            return_code = process.poll()
            self._requires_reset = True
            self._terminate_child()
            raise AdapterProcessError(
                f"Adapter child closed stdout (exit code {return_code})", stderr=self.stderr
            )
        if isinstance(item, _StreamFailure):
            self._requires_reset = True
            self._terminate_child()
            raise AdapterProcessError(
                f"Unable to read adapter child stdout: {item.message}", stderr=self.stderr
            )
        try:
            reply = decode_reply(item, max_bytes=self._max_message_bytes)
        except ProtocolMessageError as exc:
            self._protocol_failure(str(exc), cause=exc)
        if reply.operation != operation or reply.correlation_id != correlation_id:
            self._protocol_failure(
                "Adapter child response did not match the request operation and correlation ID"
            )
        if reply.type == "error":
            assert reply.error is not None
            raise AdapterChildError(
                reply.error.code,
                reply.error.message,
                details=reply.error.details,
                retryable=reply.error.retryable,
            )
        assert reply.payload is not None
        return reply.payload

    def _protocol_failure(self, message: str, *, cause: BaseException | None = None) -> NoReturn:
        self._requires_reset = True
        self._terminate_child()
        if cause is None:
            raise AdapterProtocolError(message)
        raise AdapterProtocolError(message) from cause

    def _terminate_child(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=_TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        if self._stdout_thread is not None:
            self._stdout_thread.join(timeout=0.2)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=0.2)
        self._process = None
        self._stdout_thread = None
        self._stderr_thread = None

    def _read_stdout(self, stream: BinaryIO, output_queue: queue.Queue[_StdoutItem]) -> None:
        try:
            while True:
                line = stream.readline(self._max_message_bytes + 2)
                if not line:
                    output_queue.put(_StreamEof())
                    return
                output_queue.put(line)
        except (OSError, ValueError) as exc:
            output_queue.put(_StreamFailure(str(exc)))

    def _read_stderr(self, stream: BinaryIO) -> None:
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                with self._stderr_lock:
                    remaining = _STDERR_CAPTURE_BYTES - len(self._stderr_buffer)
                    if remaining > 0:
                        self._stderr_buffer.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        self._stderr_truncated = True
        except (OSError, ValueError):
            return
