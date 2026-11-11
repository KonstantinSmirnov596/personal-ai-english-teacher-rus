"""Runs the unmodified launcher with every outgoing network call recorded (stage 4.4).

This wrapper belongs to the tests: the core has no hook for it. It records, inside the core
process, each socket connect, name lookup and datagram send:

- ``sys.addaudithook`` sees the standard socket API (``socket.connect``, ``getaddrinfo``,
  ``sendto``) used by synchronous libraries;
- asyncio's Windows proactor connects through ``ConnectEx`` without an audit event, so its
  ``connect`` is wrapped as well (this is what httpx uses for the LLM request).

One JSON line per event goes to the file named by TRAINER_NET_AUDIT_LOG.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_log = open(os.environ["TRAINER_NET_AUDIT_LOG"], "a", buffering=1, encoding="utf-8")  # noqa: SIM115


def _record(kind: str, host: Any, port: Any = None) -> None:
    _log.write(json.dumps({"kind": kind, "host": str(host), "port": port}) + "\n")


def _hook(event: str, args: tuple[Any, ...]) -> None:
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple):
            _record("connect", address[0], address[1])
        else:
            _record("connect", address)
    elif event == "socket.getaddrinfo":
        _record("lookup", args[0], args[1])
    elif event in ("socket.gethostbyname", "socket.gethostbyaddr"):
        _record("lookup", args[0])
    elif event == "socket.sendto":
        address = args[1]
        _record("sendto", address[0] if isinstance(address, tuple) else address)


sys.addaudithook(_hook)

if sys.platform == "win32":
    from asyncio import windows_events

    _original_connect = windows_events.IocpProactor.connect

    def _connect(self: Any, conn: Any, address: Any) -> Any:
        _record("connect", address[0], address[1])
        return _original_connect(self, conn, address)

    windows_events.IocpProactor.connect = _connect  # type: ignore[method-assign]

from trainer.launcher import main  # noqa: E402

raise SystemExit(main())
