"""Reference lookup of Turkish provinces and districts.

Used by the Phase 1 synthetic data generator to produce realistic addresses,
and by the Phase 4 Address Agent as its rule-based lookup table (the primary,
zero-LLM-cost path for resolving free-text addresses). Sharing one source of
truth keeps the two in sync instead of drifting apart.

Not exhaustive (Turkey has 81 provinces) — covers the most common ones for
demo purposes. Extend as needed.
"""

COUNTRY = "Türkiye"

PROVINCE_DISTRICTS: dict[str, list[str]] = {
    "İstanbul": ["Kadıköy", "Beşiktaş", "Üsküdar", "Şişli"],
    "Ankara": ["Çankaya", "Keçiören", "Yenimahalle"],
    "İzmir": ["Konak", "Bornova", "Karşıyaka"],
    "Bursa": ["Nilüfer", "Osmangazi"],
    "Antalya": ["Muratpaşa", "Kepez"],
    "Adana": ["Seyhan", "Çukurova"],
    "Konya": ["Selçuklu", "Meram"],
    "Gaziantep": ["Şahinbey", "Şehitkamil"],
    "Mersin": ["Yenişehir", "Toroslar"],
    "Kayseri": ["Melikgazi", "Kocasinan"],
    "Eskişehir": ["Odunpazarı", "Tepebaşı"],
    "Samsun": ["İlkadım", "Atakum"],
    "Trabzon": ["Ortahisar"],
    "Denizli": ["Pamukkale"],
    "Diyarbakır": ["Kayapınar"],
    "Şanlıurfa": ["Eyyübiye"],
    "Malatya": ["Yeşilyurt"],
    "Erzurum": ["Yakutiye"],
    "Van": ["İpekyolu"],
    "Manisa": ["Şehzadeler"],
}

ALL_PROVINCES = list(PROVINCE_DISTRICTS.keys())
