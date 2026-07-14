# agent/db_sender.py
#
# v4 Sinkron dengan market-price.entity.ts v4 dan price_extractor.py v4:
#   - _sanitize_entry(): hapus field yang tidak ada di DTO v4
#     (price_per_unit_min/max, price_per_kg_min/max, location_hint,
#      seller_type, raw_text_snippet).
#   - Sekarang field wajib hanya: variety_code, variety_alias, is_whole_fruit,
#     weight_reference, price_per_unit, confidence, source_name, source_url.

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from core import config
from core.logger import get_logger
from agent.price_extractor import process_variety_items, VARIETY_ALIAS

logger = get_logger("agent.db_sender")

# ══════════════════════════════════════════════════════════════════════════════
# Konfigurasi
# ══════════════════════════════════════════════════════════════════════════════

NESTJS_BASE_URL:     str = os.getenv("NESTJS_BASE_URL",         "http://localhost:3001")
NESTJS_INTERNAL_KEY: str = os.getenv("NESTJS_INTERNAL_API_KEY", "")
NESTJS_INGEST_PATH:  str = "/api/v1/ai-integration/market-report"
NESTJS_TIMEOUT_SEC:  int = int(os.getenv("NESTJS_TIMEOUT_SEC", "30"))

MIN_CONFIDENCE: float = float(os.getenv("DB_MIN_CONFIDENCE", "0.5"))


# ══════════════════════════════════════════════════════════════════════════════
# Sanitasi final sebelum kirim ke NestJS
# ══════════════════════════════════════════════════════════════════════════════

def _sanitize_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pastikan semua field wajib NestJS MarketPriceEntryDto v4 terisi dengan benar.
    Hapus field lama yang sudah tidak ada di DTO.
    """
    # variety_code — wajib
    if not entry.get("variety_code"):
        entry["variety_code"] = "D197"

    # variety_alias — nama standar, bukan judul listing
    vc = entry.get("variety_code", "")
    entry["variety_alias"] = VARIETY_ALIAS.get(vc, vc)[:100]

    # is_whole_fruit — default True (fetcher sudah filter non-whole)
    if not isinstance(entry.get("is_whole_fruit"), bool):
        entry["is_whole_fruit"] = True

    # weight_reference — wajib non-empty
    wr = entry.get("weight_reference", "")
    if not isinstance(wr, str) or not wr.strip():
        has_unit  = entry.get("price_per_unit") is not None
        has_kg    = entry.get("price_per_kg_avg") is not None
        if has_unit and has_kg:
            entry["weight_reference"] = "per buah (dengan referensi per kg)"
        elif has_unit:
            entry["weight_reference"] = "per buah"
        elif has_kg:
            entry["weight_reference"] = "per kg"
        else:
            entry["weight_reference"] = "tidak diketahui"
    entry["weight_reference"] = str(entry["weight_reference"])[:200]

    # price_per_unit — wajib > 0
    price = entry.get("price_per_unit")
    try:
        price = float(price)
        if price <= 0:
            raise ValueError("price_per_unit harus > 0")
    except (TypeError, ValueError):
        logger.warning(
            f"[DbSender] price_per_unit tidak valid ({price}), entry akan ditolak NestJS."
        )
    entry["price_per_unit"] = price

    # price_per_kg_avg — opsional
    pkg = entry.get("price_per_kg_avg")
    if pkg is not None:
        try:
            pkg = float(pkg)
            if pkg < 0:
                pkg = None
        except (TypeError, ValueError):
            pkg = None
    entry["price_per_kg_avg"] = pkg

    # confidence
    conf = entry.get("confidence", 0.5)
    try:
        conf = float(conf)
        conf = max(0.0, min(1.0, conf))
    except (TypeError, ValueError):
        conf = 0.5
    entry["confidence"] = conf

    # notes — opsional string
    notes = entry.get("notes")
    entry["notes"] = str(notes)[:500] if notes else None

    # source_name — wajib non-empty
    if not entry.get("source_name"):
        entry["source_name"] = "market-intelligence-agent"
    entry["source_name"] = str(entry["source_name"])[:255]

    # source_url — wajib (boleh empty string, tidak boleh None)
    if entry.get("source_url") is None:
        entry["source_url"] = ""
    entry["source_url"] = str(entry["source_url"])[:512]

    # Hapus field lama yang sudah tidak ada di DTO v4
    for obsolete in (
        "price_per_unit_min",   # → diganti price_per_unit
        "price_per_unit_max",   # → dihapus
        "price_per_kg_min",     # → dihapus
        "price_per_kg_max",     # → dihapus
        "location_hint",        # → dihapus
        "seller_type",          # → dihapus
        "raw_text_snippet",     # → dihapus
        "source",               # → field internal fetcher, bukan DTO field
        "price_per_unit_avg",   # → tidak ada di DTO
    ):
        entry.pop(obsolete, None)

    return entry


# ══════════════════════════════════════════════════════════════════════════════
# HMAC Signing
# ══════════════════════════════════════════════════════════════════════════════

def _sign_body(body_bytes: bytes, secret: str) -> str:
    """HMAC-SHA256 → 'sha256=<hex>'"""
    digest = hmac.new(
        key       = secret.encode("utf-8"),
        msg       = body_bytes,
        digestmod = hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


# ══════════════════════════════════════════════════════════════════════════════
# HTTP POST ke NestJS
# ══════════════════════════════════════════════════════════════════════════════

async def _post_to_nestjs(
    payload:       Dict[str, Any],
    agent_version: str,
    client:        httpx.AsyncClient,
) -> Dict[str, Any]:
    """
    Serialisasi payload (compact) → HMAC sign → POST ke NestJS.
    Body HARUS compact (separators=(",",":")) agar HMAC cocok
    dengan rawBody yang dibaca NestJS HmacSignatureGuard.
    """
    if not NESTJS_INTERNAL_KEY:
        logger.error(
            "[DbSender] NESTJS_INTERNAL_API_KEY belum diset! POST dibatalkan."
        )
        return {"success": False, "error": "NESTJS_INTERNAL_API_KEY tidak dikonfigurasi."}

    body_bytes = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")

    signature   = _sign_body(body_bytes, NESTJS_INTERNAL_KEY)
    url         = f"{NESTJS_BASE_URL}{NESTJS_INGEST_PATH}"
    entry_count = len(payload.get("entries", []))

    headers = {
        "Content-Type":    "application/json",
        "X-Signature":     signature,
        "X-Agent-Version": agent_version,
    }

    logger.info(
        f"[DbSender] → POST {url} | "
        f"{len(body_bytes)} bytes | {entry_count} entries"
    )

    try:
        resp = await client.post(
            url,
            content = body_bytes,
            headers = headers,
            timeout = NESTJS_TIMEOUT_SEC,
        )

        if resp.status_code == 200:
            result = resp.json()
            logger.info(
                f"[DbSender] ✓ NestJS OK — "
                f"saved={result.get('entries_saved', '?')}, "
                f"rejected={result.get('entries_rejected', '?')}"
            )
            return {"success": True, **result}

        logger.error(
            f"[DbSender] ✗ NestJS {resp.status_code}: {resp.text[:600]}"
        )
        return {"success": False, "error": f"HTTP {resp.status_code}", "body": resp.text[:600]}

    except httpx.ConnectError as exc:
        logger.error(f"[DbSender] Tidak bisa konek ke NestJS ({url}): {exc}")
        return {"success": False, "error": f"Connection refused: {exc}"}
    except httpx.TimeoutException:
        logger.error(f"[DbSender] Timeout {NESTJS_TIMEOUT_SEC}s saat POST ke NestJS.")
        return {"success": False, "error": "Timeout"}
    except Exception as exc:
        logger.error(f"[DbSender] Error tak terduga: {exc}", exc_info=True)
        return {"success": False, "error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════════
# Public API — dipanggil dari agent/task.py
# ══════════════════════════════════════════════════════════════════════════════

async def send_run_to_db(
    summary: Dict[str, Any],
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Entry point utama dari task.py Tahap 3.

    1. Proses setiap varietas lewat price_extractor (regex, tanpa LLM)
    2. Sanitasi entry
    3. Kirim ke NestJS via HMAC-signed POST
    """
    run_id        = summary.get("run_id", "unknown")
    agent_version = os.getenv("APP_VERSION", "1.0.0")

    _status_map = {
        "success": "success",
        "partial": "partial",
        "failed":  "scraper_error",
    }
    nestjs_status = _status_map.get(summary.get("status", "no_data"), "no_data")

    valid_varieties = [r for r in results if r.get("success") and r.get("items")]

    if not valid_varieties:
        logger.warning(
            f"[DbSender] run={run_id}: tidak ada varietas dengan item valid. "
            "Kirim laporan kosong."
        )
        return await _send_empty_report(run_id, agent_version, nestjs_status, summary)

    logger.info(
        f"[DbSender] run={run_id}: proses "
        f"{len(valid_varieties)} varietas via price_extractor..."
    )

    all_entries:       List[Dict[str, Any]] = []
    total_proc_errors: int                  = 0

    for vr in valid_varieties:
        variety_code = vr["variety_code"]
        items        = vr.get("items", [])

        logger.info(f"[DbSender] Proses {variety_code} ({len(items)} item)...")

        try:
            entries, errors = process_variety_items(items, variety_code)
            total_proc_errors += errors

            for entry in entries:
                conf = entry.get("confidence", 0.0)
                if conf < MIN_CONFIDENCE:
                    logger.debug(
                        f"[DbSender] Skip low-confidence "
                        f"(conf={conf:.2f}): {entry.get('variety_alias', '')[:60]}"
                    )
                    total_proc_errors += 1
                    continue
                all_entries.append(_sanitize_entry(entry))

            logger.info(
                f"[DbSender] {variety_code}: "
                f"{len(entries)} extracted, {errors} error"
            )

        except Exception as exc:
            logger.error(
                f"[DbSender] Gagal proses {variety_code}: {exc}", exc_info=True
            )
            total_proc_errors += 1

    if not all_entries:
        logger.warning(
            f"[DbSender] run={run_id}: tidak ada entri valid setelah ekstraksi."
        )
        return await _send_empty_report(run_id, agent_version, "no_data", summary)

    # Pre-check: pastikan semua weight_reference terisi
    for i, entry in enumerate(all_entries):
        wr = entry.get("weight_reference")
        if not wr or not isinstance(wr, str) or not wr.strip():
            logger.warning(
                f"[DbSender] Pre-check: entries[{i}] weight_reference kosong — di-patch."
            )
            entry["weight_reference"] = "per buah"

    sources_scraped   = sum(r.get("item_count", 0) for r in results)
    sources_failed    = sum(1 for r in results if not r.get("success"))
    entries_discarded = max(0, sources_scraped - len(all_entries))

    market_report: Dict[str, Any] = {
        "agent_version":     agent_version,
        "run_id":            run_id,
        "run_started_at":    summary.get("started_at", datetime.now(timezone.utc).isoformat()),
        "run_ended_at":      summary.get("ended_at",   datetime.now(timezone.utc).isoformat()),
        "status":            nestjs_status,
        "entries":           all_entries,
        "sources_scraped":   sources_scraped,
        "sources_failed":    sources_failed,
        "llm_parse_errors":  0,
        "entries_discarded": entries_discarded,
        "error_details":     None,
    }

    logger.info(
        f"[DbSender] Kirim ke NestJS: "
        f"run_id={run_id} | entries={len(all_entries)} | status={nestjs_status}"
    )

    async with httpx.AsyncClient() as client:
        result = await _post_to_nestjs(market_report, agent_version, client)

    return {
        "success":           result.get("success", False),
        "listings_inserted": result.get("entries_saved",    0),
        "listings_rejected": result.get("entries_rejected", 0),
        "llm_errors":        0,
        "error":             result.get("error"),
    }


async def _send_empty_report(
    run_id:        str,
    agent_version: str,
    status:        str,
    summary:       Dict[str, Any],
) -> Dict[str, Any]:
    market_report: Dict[str, Any] = {
        "agent_version":     agent_version,
        "run_id":            run_id,
        "run_started_at":    summary.get("started_at", datetime.now(timezone.utc).isoformat()),
        "run_ended_at":      summary.get("ended_at",   datetime.now(timezone.utc).isoformat()),
        "status":            status,
        "entries":           [],
        "sources_scraped":   summary.get("total_items", 0),
        "sources_failed":    summary.get("varieties_failed", 0),
        "llm_parse_errors":  0,
        "entries_discarded": 0,
        "error_details":     None,
    }
    async with httpx.AsyncClient() as client:
        result = await _post_to_nestjs(market_report, agent_version, client)

    return {
        "success":           result.get("success", False),
        "listings_inserted": 0,
        "listings_rejected": 0,
        "llm_errors":        0,
        "error":             result.get("error"),
    }