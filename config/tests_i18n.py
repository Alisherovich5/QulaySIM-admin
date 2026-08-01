"""Guards for the trilingual, two-theme admin.

Each test here corresponds to a way this configuration broke while it was being
built, so each one fails loudly rather than leaving a half-translated or
unreadable page for someone to notice by eye:

* a `.po` entry left empty renders the English msgid to Uzbek staff;
* `gettext("")` returns the catalogue's own metadata header, not "";
* setting `UNFOLD["THEME"]` removes the light/dark switcher entirely;
* the switcher posts to `set_language`, which 404s without the i18n URLs;
* a hardcoded `/admin/` path breaks when ADMIN_URL_PATH is renamed.
"""

from __future__ import annotations

from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation

LOCALES = ("uz", "ru")


def _catalogue(language: str) -> Path:
    return Path(settings.LOCALE_PATHS[0]) / language / "LC_MESSAGES" / "django.po"


def _entries(language: str):
    """Yield (block, msgid, msgstrs) from a .po without needing polib at runtime.

    A long entry is written as `msgid ""` followed by quoted continuation lines,
    so the msgid has to be reassembled — reading only the first line reports
    every long string as missing.
    """
    import re

    QUOTED = re.compile(r'^"((?:[^"\\]|\\.)*)"$')

    def unescape(text: str) -> str:
        return text.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")

    text = _catalogue(language).read_text()
    # Blocks are separated by a blank line; the header block has an empty msgid.
    for block in text.split("\n\n"):
        lines = block.split("\n")
        field, parts = None, {"msgid": [], "msgstr": []}
        for line in lines:
            if line.startswith("msgid_plural "):
                field = None
            elif line.startswith("msgid "):
                field = "msgid"
                parts["msgid"].append(line[len("msgid ") :].strip('"'))
            elif line.startswith("msgstr"):
                field = "msgstr"
                parts["msgstr"].append(line.split(" ", 1)[1].strip('"'))
            elif field and QUOTED.match(line):
                parts[field].append(QUOTED.match(line).group(1))
            elif not line.startswith('"'):
                field = None
        msgid = unescape("".join(parts["msgid"]))
        if not msgid:
            continue  # the metadata header
        yield block, msgid, [unescape(s) for s in parts["msgstr"]]


class CatalogueCompletenessTests(TestCase):
    def test_every_message_is_translated(self):
        """An empty msgstr is not a neutral default — it renders English."""
        for language in LOCALES:
            with self.subTest(language=language):
                untranslated = []
                for block, msgid, strs in _entries(language):
                    if not msgid and 'msgid ""' in block and "msgstr" not in block:
                        continue
                    # A multi-line entry carries its text on continuation lines,
                    # so an empty first msgstr alone does not mean untranslated.
                    if strs and not any(strs) and '"\n"' not in block:
                        untranslated.append(msgid or block.splitlines()[0])
                self.assertEqual(
                    untranslated, [], f"{language}: {len(untranslated)} untranslated"
                )

    def test_every_translatable_literal_reaches_the_catalogue(self):
        """A string wrapped in _() but never extracted renders in English.

        The completeness test above only looks at entries that are already in
        the .po, so it cannot see a msgid that makemessages was never run for —
        which is exactly what happened when the promoted-destinations columns
        were added a commit after the translation pass.
        """
        import re

        root = Path(settings.BASE_DIR)
        sources = [
            path
            for pattern in ("*/admin.py", "*/models.py", "*/forms.py", "config/*.py")
            for path in root.glob(pattern)
            if ".venv" not in str(path) and path.name != "tests_i18n.py"
        ]
        # Single-line, single-string _("...") calls. Multi-line and interpolated
        # ones are left to makemessages; this is a floor, not a parser.
        literal = re.compile(r'\b_\(\s*"((?:[^"\\]|\\.)+)"\s*\)')
        wanted = {
            m.group(1)
            for path in sources
            for m in literal.finditer(path.read_text())
        }
        for language in LOCALES:
            with self.subTest(language=language):
                have = {msgid for _b, msgid, _s in _entries(language)}
                missing = sorted(wanted - have)
                self.assertEqual(
                    missing,
                    [],
                    f"{language}: wrapped in _() but not in the catalogue — "
                    f"run makemessages: {missing}",
                )

    def test_no_fuzzy_entries(self):
        """gettext ignores a fuzzy entry, so it is the same as no translation."""
        for language in LOCALES:
            with self.subTest(language=language):
                self.assertNotIn("#, fuzzy", _catalogue(language).read_text())

    def test_compiled_catalogue_is_current(self):
        """Only the .mo is read at runtime, so a .po edited without a recompile
        ships the previous wording with nothing to show for it."""
        for language in LOCALES:
            with self.subTest(language=language):
                po = _catalogue(language)
                mo = po.with_suffix(".mo")
                self.assertTrue(mo.exists(), f"{mo} missing — run compilemessages")
                self.assertGreater(mo.stat().st_size, 10_000)
                self.assertGreaterEqual(
                    mo.stat().st_mtime,
                    po.stat().st_mtime,
                    f"{mo.name} is older than {po.name} — run compilemessages",
                )


class TranslationRenderTests(TestCase):
    def test_navigation_is_translated(self):
        """A sample from every part of the interface, in both languages."""
        samples = {
            "uz": {
                "Dashboard": "Boshqaruv paneli",
                "Supplier prices": "Ta’minotchi narxlari",
                "Total revenue": "Umumiy tushum",
                "Log in": "Kirish",
                "Light": "Kunduzgi",
                "Dark": "Tungi",
            },
            "ru": {
                "Dashboard": "Панель",
                "Supplier prices": "Цены поставщиков",
                "Total revenue": "Общая выручка",
                "Log in": "Войти",
                "Light": "Светлая",
                "Dark": "Тёмная",
            },
        }
        for language, pairs in samples.items():
            with translation.override(language):
                for msgid, expected in pairs.items():
                    self.assertEqual(str(translation.gettext(msgid)), expected)

    def test_upstream_uzbek_placeholder_spacing_is_overridden(self):
        """Django's own uz catalogue renders "Add %(name)s" as "%(name)sqo'shish",
        which reaches the page as "tarifqo'shish"."""
        with translation.override("uz"):
            rendered = translation.gettext("Add %(name)s") % {"name": "tarif"}
        self.assertEqual(rendered, "tarif qo‘shish")

    def test_field_labels_are_not_msgmerge_guesses(self):
        """makemessages fills a new msgid from a similar existing one and marks
        it fuzzy. Clearing those flags in bulk turned guesses into asserted
        translations: `network type` came out as the translation of `content
        type`, `sort order` as "buyurtma" (a purchase), `full name` as
        "familiya" (a surname). These are the ones that were wrong."""
        expected = {
            "uz": {
                "network type": "tarmoq turi",
                "sort order": "tartib raqami",
                "full name": "to‘liq ism",
                "account": "hisob",
                "price usd": "narx, USD",
                "is unlimited": "cheksiz",
                "supports hotspot": "hotspot qo‘llab-quvvatlanadi",
            },
            "ru": {
                "network type": "тип сети",
                "sort order": "порядок сортировки",
                "full name": "полное имя",
                "account": "аккаунт",
                "price usd": "цена, USD",
                "is unlimited": "безлимитный",
                "supports hotspot": "поддерживает точку доступа",
            },
        }
        for language, pairs in expected.items():
            with translation.override(language):
                for msgid, want in pairs.items():
                    self.assertEqual(str(translation.gettext(msgid)), want, msgid)

    def test_no_two_labels_share_a_translation(self):
        """Two different field labels rendering the same word is the signature
        of a fuzzy match that was accepted rather than reviewed."""
        import collections

        for language in LOCALES:
            with self.subTest(language=language):
                seen = collections.defaultdict(list)
                for _block, msgid, strs in _entries(language):
                    # Field labels are the lowercase, space-separated ones.
                    if msgid and msgid.islower() and len(strs) == 1 and strs[0]:
                        seen[strs[0]].append(msgid)
                # Genuine synonyms: two msgids that mean the same thing and
                # correctly render as one word.
                allowed = [{"is active", "active"}, {"is popular", "popular"}]
                clashes = {
                    value: ids
                    for value, ids in seen.items()
                    if len(ids) > 1 and set(ids) not in allowed
                }
                self.assertEqual(clashes, {}, f"{language}: {clashes}")

    def test_empty_string_is_never_translated(self):
        """gettext("") returns the .po metadata header. A column with a blank
        heading must therefore not be wrapped in gettext."""
        with translation.override("uz"):
            self.assertNotIn("Project-Id-Version", str(translation.gettext("")))

    def test_model_names_are_translated(self):
        with translation.override("uz"):
            self.assertEqual(
                str(apps.get_model("catalog", "Plan")._meta.verbose_name_plural),
                "tariflar",
            )
        with translation.override("ru"):
            self.assertEqual(
                str(apps.get_model("orders", "Order")._meta.verbose_name_plural),
                "заказы",
            )

    def test_app_labels_are_translated(self):
        """Breadcrumbs use the app's verbose_name; a derived one is never
        passed through gettext and stays English."""
        with translation.override("ru"):
            self.assertEqual(str(apps.get_app_config("catalog").verbose_name), "Каталог")


class LanguageSwitchingTests(TestCase):
    def test_set_language_is_routed_under_the_admin_prefix(self):
        """Unfold's switcher posts here.

        It has to live under ADMIN_URL_PATH: in production the storefront
        answers every path except that prefix, so a root-level /i18n/ was
        served the SPA's index.html and the switcher POSTed into a 405.
        """
        url = reverse("set_language")
        self.assertEqual(url, f"/{settings.ADMIN_URL_PATH}/i18n/setlang/")
        self.assertTrue(url.startswith(f"/{settings.ADMIN_URL_PATH}/"))

    def test_locale_middleware_is_installed_in_the_right_place(self):
        middleware = list(settings.MIDDLEWARE)
        self.assertIn("django.middleware.locale.LocaleMiddleware", middleware)
        self.assertLess(
            middleware.index("django.contrib.sessions.middleware.SessionMiddleware"),
            middleware.index("django.middleware.locale.LocaleMiddleware"),
            "LocaleMiddleware reads the chosen language from the session",
        )
        self.assertLess(
            middleware.index("django.middleware.locale.LocaleMiddleware"),
            middleware.index("django.middleware.common.CommonMiddleware"),
        )

    def test_switching_language_changes_the_page(self):
        from django.contrib.auth.models import User

        User.objects.create_superuser("i18n-admin", "i@example.com", "pw-for-tests-only")
        self.client.force_login(User.objects.get(username="i18n-admin"))
        index = reverse("admin:index")

        response = self.client.post(
            reverse("set_language"), {"language": "ru", "next": index}, secure=True
        )
        self.assertEqual(response.status_code, 302)
        page = self.client.get(index, secure=True)
        self.assertContains(page, "Общая выручка")
        self.assertNotContains(page, "Total revenue")

        self.client.post(
            reverse("set_language"), {"language": "uz", "next": index}, secure=True
        )
        page = self.client.get(index, secure=True)
        self.assertContains(page, "Umumiy tushum")

    def test_only_the_three_configured_languages_are_offered(self):
        self.assertEqual([code for code, _name in settings.LANGUAGES], ["uz", "ru", "en"])
        self.assertEqual(settings.LANGUAGE_CODE, "uz")


class ThemeTests(TestCase):
    def test_no_forced_theme(self):
        """Setting UNFOLD["THEME"] pins the theme and removes the switcher."""
        self.assertNotIn("THEME", settings.UNFOLD)

    @override_settings(LANGUAGE_CODE="en")
    def test_switcher_is_rendered(self):
        from django.contrib.auth.models import User

        User.objects.create_superuser("theme-admin", "t@example.com", "pw-for-tests-only")
        self.client.force_login(User.objects.get(username="theme-admin"))
        page = self.client.get(reverse("admin:index"), secure=True)
        for marker in ("switchTheme('light')", "switchTheme('dark')", "switchTheme('auto')"):
            self.assertContains(page, marker)

    def test_dashboard_carries_no_hardcoded_colours(self):
        """Every colour on the dashboard has to come from a themed variable, or
        the dark theme renders light-theme values on a dark surface."""
        import re

        template = (
            Path(settings.BASE_DIR) / "templates" / "admin" / "index.html"
        ).read_text()
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", template), [])

    def test_admin_css_defines_every_role_in_both_themes(self):
        """A role defined only in :root silently keeps its light value in dark."""
        import re

        css = (
            Path(settings.BASE_DIR) / "static" / "admin" / "qulaysim-admin.css"
        ).read_text()
        root = re.search(r":root \{(.*?)\n\}", css, flags=re.S)
        dark = re.search(r"\n\.dark \{(.*?)\n\}", css, flags=re.S)
        self.assertIsNotNone(root)
        self.assertIsNotNone(dark)
        names = lambda block: {m for m in re.findall(r"--qs-[a-z0-9-]+", block)}
        missing = names(root.group(1)) - names(dark.group(1))
        self.assertEqual(missing, set(), f"no dark value for: {sorted(missing)}")

    def test_theme_is_driven_only_by_the_dark_class(self):
        import re

        """Unfold's switcher is the authority. A prefers-color-scheme rule would
        override an explicit "light" choice on a dark-OS machine."""
        css = (
            Path(settings.BASE_DIR) / "static" / "admin" / "qulaysim-admin.css"
        ).read_text()
        # Comments explain why these are absent, so they must not count as uses.
        rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        self.assertNotIn("prefers-color-scheme", rules)
        self.assertNotIn("[data-theme=", rules)
