# jasatirta_metering.py - Ambil data AWLR dari telemetri Jasa Tirta 1
# OPTIMIZED: payload ramping, parse sekali, no file logging, timeout ketat
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import sys
import logging
import json
import re
from urllib.parse import quote
from datetime import datetime

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

logger = _setup_logger("jasatirta_metering")

if sys.platform == "win32":
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

# ── Import session helper ──────────────────────────────────────────────────────

import os, importlib.util

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

# ── Konstanta ──────────────────────────────────────────────────────────────────

BASE_URL = (
    "https://telemetri.jasatirta1.co.id/web/html/modules/monitor/detaildata/xmlhttp"
    "?idrec={station_id}&type=GSMAWLR"
)

STASIUN_MAP = {
    "kali tengah": "Kali Tengah", "srinjing": "Srinjing",
    "kedung suko": "Kedung Suko", "jemb. kuncir": "Jemb. Kuncir",
    "kuncir hilir": "Kuncir Hilir", "intake tawangsari": "Intake Tawangsari",
    "kepajaran": "Kepajaran", "awlr kali kedak": "AWLR Kali Kedak",
    "kali beng": "Kali Beng", "kadalpang": "Kadalpang",
    "kali bambang": "Kali Bambang", "madyopuro": "Madyopuro",
    "kali lekso": "Kali Lekso", "clumprit": "Clumprit",
    "konto hulu": "Konto Hulu", "pendem": "Pendem",
    "intake pdam ngagel": "Intake PDAM Ngagel", "gadang 2": "Gadang 2",
    "karang pilang": "Karang Pilang", "awlr kali biru": "AWLR Kali Biru",
    "gadang": "Gadang", "awlr kali metro": "AWLR Kali Metro",
    "segawe hulu": "Segawe Hulu", "blobo": "Blobo", "wonokerto": "Wonokerto",
    "tawingrejeni": "Tawingrejeni", "sengguruh": "Sengguruh",
    "gedogo": "Gedogo", "sutami": "Sutami", "lahor": "Lahor",
    "wlingi": "Wlingi", "bogel": "Bogel", "lodoyo": "Lodoyo",
    "dawir": "Dawir", "ngasinan": "Ngasinan", "pintu bendo": "Pintu Bendo",
    "segawe": "Segawe", "wonorejo": "Wonorejo", "tiudan": "Tiudan",
    "pompa tulungagung": "Pompa Tulungagung", "parit agung": "Parit Agung",
    "tawing": "Tawing", "brangkal": "Brangkal", "neyama 2": "Neyama 2",
    "neyama 1": "Neyama 1", "wudu": "Wudu", "jeli": "Jeli",
    "kediri": "Kediri", "mrican": "Mrican", "konto-1": "Konto-1",
    "konto-2": "Konto-2", "pinjal": "Pinjal", "wayangan": "Wayangan",
    "selorejo": "Selorejo", "bening": "Bening",
    "lengkong widas": "Lengkong Widas", "ploso": "Ploso",
    "menturus": "Menturus", "watudakon": "Watudakon", "mlirip": "Mlirip",
    "new lengkong": "New Lengkong", "sadar": "Sadar", "kambing": "Kambing",
    "marmoyo": "Marmoyo", "porong": "Porong", "perning": "Perning",
    "makmur": "Makmur", "gunungsari": "Gunungsari",
    "pintu wonokromo": "Pintu Wonokromo", "gubeng": "Gubeng",
    "jagir": "Jagir", "wonokromo": "Wonokromo",
}

mcp = FastMCP("jasatirta_metering")

BULAN = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
    5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
    9: "September", 10: "Oktober", 11: "November", 12: "Desember"
}

# Pre-compile regex
_RE_TAG  = re.compile(r"<[^>]+>")
_RE_WS   = re.compile(r"\s+")

# ── Helper ─────────────────────────────────────────────────────────────────────

def _strip_tags(raw: str) -> str:
    return _RE_WS.sub(" ", _RE_TAG.sub(" ", raw)).strip()

def _get_text_by_id(html: str, element_id: str) -> str | None:
    m = re.search(
        rf'id="{re.escape(element_id)}"[^>]*>(.*?)</(?:div|td|th|span|b|font)>',
        html, re.DOTALL | re.IGNORECASE
    )
    if m:
        t = _strip_tags(m.group(1))
        return t if t else None
    return None

def _get_div_text(html: str, div_id: str) -> str | None:
    m = re.search(rf'id="{re.escape(div_id)}"[^>]*>(.*?)<', html, re.DOTALL)
    if m:
        t = _RE_TAG.sub("", m.group(1)).strip()
        return t if t else None
    return None

def _fmt(val) -> str | None:
    if val is None:
        return None
    try:
        return f"{float(val):.10g}".replace(".", ",")
    except (ValueError, TypeError):
        return str(val)

def _tts_angka(angka, satuan: str = "") -> str:
    if angka is None:
        return "tidak tersedia"
    try:
        s = f"{float(angka):.10g}".replace(".", " koma ")
    except (ValueError, TypeError):
        s = str(angka)
    return f"{s} {satuan}".strip() if satuan else s

def _tts_jam(jam_str: str) -> str:
    if not jam_str:
        return "tidak tersedia"
    parts = jam_str.replace("jam", "").strip().replace(":", " ").split()
    return f"jam {int(parts[0])}" if parts else jam_str

def _tts_waktu(dt_str: str) -> str:
    if not dt_str or dt_str == "-":
        return "tidak tersedia"
    try:
        dt = datetime.strptime(dt_str.strip(), "%d/%m/%Y %H:%M:%S")
        return f"{dt.day} {BULAN[dt.month]} {dt.year} jam {dt.hour}"
    except ValueError:
        return dt_str

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

# ── Parse data per jam ─────────────────────────────────────────────────────────

def _parse_hourly(html: str, jam_target: int | None) -> tuple[list[dict], dict | None]:
    semua: list[dict] = []
    hasil_target = None

    for i in range(24):
        tanggal = _get_div_text(html, f"NXPTDCellDetailDate{i}")
        if tanggal is None:
            break

        wl_raw  = _get_div_text(html, f"NXPTDCellDetailValHour{i}")
        jam_str = f"{i:02d}:00"
        wl_ok   = wl_raw not in (None, "-", "")

        row = {
            "jam":          jam_str,
            "tanggal":      tanggal,
            "waterlevel_m": _fmt(wl_raw) if wl_ok else None,
            "tersedia":     wl_ok,
        }
        semua.append(row)

        if jam_target is not None and i == jam_target:
            qout    = _get_div_text(html, f"NXPTDCellDetailExValHour{i}")
            qinflow = _get_div_text(html, f"NXPTDCellDetailExVal2Hour{i}")
            hasil_target = {
                **row,
                "jam_tts":        _tts_jam(jam_str),
                "waterlevel_tts": _tts_angka(wl_raw, "meter") if wl_ok else "tidak tersedia",
                "qout_m3det":     _fmt(qout)    if qout    not in (None, "-", "") else None,
                "qout_tts":       _tts_angka(qout,    "meter kubik per detik") if qout    not in (None, "-", "") else "tidak tersedia",
                "qinflow_m3det":  _fmt(qinflow) if qinflow not in (None, "-", "") else None,
                "qinflow_tts":    _tts_angka(qinflow, "meter kubik per detik") if qinflow not in (None, "-", "") else "tidak tersedia",
            }

    if jam_target is None:
        for row in reversed(semua):
            if row["tersedia"]:
                i = int(row["jam"][:2])
                qout    = _get_div_text(html, f"NXPTDCellDetailExValHour{i}")
                qinflow = _get_div_text(html, f"NXPTDCellDetailExVal2Hour{i}")
                hasil_target = {
                    **row,
                    "jam_tts":        _tts_jam(row["jam"]),
                    "waterlevel_tts": _tts_angka(row["waterlevel_m"], "meter"),
                    "qout_m3det":     _fmt(qout)    if qout    not in (None, "-", "") else None,
                    "qout_tts":       _tts_angka(qout,    "meter kubik per detik") if qout    not in (None, "-", "") else "tidak tersedia",
                    "qinflow_m3det":  _fmt(qinflow) if qinflow not in (None, "-", "") else None,
                    "qinflow_tts":    _tts_angka(qinflow, "meter kubik per detik") if qinflow not in (None, "-", "") else "tidak tersedia",
                }
                break

    return semua, hasil_target

# ── Parse semua AWLR dari listpos JSON ────────────────────────────────────────

def _parse_awlr_from_listpos(data_value: dict) -> list[dict]:
    hasil = []
    waterlevel_list = data_value.get("gsm", {}).get("waterlevel", [])

    for st in waterlevel_list:
        nama     = st.get("name", "-")
        latest   = st.get("latestdata", {})
        data24h  = st.get("data24h", [])
        outofdate = st.get("outofdate", "false")

        wl   = _to_float_sess(latest.get("datavalue"))
        qout = _to_float_sess(latest.get("dataexvalue"))
        qin  = _to_float_sess(latest.get("dataexvalue2"))

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
            "stasiun":     nama,
            "date_upd":    date_upd,
            "wl":          wl,
            "qout":        qout,
            "qin":         qin,
            "status_warna": status_warna,
            "jam_siaga":   jam_siaga,
        })

    return hasil


# ── Tool 1: baca_data_awlr (detail 1 stasiun) ─────────────────────────────────

@mcp.tool()
def baca_data_awlr(
    stasiun: str,
    jam: int | None = None,
) -> dict:
    """
    Membaca data detail AWLR (Automatic Water Level Recorder) satu stasiun
    dari telemetri Jasa Tirta 1.

    Gunakan tool ini untuk pertanyaan tentang stasiun TERTENTU:
    - "TMA Ngasinan sekarang?"
    - "Water level Sutami jam 3?"
    - "Status siaga Lodoyo?"
    - "Debit Wlingi berapa?"

    Cara Membaca :
    - format jam (13:00 dibaca “jam tigabelas nol nol”)
    - satuan (mm/jam dibaca “milimeter per jam”)
    - angka desimal (12.5 atau 12,5 dibaca “duabelas koma lima”)

    Untuk semua stasiun sekaligus, gunakan cek_semua_awlr.
    - "Apa ada stasiun AWLR yang sedang siaga?"
    - "Rekap water level semua stasiun"
    - "Stasiun mana yang WL-nya paling tinggi?"

    Args:
        stasiun: Nama stasiun AWLR yang disebut pengguna, tidak case-sensitive.
            Contoh: "kali tengah", "srinjing", "kedung suko", "jemb. kuncir",
            "kuncir hilir", "intake tawangsari", "kepajaran", "awlr kali kedak",
            "kali beng", "kadalpang", "kali bambang", "madyopuro", "kali lekso",
            "clumprit", "konto hulu", "pendem", "intake pdam ngagel", "gadang 2",
            "karang pilang", "awlr kali biru", "gadang", "awlr kali metro",
            "segawe hulu", "blobo", "wonokerto", "tawingrejeni", "sengguruh",
            "gedogo", "sutami", "lahor", "wlingi", "bogel", "lodoyo", "dawir",
            "ngasinan", "pintu bendo", "segawe", "wonorejo", "tiudan",
            "pompa tulungagung", "parit agung", "tawing", "brangkal",
            "neyama 2", "neyama 1", "wudu", "jeli", "kediri", "mrican",
            "konto-1", "konto-2", "pinjal", "wayangan", "selorejo", "bening",
            "lengkong widas", "ploso", "menturus", "watudakon", "mlirip",
            "new lengkong", "sadar", "kambing", "marmoyo", "porong", "perning",
            "makmur", "gunungsari", "pintu wonokromo", "gubeng", "jagir", "wonokromo".
        jam: Jam tertentu (0-23). Jika None, data terbaru yang dikembalikan.
    """
    nama_lower = stasiun.strip().lower()
    if nama_lower not in STASIUN_MAP:
        return {
            "success": False,
            "error": f"Stasiun '{stasiun}' tidak dikenali. "
                     f"Gunakan cek_semua_awlr untuk melihat daftar stasiun."
        }
    if jam is not None and not (0 <= jam <= 23):
        return {"success": False, "error": "Jam harus antara 0 dan 23."}

    station_id = STASIUN_MAP[nama_lower]
    encoded_station = quote(station_id)
    url = BASE_URL.format(station_id=encoded_station)   
    html = _fetch_html(url, timeout=30)
    if not html:
        return {"success": False, "error": "Koneksi ke server Jasa Tirta gagal."}

    nama_match = re.search(r"<h1>([^<]+)</h1>", html, re.IGNORECASE)
    nama_resmi = nama_match.group(1).strip() if nama_match else station_id

    station_type = _get_text_by_id(html, "NXPSummaryStationType") or "-"
    status_raw   = _get_text_by_id(html, "NXPSummaryStatus") or "-"
    last_update  = _get_text_by_id(html, "NXPSummaryLastUpdate") or "-"
    tma_raw      = _get_text_by_id(html, "NXPSummaryValue") or "-"
    siaga_raw    = _get_text_by_id(html, "NXPSummarySIAGAStatus") or "-"

    status = ("ONLINE"  if "ONLINE"  in status_raw.upper() else
              "OFFLINE" if "OFFLINE" in status_raw.upper() else status_raw)
    siaga  = _RE_WS.sub(" ", _RE_TAG.sub(" ", siaga_raw)).strip()

    rata_m = re.search(r"Average.*?WL\s*:\s*([\d.]+)", html, re.DOTALL)
    maks_m = re.search(r"Maximum.*?WL\s*:\s*([^,<]+),\s*([\d.]+)", html, re.DOTALL)
    mini_m = re.search(r"Minimum.*?WL\s*:\s*([^,<]+),\s*([\d.]+)", html, re.DOTALL)

    rata_wl = _fmt(rata_m.group(1)) if rata_m else None
    maks_wl = f"{_fmt(maks_m.group(2))} (tgl {maks_m.group(1).strip()})" if maks_m else None
    mini_wl = f"{_fmt(mini_m.group(2))} (tgl {mini_m.group(1).strip()})" if mini_m else None

    tma_clean = re.sub(r"[^\d.]", "", tma_raw.split("m")[0]).strip()
    tma_fmt   = _fmt(tma_clean) if tma_clean else tma_raw

    _, data_jam = _parse_hourly(html, jam)

    tts_ringkasan = (
        f"Data stasiun {nama_resmi}. "
        f"Status {status}. "
        f"Update terakhir {_tts_waktu(last_update)}. "
        f"Tinggi muka air {_tts_angka(tma_clean, 'meter') if tma_clean else 'tidak tersedia'}. "
        f"Status siaga {siaga or 'tidak tersedia'}. "
        f"Rata-rata WL {_tts_angka(rata_m.group(1) if rata_m else None, 'meter')}. "
        f"Maks WL {_tts_angka(maks_m.group(2) if maks_m else None, 'meter')}. "
        f"Min WL {_tts_angka(mini_m.group(2) if mini_m else None, 'meter')}."
    )

    logger.info(f"[baca_data_awlr] {nama_resmi} → Status={status} | TMA={tma_fmt}")

    return {
        "success":       True,
        "stasiun":       nama_resmi,
        "station_type":  station_type.strip(),
        "status":        status,
        "last_update":   last_update,
        "tma_terbaru":   tma_fmt,
        "status_siaga":  siaga,
        "rata_rata_wl":  rata_wl,
        "maksimum_wl":   maks_wl,
        "minimum_wl":    mini_wl,
        "data_jam":      data_jam,
        "tts_ringkasan": tts_ringkasan,
    }


# ── Tool 2: cek_semua_awlr (semua stasiun via listpos) ────────────────────────

@mcp.tool()
def cek_semua_awlr(filter: str = "semua") -> dict:
    """
    Mengambil data SEMUA stasiun AWLR Jasa Tirta 1 sekaligus dalam 1 request.
    Status siaga dihitung dari data per jam hari ini — stasiun dianggap SIAGA
    jika ada minimal 1 jam yang pernah siaga dalam 24 jam terakhir.

    Gunakan tool ini untuk:
    - "Stasiun mana yang siaga banjir?"
    - "Ada AWLR yang offline?"
    - "Rekap water level semua stasiun"
    - "Stasiun mana yang WL-nya paling tinggi?"
    - "Berapa stasiun AWLR yang siaga?"

    Cara Membaca :
    - format jam (13:00 dibaca “jam tigabelas nol nol”)
    - satuan (mm/jam dibaca “milimeter per jam”)
    - angka desimal (12.5 atau 12,5 dibaca “duabelas koma lima”)

    Args:
        filter:
            - "semua"   → semua stasiun
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

    semua = _parse_awlr_from_listpos(data_value)
    if not semua:
        return {"success": False, "error": "Tidak ada data stasiun AWLR yang berhasil diparsing."}

    filter_lower = filter.strip().lower()

    if filter_lower == "siaga":
        data = [s for s in semua if s["status_warna"] == "SIAGA"]
    elif filter_lower == "offline":
        data = [s for s in semua if s["status_warna"] == "OFFLINE"]
    elif filter_lower == "normal":
        data = [s for s in semua if s["status_warna"] == "NORMAL"]
    else:
        data = semua

    total         = len(semua)
    total_siaga   = sum(1 for s in semua if s["status_warna"] == "SIAGA")
    total_offline = sum(1 for s in semua if s["status_warna"] == "OFFLINE")
    total_normal  = sum(1 for s in semua if s["status_warna"] == "NORMAL")
    nama_siaga    = [s["stasiun"] for s in semua if s["status_warna"] == "SIAGA"]

    tts_ringkasan = (
        f"Total {total} stasiun AWLR. "
        f"{total_siaga} stasiun pernah siaga hari ini: "
        f"{', '.join(nama_siaga) if nama_siaga else 'tidak ada'}. "
        f"{total_offline} stasiun offline. "
        f"{total_normal} stasiun normal."
    )

    if filter_lower != "siaga":
        for s in data:
            s.pop("jam_siaga", None)

    logger.info(
        f"[cek_semua_awlr] total={total} siaga={total_siaga} "
        f"offline={total_offline} normal={total_normal}"
    )

    return {
        "success":        True,
        "filter":         filter_lower,
        "total_stasiun":  total,
        "total_siaga":    total_siaga,
        "total_offline":  total_offline,
        "total_normal":   total_normal,
        "stasiun_siaga":  nama_siaga,
        "data":           data,
        "tts_ringkasan":  tts_ringkasan,
    }


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("MCP Server 'jasatirta_metering' starting...")
    if init_session:
        ok = init_session()
        if not ok:
            logger.error("Login gagal saat startup — tool cek_semua_awlr mungkin tidak berfungsi")
    else:
        logger.error("init_session tidak tersedia")
    mcp.run(transport="stdio")
