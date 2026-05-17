# firebase_metering.py - Ambil data sensor KLOPLOGGER dari Firebase Realtime Database
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import sys
import logging
import logging.handlers
import json
import os
import urllib.request
import urllib.error
from datetime import datetime

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
            "firebase_metering.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    return logger

logger = _setup_logger("firebase_metering")

# ── Windows UTF-8 fix ──────────────────────────────────────────────────────────

if sys.platform == "win32":
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

# ── Konstanta Firebase ─────────────────────────────────────────────────────────

FIREBASE_BASE_URL = (
    "https://tekno-iot-default-rtdb.asia-southeast1.firebasedatabase.app"
    "/KLOPLOGGER/{device_id}/METERING.json"
)

# ── Mapping Nama Stasiun → Device ID ──────────────────────────────────────────
# Tambahkan stasiun baru di sini dengan format:
# "nama_stasiun": "DEVICE_ID_FIREBASE",

STASIUN_MAP = {
    "bendo":  "TAR012CSS27202305",
    # "karanglo": "KRG001XYZ...",   # ← contoh tambah stasiun lain
}

# Mapping urutan nilai dalam field LIST
LIST_FIELDS = [
    "tma_meter",        # Tinggi Muka Air / Elevasi (meter)
    "suhu_celsius",     # Temperatur udara (°C)
    "voltase_baterai",  # Tegangan baterai (V)
    "ampere_baterai",   # Arus baterai (A)
    "persen_baterai",   # Kapasitas baterai (%)
    "voltase_pv",       # Tegangan Panel Surya / PV (V)
    "ampere_pv",        # Arus Panel Surya / PV (A)
]

mcp = FastMCP("firebase_metering")

# ── Helper: parse field LIST ───────────────────────────────────────────────────

def _parse_list_field(raw: str) -> dict:
    """
    Urai string LIST dari Firebase ke dict yang mudah dibaca.
    Format: "DD/MM/YYYY HH:MM:SS;val1,val2,val3,..."
    """
    try:
        bagian = raw.split(";", 1)
        if len(bagian) != 2:
            return {"raw": raw, "error": "Format LIST tidak dikenali"}

        timestamp_str, nilai_str = bagian
        nilai = nilai_str.split(",")

        # Parse timestamp
        try:
            dt = datetime.strptime(timestamp_str.strip(), "%d/%m/%Y %H:%M:%S")
            timestamp_iso = dt.isoformat()
        except ValueError:
            timestamp_iso = timestamp_str.strip()

        hasil = {
            "timestamp": timestamp_iso,
            "timestamp_lokal": timestamp_str.strip(),
        }

        for i, field_name in enumerate(LIST_FIELDS):
            if i < len(nilai):
                try:
                    hasil[field_name] = float(nilai[i].strip())
                except ValueError:
                    hasil[field_name] = nilai[i].strip()
            else:
                hasil[field_name] = None

        return hasil

    except Exception as e:
        return {"raw": raw, "error": f"Gagal parse: {e}"}


# ── Helper: Format untuk TTS (Text-to-Speech) ──────────────────────────────────

def _format_angka_tts(angka: float | int | None, satuan: str = "") -> str:
    """
    Format angka desimal untuk TTS agar dibaca benar di Xiaozhi AI.
    Contoh: 1.23 -> "1 koma 23", 11.5 -> "11 koma 5"
    """
    if angka is None:
        return "tidak tersedia"

    angka_str = str(angka)
    # Ganti titik desimal dengan kata "koma" untuk TTS
    angka_tts = angka_str.replace(".", " koma ")

    if satuan:
        return f"{angka_tts} {satuan}"
    return angka_tts


def _format_waktu_tts(timestamp_str: str) -> dict:
    """
    Format waktu DD/MM/YYYY HH:MM:SS untuk TTS yang benar.
    Contoh: "13/05/2026 11:30:45" -> 
            tanggal: "13 Mei 2026"
            waktu: "jam 11 lewat 30 menit 45 detik"
    """
    try:
        dt = datetime.strptime(timestamp_str.strip(), "%d/%m/%Y %H:%M:%S")

        # Format tanggal: "13 Mei 2026"
        bulan_map = {
            1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
            5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
            9: "September", 10: "Oktober", 11: "November", 12: "Desember"
        }
        tanggal_tts = f"{dt.day} {bulan_map[dt.month]} {dt.year}"

        # Format waktu: "jam 11 lewat 30 menit 45 detik"
        waktu_tts = f"jam {dt.hour} lewat {dt.minute} menit"
        if dt.second > 0:
            waktu_tts += f" {dt.second} detik"

        return {
            "tanggal_tts": tanggal_tts,
            "waktu_tts": waktu_tts,
            "datetime_tts": f"{tanggal_tts} {waktu_tts}"
        }
    except ValueError:
        # Fallback jika parsing gagal
        return {
            "tanggal_tts": timestamp_str,
            "waktu_tts": timestamp_str,
            "datetime_tts": timestamp_str
        }


def _format_persen_tts(nilai: float | int | None) -> str:
    """Format persentase untuk TTS."""
    if nilai is None:
        return "tidak tersedia"
    return _format_angka_tts(nilai, "persen")


def _format_tegangan_tts(nilai: float | int | None) -> str:
    """Format tegangan untuk TTS."""
    if nilai is None:
        return "tidak tersedia"
    return _format_angka_tts(nilai, "volt")


def _format_arus_tts(nilai: float | int | None) -> str:
    """Format arus untuk TTS."""
    if nilai is None:
        return "tidak tersedia"
    return _format_angka_tts(nilai, "ampere")


def _format_suhu_tts(nilai: float | int | None) -> str:
    """Format suhu untuk TTS."""
    if nilai is None:
        return "tidak tersedia"
    return _format_angka_tts(nilai, "derajat celsius")


def _format_tma_tts(nilai: float | int | None) -> str:
    """Format TMA (Tinggi Muka Air) untuk TTS."""
    if nilai is None:
        return "tidak tersedia"
    return _format_angka_tts(nilai, "meter")


def _format_sinyal_tts(nilai: int | None) -> str:
    """Format sinyal untuk TTS."""
    if nilai is None:
        return "tidak tersedia"
    return f"{nilai} desibel mili"


# ── Tool: baca_data_metering ───────────────────────────────────────────────────

@mcp.tool()
def baca_data_metering(
    device_id: str = "TAR012CSS27202305",
) -> dict:
    """
    Membaca data sensor terbaru dari stasiun KLOPLOGGER via Firebase Realtime Database.

    KAPAN pakai tool ini:
    - Pengguna tanya tentang kondisi air, tinggi muka air, atau TMA
    - Ada kata 'ketinggian air', 'debit', 'banjir', 'elevasi', 'TMA'
    - Ada nama stasiun seperti 'bendo' atau nama lain yang terdaftar
    - Contoh: "TMA Bendo sekarang?", "kondisi air di Bendo?", "elevasi bendo?"
    - Pengguna tanya status baterai atau panel surya stasiun sensor
    - Ada kata 'sensor', 'stasiun', 'KLOPLOGGER', 'metering', 'monitoring'
    - Pertanyaan seperti: "berapa tinggi air sekarang?", "kondisi baterai stasiun?",
      "cek data sensor terbaru", "suhu di stasiun berapa?"
    - Jika membaca data tanggal di baca tanggal bulan tahun jam menit detik, untuk memudahkan pembacaan tanggal
    - Semua angka desimal dibaca dengan "koma" bukan "titik"
    - Contoh: 1.23 dibaca "1 koma 23", 11.30 dibaca "11 koma 30"

    JANGAN pakai untuk:
    - Pertanyaan cuaca umum yang tidak terkait stasiun sensor ini
    - Data historis panjang (tool ini hanya ambil data terbaru/real-time)

    Args:
        device_id:
            ID perangkat KLOPLOGGER atau nama stasiun (contoh: 'bendo').
            Default: "TAR012CSS27202305".
            Nama stasiun yang didukung: bendo.
            Ganti jika pengguna menyebut ID atau nama stasiun yang berbeda.

    Returns:
        {
            "success":         bool,
            "device_id":       str,   # Device ID yang digunakan (sudah di-resolve)
            "stasiun":         str,   # Nama stasiun (jika pakai nama, misal 'bendo')
            "timestamp":       str,   # Waktu pengukuran (ISO format)
            "timestamp_lokal": str,   # Waktu pengukuran (format lokal DD/MM/YYYY)
            "tma_meter":       float, # Tinggi Muka Air / Elevasi (meter shvp)
            "suhu_celsius":    float, # Temperatur (°C)
            "voltase_baterai": float, # Tegangan baterai (V)
            "ampere_baterai":  float, # Arus baterai (A)
            "persen_baterai":  float, # Kapasitas baterai (%)
            "voltase_pv":      float, # Tegangan Panel Surya (V)
            "ampere_pv":       float, # Arus Panel Surya (A)
            "sinyal_dbm":      int,   # Kekuatan sinyal (dBm)
            "raw":             dict,  # Data mentah dari Firebase
            # === FIELD TTS (Text-to-Speech friendly) ===
            "tts_tma":         str,   # TMA untuk TTS: "1 koma 23 meter"
            "tts_suhu":        str,   # Suhu untuk TTS: "25 koma 5 derajat celsius"
            "tts_volt_baterai":str,   # Voltase baterai untuk TTS
            "tts_amp_baterai": str,   # Arus baterai untuk TTS
            "tts_persen_baterai": str, # Persen baterai untuk TTS
            "tts_volt_pv":     str,   # Voltase PV untuk TTS
            "tts_amp_pv":      str,   # Arus PV untuk TTS
            "tts_sinyal":      str,   # Sinyal untuk TTS: "-85 desibel mili"
            "tts_tanggal":     str,   # Tanggal untuk TTS: "13 Mei 2026"
            "tts_waktu":       str,   # Waktu untuk TTS: "jam 11 lewat 30 menit"
            "tts_datetime":    str,   # Tanggal + waktu lengkap untuk TTS
            "tts_ringkasan":   str,   # Ringkasan lengkap siap baca TTS
        }
    """
    nama_stasiun = device_id.strip().lower()

    # Resolve nama stasiun → Device ID (jika ada di mapping)
    device_id_resolved = STASIUN_MAP.get(nama_stasiun, device_id.strip())

    if not device_id_resolved:
        return {"success": False, "error": "device_id tidak boleh kosong."}

    url = FIREBASE_BASE_URL.format(device_id=device_id_resolved)

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_bytes = resp.read()
            data = json.loads(raw_bytes.decode("utf-8"))

        if data is None:
            return {
                "success": False,
                "error": f"Data untuk device '{device_id_resolved}' tidak ditemukan di Firebase.",
            }

        # Ambil field LIST (data utama)
        list_raw = data.get("LIST", "")
        if not list_raw:
            return {"success": False, "error": "Field LIST kosong atau tidak tersedia."}

        parsed = _parse_list_field(list_raw)

        if "error" in parsed:
            return {"success": False, "error": parsed["error"], "raw": data}

        # Ambil sinyal
        try:
            sinyal = int(data.get("SINYAL", 0))
        except (ValueError, TypeError):
            sinyal = None

        # Format waktu untuk TTS
        waktu_tts = _format_waktu_tts(parsed.get("timestamp_lokal", ""))

        # Buat ringkasan TTS lengkap
        tma_tts = _format_tma_tts(parsed.get("tma_meter"))
        suhu_tts = _format_suhu_tts(parsed.get("suhu_celsius"))
        volt_baterai_tts = _format_tegangan_tts(parsed.get("voltase_baterai"))
        amp_baterai_tts = _format_arus_tts(parsed.get("ampere_baterai"))
        persen_baterai_tts = _format_persen_tts(parsed.get("persen_baterai"))
        volt_pv_tts = _format_tegangan_tts(parsed.get("voltase_pv"))
        amp_pv_tts = _format_arus_tts(parsed.get("ampere_pv"))
        sinyal_tts = _format_sinyal_tts(sinyal)

        ringkasan_tts = (
            f"Data sensor stasiun {nama_stasiun} pada {waktu_tts['datetime_tts']}. "
            f"Tinggi muka air {tma_tts}. "
            f"Suhu udara {suhu_tts}. "
            f"Tegangan baterai {volt_baterai_tts} dengan arus {amp_baterai_tts}. "
            f"Kapasitas baterai {persen_baterai_tts}. "
            f"Tegangan panel surya {volt_pv_tts} dengan arus {amp_pv_tts}. "
            f"Kekuatan sinyal {sinyal_tts}."
        )

        logger.info(
            f"[baca_data_metering] {device_id_resolved} ({nama_stasiun}) → "
            f"TMA={parsed.get('tma_meter')}m | "
            f"Baterai={parsed.get('persen_baterai')}% | "
            f"Sinyal={sinyal}dBm"
        )

        return {
            "success":         True,
            "device_id":       device_id_resolved,
            "stasiun":         nama_stasiun,
            "timestamp":       parsed.get("timestamp"),
            "timestamp_lokal": parsed.get("timestamp_lokal"),
            "tma_meter":       parsed.get("tma_meter"),
            "suhu_celsius":    parsed.get("suhu_celsius"),
            "voltase_baterai": parsed.get("voltase_baterai"),
            "ampere_baterai":  parsed.get("ampere_baterai"),
            "persen_baterai":  parsed.get("persen_baterai"),
            "voltase_pv":      parsed.get("voltase_pv"),
            "ampere_pv":       parsed.get("ampere_pv"),
            "sinyal_dbm":      sinyal,
            "raw":             data,
            # === FIELD TTS ===
            "tts_tma":         tma_tts,
            "tts_suhu":        suhu_tts,
            "tts_volt_baterai": volt_baterai_tts,
            "tts_amp_baterai": amp_baterai_tts,
            "tts_persen_baterai": persen_baterai_tts,
            "tts_volt_pv":     volt_pv_tts,
            "tts_amp_pv":      amp_pv_tts,
            "tts_sinyal":      sinyal_tts,
            "tts_tanggal":     waktu_tts["tanggal_tts"],
            "tts_waktu":       waktu_tts["waktu_tts"],
            "tts_datetime":    waktu_tts["datetime_tts"],
            "tts_ringkasan":   ringkasan_tts,
        }

    except urllib.error.URLError as e:
        logger.error(f"[baca_data_metering] Koneksi gagal: {e}")
        return {"success": False, "error": f"Koneksi ke Firebase gagal: {e}"}
    except json.JSONDecodeError as e:
        logger.error(f"[baca_data_metering] JSON tidak valid: {e}")
        return {"success": False, "error": f"Response bukan JSON valid: {e}"}
    except Exception as e:
        logger.error(f"[baca_data_metering] Error tidak terduga: {e}")
        return {"success": False, "error": str(e)}


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("MCP Server 'firebase_metering' starting...")
    mcp.run(transport="stdio")
