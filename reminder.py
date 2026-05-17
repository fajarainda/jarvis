# reminder.py — Jalur B: Early Warning TMA dengan suara langsung ke Otto
#
# Cara kerja:
#   1. Scheduler background cek TMA Bendo/Ngasinan setiap 5 menit
#   2. Saat TMA melewati threshold → kirim HTTP POST ke Otto port 8080
#   3. Otto terima perintah MCP tool "self.speak_alert" → buka sesi Xiaozhi
#   4. Server Xiaozhi TTS-kan teks alert → Otto berbicara langsung
#
# Tools MCP yang tersedia untuk AI:
#   set_threshold()    — aktifkan early warning TMA
#   set_reminder()     — jadwal bacaan TMA terjadwal
#   cancel_reminder()  — batalkan reminder/threshold
#   list_reminders()   — lihat semua yang aktif
#   cek_alert()        — (fallback) polling antrian alert
#
# State disimpan di reminder_state.json → auto-restore saat restart
#
from __future__ import annotations

import json
import logging
import logging.handlers
import math
import os
import re
import sys
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

from mcp.server.fastmcp import FastMCP

# ── Logging ────────────────────────────────────────────────────────────────────

def _setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    try:
        fh = logging.handlers.RotatingFileHandler(
            "reminder.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    return logger

logger = _setup_logger("reminder")

if sys.platform == "win32":
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

# ── Konstanta ──────────────────────────────────────────────────────────────────

CHECK_INTERVAL_THRESHOLD = 5 * 60      # cek threshold setiap 5 menit
OTTO_HTTP_PORT           = 8080        # port WebSocket/HTTP Otto
OTTO_HTTP_TIMEOUT        = 8           # timeout request ke Otto (detik)
RETRY_ATTEMPTS           = 3           # jumlah retry kirim ke Otto
RETRY_DELAY              = 2           # detik antar retry

# Diisi dari config.json saat startup
OTTO_IP: str = ""

# ── Endpoint data sensor ───────────────────────────────────────────────────────

JASATIRTA_URL = (
    "https://telemetri.jasatirta1.co.id/web/html/modules/monitor/detaildata/xmlhttp"
    "?idrec={station_id}&type=GSMAWLR"
)
FIREBASE_URL = (
    "https://tekno-iot-default-rtdb.asia-southeast1.firebasedatabase.app"
    "/KLOPLOGGER/{device_id}/METERING.json"
)

STASIUN_AWLR     = {"ngasinan": "Ngasinan"}
STASIUN_FIREBASE = {"bendo": "TAR012CSS27202305"}

# ── Storage ────────────────────────────────────────────────────────────────────

_reminders: dict[str, dict] = {}
_lock    = threading.Lock()
_counter = 0

def _new_id() -> str:
    global _counter
    _counter += 1
    return f"R{_counter:04d}"

# ── Antrian alert (fallback jika Otto tidak bisa dicapai) ─────────────────────

_alert_queue: list[dict] = []
_alert_lock  = threading.Lock()

def _enqueue_alert_fallback(rid: str, teks: str) -> None:
    with _alert_lock:
        _alert_queue.append({
            "rid":   rid,
            "teks":  teks,
            "waktu": datetime.now().isoformat(),
        })
    logger.warning(f"[enqueue_fallback] Alert {rid} masuk antrian fallback "
                   f"(total={len(_alert_queue)}). "
                   f"Akan dibacakan saat Otto aktif & cek_alert() dipanggil.")

# ── Kirim perintah speak_alert ke Otto via HTTP ───────────────────────────────

def _send_to_otto(teks: str, rid: str) -> bool:
    """
    HTTP POST ke Otto WebSocket control server port 8080.
    Format: MCP JSON-RPC tools/call → self.speak_alert
    Otto akan membuka sesi Xiaozhi dan TTS-kan teks.

    Return True jika berhasil, False jika gagal semua retry.
    """
    if not OTTO_IP:
        logger.warning("[send_to_otto] OTTO_IP belum diset, masuk antrian fallback.")
        _enqueue_alert_fallback(rid, teks)
        return False

    url = f"http://{OTTO_IP}:{OTTO_HTTP_PORT}/ws"

    payload = json.dumps({
        "type": "mcp",
        "payload": {
            "jsonrpc": "2.0",
            "method":  "tools/call",
            "params": {
                "name":      "self.speak_alert",
                "arguments": {"text": teks}
            },
            "id": 1
        }
    }, ensure_ascii=False).encode("utf-8")

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept":       "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=OTTO_HTTP_TIMEOUT) as resp:
                status = resp.status
                body   = resp.read().decode("utf-8", errors="replace")
                if status == 200:
                    logger.info(
                        f"[send_to_otto] OK (attempt {attempt}): "
                        f"status={status} | response={body[:80]}"
                    )
                    return True
                else:
                    logger.warning(
                        f"[send_to_otto] HTTP {status} (attempt {attempt}): {body[:80]}"
                    )

        except urllib.error.URLError as e:
            logger.warning(f"[send_to_otto] URLError (attempt {attempt}): {e}")
        except Exception as e:
            logger.warning(f"[send_to_otto] Error (attempt {attempt}): {e}")

        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_DELAY)

    logger.error(
        f"[send_to_otto] Semua {RETRY_ATTEMPTS} attempt gagal. "
        f"Alert masuk antrian fallback."
    )
    _enqueue_alert_fallback(rid, teks)
    return False

# ── Helper: format TTS ─────────────────────────────────────────────────────────

def _fmt(val) -> str:
    if val is None:
        return "tidak tersedia"
    try:
        f = float(val)
        s = f"{f:.2f}".rstrip("0").rstrip(".")
        return s.replace(".", " koma ")
    except (ValueError, TypeError):
        return str(val)

def _fmt_meter(val) -> str:
    return f"{_fmt(val)} meter"

def _bulan(n: int) -> str:
    return ["","Januari","Februari","Maret","April","Mei","Juni",
            "Juli","Agustus","September","Oktober","November","Desember"][n]

# ── Helper: parse HTML Jasa Tirta ─────────────────────────────────────────────

def _get_text_by_id(html: str, element_id: str) -> str | None:
    m = re.search(
        rf'id="{re.escape(element_id)}"[^>]*>(.*?)</(?:div|td|th|span|b|font)>',
        html, re.DOTALL | re.IGNORECASE
    )
    if m:
        raw = re.sub(r"<[^>]+>", " ", m.group(1)).strip()
        return re.sub(r"\s+", " ", raw).strip() or None
    return None

# ── Fetch TMA sebagai float ───────────────────────────────────────────────────

def _fetch_tma_float_firebase(device_id: str) -> tuple[float | None, str]:
    try:
        url = FIREBASE_URL.format(device_id=device_id)
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data:
            return None, "Data tidak tersedia."
        list_raw = data.get("LIST", "")
        if not list_raw:
            return None, "Field LIST kosong."
        bagian = list_raw.split(";", 1)
        if len(bagian) != 2:
            return None, "Format LIST tidak dikenali."
        nilai = bagian[1].split(",")
        tma = float(nilai[0].strip()) if nilai else None
        return tma, ""
    except Exception as e:
        return None, str(e)

def _fetch_tma_float_awlr(station_id: str) -> tuple[float | None, str]:
    try:
        url = JASATIRTA_URL.format(station_id=station_id)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
        try:
            j = json.loads(raw)
            if isinstance(j, dict) and "value" in j:
                raw = j["value"]
        except (json.JSONDecodeError, KeyError):
            pass
        tma_raw   = _get_text_by_id(raw, "NXPSummaryValue") or ""
        tma_clean = re.sub(r"[^\d.]", "", tma_raw.split("m")[0]).strip()
        tma = float(tma_clean) if tma_clean else None
        return tma, ""
    except Exception as e:
        return None, str(e)

def _fetch_tma_float(stasiun: str) -> tuple[float | None, str]:
    s = stasiun.lower().strip()
    if s in STASIUN_AWLR:
        return _fetch_tma_float_awlr(STASIUN_AWLR[s])
    if s in STASIUN_FIREBASE:
        return _fetch_tma_float_firebase(STASIUN_FIREBASE[s])
    return None, f"Stasiun '{stasiun}' tidak dikenali."

# ── Fetch ringkasan teks TTS ──────────────────────────────────────────────────

def _fetch_ringkasan_ngasinan() -> str:
    try:
        url = JASATIRTA_URL.format(station_id="Ngasinan")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
        try:
            j = json.loads(raw)
            if isinstance(j, dict) and "value" in j:
                raw = j["value"]
        except (json.JSONDecodeError, KeyError):
            pass
        status_raw  = _get_text_by_id(raw, "NXPSummaryStatus") or "-"
        last_update = _get_text_by_id(raw, "NXPSummaryLastUpdate") or "-"
        tma_raw     = _get_text_by_id(raw, "NXPSummaryValue") or "-"
        siaga_raw   = _get_text_by_id(raw, "NXPSummarySIAGAStatus") or "-"
        status      = "ONLINE" if "ONLINE" in status_raw.upper() else "OFFLINE"
        siaga       = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", siaga_raw)).strip()
        tma_clean   = re.sub(r"[^\d.]", "", tma_raw.split("m")[0]).strip()
        tma_tts     = _fmt_meter(tma_clean) if tma_clean else "tidak tersedia"
        waktu_tts   = last_update
        try:
            dt = datetime.strptime(last_update.strip(), "%d/%m/%Y %H:%M:%S")
            waktu_tts = f"{dt.day} {_bulan(dt.month)} {dt.year} jam {dt.hour}"
        except ValueError:
            pass
        return (
            f"Pengingat TMA Ngasinan. Status {status}. "
            f"Update terakhir {waktu_tts}. "
            f"Tinggi muka air {tma_tts}. "
            f"Status siaga {siaga}."
        )
    except Exception as e:
        return f"Gagal mengambil data TMA Ngasinan. Error: {e}"

def _fetch_ringkasan_bendo() -> str:
    try:
        url = FIREBASE_URL.format(device_id="TAR012CSS27202305")
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data:
            return "Data stasiun Bendo tidak tersedia."
        list_raw = data.get("LIST", "")
        bagian   = list_raw.split(";", 1)
        if len(bagian) != 2:
            return "Format data Bendo tidak dikenali."
        timestamp_str, nilai_str = bagian
        nilai  = nilai_str.split(",")
        def sf(s):
            try: return float(s.strip())
            except: return None
        tma    = sf(nilai[0]) if len(nilai) > 0 else None
        persen = sf(nilai[4]) if len(nilai) > 4 else None
        tma_tts = _fmt_meter(tma) if tma is not None else "tidak tersedia"
        bat_tts = f"{_fmt(persen)} persen" if persen is not None else "tidak tersedia"
        try:
            dt = datetime.strptime(timestamp_str.strip(), "%d/%m/%Y %H:%M:%S")
            waktu_tts = (
                f"{dt.day} {_bulan(dt.month)} {dt.year} "
                f"jam {dt.hour} lewat {dt.minute} menit"
            )
        except ValueError:
            waktu_tts = timestamp_str.strip()
        return (
            f"Pengingat sensor Bendo. Data pada {waktu_tts}. "
            f"Tinggi muka air {tma_tts}. "
            f"Kapasitas baterai {bat_tts}."
        )
    except Exception as e:
        return f"Gagal mengambil data Bendo. Error: {e}"

def _fetch_ringkasan(stasiun: str, pesan_custom: str = "") -> str:
    if pesan_custom:
        return pesan_custom
    s = stasiun.lower().strip()
    if s in STASIUN_AWLR:
        return _fetch_ringkasan_ngasinan()
    if s in STASIUN_FIREBASE:
        return _fetch_ringkasan_bendo()
    return _fetch_ringkasan_ngasinan()

# ── Persist state ──────────────────────────────────────────────────────────────

def _state_file() -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "reminder_state.json"
    )

def _save_state() -> None:
    try:
        data = {
            "_counter": _counter,
            "reminders": {
                rid: {k: v for k, v in rem.items() if k != "thread"}
                for rid, rem in _reminders.items()
            },
        }
        tmp = _state_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _state_file())
    except Exception as e:
        logger.error(f"[save_state] Gagal: {e}")

def _load_and_restore() -> None:
    global _counter
    path = _state_file()
    if not os.path.exists(path):
        logger.info("[load_state] Tidak ada file state, mulai bersih.")
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"[load_state] Gagal baca state: {e}")
        return

    _counter  = data.get("_counter", 0)
    reminders = data.get("reminders", {})
    restored  = 0

    for rid, rem in reminders.items():
        rem["thread"] = None
        _reminders[rid] = rem
        if not rem.get("aktif"):
            continue
        tipe    = rem.get("tipe")
        stasiun = rem.get("stasiun", "ngasinan")

        if tipe == "threshold":
            last_lvl = rem.get("last_level")
            t = threading.Thread(
                target=_scheduler_threshold,
                args=(rid, stasiun,
                      float(rem.get("threshold_min", 0)),
                      float(rem.get("step", 0.10)),
                      last_lvl),
                daemon=True, name=f"threshold-{rid}",
            )
            rem["thread"] = t
            t.start()
            restored += 1

        elif tipe == "berulang":
            interval = int(rem.get("interval_menit", 30))
            pesan    = rem.get("pesan_custom", "")
            t = threading.Thread(
                target=_scheduler_berulang,
                args=(rid, interval, stasiun, pesan),
                daemon=True, name=f"rem-{rid}",
            )
            rem["thread"] = t
            t.start()
            restored += 1

        elif tipe == "sekali":
            next_run_str = rem.get("next_run", "")
            try:
                target_dt = datetime.fromisoformat(next_run_str)
            except Exception:
                rem["aktif"] = False
                continue
            if target_dt <= datetime.now():
                rem["aktif"] = False
                continue
            pesan = rem.get("pesan_custom", "")
            t = threading.Thread(
                target=_scheduler_sekali,
                args=(rid, target_dt, stasiun, pesan),
                daemon=True, name=f"rem-{rid}",
            )
            rem["thread"] = t
            t.start()
            restored += 1

    _save_state()
    logger.info(f"[load_state] Selesai: {restored} item dipulihkan.")

# ── Scheduler: sekali ─────────────────────────────────────────────────────────

def _scheduler_sekali(rid: str, target: datetime,
                       stasiun: str, pesan_custom: str) -> None:
    sisa = (target - datetime.now()).total_seconds()
    if sisa > 0:
        logger.info(f"[{rid}] Menunggu {sisa:.0f}s → {target.strftime('%H:%M')}")
        time.sleep(sisa)
    with _lock:
        if not _reminders.get(rid, {}).get("aktif"):
            return
    teks = _fetch_ringkasan(stasiun, pesan_custom)
    logger.info(f"[{rid}] Kirim ke Otto: {teks[:60]}...")
    _send_to_otto(teks, rid)
    with _lock:
        if rid in _reminders:
            _reminders[rid]["aktif"] = False
    _save_state()

# ── Scheduler: berulang ───────────────────────────────────────────────────────

def _scheduler_berulang(rid: str, interval_menit: int,
                         stasiun: str, pesan_custom: str) -> None:
    logger.info(f"[{rid}] Berulang setiap {interval_menit} menit")
    while True:
        with _lock:
            if not _reminders.get(rid, {}).get("aktif"):
                return
        teks = _fetch_ringkasan(stasiun, pesan_custom)
        logger.info(f"[{rid}] Kirim ke Otto: {teks[:60]}...")
        _send_to_otto(teks, rid)
        with _lock:
            if rid in _reminders:
                _reminders[rid]["next_run"] = (
                    datetime.now() + timedelta(minutes=interval_menit)
                ).isoformat()
        time.sleep(interval_menit * 60)

# ── Scheduler: threshold ──────────────────────────────────────────────────────
#
# level = floor((TMA - threshold_min) / step)
#   None  → TMA di bawah threshold (diam)
#   int   → level terakhir yang sudah di-alert
#
# Alert dikirim setiap kali level berubah (naik ATAU turun).
# Saat TMA turun di bawah threshold_min → reset ke None.

def _scheduler_threshold(rid: str, stasiun: str,
                          threshold_min: float, step: float,
                          resume_level: int | None = None) -> None:
    logger.info(
        f"[{rid}] Threshold aktif: stasiun={stasiun} "
        f"min={threshold_min} step={step}"
    )
    last_level: int | None = resume_level

    while True:
        with _lock:
            if not _reminders.get(rid, {}).get("aktif"):
                logger.info(f"[{rid}] Threshold dihentikan.")
                return

        tma, err = _fetch_tma_float(stasiun)

        if tma is None:
            logger.warning(f"[{rid}] Gagal ambil TMA: {err}")
        else:
            logger.debug(
                f"[{rid}] TMA={tma:.3f} min={threshold_min} "
                f"last_level={last_level}"
            )

            if tma < threshold_min:
                if last_level is not None:
                    logger.info(
                        f"[{rid}] TMA {tma:.3f} < min {threshold_min} → reset."
                    )
                    last_level = None
            else:
                level = math.floor((tma - threshold_min) / step + 1e-9)

                if level != last_level:
                    if last_level is None:
                        arah = "mencapai"
                    elif level > last_level:
                        arah = "naik ke"
                    else:
                        arah = "turun ke"

                    batas_tma = threshold_min + level * step
                    tma_tts   = _fmt_meter(tma)
                    batas_tts = _fmt_meter(batas_tma)

                    teks = (
                        f"Peringatan! TMA {stasiun.capitalize()} {arah} {batas_tts}. "
                        f"Pembacaan saat ini {tma_tts}. "
                        f"Mohon segera periksa kondisi lapangan."
                    )

                    logger.info(
                        f"[{rid}] ALERT level={level} ({arah}): "
                        f"TMA={tma:.3f} → kirim ke Otto"
                    )
                    _send_to_otto(teks, rid)
                    last_level = level

        with _lock:
            if rid in _reminders:
                _reminders[rid]["last_tma"]   = tma
                _reminders[rid]["last_level"] = last_level
                _reminders[rid]["last_check"] = datetime.now().isoformat()
        _save_state()

        time.sleep(CHECK_INTERVAL_THRESHOLD)

# ── MCP Server ─────────────────────────────────────────────────────────────────

mcp = FastMCP("reminder")

# ── Tool: set_reminder ────────────────────────────────────────────────────────

@mcp.tool()
def set_reminder(
    stasiun: str = "ngasinan",
    jam: str = "",
    interval_menit: int = 0,
    pesan_custom: str = "",
) -> dict:
    """
    Jadwalkan pengingat data TMA pada jam tertentu atau secara berulang.
    Otto akan berbicara langsung saat waktunya tiba.

    KAPAN pakai:
    - "ingatkan TMA ngasinan jam 6 pagi"
    - "bacakan TMA Bendo jam 17:30"
    - "bacakan setiap 30 menit"
    - "setiap 1 jam bacakan TMA Ngasinan"

    Jangan pakai untuk alert threshold → pakai set_threshold().

    Args:
        stasiun:        "ngasinan" atau "bendo". Default: "ngasinan".
        jam:            Waktu sekali: "6", "17:30". Kosong jika berulang.
        interval_menit: Interval menit berulang (>0). 0 = tidak berulang.
        pesan_custom:   Teks langsung dibacakan (kosong = ambil data sensor).

    Returns:
        { success, reminder_id, tipe, stasiun, jadwal, tts_konfirmasi }
    """
    if not jam and interval_menit <= 0 and not pesan_custom:
        return {"success": False,
                "error": "Tentukan jam (misal '6') atau interval_menit (misal 30)."}

    semua = {**STASIUN_AWLR, **STASIUN_FIREBASE}
    if stasiun.lower().strip() not in semua:
        return {"success": False,
                "error": f"Stasiun '{stasiun}' tidak dikenali. Pilihan: {', '.join(semua)}."}

    rid = _new_id()

    # ── Sekali ────────────────────────────────────────────────────────────────
    if jam and interval_menit <= 0:
        jam_clean = jam.strip().replace(".", ":")
        if ":" not in jam_clean:
            jam_clean = f"{int(jam_clean):02d}:00"
        try:
            h, m = map(int, jam_clean.split(":"))
        except ValueError:
            return {"success": False, "error": f"Format jam tidak valid: '{jam}'"}

        now    = datetime.now()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)

        jadwal_str = f"jam {h:02d}:{m:02d}"
        konfirmasi = (
            f"Baik, saya akan mengingatkan data {stasiun} pada {jadwal_str}. "
            f"ID reminder {rid}."
        )

        with _lock:
            _reminders[rid] = {
                "id": rid, "tipe": "sekali", "stasiun": stasiun,
                "jam": jam_clean, "interval_menit": 0,
                "pesan_custom": pesan_custom, "aktif": True,
                "next_run": target.isoformat(), "thread": None,
            }
        t = threading.Thread(
            target=_scheduler_sekali,
            args=(rid, target, stasiun, pesan_custom),
            daemon=True, name=f"rem-{rid}",
        )
        with _lock:
            _reminders[rid]["thread"] = t
        t.start()
        _save_state()

        return {"success": True, "reminder_id": rid, "tipe": "sekali",
                "stasiun": stasiun, "jadwal": jadwal_str,
                "tts_konfirmasi": konfirmasi}

    # ── Berulang ──────────────────────────────────────────────────────────────
    if interval_menit > 0:
        jam_str = (f"setiap {interval_menit} menit" if interval_menit < 60
                   else f"setiap {interval_menit // 60} jam"
                        + (f" {interval_menit % 60} menit"
                           if interval_menit % 60 else ""))
        konfirmasi = (
            f"Baik, saya akan mengingatkan data {stasiun} {jam_str}. "
            f"ID reminder {rid}."
        )

        with _lock:
            _reminders[rid] = {
                "id": rid, "tipe": "berulang", "stasiun": stasiun,
                "jam": "", "interval_menit": interval_menit,
                "pesan_custom": pesan_custom, "aktif": True,
                "next_run": datetime.now().isoformat(), "thread": None,
            }
        t = threading.Thread(
            target=_scheduler_berulang,
            args=(rid, interval_menit, stasiun, pesan_custom),
            daemon=True, name=f"rem-{rid}",
        )
        with _lock:
            _reminders[rid]["thread"] = t
        t.start()
        _save_state()

        return {"success": True, "reminder_id": rid, "tipe": "berulang",
                "stasiun": stasiun, "jadwal": jam_str,
                "tts_konfirmasi": konfirmasi}

    return {"success": False, "error": "Parameter tidak valid."}


# ── Tool: set_threshold ───────────────────────────────────────────────────────

@mcp.tool()
def set_threshold(
    stasiun: str = "bendo",
    threshold_min: float = 90.70,
    step: float = 0.10,
) -> dict:
    """
    Aktifkan early warning: Otto berbicara langsung saat TMA melewati batas.
    Cek setiap 5 menit. Tidak perlu pengguna bicara dulu.

    KAPAN pakai:
    - "pantau TMA bendo, alert kalau naik dari 90,70 setiap 10 cm"
    - "monitor ngasinan dari 106,50 setiap 5 cm"
    - "aktifkan early warning bendo"

    Logika:
    - TMA >= threshold_min → alert setiap perubahan kelipatan step
    - TMA < threshold_min  → diam, state reset (siap alert lagi nanti)

    Args:
        stasiun:       "bendo" atau "ngasinan". Default: "bendo".
        threshold_min: TMA minimum (meter) untuk mulai alert.
        step:          Kelipatan perubahan TMA (meter) per alert.
                       Default: 0.10 (10 cm).

    Returns:
        { success, reminder_id, stasiun, threshold_min, step, tts_konfirmasi }
    """
    if step <= 0:
        return {"success": False, "error": "step harus lebih dari 0."}

    semua = {**STASIUN_AWLR, **STASIUN_FIREBASE}
    if stasiun.lower().strip() not in semua:
        return {"success": False,
                "error": f"Stasiun '{stasiun}' tidak dikenali. Pilihan: {', '.join(semua)}."}

    rid      = _new_id()
    step_cm  = int(round(step * 100))
    tmin_tts = _fmt_meter(threshold_min)
    step_tts = (f"{step_cm} sentimeter" if step_cm < 100
                else f"{_fmt(step)} meter")

    konfirmasi = (
        f"Siap. Saya akan memantau TMA {stasiun} setiap 5 menit "
        f"dan langsung berbicara setiap kali berubah {step_tts} "
        f"saat masih di atas {tmin_tts}. "
        f"ID monitoring {rid}."
    )

    with _lock:
        _reminders[rid] = {
            "id":            rid,
            "tipe":          "threshold",
            "stasiun":       stasiun,
            "threshold_min": threshold_min,
            "step":          step,
            "aktif":         True,
            "last_tma":      None,
            "last_level":    None,
            "last_check":    None,
            "thread":        None,
        }

    t = threading.Thread(
        target=_scheduler_threshold,
        args=(rid, stasiun, threshold_min, step),
        daemon=True, name=f"threshold-{rid}",
    )
    with _lock:
        _reminders[rid]["thread"] = t
    t.start()
    _save_state()

    return {
        "success":        True,
        "reminder_id":    rid,
        "stasiun":        stasiun,
        "threshold_min":  threshold_min,
        "step":           step,
        "tts_konfirmasi": konfirmasi,
    }


# ── Tool: cancel_reminder ─────────────────────────────────────────────────────

@mcp.tool()
def cancel_reminder(reminder_id: str) -> dict:
    """
    Batalkan reminder atau threshold monitoring.

    KAPAN pakai:
    - "batalkan reminder R0001"
    - "hentikan semua pengingat"
    - "stop monitoring bendo"

    Args:
        reminder_id: ID reminder (misal "R0001") atau "semua"/"all".

    Returns:
        { success, dibatalkan: list[str], tts_konfirmasi }
    """
    dibatalkan = []
    with _lock:
        if reminder_id.lower() in ("semua", "all"):
            for rid, rem in _reminders.items():
                if rem["aktif"]:
                    rem["aktif"] = False
                    dibatalkan.append(rid)
        else:
            rid = reminder_id.strip().upper()
            if rid in _reminders and _reminders[rid]["aktif"]:
                _reminders[rid]["aktif"] = False
                dibatalkan.append(rid)
            else:
                return {
                    "success": False,
                    "error": (f"Reminder '{reminder_id}' tidak ditemukan "
                              f"atau sudah tidak aktif."),
                }

    _save_state()
    konfirmasi = (f"Reminder {', '.join(dibatalkan)} telah dibatalkan."
                  if dibatalkan else "Tidak ada reminder aktif.")
    return {"success": True, "dibatalkan": dibatalkan, "tts_konfirmasi": konfirmasi}


# ── Tool: list_reminders ──────────────────────────────────────────────────────

@mcp.tool()
def list_reminders() -> dict:
    """
    Tampilkan semua reminder dan threshold monitoring yang aktif.

    KAPAN pakai:
    - "reminder apa yang aktif?"
    - "ada monitoring yang berjalan?"
    - "cek pengingat aktif"

    Returns:
        { success, aktif: list[dict], tts_ringkasan }
    """
    with _lock:
        aktif = [
            {k: v for k, v in r.items() if k != "thread"}
            for r in _reminders.values()
            if r["aktif"]
        ]

    if not aktif:
        return {"success": True, "aktif": [],
                "tts_ringkasan": "Tidak ada reminder atau monitoring yang aktif."}

    baris = []
    for r in aktif:
        tipe = r["tipe"]
        if tipe == "sekali":
            try:
                dt    = datetime.fromisoformat(r["next_run"])
                waktu = f"jam {dt.hour:02d}:{dt.minute:02d}"
            except Exception:
                waktu = r.get("next_run", "-")
            baris.append(f"{r['id']}: {r['stasiun']} sekali pada {waktu}")
        elif tipe == "berulang":
            iv = r.get("interval_menit", 0)
            jd = (f"setiap {iv} menit" if iv < 60 else f"setiap {iv // 60} jam")
            baris.append(f"{r['id']}: {r['stasiun']} {jd}")
        elif tipe == "threshold":
            tmin     = r.get("threshold_min", "?")
            stp      = r.get("step", "?")
            ltma     = r.get("last_tma")
            ltma_str = _fmt_meter(ltma) if ltma is not None else "belum dicek"
            step_cm  = int(round(float(stp) * 100)) if stp != "?" else "?"
            baris.append(
                f"{r['id']}: threshold {r['stasiun']} ≥ {_fmt_meter(tmin)} "
                f"per {step_cm} cm — TMA terakhir {ltma_str}"
            )

    return {
        "success":       True,
        "aktif":         aktif,
        "tts_ringkasan": "Aktif: " + "; ".join(baris) + ".",
    }


# ── Tool: cek_alert (fallback) ────────────────────────────────────────────────

@mcp.tool()
def cek_alert() -> dict:
    """
    Cek antrian alert fallback (dipakai saat Otto tidak bisa dicapai via HTTP).

    Dalam Jalur B, reminder.py langsung push ke Otto via HTTP.
    Tool ini hanya sebagai jaring pengaman: jika HTTP gagal, alert masuk
    antrian ini dan dibacakan saat Otto aktif dan memanggil cek_alert().

    CARA PAKAI DI SYSTEM PROMPT OTTO:
    "Setiap kali selesai menjawab, panggil cek_alert().
     Jika ada_alert=true, bacakan field 'teks' persis.
     Jika sisa>0, panggil lagi setelah selesai bicara."

    Returns:
        { ada_alert: bool, teks: str, sisa: int }
    """
    with _alert_lock:
        if not _alert_queue:
            return {"ada_alert": False, "teks": "", "sisa": 0}
        item = _alert_queue.pop(0)
        sisa = len(_alert_queue)

    logger.info(f"[cek_alert] Fallback alert dari {item['rid']}, sisa={sisa}")
    return {"ada_alert": True, "teks": item["teks"], "sisa": sisa}


# ── Tool: set_otto_ip ─────────────────────────────────────────────────────────

@mcp.tool()
def set_otto_ip(ip: str) -> dict:
    """
    Set alamat IP Otto untuk pengiriman alert langsung via HTTP.
    Dipanggil sekali setelah Otto terhubung ke WiFi.

    KAPAN pakai:
    - "set IP Otto 192.168.1.100"
    - "IP Otto berapa sekarang?"

    Args:
        ip: Alamat IP Otto di jaringan lokal. Contoh: "192.168.1.100".
           Kosongkan untuk melihat IP yang sedang aktif.

    Returns:
        { success, otto_ip, tts_konfirmasi }
    """
    global OTTO_IP

    if not ip.strip():
        return {
            "success":        True,
            "otto_ip":        OTTO_IP or "(belum diset)",
            "tts_konfirmasi": (f"IP Otto saat ini {OTTO_IP}."
                               if OTTO_IP else "IP Otto belum diset."),
        }

    OTTO_IP = ip.strip()

    # Simpan ke config agar bertahan saat restart
    try:
        cfg_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.json"
        )
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["otto_ip"] = OTTO_IP
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            logger.info(f"[set_otto_ip] IP disimpan ke config.json: {OTTO_IP}")
    except Exception as e:
        logger.warning(f"[set_otto_ip] Gagal simpan ke config: {e}")

    konfirmasi = f"IP Otto diset ke {OTTO_IP}. Siap kirim alert langsung."
    logger.info(f"[set_otto_ip] {OTTO_IP}")
    return {"success": True, "otto_ip": OTTO_IP, "tts_konfirmasi": konfirmasi}


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Load config
    cfg_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.json"
    )
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            OTTO_IP = cfg.get("otto_ip", "").strip()
        except Exception as e:
            logger.warning(f"[startup] Gagal baca config: {e}")

    logger.info(
        f"MCP Server 'reminder' (Jalur B) starting... "
        f"otto_ip={'set: ' + OTTO_IP if OTTO_IP else 'BELUM DISET — jalankan set_otto_ip()'}"
    )
    _load_and_restore()
    mcp.run(transport="stdio")
