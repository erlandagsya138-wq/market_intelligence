# agent/price_extractor.py
#
# v4 Sinkron dengan market-price.entity.ts v4:
#   - HAPUS price_per_unit_max (selalu identik dengan min, tidak perlu dua field)
#   - RENAME price_per_unit_min → price_per_unit
#   - HAPUS price_per_kg_min, price_per_kg_max (tidak pernah diisi, dihapus dari entity)
#   - HAPUS location_hint, seller_type (dihapus dari entity)
#   - HAPUS raw_text_snippet (debug artifact, dihapus dari entity)
#
# v3.1 Bugfix (dipertahankan):
#   - early-reject D2 jika judul mengandung kode superset (D214, D24, dll)
#   - _D2_WORD_BOUNDARY regex untuk pengecekan "\bd2\b"

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger

logger = get_logger("agent.price_extractor")

# ── Regex word boundary untuk kode varietas yang ambigu ──────────────────────
_D2_WORD_BOUNDARY  = re.compile(r'\bd2\b',    re.I)
_D2_SUPERSET_CODES = re.compile(r'\bd2\d+\b', re.I)

# ── Nama standar varietas ─────────────────────────────────────────────────────
VARIETY_ALIAS: Dict[str, str] = {
    "D197": "Musang King",
    "D13":  "Golden Bun",
    "D24":  "Sultan / D24",
    "D2":   "Dato Nina",
}

# ── Estimasi berat buah utuh (kg) per varietas ────────────────────────────────
VARIETY_WEIGHT_ESTIMATE: Dict[str, float] = {
    "D197": 2.0,
    "D13":  2.5,
    "D24":  1.5,
    "D2":   2.0,
}

# ── Batas harga per buah utuh yang masuk akal (IDR) ──────────────────────────
VARIETY_UNIT_PRICE_BOUNDS: Dict[str, Tuple[float, float]] = {
    "D197": (350_000, 6_000_000),
    "D13":  (250_000, 5_000_000),
    "D24":  (100_000, 4_000_000),
    "D2":   (200_000, 5_000_000),
}

# Confidence minimum — entry di bawah ini dibuang di Python sebelum dikirim ke NestJS
MIN_CONFIDENCE = 0.70

# ── Sinyal produk olahan (bukan buah utuh) ────────────────────────────────────
_PROCESSED_SIGNALS: frozenset = frozenset({
    "kupas", "dikupas", "flesh", "pulp", "daging", "frozen", "beku",
    "pancake", "biskuit", "kue", "cake", "pudding", "jelly", "extract",
    "juice", "dodol", "lempok", "bibit", "benih", "seedling",
    "sabun", "parfum", "lotion",
})

# ── Regex berat ───────────────────────────────────────────────────────────────
_WEIGHT_PATTERNS: List[re.Pattern] = [
    re.compile(r"(\d+[,.]?\d*)\s*[-–]\s*(\d+[,.]?\d*)\s*kg", re.I),
    re.compile(r"~?\s*(\d+[,.]?\d+)\s*kg",                    re.I),
    re.compile(r"\b(\d)\s*kg\b",                               re.I),
    re.compile(r"[(\[]\s*(\d+[,.]?\d*)\s*kg\s*[)\]]",         re.I),
]

# ── Sinyal satuan ─────────────────────────────────────────────────────────────
_KG_SIGNALS   = ["per kg", "per-kg", "/kg", "harga kg", "1 kg", "1kg", "per kilo"]
_BUAH_SIGNALS = [
    "per buah", "per biji", "1 buah", "1buah", "satu buah",
    "(l)", "(m)", "(s)", "(xl)", "pcs", "per pcs", "/pcs",
]
_WHOLE_FRUIT_INFERENCE_SIGNALS = [
    "utuh", "bulat", "berkulit", "segar", "fresh", "whole",
    "impor", "import", "malaysia", "imported",
]


def _extract_weight_kg(text: str) -> Optional[float]:
    for pat in _WEIGHT_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                groups = m.groups()
                if len(groups) == 2 and groups[1] is not None:
                    lo = float(groups[0].replace(",", "."))
                    hi = float(groups[1].replace(",", "."))
                    return round((lo + hi) / 2, 2)
                return round(float(groups[0].replace(",", ".")), 2)
            except (ValueError, TypeError):
                continue
    return None


def _detect_price_unit(title: str) -> str:
    """
    Deteksi satuan harga dari judul listing.
    Dipanggil fresh dari judul — tidak pakai nilai price_unit dari fetcher
    karena fetcher bisa menghasilkan "unknown" untuk sinyal yang valid
    (contoh: "pcs" setelah separator pipe tidak terdeteksi fetcher).
    """
    t = title.lower()
    if any(kw in t for kw in _KG_SIGNALS):   return "per_kg"
    if any(kw in t for kw in _BUAH_SIGNALS): return "per_buah"
    if _extract_weight_kg(title) is not None: return "per_buah"
    return "unknown"


def _infer_per_buah_from_whole_signals(
    title:        str,
    price_idr:    float,
    variety_code: str,
) -> bool:
    """
    Jika price_unit masih "unknown" tapi judul mengandung sinyal buah utuh
    DAN harga dalam rentang wajar → inferensikan sebagai per_buah.
    """
    t = title.lower()
    if not any(sig in t for sig in _WHOLE_FRUIT_INFERENCE_SIGNALS):
        return False
    lo, hi = VARIETY_UNIT_PRICE_BOUNDS.get(variety_code, (100_000, 10_000_000))
    return lo <= price_idr <= hi


def _detect_is_whole_fruit(title: str) -> bool:
    t = title.lower()
    return not any(sig in t for sig in _PROCESSED_SIGNALS)


def _compute_confidence(
    variety_code: str,
    title:        str,
    price_unit:   str,
    weight_kg:    Optional[float],
    is_whole:     bool,
    is_inferred:  bool,
) -> float:
    score = 0.50
    t = title.lower()

    if price_unit == "per_buah" and not is_inferred:
        score += 0.20
    elif price_unit == "per_buah" and is_inferred:
        score += 0.10
    elif price_unit == "per_kg":
        score += 0.10

    if weight_kg is not None:
        score += 0.15

    if is_whole:
        score += 0.10

    variety_kws = {
        "D197": ["musang king", "mao shan wang", "msw", "raja kunyit", "musangking"],
        "D13":  ["golden bun", "d13"],
        "D24":  ["d24", "sultan", "bukit merah", "malayd24", "malay d24"],
        "D2":   ["dato nina", "datuk nina"],
    }
    has_variety_kw = any(kw in t for kw in variety_kws.get(variety_code, []))

    # D2: cek \bd2\b agar tidak false-match D214, D24, dll.
    if variety_code == "D2" and not has_variety_kw:
        has_d2_exact      = bool(_D2_WORD_BOUNDARY.search(t))
        has_superset_code = bool(_D2_SUPERSET_CODES.search(t))
        if has_d2_exact and not has_superset_code:
            has_variety_kw = True

    score += 0.10 if has_variety_kw else -0.15

    return round(max(0.0, min(1.0, score)), 2)


def _to_unit_price(
    price_idr:    float,
    price_unit:   str,
    weight_kg:    Optional[float],
    variety_code: str,
) -> Optional[Tuple[float, str, str]]:
    """
    Konversi harga listing → (price_per_unit, weight_reference, notes).
    Returns None jika tidak bisa dikonversi.
    """
    if price_unit == "per_buah":
        if weight_kg:
            return (
                price_idr,
                f"per buah {weight_kg} kg",
                f"Rp{price_idr:,.0f}/buah (berat dari judul: {weight_kg} kg)",
            )
        else:
            est = VARIETY_WEIGHT_ESTIMATE.get(variety_code, 2.0)
            return (
                price_idr,
                f"per buah ~{est} kg (estimasi)",
                f"Berat tidak ada di judul, pakai estimasi {est} kg untuk {variety_code}.",
            )

    if price_unit == "per_kg":
        if weight_kg is None:
            return None   # Tidak bisa konversi tanpa berat eksplisit
        unit_price = round(price_idr * weight_kg)
        return (
            unit_price,
            f"per kg × {weight_kg} kg",
            f"Rp{price_idr:,.0f}/kg × {weight_kg} kg = Rp{unit_price:,}/buah",
        )

    return None


def extract_entry(
    item:         Dict[str, Any],
    variety_code: str,
) -> Optional[Dict[str, Any]]:
    """
    Konversi satu raw item dari fetcher → dict siap dikirim ke NestJS.
    Returns None jika entry tidak layak disimpan.

    Field output yang dihasilkan sesuai market-price.entity.ts v4:
      variety_code, variety_alias, is_whole_fruit, weight_reference,
      notes, price_per_unit, price_per_kg_avg, confidence,
      source_name, source_url
    """
    title      = item.get("title", "").strip()
    price_idr  = item.get("price_idr")
    price_str  = item.get("price_str", "")
    source     = item.get("source", "Unknown")
    source_url = item.get("product_link") or item.get("source_url") or ""

    if not title or price_idr is None:
        return None

    try:
        price_idr = float(price_idr)
    except (TypeError, ValueError):
        return None

    # ── Early-reject D2: cegah false-match kode varietas lain ────────────────
    if variety_code == "D2":
        t_check      = title.lower()
        has_superset = bool(_D2_SUPERSET_CODES.search(t_check))
        if has_superset:
            logger.debug(
                f"[Extractor] BUANG D2 false-match (kode superset d2xx): "
                f"'{title[:70]}'"
            )
            return None
        has_named_kw   = any(kw in t_check for kw in ("dato nina", "datuk nina", "dato nena"))
        has_d2_exact   = bool(_D2_WORD_BOUNDARY.search(t_check))
        has_durian_ctx = "durian" in t_check or "duren" in t_check
        if not has_named_kw and not (has_d2_exact and has_durian_ctx):
            logger.debug(
                f"[Extractor] BUANG D2: tidak ada keyword valid: '{title[:70]}'"
            )
            return None

    # ── Deteksi price_unit fresh dari judul ───────────────────────────────────
    weight_kg   = item.get("weight_kg_hint") or _extract_weight_kg(title)
    price_unit  = _detect_price_unit(title)
    is_whole    = _detect_is_whole_fruit(title)
    is_inferred = False

    # ── Inferensi per_buah dari sinyal buah utuh jika masih "unknown" ─────────
    if price_unit == "unknown":
        if _infer_per_buah_from_whole_signals(title, price_idr, variety_code):
            price_unit  = "per_buah"
            is_inferred = True
            logger.debug(
                f"[Extractor] Inferensi per_buah (whole_fruit): "
                f"'{title[:60]}' Rp{price_idr:,.0f}"
            )
        else:
            logger.debug(
                f"[Extractor] BUANG unknown unit: '{title[:60]}' Rp{price_idr:,.0f}"
            )
            return None

    # ── Konversi ke harga per buah ────────────────────────────────────────────
    result = _to_unit_price(price_idr, price_unit, weight_kg, variety_code)
    if result is None:
        logger.debug(
            f"[Extractor] BUANG per_kg tanpa berat eksplisit: '{title[:60]}'"
        )
        return None

    unit_price, weight_reference, notes = result

    if is_inferred:
        notes = f"[Inferensi per-buah dari sinyal whole_fruit] {notes}"

    # ── Filter harga di luar batas wajar per varietas ─────────────────────────
    lo, hi = VARIETY_UNIT_PRICE_BOUNDS.get(variety_code, (100_000, 10_000_000))
    if not (lo <= unit_price <= hi):
        logger.debug(
            f"[Extractor] BUANG out-of-range: {variety_code} "
            f"Rp{unit_price:,.0f}/buah (batas Rp{lo:,.0f}–Rp{hi:,.0f})"
        )
        return None

    # ── Confidence ────────────────────────────────────────────────────────────
    confidence = _compute_confidence(
        variety_code, title, price_unit, weight_kg, is_whole, is_inferred
    )
    if confidence < MIN_CONFIDENCE:
        logger.debug(
            f"[Extractor] BUANG low confidence ({confidence:.2f}): '{title[:60]}'"
        )
        return None

    # ── Hitung price_per_kg_avg sebagai data sekunder ─────────────────────────
    if price_unit == "per_buah" and weight_kg:
        pkg_avg: Optional[float] = round(unit_price / weight_kg)
    elif price_unit == "per_kg":
        pkg_avg = int(price_idr)
    else:
        est     = VARIETY_WEIGHT_ESTIMATE.get(variety_code, 2.0)
        pkg_avg = round(unit_price / est)

    entry = {
        # ── Field wajib NestJS DTO ────────────────────────────────────────────
        "variety_code":     variety_code,
        "variety_alias":    VARIETY_ALIAS.get(variety_code, variety_code),
        "is_whole_fruit":   is_whole,
        "weight_reference": weight_reference,
        "notes":            notes,
        "price_per_unit":   unit_price,          # ← field utama untuk agregasi
        "price_per_kg_avg": pkg_avg,             # ← data sekunder, nullable
        "confidence":       confidence,
        "source_name":      source,
        "source_url":       source_url,
    }

    logger.debug(
        f"[Extractor] OK {variety_code}: '{title[:55]}' "
        f"→ Rp{unit_price:,.0f}/buah | unit={price_unit}"
        f"{'(inferred)' if is_inferred else ''} | conf={confidence:.2f}"
    )
    return entry


def process_variety_items(
    items:        List[Dict[str, Any]],
    variety_code: str,
) -> Tuple[List[Dict[str, Any]], int]:
    entries: List[Dict[str, Any]] = []
    errors = 0

    for item in items:
        try:
            entry = extract_entry(item, variety_code)
            if entry is not None:
                entries.append(entry)
            else:
                errors += 1
        except Exception as exc:
            logger.error(
                f"[Extractor] Error '{item.get('title', '')[:60]}': {exc}",
                exc_info=True,
            )
            errors += 1

    logger.info(
        f"[Extractor] {variety_code}: "
        f"{len(entries)} valid, {errors} dibuang dari {len(items)} item"
    )
    return entries, errors