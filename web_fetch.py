# web_fetch.py - Ambil dan baca isi konten website (via Tavily Extract)
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from tavily import TavilyClient

import sys
import logging
import logging.handlers
import json
import os

# ── Logging ────────────────────────────────────────────────────────────────────

def _setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                             datefmt="%Y-%m-%d %H:%M:%S")
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    try:
        fh = logging.handlers.RotatingFileHandler(
            "web_fetch.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    return logger

logger = _setup_logger("web_fetch")

# ── Windows UTF-8 fix ──────────────────────────────────────────────────────────

if sys.platform == "win32":
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

# ── Config & Client ────────────────────────────────────────────────────────────

def _load_config() -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    key = os.getenv("TAVILY_API_KEY")
    if key:
        return {"tavily_api_key": key}
    raise RuntimeError("API key tidak ditemukan. Isi config.json atau set env TAVILY_API_KEY.")

_config = _load_config()
client  = TavilyClient(api_key=_config["tavily_api_key"])
mcp     = FastMCP("web_fetch")

# ── Tool: ambil_konten_website ─────────────────────────────────────────────────

@mcp.tool()
def ambil_konten_website(
    url: str,
    max_chars: int = 5000,
) -> dict:
    """
    Membuka dan mengekstrak konten teks dari sebuah URL menggunakan Tavily Extract.

    KAPAN pakai tool ini:
    - Pengguna kasih link dan minta dibacakan / diringkas / dianalisis
    - Ada kata 'buka link', 'bacakan artikel', 'ringkas halaman', 'cek URL ini'
    - Follow-up dari hasil cari_di_web: "buka link pertama itu"
    - Pengguna paste URL dan tanya tentang isinya

    JANGAN pakai jika:
    - Tidak ada URL yang diberikan — gunakan cari_di_web saja
    - URL ke file biner (PDF, gambar, zip) yang tidak bisa diekstrak teks

    Args:
        url:
            Alamat website lengkap. Harus dimulai dengan http:// atau https://
            Contoh: "https://www.kompas.com/artikel/xyz"

        max_chars:
            Batas panjang konten yang dikembalikan (karakter). Default 5000.
            Naikkan jika butuh artikel panjang, turunkan untuk hemat token.
            Rentang: 500–20000.

    Returns:
        {
            "success":    bool,
            "url":        str,
            "title":      str,
            "konten":     str,          # Isi teks halaman
            "truncated":  bool,         # True jika konten dipotong
            "char_count": int           # Panjang konten (sebelum dipotong)
        }
    """
    url = url.strip()
    if not url:
        return {"success": False, "error": "URL tidak boleh kosong."}
    if not url.startswith(("http://", "https://")):
        return {"success": False, "error": "URL tidak valid. Harus dimulai dengan http:// atau https://"}
    if not (500 <= max_chars <= 20_000):
        return {"success": False, "error": "max_chars harus antara 500 dan 20000."}

    try:
        response = client.extract(urls=[url])

        results = response.get("results", [])
        if not results:
            return {"success": False, "error": "Tavily tidak bisa mengambil konten dari URL ini."}

        page         = results[0]
        raw_content  = page.get("raw_content") or page.get("content", "")

        if not raw_content:
            return {"success": False, "error": "Konten kosong atau tidak tersedia dari URL ini."}

        char_count = len(raw_content)
        truncated  = char_count > max_chars
        if truncated:
            raw_content = raw_content[:max_chars] + f"\n\n[... konten dipotong, total {char_count} karakter]"

        logger.info(f"[ambil_konten_website] {url} → {char_count} karakter (truncated={truncated})")
        return {
            "success":    True,
            "url":        url,
            "title":      page.get("title", ""),
            "konten":     raw_content,
            "truncated":  truncated,
            "char_count": char_count,
        }

    except Exception as e:
        logger.error(f"[ambil_konten_website] Error: {e}")
        return {"success": False, "error": str(e)}


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("MCP Server 'web_fetch' starting...")
    mcp.run(transport="stdio")
