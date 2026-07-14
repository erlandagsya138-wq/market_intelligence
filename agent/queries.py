# agent/queries.py
#
# Perubahan dari versi sebelumnya:
#   D2 Dato Nina:
#   - Query diubah dari "dato nina" (nama tidak dikenal di Indonesia)
#     ke kombinasi "durian d2 + malaysia/impor/fresh" yang lebih realistis.
#   - variety_keyword_extras: HAPUS "durian d2" — substring biasa akan
#     false-match "durian d214", "durian d24", dll. Pengecekan "d2" dilakukan
#     via _D2_WORD_BOUNDARY regex di fetcher._is_valid_item().
#   - "d 2" (dengan spasi) tetap dipertahankan — penulisan ini berdiri sendiri
#     dan tidak ambigu seperti "d2" tanpa spasi.
#   - relaxed_variety_check tetap False — lebih baik 0 data benar
#     daripada data salah varietas.
#   - min_results diturunkan ke 1 agar run tidak dianggap gagal
#     hanya karena D2 memang langka.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional


@dataclass(frozen=True)
class DurianQuery:
    variety_code:           str
    variety_name:           str
    search_queries:         List[str]
    variety_keyword_extras: FrozenSet[str] = frozenset()
    min_results:            int  = 3
    num_results:            int  = 40
    gl:                     str  = "id"
    hl:                     str  = "id"
    relaxed_variety_check:  bool = False

    def __post_init__(self) -> None:
        if not self.variety_code.strip():
            raise ValueError("variety_code tidak boleh kosong.")
        if not self.search_queries:
            raise ValueError("search_queries tidak boleh kosong.")
        if self.min_results < 1:
            raise ValueError("min_results harus >= 1.")
        if not 1 <= self.num_results <= 100:
            raise ValueError("num_results harus 1–100.")


DURIAN_QUERIES: List[DurianQuery] = [

    # ── D197 Musang King ──────────────────────────────────────────────────────
    DurianQuery(
        variety_code = "D197",
        variety_name = "Musang King / Raja Kunyit / Mao Shan Wang",
        search_queries = [
            "durian musang king utuh segar",
            "durian musang king buah utuh",
            "durian mao shan wang buah utuh",
            "durian raja kunyit segar berkulit",
            "durian musangking fresh impor",
        ],
        variety_keyword_extras = frozenset({"msw", "mao shan wang", "raja kunyit", "musangking"}),
        min_results = 5,
        num_results = 40,
        relaxed_variety_check = False,
    ),

    # ── D13 Golden Bun ────────────────────────────────────────────────────────
    DurianQuery(
        variety_code = "D13",
        variety_name = "Golden Bun",
        search_queries = [
            "durian golden bun d13 utuh",
            "durian d13 golden bun segar",
            "jual durian d13 buah utuh",
            "durian golden bun impor malaysia",
            "durian d13 impor malaysia segar",
        ],
        variety_keyword_extras = frozenset({
            "golden bun", "d13", "goldenbun", "golden-bun",
        }),
        min_results = 1,
        num_results = 60,
        relaxed_variety_check = True,
    ),

    # ── D24 Sultan ────────────────────────────────────────────────────────────
    DurianQuery(
        variety_code = "D24",
        variety_name = "Sultan / Bukit Merah",
        search_queries = [
            "durian D24 buah utuh segar",
            "durian sultan D24 utuh",
            "durian bukit merah D24 segar",
            "durian D24 fresh berkulit impor",
            "jual durian D24 asli malaysia",
        ],
        variety_keyword_extras = frozenset({"bukit merah", "sultan d24", "malayd24", "malay d24"}),
        min_results = 2,
        num_results = 40,
        relaxed_variety_check = False,
    ),

    # ── D2 Dato Nina ──────────────────────────────────────────────────────────
    #
    # Nama "Dato Nina" / "Datuk Nina" hampir tidak pernah dipakai di listing
    # marketplace Indonesia. Varietas ini lebih dikenal dengan kode "D2" saja,
    # sering digabung dengan kata "malaysia" atau "impor".
    #
    # CATATAN PENTING — variety_keyword_extras:
    #   "durian d2" DIHAPUS karena substring check ("durian d2" in title) akan
    #   false-match "durian d214", "durian d24", dst. Pengecekan kode "d2" kini
    #   HANYA dilakukan via _D2_WORD_BOUNDARY (regex \bd2\b) di fetcher.py,
    #   yang memastikan "d2" berdiri sendiri sebagai kata penuh.
    #
    #   "d 2" (dengan spasi) tetap aman karena "d 2" tidak menjadi bagian dari
    #   kode varietas lain manapun.
    DurianQuery(
        variety_code = "D2",
        variety_name = "Dato Nina / D2",
        search_queries = [
            "durian d2 malaysia utuh segar",       # paling spesifik
            "durian d2 impor buah utuh",
            "jual durian d2 fresh malaysia",
            "durian d2 buah segar berkulit",
            "durian dato nina d2 utuh",            # tetap coba nama asli sebagai fallback
        ],
        variety_keyword_extras = frozenset({
            "dato nina", "datuk nina", "dato nena",
            # "durian d2" — DIHAPUS, ambigu (false-match "durian d214", "durian d24")
            "d 2",          # penulisan dengan spasi: "D 2" — aman, tidak ambigu
        }),
        min_results = 1,
        num_results = 60,
        relaxed_variety_check = False,  # wajib ada "\bd2\b" + "durian" di judul
    ),

]