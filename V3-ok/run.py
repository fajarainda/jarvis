# run.py - Entry point: jalankan semua MCP service sekaligus
from __future__ import annotations

import subprocess
import os
import sys
import json
import signal
import time

# ── Load Config ────────────────────────────────────────────────────────────────

def load_config() -> dict:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(current_dir, "config.json")

    if not os.path.exists(config_path):
        print(f"[ERROR] config.json tidak ditemukan di: {config_path}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f), current_dir

# ── Helpers ────────────────────────────────────────────────────────────────────

def check_file(path: str) -> None:
    if not os.path.exists(path):
        print(f"[ERROR] File tidak ditemukan: {path}")
        sys.exit(1)

def stop_all(processes: list[subprocess.Popen], timeout: int = 5) -> None:
    print("\nMenghentikan semua service...")
    for p in processes:
        if p.poll() is None:
            p.terminate()
    for p in processes:
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
    print("Semua service dihentikan.")

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    config, current_dir = load_config()

    mcp_endpoint = config.get("MCP_ENDPOINT")
    if not mcp_endpoint:
        print("[ERROR] MCP_ENDPOINT tidak ditemukan di config.json")
        sys.exit(1)

    # Daftar MCP script yang akan dijalankan
    # Tambah entry baru di sini jika ada tool baru
    mcp_scripts = [
        ("web_search",          "web_search.py"),
        ("web_fetch",           "web_fetch.py"),
        ("firebase_metering",   "firebase_metering.py"),
        ("jasatirta_metering",  "jasatirta_metering.py"),
        ("arr_jasatirta",      "arr_jasatirta.py"),
    ]

    pipe_script = os.path.join(current_dir, "mcp_pipe.py")
    check_file(pipe_script)

    for name, script_file in mcp_scripts:
        check_file(os.path.join(current_dir, script_file))

    processes: list[tuple[str, subprocess.Popen]] = []

    cache_proc = subprocess.Popen(
    [sys.executable, os.path.join(current_dir, "cache_jasatirta.py")],
    cwd=current_dir,
    )

    processes.append(("cache_fetcher", cache_proc))

    # ── Jalankan semua service ─────────────────────────────────────────────────

    print(f"[INFO] Memulai {len(mcp_scripts)} MCP service...\n")

    for name, script_file in mcp_scripts:
        script_path = os.path.join(current_dir, script_file)
        p = subprocess.Popen(
            [sys.executable, pipe_script, script_path, mcp_endpoint],
            cwd=current_dir,
        )
        processes.append((name, p))
        print(f"  ✓ {name:<20} PID={p.pid}")

    print(f"\n[INFO] Semua service berjalan. Tekan Ctrl+C untuk berhenti.\n")

    # ── Monitor proses — restart jika ada yang mati ────────────────────────────

    def signal_handler(sig, frame):
        stop_all([p for _, p in processes])
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal_handler)

    try:
        while True:
            for i, (name, p) in enumerate(processes):
                ret = p.poll()
                if ret is not None:
                    print(f"[WARN] Service '{name}' (PID={p.pid}) berhenti (exit={ret}), restart...")
                    script_file = mcp_scripts[i][1]
                    script_path = os.path.join(current_dir, script_file)
                    new_p = subprocess.Popen(
                        [sys.executable, pipe_script, script_path, mcp_endpoint],
                        cwd=current_dir,
                    )
                    processes[i] = (name, new_p)
                    print(f"  ✓ {name} di-restart (PID={new_p.pid})")
            time.sleep(5)

    except KeyboardInterrupt:
        stop_all([p for _, p in processes])


if __name__ == "__main__":
    main()
