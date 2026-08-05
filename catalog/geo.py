"""Turning a supplier's two-letter country code into a destination we can sell.

The wholesalers hand us ISO codes and English names and nothing else. A
destination on the site needs more than that — an Uzbek name, a Russian name, a
slug, and the region it belongs to — and every one of those was being typed in by
hand, which is why the catalogue sat at 25 countries while the APIs offered 193.

Names come from CLDR via Babel, not from a list we maintain. That is the whole
point: "Amerika Qo'shma Shtatlari" and "Buyuk Britaniya" are already the names
the site shows, because they are the standard Uzbek forms, and nobody has to
translate 193 countries by hand or keep them in step.

Regions are ours, deliberately. CLDR has no "Middle East" and files Turkey under
Asia; a traveller shopping for an eSIM does not think that way, and the region is
what groups destinations on the site and in its SEO hub pages. So the mapping
below is a product decision written down, not reference data we could look up.
"""

from __future__ import annotations

from django.utils.text import slugify

# Our seven regions, as they exist in catalog_region. A country whose code is
# absent lands in `DEFAULT_REGION` rather than being dropped: an unrouted
# destination that still sells beats a destination that silently never appears.
EUROPE = "europe"
ASIA = "asia"
MIDDLE_EAST = "middle-east"
NORTH_AMERICA = "north-america"
AFRICA = "africa"
OCEANIA = "oceania"
LATIN_AMERICA = "latin-america"

DEFAULT_REGION = ASIA

REGION_BY_ISO2: dict[str, str] = {
    # Europe — the EU, the rest of the continent, and the microstates. Cyprus and
    # Turkey sit here rather than in Asia because that is how a traveller and a
    # roaming zone both treat them.
    **dict.fromkeys(
        "AD AL AM AT AX AZ BA BE BG BY CH CY CZ DE DK EE ES FI FO FR GB GE GG GI GR "
        "HR HU IE IM IS IT JE LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS RU SE SI "
        "SJ SK SM TR UA VA XK".split(),
        EUROPE,
    ),
    # Middle East — the Gulf and the Levant. Our own grouping; CLDR calls this
    # Western Asia and puts Turkey and Cyprus in it, which we do not.
    **dict.fromkeys(
        "AE BH IL IQ IR JO KW LB OM PS QA SA SY YE".split(),
        MIDDLE_EAST,
    ),
    # Asia — Central, South, East and South-East, plus the Caucasus that is not
    # already in Europe.
    **dict.fromkeys(
        "AF BD BN BT CN HK ID IN JP KG KH KP KR KZ LA LK MM MN MO MV MY NP PH PK SG "
        "TH TJ TL TM TW UZ VN".split(),
        ASIA,
    ),
    # North America — the USMCA three plus the Caribbean and Central America the
    # site sells alongside them.
    **dict.fromkeys(
        "AG AI AW BB BL BM BQ BS BZ CA CR CU CW DM DO GD GL GP GT HN HT JM KN KY LC "
        "MF MQ MS MX NI PA PM PR SV SX TC TT US VC VG VI".split(),
        NORTH_AMERICA,
    ),
    # Latin America — South America. Mexico and Central America are grouped with
    # North America above, matching how the flights and the roaming zones run.
    **dict.fromkeys(
        "AR BO BR CL CO EC FK GF GY PE PY SR UY VE".split(),
        LATIN_AMERICA,
    ),
    # Africa, north to south.
    **dict.fromkeys(
        "AO BF BI BJ BW CD CF CG CI CM CV DJ DZ EG EH ER ET GA GH GM GN GQ GW KE KM "
        "LR LS LY MA MG ML MR MU MW MZ NA NE NG RE RW SC SD SH SL SN SO SS ST SZ TD "
        "TG TN TZ UG ZA ZM ZW".split(),
        AFRICA,
    ),
    # Oceania.
    **dict.fromkeys(
        "AS AU CK FJ FM GU KI MH MP NC NF NR NU NZ PF PG PW SB TK TO TV VU WF WS".split(),
        OCEANIA,
    ),
}

# Region metadata, so a sync can create a missing region rather than skipping
# every country that belongs to it.
REGION_NAMES: dict[str, tuple[str, str, str, int]] = {
    #  slug          English          Uzbek               Russian            order
    EUROPE: ("Europe", "Yevropa", "Европа", 1),
    ASIA: ("Asia", "Osiyo", "Азия", 2),
    MIDDLE_EAST: ("Middle East", "Yaqin Sharq", "Ближний Восток", 3),
    NORTH_AMERICA: ("North America", "Shimoliy Amerika", "Северная Америка", 4),
    AFRICA: ("Africa", "Afrika", "Африка", 5),
    OCEANIA: ("Oceania", "Okeaniya", "Океания", 6),
    LATIN_AMERICA: ("Latin America", "Lotin Amerikasi", "Латинская Америка", 7),
}

# Where CLDR's name is not the one the market uses. Kept as short as it can be —
# every entry here is a name someone has to maintain, which is what this module
# exists to avoid.
NAME_OVERRIDES: dict[str, dict[str, str]] = {
    "AE": {"uz": "Dubay (BAA)", "ru": "ОАЭ", "en": "United Arab Emirates"},
    "KR": {"uz": "Janubiy Koreya", "ru": "Южная Корея", "en": "South Korea"},
    "US": {"uz": "Amerika Qo‘shma Shtatlari", "ru": "США", "en": "United States"},
    "GB": {"uz": "Buyuk Britaniya", "ru": "Великобритания", "en": "United Kingdom"},
}


def region_slug_for(iso2: str) -> str:
    """Which of our regions a country belongs to."""
    return REGION_BY_ISO2.get((iso2 or "").upper(), DEFAULT_REGION)


def _cldr_names(iso2: str) -> dict[str, str]:
    """Country name in English, Uzbek and Russian, from CLDR.

    Babel is imported lazily and its absence is survivable: a sync that cannot
    localise names is still worth running with the supplier's English name in all
    three fields, because an untranslated destination sells and a missing one
    does not.
    """
    try:
        from babel import Locale
    except ImportError:  # pragma: no cover - Babel is a declared dependency
        return {}

    code = iso2.upper()
    names = {}
    for lang in ("en", "uz", "ru"):
        try:
            value = Locale.parse(lang).territories.get(code)
        except Exception:  # pragma: no cover - unknown locale data
            value = None
        if value:
            names[lang] = value
    return names


def names_for(iso2: str, supplier_name: str = "") -> dict[str, str]:
    """The three names and the slug for one destination.

    Falls back through CLDR, then the supplier's own English name, then the bare
    code — so there is always something to show.
    """
    code = (iso2 or "").upper()
    names = _cldr_names(code)
    names.update(NAME_OVERRIDES.get(code, {}))

    english = names.get("en") or supplier_name.strip() or code
    return {
        "name": english,
        "name_uz": names.get("uz") or english,
        "name_ru": names.get("ru") or english,
        # Slugged from English so URLs stay stable when a translation changes.
        "slug": slugify(english) or code.lower(),
    }
