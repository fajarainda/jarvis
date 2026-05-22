# jasatirta_session.py - Helper login & fetch dengan session untuk Jasa Tirta 1
# Login sekali saat dipanggil _init_session(), retry otomatis jika 403/401
from __future__ import annotations

import logging
import json
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar

logger = logging.getLogger("jasatirta_session")

# ── Konstanta ──────────────────────────────────────────────────────────────────

LOGIN_URL   = "https://telemetri.jasatirta1.co.id/web/administration"
LISTPOS_URL = "https://telemetri.jasatirta1.co.id/web/html/modules/monitor/listpos/xmlhttp"

LOGIN_PAYLOAD = {
    "NXPEdtMemberAuthUsername": "telemetri",
    "NXPEdtMemberAuthPassword": "TelemetriGSM123",
    "mod":  "memberauth",
    "mode": "xmlhttp",
    "mid":  "",
    "op":   "login",
}

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# ── Session tunggal ────────────────────────────────────────────────────────────

_opener: urllib.request.OpenerDirector | None = None
_logged_in: bool = False

# ── Internal ───────────────────────────────────────────────────────────────────

def _make_opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar)
    )


def _do_login(opener: urllib.request.OpenerDirector, timeout: int = 10) -> bool:
    data = urllib.parse.urlencode(LOGIN_PAYLOAD).encode("utf-8")
    req  = urllib.request.Request(
        LOGIN_URL,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent":   _USER_AGENT,
        },
        method="POST",
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            logger.info(f"[_do_login] status={resp.status} body={body[:80]}")
            return resp.status == 200
    except Exception as e:
        logger.error(f"[_do_login] gagal: {e}")
        return False


def _reset() -> None:
    global _opener, _logged_in
    _opener    = None
    _logged_in = False
    logger.warning("[session] session di-reset, akan login ulang")


def _fetch(url: str, timeout: int = 20, _retry: bool = True) -> str | None:
    """Fetch URL dengan session aktif. Retry login sekali jika dapat 403/401."""
    global _opener, _logged_in

    if not _logged_in or _opener is None:
        logger.warning("[session] belum login saat fetch dipanggil, coba login...")
        if not init_session():
            return None

    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json, text/html", "User-Agent": _USER_AGENT},
    )
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")

    except urllib.error.HTTPError as e:
        if e.code in (401, 403) and _retry:
            logger.warning(f"[session] HTTP {e.code}, session expired — login ulang...")
            _reset()
            if init_session():
                return _fetch(url, timeout=timeout, _retry=False)
        logger.error(f"[session] HTTP {e.code}: {url}")
        return None

    except Exception as e:
        logger.error(f"[session] fetch gagal: {e}")
        return None

# ── Public API ─────────────────────────────────────────────────────────────────

def init_session(timeout: int = 10) -> bool:
    """
    Login ke Jasa Tirta dan simpan session.
    Panggil ini SATU KALI di awal __main__ sebelum mcp.run().
    Return True jika berhasil.
    """
    global _opener, _logged_in

    if _logged_in and _opener is not None:
        logger.info("[session] sudah login, skip")
        return True

    _opener = _make_opener()
    if _do_login(_opener, timeout=timeout):
        _logged_in = True
        logger.info("[session] login berhasil ✓")
        return True

    _opener = None
    logger.error("[session] login GAGAL")
    return False


def fetch_listpos(timeout: int = 60) -> dict | None:
    """
    Ambil data semua stasiun dari listpos/xmlhttp.
    Return dict value (gsm.rainfall + gsm.waterlevel) atau None jika gagal.
    """
    raw = _fetch(LISTPOS_URL, timeout=timeout)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "value" in parsed:
            return parsed["value"]
        return parsed
    except Exception as e:
        logger.error(f"[fetch_listpos] gagal parse JSON: {e}")
        return None


def fetch_url(url: str, timeout: int = 20) -> str | None:
    """Fetch URL arbitrari pakai session aktif (untuk detail per stasiun)."""
    return _fetch(url, timeout=timeout)


# ── Helper siaga ───────────────────────────────────────────────────────────────

def cek_siaga_harian(data24h: list) -> bool:
    for jam in data24h:
        if jam.get("siaga1") == "SIAGA" or jam.get("siaga2") == "SIAGA":
            return True
    return False


def status_siaga_harian(data24h: list) -> str:
    return "SIAGA" if cek_siaga_harian(data24h) else "NORMAL"


def _to_float(s) -> float | None:
    if s is None or s in ("-", "", "0.00"):
        return None
    try:
        return float(str(s).replace(",", "."))
    except (ValueError, TypeError):
        return None
