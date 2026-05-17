# jasatirta_metering.py - Ambil data AWLR dari telemetri Jasa Tirta 1
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import sys
import logging
import logging.handlers
import json
import re
import urllib.request
import urllib.error
from datetime import datetime
from html.parser import HTMLParser

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
            "jasatirta_metering.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    return logger

logger = _setup_logger("jasatirta_metering")

# ── Windows UTF-8 fix ──────────────────────────────────────────────────────────

if sys.platform == "win32":
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

# ── Konstanta ──────────────────────────────────────────────────────────────────

BASE_URL = (
    "https://telemetri.jasatirta1.co.id/web/html/modules/monitor/detaildata/xmlhttp"
    "?idrec={station_id}&type=GSMAWLR"
)

# Mapping nama stasiun → ID di URL Jasa Tirta
STASIUN_MAP = {
    "ngasinan": "Ngasinan",
    # Tambah stasiun lain di sini:
    # "sutami":   "Sutami",
    # "selorejo": "Selorejo",
}

mcp = FastMCP("jasatirta_metering")


# ═══════════════════════════════════════════════════════════════════════════════
# ── Helper: Format untuk TTS (Text-to-Speech) ─────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def _fmt(val) -> str | None:
    """Ubah float ke string dengan koma sebagai desimal. Misal: 106.64 → '106,64'"""
    if val is None:
        return None
    try:
        f = float(val)
        return f"{f:.10g}".replace(".", ",")
    except (ValueError, TypeError):
        return str(val)


def _fmt_tts_angka(angka: float | int | str | None, satuan: str = "") -> str:
    """
    Format angka desimal untuk TTS agar dibaca benar di Xiaozhi AI.
    Contoh: 106.64 → "106 koma 64", 12.0 → "12"
    """
    if angka is None:
        return "tidak tersedia"

    try:
        f = float(angka)
        angka_str = f"{f:.10g}"
    except (ValueError, TypeError):
        angka_str = str(angka)

    # Ganti titik desimal dengan kata "koma" untuk TTS
    angka_tts = angka_str.replace(".", " koma ")

    if satuan:
        return f"{angka_tts} {satuan}"
    return angka_tts


def _fmt_tts_jam(jam_str: str) -> str:
    """
    Format jam HH:MM untuk TTS.
    Contoh: "01:00" → "jam 1", "14:00" → "jam 14"
    """
    if not jam_str:
        return "tidak tersedia"

    # Hapus kata "jam" jika sudah ada, lalu bersihkan
    jam_bersih = jam_str.replace("jam", "").strip()

    # Ambil bagian jam saja (HH), abaikan menit
    parts = jam_bersih.replace(":", " ").split()
    if len(parts) >= 1:
        return f"jam {int(parts[0])}"
    return f"jam {int(jam_bersih)}"


def _fmt_tts_waktu_lengkap(datetime_str: str) -> str:
    """
    Format string waktu lengkap untuk TTS.
    Contoh: "13/05/2026 11:30:45" → "13 Mei 2026 jam 11"
    """
    if not datetime_str or datetime_str == "-":
        return "tidak tersedia"

    try:
        # Coba parse format DD/MM/YYYY HH:MM:SS
        dt = datetime.strptime(datetime_str.strip(), "%d/%m/%Y %H:%M:%S")

        bulan_map = {
            1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
            5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
            9: "September", 10: "Oktober", 11: "November", 12: "Desember"
        }

        return f"{dt.day} {bulan_map[dt.month]} {dt.year} jam {dt.hour}"
    except ValueError:
        # Fallback: kalau format beda
        return datetime_str.replace(":", " ")


def _fmt_tts_tanggal(tanggal_str: str) -> str:
    """
    Format tanggal DD/MM/YYYY untuk TTS.
    Contoh: "13/05/2026" → "13 Mei 2026"
    """
    if not tanggal_str or tanggal_str == "-":
        return "tidak tersedia"

    try:
        dt = datetime.strptime(tanggal_str.strip(), "%d/%m/%Y")
        bulan_map = {
            1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
            5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
            9: "September", 10: "Oktober", 11: "November", 12: "Desember"
        }
        return f"{dt.day} {bulan_map[dt.month]} {dt.year}"
    except ValueError:
        return tanggal_str


def _fmt_tts_tma(nilai: str | float | None) -> str:
    """Format TMA (Tinggi Muka Air) untuk TTS."""
    return _fmt_tts_angka(nilai, "meter")


def _fmt_tts_debit(nilai: str | float | None) -> str:
    """Format debit (Q) untuk TTS."""
    return _fmt_tts_angka(nilai, "meter kubik per detik")


def _fmt_tts_status(status: str) -> str:
    """Format status untuk TTS."""
    if not status or status == "-":
        return "tidak diketahui"
    return status.upper()


# ── Helper: ambil teks dari div by id ─────────────────────────────────────────

def _get_div_text(html: str, div_id: str) -> str | None:
    """Ambil teks dalam <div id="...">teks</div>"""
    pattern = rf'id="{re.escape(div_id)}"[^>]*>(.*?)<'
    match = re.search(pattern, html, re.DOTALL)
    if match:
        raw = match.group(1).strip()
        # Bersihkan tag HTML
        raw = re.sub(r"<[^>]+>", "", raw).strip()
        return raw if raw else None
    return None


# ── Helper: ambil teks dari tag dengan id ─────────────────────────────────────

def _get_text_by_id(html: str, element_id: str) -> str | None:
    """Ambil teks dalam elemen dengan id tertentu (generik)"""
    pattern = rf'id="{re.escape(element_id)}"[^>]*>(.*?)</(?:div|td|th|span|b|font)>'
    match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
    if match:
        raw = match.group(1).strip()
        raw = re.sub(r"<[^>]+>", " ", raw).strip()
        raw = re.sub(r"\s+", " ", raw).strip()
        return raw if raw else None
    return None


# ── Helper: parse data per jam ────────────────────────────────────────────────

def _parse_hourly_data(html: str) -> list[dict]:
    """Parse tabel data per jam dari HTML."""
    data_per_jam = []
    for i in range(24):
        tanggal = _get_div_text(html, f"NXPTDCellDetailDate{i}")
        wl_raw  = _get_div_text(html, f"NXPTDCellDetailValHour{i}")
        qout    = _get_div_text(html, f"NXPTDCellDetailExValHour{i}")
        qinflow = _get_div_text(html, f"NXPTDCellDetailExVal2Hour{i}")

        if tanggal is None:
            break

        jam_str = f"{i:02d}:00"
        wl_valid = wl_raw not in (None, "-", "")

        data_per_jam.append({
            "jam":            jam_str,
            "jam_tts":        _fmt_tts_jam(jam_str),
            "tanggal":        tanggal,
            "tanggal_tts":    _fmt_tts_tanggal(tanggal),
            "waterlevel_m":   _fmt(wl_raw) if wl_valid else None,
            "waterlevel_tts": _fmt_tts_tma(wl_raw) if wl_valid else None,
            "qout_m3det":     _fmt(qout)   if qout not in (None, "-", "") else None,
            "qout_tts":       _fmt_tts_debit(qout) if qout not in (None, "-", "") else None,
            "qinflow_m3det":  _fmt(qinflow) if qinflow not in (None, "-", "") else None,
            "qinflow_tts":    _fmt_tts_debit(qinflow) if qinflow not in (None, "-", "") else None,
            "tersedia":       wl_valid,
        })

    return data_per_jam


# ── Tool: baca_data_awlr ───────────────────────────────────────────────────────

@mcp.tool()
def baca_data_awlr(
    stasiun: str = "ngasinan",
    jam: int | None = None,
) -> dict:
    """
    Membaca data AWLR (Automatic Water Level Recorder) dari telemetri Jasa Tirta 1.

    KAPAN pakai tool ini:
    - Pengguna tanya TMA, water level, atau ketinggian air stasiun Jasa Tirta
    - Ada nama stasiun seperti 'ngasinan'
    - Contoh: "TMA Ngasinan sekarang?", "water level ngasinan jam 3?",
      "status siaga ngasinan?", "ngasinan online tidak?"
    - Pertanyaan tentang debit air (Qout, Qinflow) stasiun AWLR

    JANGAN pakai untuk:
    - Stasiun KLOPLOGGER / Firebase (pakai baca_data_metering)
    - Data curah hujan (berbeda sistem)

    Args:
        stasiun:
            Nama stasiun AWLR. Default: "ngasinan".
            Stasiun yang didukung: ngasinan.
            Bisa juga pakai ID langsung seperti "Ngasinan".

        jam:
            Jam tertentu yang ingin ditanyakan (0–23). Opsional.
            Jika None, hanya data terbaru (last update) yang dikembalikan.
            Contoh: jam=5 → data pukul 05:00, jam=0 → data pukul 00:00.

    Returns:
        {
            "success":        bool,
            "stasiun":        str,    # Nama stasiun
            "station_type":   str,    # Tipe stasiun (misal "GSM AWLR")
            "status":         str,    # "ONLINE" atau "OFFLINE"
            "last_update":    str,    # Waktu update terakhir
            "tma_terbaru":    str,    # Water level terbaru dalam meter (desimal koma)
            "status_siaga":   str,    # Status siaga WL dan DISCH
            "rata_rata_wl":   str,    # Rata-rata water level
            "maksimum_wl":    str,    # Nilai maksimum WL
            "minimum_wl":     str,    # Nilai minimum WL
            "data_jam":       dict | list,  # Data jam tertentu atau semua jam
            # === FIELD TTS (Text-to-Speech friendly) ===
            "tts_tma":        str,    # TMA untuk TTS: "106 koma 64 meter"
            "tts_status":     str,    # Status untuk TTS: "ONLINE"
            "tts_last_update":str,    # Update untuk TTS: "13 Mei 2026 jam 11 30"
            "tts_status_siaga":str,   # Siaga untuk TTS
            "tts_rata_wl":    str,    # Rata-rata WL untuk TTS
            "tts_maks_wl":    str,    # Maks WL untuk TTS
            "tts_min_wl":     str,    # Min WL untuk TTS
            "tts_ringkasan":  str,    # Ringkasan lengkap siap baca TTS
        }

    PENTING untuk asisten:
    - Nilai water level menggunakan koma sebagai pemisah desimal.
      Contoh: "106,64" dibaca "seratus enam koma enam empat meter"
    - Jam dibaca tanpa titik dua, contoh: "01:00" dibaca "jam 01 00"
    - Jika pengguna tanya jam tertentu, sampaikan nilai jam tersebut dari data_jam.
    - Jika nilai "-" atau null berarti data belum tersedia untuk jam itu.
    - Satuan water level adalah meter (m), Qout dan Qinflow adalah m3/detik.
    """
    nama_stasiun = stasiun.strip().lower()
    station_id   = STASIUN_MAP.get(nama_stasiun, stasiun.strip())

    if not station_id:
        return {"success": False, "error": "Nama stasiun tidak boleh kosong."}

    url = BASE_URL.format(station_id=station_id)

    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json, text/html",
                "User-Agent": "Mozilla/5.0",
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_bytes = resp.read()
            raw_text  = raw_bytes.decode("utf-8")

        # Response bisa wrapped JSON {"value": "<html>..."} atau HTML langsung
        html = raw_text
        try:
            parsed_json = json.loads(raw_text)
            if isinstance(parsed_json, dict) and "value" in parsed_json:
                html = parsed_json["value"]
        except (json.JSONDecodeError, KeyError):
            pass  # Bukan JSON, gunakan sebagai HTML langsung

        if not html:
            return {"success": False, "error": "Response kosong dari server."}

        # ── Parse Summary ──────────────────────────────────────────────────────

        # Nama stasiun dari H1
        nama_match = re.search(r"<h1>([^<]+)</h1>", html, re.IGNORECASE)
        nama_stasiun_resmi = nama_match.group(1).strip() if nama_match else station_id

        station_type = _get_text_by_id(html, "NXPSummaryStationType") or "-"
        status_raw   = _get_text_by_id(html, "NXPSummaryStatus") or "-"
        last_update  = _get_text_by_id(html, "NXPSummaryLastUpdate") or "-"
        tma_terbaru  = _get_text_by_id(html, "NXPSummaryValue") or "-"
        kondisi      = _get_text_by_id(html, "NXPSummaryCondition") or "-"
        siaga_raw    = _get_text_by_id(html, "NXPSummarySIAGAStatus") or "-"

        # Bersihkan status (ambil ONLINE/OFFLINE saja)
        status = "ONLINE" if "ONLINE" in status_raw.upper() else                  "OFFLINE" if "OFFLINE" in status_raw.upper() else status_raw

        # Bersihkan siaga — hapus tag font, ambil teks bersih
        siaga = re.sub(r"<[^>]+>", " ", siaga_raw).strip()
        siaga = re.sub(r"\s+", " ", siaga).strip()

        # Ambil rata-rata, maks, min dari tabel summary
        rata_match = re.search(r"Average.*?WL\s*:\s*([\d.]+)", html, re.DOTALL)
        maks_match = re.search(r"Maximum.*?WL\s*:\s*([^,<]+),\s*([\d.]+)", html, re.DOTALL)
        mini_match = re.search(r"Minimum.*?WL\s*:\s*([^,<]+),\s*([\d.]+)", html, re.DOTALL)

        rata_wl = _fmt(rata_match.group(1)) if rata_match else None
        maks_wl = f"{maks_match.group(2).replace('.', ',')} (tgl {maks_match.group(1).strip()})" if maks_match else None
        mini_wl = f"{mini_match.group(2).replace('.', ',')} (tgl {mini_match.group(1).strip()})" if mini_match else None

        # Format TMA terbaru pakai koma
        tma_clean = re.sub(r"[^\d.]", "", tma_terbaru.split("m")[0]).strip()
        tma_fmt   = _fmt(tma_clean) if tma_clean else tma_terbaru

        # ── Parse Data Per Jam ─────────────────────────────────────────────────

        semua_jam = _parse_hourly_data(html)

        if jam is not None:
            # Filter jam tertentu
            if not (0 <= jam <= 23):
                return {"success": False, "error": "Jam harus antara 0 dan 23."}
            data_jam_result = next(
                (d for d in semua_jam if d["jam"] == f"{jam:02d}:00"),
                {"jam": f"{jam:02d}:00", "jam_tts": _fmt_tts_jam(f"{jam:02d}:00"), "tersedia": False, "waterlevel_m": None, "waterlevel_tts": None}
            )
        else:
            # Kembalikan data jam terakhir yang tersedia
            data_jam_result = next(
                (d for d in reversed(semua_jam) if d["tersedia"]),
                None
            )

        # ── Format TTS untuk Summary ───────────────────────────────────────────

        tts_tma = _fmt_tts_tma(tma_clean) if tma_clean else "tidak tersedia"
        tts_status = _fmt_tts_status(status)
        tts_last_update = _fmt_tts_waktu_lengkap(last_update) if last_update != "-" else "tidak tersedia"
        tts_siaga = siaga if siaga else "tidak tersedia"
        tts_rata_wl = _fmt_tts_tma(rata_match.group(1)) if rata_match else "tidak tersedia"
        tts_maks_wl = _fmt_tts_tma(maks_match.group(2)) if maks_match else "tidak tersedia"
        tts_min_wl = _fmt_tts_tma(mini_match.group(2)) if mini_match else "tidak tersedia"

        # Ringkasan TTS lengkap
        tts_ringkasan = (
            f"Data stasiun {nama_stasiun_resmi}. "
            f"Status {tts_status}. "
            f"Update terakhir {tts_last_update}. "
            f"Tinggi muka air {tts_tma}. "
            f"Status siaga {tts_siaga}. "
            f"Rata-rata water level {tts_rata_wl}. "
            f"Water level maksimum {tts_maks_wl}. "
            f"Water level minimum {tts_min_wl}."
        )

        # Ringkasan TTS untuk data jam tertentu
        if data_jam_result and data_jam_result.get("tersedia"):
            tts_ringkasan_jam = (
                f"Data {nama_stasiun_resmi} pada {data_jam_result['jam_tts']}. "
                f"Tinggi muka air {data_jam_result.get('waterlevel_tts', 'tidak tersedia')}. "
                f"Debit keluar {data_jam_result.get('qout_tts', 'tidak tersedia')}. "
                f"Debit masuk {data_jam_result.get('qinflow_tts', 'tidak tersedia')}."
            )
        else:
            tts_ringkasan_jam = f"Data {nama_stasiun_resmi} pada {_fmt_tts_jam(f'{jam:02d}:00' if jam is not None else 'terakhir')} tidak tersedia."

        logger.info(
            f"[baca_data_awlr] {nama_stasiun_resmi} → "
            f"Status={status} | TMA={tma_fmt} | Update={last_update}"
        )

        return {
            "success":       True,
            "stasiun":       nama_stasiun_resmi,
            "station_type":  station_type.strip(),
            "status":        status,
            "last_update":   last_update,
            "tma_terbaru":   tma_fmt,
            "kondisi":       kondisi,
            "status_siaga":  siaga,
            "rata_rata_wl":  rata_wl,
            "maksimum_wl":   maks_wl,
            "minimum_wl":    mini_wl,
            "data_jam":      data_jam_result,
            "semua_jam":     semua_jam,
            # === FIELD TTS ===
            "tts_tma":       tts_tma,
            "tts_status":    tts_status,
            "tts_last_update": tts_last_update,
            "tts_status_siaga": tts_siaga,
            "tts_rata_wl":   tts_rata_wl,
            "tts_maks_wl":   tts_maks_wl,
            "tts_min_wl":    tts_min_wl,
            "tts_ringkasan": tts_ringkasan,
            "tts_ringkasan_jam": tts_ringkasan_jam,
        }

    except urllib.error.URLError as e:
        logger.error(f"[baca_data_awlr] Koneksi gagal: {e}")
        return {"success": False, "error": f"Koneksi ke server Jasa Tirta gagal: {e}"}
    except Exception as e:
        logger.error(f"[baca_data_awlr] Error tidak terduga: {e}")
        return {"success": False, "error": str(e)}


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("MCP Server 'jasatirta_metering' starting...")
    mcp.run(transport="stdio")
