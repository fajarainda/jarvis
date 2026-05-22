# arr_jasatirta.py
# MCP ARR (Automatic Rain Recorder) Jasa Tirta 1 — OPTIMIZED
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import sys
import logging
import json
import re
import os
import importlib.util
from urllib.parse import quote

# ── Logging (stderr only) ──────────────────────────────────────────────────────

def _setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                             datefmt="%Y-%m-%d %H:%M:%S")
    logger.addHandler(logging.StreamHandler(sys.stderr))
    return logger

logger = _setup_logger("jasatirta_arr")

if sys.platform == "win32":
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

# ── Import session helper ──────────────────────────────────────────────────────

def _load_session_helper():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "jasatirta_session.py")
    spec = importlib.util.spec_from_file_location("jasatirta_session", path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

try:
    _sess = _load_session_helper()
    init_session        = _sess.init_session
    fetch_listpos       = _sess.fetch_listpos
    fetch_url           = _sess.fetch_url
    status_siaga_harian = _sess.status_siaga_harian
    _to_float_sess      = _sess._to_float
    logger.info("jasatirta_session loaded ✓")
except Exception as e:
    logger.error(f"Gagal load jasatirta_session: {e}")
    init_session        = None
    fetch_listpos       = None
    fetch_url           = None
    status_siaga_harian = None
    _to_float_sess      = None

# ── MCP ────────────────────────────────────────────────────────────────────────

mcp = FastMCP("jasatirta_arr")

# ── Konstanta ──────────────────────────────────────────────────────────────────

BASE_URL_ARR = (
    "https://telemetri.jasatirta1.co.id/web/html/modules/monitor/detaildata/xmlhttp"
    "?idrec={station_id}&type=GSMARR"
)

ARR_STASIUN_MAP = {
    # ===== MALANG =====
    "metro": "Metro", "tawingrejeni": "Tawingrejeni", "srinjing": "Srinjing",
    "watu dakon": "Watu Dakon", "kedurus hulu": "Kedurus Hulu", "kutut": "Kutut",
    "geger": "Geger", "simo": "Simo", "besowo": "Besowo", "dau": "Dau",
    "patok picis": "Patok Picis", "watugede": "Watugede",
    "gubuk klakah": "Gubuk Klakah", "srimulyo": "Srimulyo",
    "sumber rejo": "Sumber Rejo", "gubeng": "Gubeng", "kali biru": "Kali Biru",
    "boyolangu": "Boyolangu", "neyama": "Neyama", "sumber brantas": "Sumber Brantas",
    "ngajum": "NGAJUM", "malang": "Malang", "gudo": "Gudo",
    # ===== BLITAR / MALANG =====
    "jabung": "Jabung", "tangkil": "Tangkil", "dampit": "Dampit",
    "poncokusumo": "Poncokusumo", "semen": "Semen", "doko": "Doko",
    "sengguruh": "Sengguruh", "wagir": "Wagir", "sutami": "Sutami",
    "wlingi": "Wlingi", "gandekan": "Gandekan", "wates wlingi": "Wates Wlingi",
    "sumberagung": "Sumberagung", "birowo": "Birowo", "tunggorono": "Tunggorono",
    "bogel": "Bogel", "kampak": "Kampak", "puru": "Puru",
    "rejotangan": "Rejotangan", "jombok": "Jombok", "karangan": "Karangan",
    "pangkal": "Pangkal",
    # ===== KEDIRI =====
    "tugu": "Tugu", "prambon": "Prambon", "bagong": "Bagong",
    "widoro": "Widoro", "bendo": "Bendo", "gondang brts": "Gondang BRTS",
    "wonorejo-1": "Wonorejo-1", "wonorejo-2": "Wonorejo-2",
    "pagerwojo": "Pagerwojo", "bendungan": "Bendungan",
    "karangrejo": "Karangrejo", "jeli": "Jeli", "wates kediri": "Wates Kediri",
    "wilis": "Wilis", "kediri": "Kediri", "kertosono": "Kertosono",
    "pujon": "Pujon", "tawangsari": "Tawangsari", "madiredo": "Madiredo",
    "selorejo": "Selorejo", "ngliman sawahan": "Ngliman Sawahan",
    "gemarang": "Gemarang",
    # ===== MOJOKERTO / SURABAYA =====
    "rejoso": "Rejoso", "berbek": "Berbek", "kabuh": "Kabuh",
    "kesamben": "Kesamben", "wonosalam": "Wonosalam", "brangkal": "Brangkal",
    "tampung": "Tampung", "trawas": "Trawas", "sadar": "Sadar",
    "kambing": "Kambing", "sukodadi": "Sukodadi", "mernung": "Mernung",
    "marmoyo": "Marmoyo", "karangpilang": "Karangpilang",
    "gunungsari": "Gunungsari",
}

# Pre-compile regex
_RE_TAG = re.compile(r"<[^>]+>")
_RE_WS  = re.compile(r"\s+")

# ── Helper ─────────────────────────────────────────────────────────────────────

def _get_text_by_id(html: str, element_id: str) -> str | None:
    m = re.search(
        rf'id="{re.escape(element_id)}"[^>]*>(.*?)</(?:div|td|th|span|b|font)>',
        html, re.DOTALL | re.IGNORECASE
    )
    if m:
        t = _RE_WS.sub(" ", _RE_TAG.sub(" ", m.group(1))).strip()
        return t if t else None
    return None


def _strip(s: str) -> str:
    return _RE_WS.sub(" ", _RE_TAG.sub("", s)).strip()


def _fmt(val) -> str | None:
    if val is None:
        return None
    try:
        return f"{float(val):.10g}".replace(".", ",")
    except (ValueError, TypeError):
        return str(val)


def _fetch_html(url: str, timeout: int = 180) -> str | None:
    if fetch_url is None:
        logger.error("[_fetch_html] fetch_url tidak tersedia")
        return None
    raw = fetch_url(url, timeout=timeout)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "value" in parsed:
            return parsed["value"]
    except Exception:
        pass
    return raw
    
def _get_div_text(html: str, div_id: str) -> str | None:
    m = re.search(
        rf'id="{re.escape(div_id)}"[^>]*>\s*(.*?)\s*<',
        html, re.DOTALL
    )
    if m:
        t = _RE_TAG.sub("", m.group(1)).strip()
        return t if t else None
    return None


# ── Parse data per jam dari HTML tabel ARR ────────────────────────────────────

def _parse_hourly_arr(html: str, jam_target: int | None) -> tuple[list[dict], dict | None]:
    """
    Parse data per jam ARR dari HTML — menggunakan element ID seperti AWLR.
    NXPTDCellDetailValHour{i} = rainfall, NXPTDCellDetailExValHour{i} = kumulatif
    """
    semua: list[dict] = []
    hasil_target = None

    for i in range(24):
        tanggal = _get_div_text(html, f"NXPTDCellDetailDate{i}")
        if tanggal is None:
            break

        rf_raw  = _get_div_text(html, f"NXPTDCellDetailValHour{i}")
        cum_raw = _get_div_text(html, f"NXPTDCellDetailExValHour{i}")
        jam_str = f"{i:02d}:00"
        rf_ok   = rf_raw not in (None, "-", "")

        row = {
            "jam":       jam_str,
            "tanggal":   tanggal,
            "rainfall":  _fmt(rf_raw)  if rf_ok else None,
            "kumulatif": _fmt(cum_raw) if cum_raw not in (None, "-", "") else None,
            "tersedia":  rf_ok,
        }
        semua.append(row)

        if jam_target is not None and i == jam_target:
            hasil_target = row

    if jam_target is None:
        for row in reversed(semua):
            if row["tersedia"]:
                hasil_target = row
                break

    return semua, hasil_target


# ── Parse semua ARR dari listpos JSON ─────────────────────────────────────────

def _parse_arr_from_listpos(data_value: dict) -> list[dict]:
    hasil = []
    rainfall_list = data_value.get("gsm", {}).get("rainfall", [])

    for st in rainfall_list:
        nama      = st.get("name", "-")
        latest    = st.get("latestdata", {})
        data24h   = st.get("data24h", [])
        outofdate = st.get("outofdate", "false")
        rainy     = st.get("rainy", "false")

        rf    = _to_float_sess(latest.get("datavalue"))
        cumul = _to_float_sess(latest.get("dataexvalue"))

        date_upd = f"{latest.get('datadate', '-')} {latest.get('datahour', '-')}"

        if outofdate == "true":
            status_warna = "OFFLINE"
        elif status_siaga_harian(data24h) == "SIAGA":
            status_warna = "SIAGA"
        else:
            status_warna = "NORMAL"

        jam_siaga = []
        for idx, jam in enumerate(data24h):
            if jam.get("siaga1") == "SIAGA" or jam.get("siaga2") == "SIAGA":
                jam_siaga.append(f"{idx:02d}:00")

        hasil.append({
            "stasiun":      nama,
            "date_upd":     date_upd,
            "rf":           rf,
            "cumul":        cumul,
            "rainy":        rainy == "true",
            "status_warna": status_warna,
            "jam_siaga":    jam_siaga,
        })

    return hasil


# ── Tool 1: baca_data_arr (detail 1 stasiun) ──────────────────────────────────

@mcp.tool()
def baca_data_arr(stasiun: str, jam: int | None = None) -> dict:
    """
    Membaca data detail ARR (curah hujan) satu stasiun dari telemetri Jasa Tirta 1.
    Mendukung query data per jam spesifik (0-23).

    Gunakan tool ini untuk pertanyaan tentang stasiun TERTENTU:
    - "Curah hujan Jabung berapa?"
    - "ARR Sengguruh online tidak?"
    - "Rainfall Birowo sekarang?"
    - "Kumulatif hujan Geger hari ini?"
    - "Curah hujan Jabung jam 10?"
    - "Rainfall Dampit jam 3 pagi?"

    Cara Membaca :
    - format jam (13:00 dibaca “jam tigabelas nol nol”)
    - satuan (mm/jam dibaca “milimeter per jam”)
    - angka desimal (12.5 atau 12,5 dibaca “duabelas koma lima”)

    Untuk semua stasiun sekaligus, gunakan cek_semua_arr.

    Args:
        stasiun: Nama stasiun ARR yang disebut pengguna, tidak case-sensitive.
            Contoh: "metro", "tawingrejeni", "srinjing", "watu dakon",
            "kedurus hulu", "kutut", "geger", "simo", "besowo", "dau",
            "patok picis", "watugede", "gubuk klakah", "srimulyo",
            "sumber rejo", "gubeng", "kali biru", "boyolangu", "neyama",
            "sumber brantas", "ngajum", "malang", "gudo", "jabung",
            "tangkil", "dampit", "poncokusumo", "semen", "doko",
            "sengguruh", "wagir", "sutami", "wlingi", "gandekan",
            "wates wlingi", "sumberagung", "birowo", "tunggorono",
            "bogel", "kampak", "puru", "rejotangan", "jombok",
            "karangan", "pangkal", "tugu", "prambon", "bagong",
            "widoro", "bendo", "gondang brts", "wonorejo-1", "wonorejo-2",
            "pagerwojo", "bendungan", "karangrejo", "jeli", "wates kediri",
            "wilis", "kediri", "kertosono", "pujon", "tawangsari",
            "madiredo", "selorejo", "ngliman sawahan", "gemarang",
            "rejoso", "berbek", "kabuh", "kesamben", "wonosalam",
            "brangkal", "tampung", "trawas", "sadar", "kambing",
            "sukodadi", "mernung", "marmoyo", "karangpilang", "gunungsari".
        jam: Jam spesifik (0-23). Jika None, data terbaru yang dikembalikan.
    """
    nama_lower = stasiun.strip().lower()
    if nama_lower not in ARR_STASIUN_MAP:
        return {
            "success": False,
            "error": f"Stasiun '{stasiun}' tidak dikenali. "
                     f"Gunakan cek_semua_arr untuk melihat daftar stasiun."
        }
    if jam is not None and not (0 <= jam <= 23):
        return {"success": False, "error": "Jam harus antara 0 dan 23."}

    station_id = ARR_STASIUN_MAP[nama_lower]
    url = BASE_URL_ARR.format(station_id=quote(station_id))

    html = _fetch_html(url, timeout=30)
    if not html:
        return {"success": False, "error": "Koneksi ke server Jasa Tirta gagal."}

    nama_m     = re.search(r"<h1>([^<]+)</h1>", html, re.IGNORECASE)
    nama_resmi = nama_m.group(1).strip() if nama_m else station_id

    status_raw  = _get_text_by_id(html, "NXPSummaryStatus") or "-"
    status      = ("ONLINE"  if "ONLINE"  in status_raw.upper() else
                   "OFFLINE" if "OFFLINE" in status_raw.upper() else status_raw)
    rainfall    = _get_text_by_id(html, "NXPSummaryValue") or "-"
    last_update = _get_text_by_id(html, "NXPSummaryLastUpdate") or "-"
    kondisi     = _get_text_by_id(html, "NXPSummaryCondition") or "-"

    # Statistik
    avg_m  = re.search(r"Average Rainfall.*?<td[^>]*>([\d.]+)<", html, re.DOTALL | re.IGNORECASE)
    max_m  = re.search(r"Maximum Rainfall.*?<td[^>]*>([^<]+)<", html, re.DOTALL | re.IGNORECASE)
    min_m  = re.search(r"Minimum Rainfall.*?<td[^>]*>([^<]+)<", html, re.DOTALL | re.IGNORECASE)

    avg_rf = _strip(avg_m.group(1)) if avg_m else None
    max_rf = _strip(max_m.group(1)) if max_m else None
    min_rf = _strip(min_m.group(1)) if min_m else None

    # Data per jam
    semua_jam, data_jam = _parse_hourly_arr(html, jam)

    tts_ringkasan = (
        f"Data stasiun ARR {nama_resmi}. "
        f"Status {status}. Update terakhir {last_update}. "
        f"Curah hujan terbaru {rainfall}. Kondisi {kondisi}. "
    )
    if data_jam:
        tts_ringkasan += (
            f"Jam {data_jam['jam']}: "
            f"rainfall {data_jam['rainfall'] or '0'} mm/h, "
            f"kumulatif {data_jam['kumulatif'] or '0'} mm."
        )

    logger.info(f"[baca_data_arr] {nama_resmi} | Status={status} | Rainfall={rainfall} | jam={jam}")

    return {
        "success":       True,
        "stasiun":       nama_resmi,
        "status":        status,
        "last_update":   last_update,
        "rainfall":      rainfall,
        "kondisi":       kondisi,
        "avg_rainfall":  avg_rf,
        "max_rainfall":  max_rf,
        "min_rainfall":  min_rf,
        "data_jam":      data_jam,
        "semua_jam":     semua_jam,
        "tts_ringkasan": tts_ringkasan,
    }


# ── Tool 2: cek_semua_arr (semua stasiun via listpos) ─────────────────────────

@mcp.tool()
def cek_semua_arr(filter: str = "semua") -> dict:
    """
    Mengambil data SEMUA stasiun ARR Jasa Tirta 1 sekaligus dalam 1 request.
    Status siaga dihitung dari data per jam hari ini — stasiun dianggap SIAGA
    jika ada minimal 1 jam yang pernah siaga dalam 24 jam terakhir.
    Kumulatif hujan diambil dari field dataexvalue per stasiun.

    Gunakan tool ini untuk:
    - "Stasiun mana yang sedang hujan?"
    - "Ada stasiun ARR yang siaga?"
    - "Rekap curah hujan semua stasiun"
    - "Berapa stasiun yang hujan sekarang?"
    - "ARR mana yang offline?"
    - "Kumulatif hujan hari ini di semua stasiun?"

    Cara Membaca :
    - format jam (13:00 dibaca “jam tigabelas nol nol”)
    - satuan (mm/jam dibaca “milimeter per jam”)
    - angka desimal (12.5 atau 12,5 dibaca “duabelas koma lima”)
    
    Args:
        filter:
            - "semua"   → semua stasiun
            - "hujan"   → hanya stasiun dengan RF > 0 saat ini
            - "siaga"   → hanya stasiun yang pernah siaga hari ini
            - "offline" → hanya stasiun yang tidak update / offline
            - "normal"  → hanya stasiun berstatus normal / aman
    """
    if fetch_listpos is None:
        return {"success": False, "error": "Session helper tidak tersedia."}

    import time
    try:
        with open("cache_awlr.json") as f:cached = json.load(f)

        age = time.time() - cached["ts"]

        if age > 300:
            return {
            "success": False,
            "error": f"Data cache terlalu lama ({int(age)}s), coba lagi sebentar."
        }
    
        data_value = cached["data"]
    except FileNotFoundError:
        return {
        "success": False,
        "error": "Cache belum tersedia, tunggu 1-2 menit."
    }
    
    if not data_value:
        return {"success": False, "error": "Koneksi ke server Jasa Tirta gagal atau login tidak berhasil."}

    semua = _parse_arr_from_listpos(data_value)
    if not semua:
        return {"success": False, "error": "Tidak ada data stasiun ARR yang berhasil diparsing."}

    filter_lower = filter.strip().lower()

    if filter_lower == "hujan":
        data = [s for s in semua if s["rf"] is not None and s["rf"] > 0]
    elif filter_lower == "siaga":
        data = [s for s in semua if s["status_warna"] == "SIAGA"]
    elif filter_lower == "offline":
        data = [s for s in semua if s["status_warna"] == "OFFLINE"]
    elif filter_lower == "normal":
        data = [s for s in semua if s["status_warna"] == "NORMAL"]
    else:
        data = semua

    total         = len(semua)
    total_hujan   = sum(1 for s in semua if s["rf"] is not None and s["rf"] > 0)
    total_siaga   = sum(1 for s in semua if s["status_warna"] == "SIAGA")
    total_offline = sum(1 for s in semua if s["status_warna"] == "OFFLINE")
    nama_hujan    = [s["stasiun"] for s in semua if s["rf"] is not None and s["rf"] > 0]
    nama_siaga    = [s["stasiun"] for s in semua if s["status_warna"] == "SIAGA"]

    tts_ringkasan = (
        f"Total {total} stasiun ARR. "
        f"{total_hujan} stasiun sedang hujan: {', '.join(nama_hujan) if nama_hujan else 'tidak ada'}. "
        f"{total_siaga} stasiun pernah siaga hari ini: {', '.join(nama_siaga) if nama_siaga else 'tidak ada'}. "
        f"{total_offline} stasiun offline."
    )

    if filter_lower != "siaga":
        for s in data:
            s.pop("jam_siaga", None)

    logger.info(
        f"[cek_semua_arr] total={total} hujan={total_hujan} "
        f"siaga={total_siaga} offline={total_offline}"
    )

    return {
        "success":        True,
        "filter":         filter_lower,
        "total_stasiun":  total,
        "total_hujan":    total_hujan,
        "total_siaga":    total_siaga,
        "total_offline":  total_offline,
        "stasiun_hujan":  nama_hujan,
        "stasiun_siaga":  nama_siaga,
        "data":           data,
        "tts_ringkasan":  tts_ringkasan,
    }


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("MCP Server 'jasatirta_arr' starting...")
    if init_session:
        ok = init_session()
        if not ok:
            logger.error("Login gagal saat startup — tool cek_semua_arr mungkin tidak berfungsi")
    else:
        logger.error("init_session tidak tersedia")
    mcp.run(transport="stdio")
