"""Experimental Bluetooth remote for the TCL 43C61B on Windows.

    python tv_bluetooth.py --probe
    python tv_bluetooth.py --start
    # On TV: Settings > Remotes & Accessories > Pair accessory > THUIS
    python tv_bluetooth.py --status
    python tv_bluetooth.py vol-
    python tv_bluetooth.py brightness-
    python tv_bluetooth.py off
    python tv_bluetooth.py on
    python tv_bluetooth.py --stop

All TV commands require pairing and are unverified on this model. "on" sends
System Wake Up, "off" sends System Sleep, and "power" sends a toggle. Bluetooth
cannot report the TV's power state or set an absolute backlight percentage.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

from src.bluetooth_hid import BluetoothRemote, KEYS, key_report, validate_send


STATE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "tcl-ir"
ENDPOINT = STATE_DIR / "bluetooth-endpoint.json"
LOG_FILE = STATE_DIR / "bluetooth.log"


def request(message: dict) -> dict:
    try:
        endpoint = json.loads(ENDPOINT.read_text(encoding="utf-8"))
        message = {**message, "token": endpoint["token"]}
        with socket.create_connection(("127.0.0.1", int(endpoint["port"])), timeout=3) as connection:
            connection.settimeout(60)
            connection.sendall(json.dumps(message).encode() + b"\n")
            with connection.makefile("rb") as stream:
                response = json.loads(stream.readline(65536))
    except (OSError, ValueError, KeyError) as error:
        raise RuntimeError("Bluetooth helper is unavailable; run --start (details in " + str(LOG_FILE) + ")") from error
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "Bluetooth command failed"))
    return response["result"]


def configure_log():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("tcl_ir.bluetooth")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


async def serve(logger):
    # One long-lived provider preserves the HID database between CLI commands.
    # Recreating it for every key can break Android's report subscriptions.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = kernel32.CreateMutexW(None, False, "Local\\tcl-ir-bluetooth-remote")
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(mutex)
        raise RuntimeError("Bluetooth helper is already running")
    token = secrets.token_hex(32)
    remote = None
    stop = asyncio.Event()
    server = None
    command_lock = asyncio.Lock()
    handlers = set()

    async def handle(reader, writer):
        try:
            payload = json.loads(await asyncio.wait_for(reader.readline(), timeout=3))
            if not secrets.compare_digest(str(payload.get("token", "")), token):
                raise ValueError("invalid helper token")
            async with command_lock:
                if stop.is_set():
                    raise RuntimeError("helper is stopping; no keys sent")
                operation = payload.get("operation")
                if operation == "status":
                    result = await remote.status()
                elif operation == "stop":
                    result = {"stopped": True}
                    stop.set()
                elif operation == "send":
                    # No timeout retry: a partial send must never be replayed.
                    result = await asyncio.wait_for(remote.send(
                        payload["keys"], payload.get("repeat", 1), payload.get("gap", 0.25), payload.get("target")
                    ), timeout=50)
                else:
                    raise ValueError("unknown helper operation")
            response = {"ok": True, "result": result}
        except Exception as error:
            logger.warning("request failed: %s", error)
            response = {"ok": False, "error": str(error) or type(error).__name__}
        try:
            writer.write(json.dumps(response).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def connection(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        try:
            await handle(reader, writer)
        finally:
            handlers.discard(task)

    try:
        remote = BluetoothRemote(logger)
        await remote.start()
        server = await asyncio.start_server(connection, "127.0.0.1", 0, limit=16384)
        endpoint = {"port": server.sockets[0].getsockname()[1], "token": token, "pid": os.getpid()}
        temporary = ENDPOINT.with_suffix(".tmp")
        temporary.write_text(json.dumps(endpoint), encoding="utf-8")
        temporary.replace(ENDPOINT)
        logger.info("helper ready; awaiting pairing/subscriptions; no TV keys sent")
        async with server:
            await stop.wait()
    finally:
        if server is not None:
            server.close()
            await server.wait_closed()
        if handlers:
            await asyncio.gather(*handlers, return_exceptions=True)
        if remote is not None:
            await remote.close()
        # A stale file from a failed startup is harmless; only remove ours.
        if ENDPOINT.exists():
            try:
                if json.loads(ENDPOINT.read_text(encoding="utf-8")).get("token") == token:
                    ENDPOINT.unlink()
            except (OSError, ValueError):
                pass
        kernel32.CloseHandle(mutex)
        logger.info("helper stopped")


def start_helper() -> dict:
    try:
        return request({"operation": "status"})
    except RuntimeError:
        pass
    executable = Path(sys.executable).with_name("pythonw.exe")
    if not executable.exists():
        executable = Path(sys.executable)
    process = subprocess.Popen(
        [str(executable), str(Path(__file__).resolve()), "--serve"],
        cwd=Path(__file__).resolve().parent,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
    )
    for _ in range(150):
        if process.poll() is not None:
            raise RuntimeError(f"Bluetooth helper exited ({process.returncode}); see {LOG_FILE}")
        try:
            return request({"operation": "status"})
        except RuntimeError:
            time.sleep(0.1)
    raise RuntimeError(f"Bluetooth helper has not become ready; see {LOG_FILE}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("keys", nargs="*")
    actions = parser.add_mutually_exclusive_group()
    for action in ("probe", "start", "stop", "status", "list", "serve"):
        actions.add_argument("--" + action, action="store_true", help=argparse.SUPPRESS if action == "serve" else action)
    parser.add_argument("--dry-run", action="store_true", help="print reports without starting Bluetooth or sending")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--gap", type=float, default=0.25)
    parser.add_argument("--target", help="exact subscribed TV device_id from --status; never broadcasts")
    args = parser.parse_args(argv)
    if any(getattr(args, action) for action in ("probe", "start", "stop", "status", "list", "serve")):
        if args.keys or args.dry_run:
            parser.error("use an action by itself, or keys with optional --dry-run")
    else:
        try:
            args.keys = validate_send(args.keys, args.repeat, args.gap)
        except ValueError as error:
            parser.error(str(error))
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.list:
        for name, key in KEYS.items():
            print(f"{name:18} report={key.report_id} value=0x{key.value:04X}  {key.note}")
        return 0
    if args.dry_run:
        for name in args.keys:
            report_id, data = key_report(name)
            print(f"{name}: report={report_id} press={data.hex(' ')} release={bytes(len(data)).hex(' ')}")
        return 0
    if sys.platform != "win32":
        print("ERROR: Bluetooth peripheral control requires Windows", file=sys.stderr)
        return 1
    logger = configure_log() if args.serve else logging.getLogger("tcl_ir.bluetooth")
    try:
        if args.serve:
            asyncio.run(serve(logger))
            return 0
        if args.probe:
            result = asyncio.run(BluetoothRemote(logger).probe())
        elif args.start:
            result = start_helper()
        elif args.status or args.stop:
            result = request({"operation": "stop" if args.stop else "status"})
        else:
            result = request({"operation": "send", "keys": args.keys, "repeat": args.repeat,
                              "gap": args.gap, "target": args.target})
        print(json.dumps(result, indent=2))
        if args.start:
            print("Pair this PC in TV Settings > Remotes & Accessories > Pair accessory.")
            print("No keys sent. All TV effects, brightness and standby wake remain unverified.")
        return 0
    except (ImportError, OSError, RuntimeError) as error:
        if args.serve:
            logger.exception("Bluetooth helper failed")
        elif isinstance(error, ImportError):
            print("ERROR: install optional dependencies: python -m pip install -r requirements-bluetooth.txt", file=sys.stderr)
        else:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
