# web_search.py - Cari informasi di web menggunakan Tavily
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from tavily import TavilyClient
from typing import Literal

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
            "web_search.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    return logger

logger = _setup_logger("web_search")

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
mcp     = FastMCP("web_search")

# ── Tool: cari_di_web ──────────────────────────────────────────────────────────

@mcp.tool()
def cari_di_web(
    query: str,
    max_results: int = 5,
    search_depth: Literal["basic", "advanced"] = "basic",
    include_answer: bool = True,
    topic: Literal["general", "news"] = "general",
    days: int | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> dict:
    """
    Mencari informasi terbaru di internet menggunakan Tavily Search API.

    KAPAN pakai tool ini:
    - Pengguna tanya berita atau info yang butuh data real-time / terkini
    - Ada kata 'cari', 'cariin', 'googling', 'cek internet', 'ada info tentang'
    - Topik berubah cepat: harga, rilis produk, teknologi baru, cuaca, event
    - Informasi yang mungkin di luar pengetahuan AI (post-cutoff date)

    JANGAN pakai untuk:
    - Fakta statis yang AI sudah tahu (rumus, sejarah, definisi)
    - Small talk / percakapan ringan
    - Tugas coding atau kalkulasi murni

    Args:
        query:
            Pertanyaan atau kata kunci (gunakan bahasa natural, min 3 kata).
            Contoh baik : "harga emas hari ini per gram"
            Contoh buruk : "emas"

        max_results:
            Jumlah hasil (1–10). Default 5.
            Gunakan 3 untuk jawaban cepat, 8–10 untuk riset mendalam.

        search_depth:
            "basic"    → Cepat, hemat kuota, cocok untuk query umum (default).
            "advanced" → Lebih dalam, untuk topik kompleks atau riset.

        include_answer:
            Sertakan ringkasan AI dari Tavily. Sangat berguna untuk pertanyaan
            faktual. Default True.

        topic:
            "general" → Pencarian web umum (default).
            "news"    → Fokus ke artikel berita terbaru.

        days:
            Filter berita N hari terakhir. Hanya berlaku jika topic="news".
            Contoh: 1 (24 jam), 7 (seminggu). None = tanpa filter waktu.

        include_domains:
            Batasi ke domain tertentu. Contoh: ["kompas.com", "detik.com"]

        exclude_domains:
            Kecualikan domain tertentu. Contoh: ["reddit.com"]

    Returns:
        {
            "success": bool,
            "query":   str,
            "answer":  str | None,
            "results": [{"title": str, "url": str, "content": str, "score": float}],
            "total":   int
        }
    """
    query = query.strip()
    if not query:
        return {"success": False, "error": "Query tidak boleh kosong."}
    if not (1 <= max_results <= 10):
        return {"success": False, "error": "max_results harus antara 1 dan 10."}
    if days is not None and topic != "news":
        return {"success": False, "error": "Parameter 'days' hanya berlaku saat topic='news'."}

    try:
        params: dict = {
            "query":          query,
            "max_results":    max_results,
            "search_depth":   search_depth,
            "include_answer": include_answer,
            "topic":          topic,
        }
        if days is not None:
            params["days"] = days
        if include_domains:
            params["include_domains"] = include_domains
        if exclude_domains:
            params["exclude_domains"] = exclude_domains

        response = client.search(**params)

        results = [
            {
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "content": r.get("content", ""),
                "score":   round(r.get("score", 0.0), 4),
            }
            for r in response.get("results", [])
        ]

        if not results:
            return {"success": False, "error": "Tidak ada hasil ditemukan untuk query ini."}

        logger.info(f"[cari_di_web] '{query}' → {len(results)} hasil (depth={search_depth}, topic={topic})")
        return {
            "success": True,
            "query":   query,
            "answer":  response.get("answer"),
            "results": results,
            "total":   len(results),
        }

    except Exception as e:
        logger.error(f"[cari_di_web] Error: {e}")
        return {"success": False, "error": str(e)}


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("MCP Server 'web_search' starting...")
    mcp.run(transport="stdio")
