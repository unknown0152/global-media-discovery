from __future__ import annotations

import unittest

from gmd.normalize import normalize_country, normalize_language, normalize_title


class NormalizeTests(unittest.TestCase):
    def test_worldwide_iso_country_codes_are_normalized(self) -> None:
        self.assertEqual(normalize_country("are"), "AE")
        self.assertEqual(normalize_country("bih"), "BA")
        self.assertEqual(normalize_country("dnk"), "DK")
        self.assertEqual(normalize_country("gb"), "GB")

    def test_language_bibliographic_and_script_codes_are_normalized(self) -> None:
        self.assertEqual(normalize_language("sqi"), "sq")
        self.assertEqual(normalize_language("alb"), "sq")
        self.assertEqual(normalize_language("zho"), "zh")
        self.assertEqual(normalize_language("zhtw"), "zh-Hant")

    def test_title_normalization_removes_only_terminal_year(self) -> None:
        self.assertEqual(normalize_title("State of Play (2026)"), "state of play")
        self.assertEqual(normalize_title("1984: The Story"), "1984 the story")


if __name__ == "__main__":
    unittest.main()
