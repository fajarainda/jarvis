# mcp_pipe.py - Jembatan WebSocket ↔ MCP subprocess dengan auto-reconnect
from __future__ import annotations

import asyncio
import websockets
import subprocess
import logging
import os
import signal
import sys
import random
import ssl

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger("MCP_PIPE")

# ── Konstanta ──────────────────────────────────────────────────────────────────

INITIAL_BACKOFF  = 1      # detik
MAX_BACKOFF      = 600    # 10 menit
PING_INTERVAL    = 20     # keepalive WebSocket (detik)
PING_TIMEOUT     = 10     # timeout per ping (detik)
PROCESS_KILL_TIMEOUT = 5  # detik sebelum SIGKILL

# ── State reconnect ────────────────────────────────────────────────────────────

reconnect_attempt = 0
backoff           = INITIAL_BACKOFF

# ── Core: reconnect loop ───────────────────────────────────────────────────────

async def connect_with_retry(uri: str) -> None:
    global reconnect_attempt, backoff

    while True:
        if reconnect_attempt > 0:
            # Jitter ±10% supaya beberapa instance tidak reconnect barengan
            wait = backoff * (1 + random.uniform(-0.1, 0.1))
            logger.info(f"Reconnect #{reconnect_attempt} dalam {wait:.1f}s...")
            await asyncio.sleep(wait)

        try:
            await _connect_to_server(uri)
            # Jika connect sukses dan selesai normal, reset state
            reconnect_attempt = 0
            backoff = INITIAL_BACKOFF
        except Exception as e:
            reconnect_attempt += 1
            logger.warning(f"Koneksi terputus (attempt #{reconnect_attempt}): {e}")
            backoff = min(backoff * 2, MAX_BACKOFF)


async def _connect_to_server(uri: str) -> None:
    global reconnect_attempt, backoff

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode    = ssl.CERT_NONE

    logger.info("Menghubungkan ke WebSocket server...")

    async with websockets.connect(
        uri,
        ssl=ssl_ctx,
        ping_interval=PING_INTERVAL,
        ping_timeout=PING_TIMEOUT,
    ) as ws:
        logger.info("Terhubung ke WebSocket server ✓")
        reconnect_attempt = 0
        backoff = INITIAL_BACKOFF

        process = subprocess.Popen(
            [sys.executable, mcp_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=os.environ,
            # Pastikan child process tidak blok saat parent mati
            close_fds=(sys.platform != "win32"),
        )
        logger.info(f"Proses '{mcp_script}' dimulai (PID={process.pid})")

        try:
            await asyncio.gather(
                _ws_to_process(ws, process),
                _process_to_ws(process, ws),
                _stderr_to_terminal(process),
            )
        finally:
            _terminate_process(process)


# ── Pipe helpers ───────────────────────────────────────────────────────────────

async def _ws_to_process(ws, process: subprocess.Popen) -> None:
    """Teruskan pesan dari WebSocket ke stdin proses."""
    try:
        async for message in ws:
            if isinstance(message, bytes):
                message = message.decode("utf-8")
            logger.debug(f"<< {message[:120]}")
            if process.poll() is not None:
                logger.warning("[ws_to_process] Proses sudah mati, hentikan loop")
                break
            try:
                process.stdin.write(message + "\n")
                process.stdin.flush()
            except (OSError, ValueError) as e:
                logger.error(f"[ws_to_process] pipe error: {e}")
                break
    except websockets.exceptions.ConnectionClosed:
        logger.info("WebSocket ditutup (ws_to_process)")
        raise
    except Exception as e:
        logger.error(f"[ws_to_process] {e}")
        raise
    finally:
        if not process.stdin.closed:
            process.stdin.close()


async def _process_to_ws(process: subprocess.Popen, ws) -> None:
    """Teruskan stdout proses ke WebSocket."""
    loop = asyncio.get_event_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, process.stdout.readline)
            if not line:
                logger.info("Proses selesai (stdout EOF)")
                break
            logger.debug(f">> {line[:120]}")
            await ws.send(line)
    except Exception as e:
        logger.error(f"[process_to_ws] {e}")
        raise


async def _stderr_to_terminal(process: subprocess.Popen) -> None:
    """Teruskan stderr proses ke terminal kita (untuk debugging)."""
    loop = asyncio.get_event_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, process.stderr.readline)
            if not line:
                break
            sys.stderr.write(line)
            sys.stderr.flush()
    except Exception as e:
        logger.error(f"[stderr_to_terminal] {e}")


def _terminate_process(process: subprocess.Popen) -> None:
    """Matikan proses dengan graceful (SIGTERM → SIGKILL)."""
    if process.poll() is not None:
        return  # Sudah mati sendiri
    logger.info(f"Menghentikan proses PID={process.pid}...")
    try:
        process.terminate()
        process.wait(timeout=PROCESS_KILL_TIMEOUT)
        logger.info("Proses berhenti (SIGTERM)")
    except subprocess.TimeoutExpired:
        process.kill()
        logger.warning("Proses di-SIGKILL (tidak merespons SIGTERM)")


# ── Signal handler ─────────────────────────────────────────────────────────────

def _signal_handler(sig, frame) -> None:
    logger.info("Sinyal interrupt diterima, mematikan...")
    sys.exit(0)


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    signal.signal(signal.SIGINT, _signal_handler)

    if len(sys.argv) < 3:
        logger.error("Usage: mcp_pipe.py <mcp_script> <mcp_endpoint>")
        sys.exit(1)

    mcp_script   = sys.argv[1]
    endpoint_url = sys.argv[2]

    if not endpoint_url:
        logger.error("MCP_ENDPOINT tidak boleh kosong.")
        sys.exit(1)

    if not os.path.isfile(mcp_script):
        logger.error(f"Script tidak ditemukan: {mcp_script}")
        sys.exit(1)

    logger.info(f"MCP Pipe mulai: script={mcp_script}")

    try:
        asyncio.run(connect_with_retry(endpoint_url))
    except KeyboardInterrupt:
        logger.info("Dihentikan oleh pengguna.")
    except Exception as e:
        logger.error(f"Error fatal: {e}")
        sys.exit(1)
