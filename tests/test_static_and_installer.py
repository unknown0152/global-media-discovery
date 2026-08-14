from __future__ import annotations

from io import BytesIO
from pathlib import Path
import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
import tomllib
import unittest


class StaticAndInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.html = (cls.root / "web/index.html").read_text(encoding="utf-8")
        cls.javascript = (cls.root / "web/assets/app.js").read_text(encoding="utf-8")

    def test_every_javascript_id_selector_exists(self) -> None:
        html_ids = set(re.findall(r'\bid=["\']([^"\']+)', self.html))
        selectors = set(
            re.findall(r'querySelector\(["\']#([^"\']+)', self.javascript)
        )
        selectors.update(
            re.findall(r'getElementById\(["\']([^"\']+)', self.javascript)
        )
        self.assertEqual(selectors - html_ids, set())

    def test_csp_compatible_assets_and_valid_date_state(self) -> None:
        self.assertNotRegex(self.html, r"<script(?![^>]*\bsrc=)")
        self.assertNotRegex(self.html, r"<style\b")
        self.assertIn("? value : null", self.javascript)
        for reference in re.findall(r'(?:src|href)=["\'](/[^"\']+)', self.html):
            clean = reference.split("?", 1)[0]
            self.assertTrue((self.root / "web" / clean.lstrip("/")).exists(), clean)

    def test_node_parses_frontend(self) -> None:
        result = subprocess.run(
            ["node", "--check", str(self.root / "web/assets/app.js")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_filter_selects_use_state_and_locale_is_sanitized(self) -> None:
        populate_select = re.search(
            r"function populateSelect\(.*?\n}\n\nfunction filterKeyFor",
            self.javascript,
            re.DOTALL,
        )
        self.assertIsNotNone(populate_select)
        implementation = populate_select.group(0)  # type: ignore[union-attr]
        self.assertIn("state.filters[key]", implementation)
        self.assertNotIn("select.value ||", implementation)
        self.assertIn('element("option", { value: selected }', implementation)
        self.assertNotRegex(
            self.javascript,
            r'control\.addEventListener\("input",\s*updateFilter\)',
        )
        self.assertIn('control.addEventListener("change", updateFilter)', self.javascript)
        self.assertNotIn('[dom.countryFilter, "country"]', self.javascript)
        self.assertIn("function populateCountryPicker(values)", self.javascript)
        self.assertIn('id="countryPicker"', self.html)
        self.assertNotIn('select id="countryFilter"', self.html)
        self.assertIn('cache: attempt ? "reload" : "no-store"', self.javascript)
        self.assertIn("const body = await response.text()", self.javascript)
        self.assertNotIn("return response.json()", self.javascript)
        self.assertIn("Intl.getCanonicalLocales", self.javascript)
        self.assertIn("const displayLocale = safeLocale(navigator.language)", self.javascript)
        self.assertNotRegex(self.javascript, r"new Intl\.[A-Za-z]+\([^\n]*navigator\.language")

    def test_tailwind_and_htmx_are_pinned_local_assets(self) -> None:
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        dependencies = package["devDependencies"]
        self.assertEqual(dependencies["tailwindcss"], "4.3.3")
        self.assertEqual(dependencies["@tailwindcss/cli"], "4.3.3")
        self.assertEqual(dependencies["htmx.org"], "4.0.0-beta6")
        self.assertIn('/assets/tailwind.css?v=1.3.0', self.html)
        self.assertIn('/assets/htmx.min.js?v=4.0.0-beta6', self.html)
        self.assertNotIn("cdn.jsdelivr.net", self.html)
        self.assertIn('hx-get="/ui/v1/credits"', self.html)
        self.assertIn('hx-target="#creditsSources"', self.html)
        self.assertIn('hx-get="/ui/v1/coverage"', self.html)
        for asset in ("web/assets/tailwind.css", "web/assets/htmx.min.js"):
            path = self.root / asset
            self.assertTrue(path.exists(), asset)
            self.assertGreater(path.stat().st_size, 1000, asset)

    def test_public_version_references_are_synchronized(self) -> None:
        version = (self.root / "VERSION").read_text(encoding="utf-8").strip()
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        project = tomllib.loads(
            (self.root / "pyproject.toml").read_text(encoding="utf-8")
        )
        module = (self.root / "src/gmd/__init__.py").read_text(encoding="utf-8")
        compose = (self.root / "compose.yaml").read_text(encoding="utf-8")
        example = (self.root / ".env.example").read_text(encoding="utf-8")
        self.assertEqual(package["version"], version)
        self.assertEqual(project["project"]["version"], version)
        self.assertIn(f'__version__ = "{version}"', module)
        self.assertEqual(compose.count(f"${{GMD_VERSION:-{version}}}"), 2)
        self.assertIn(f"GMD_VERSION={version}", example)
        self.assertIn(f"/assets/app.js?v={version}", self.html)

    def test_self_extracting_installer_contains_expected_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "installer.run"
            result = subprocess.run(
                ["bash", str(self.root / "scripts/build-installer.sh"), str(output)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = output.read_bytes()
            marker = b"__GMD_PAYLOAD_BELOW__\n"
            self.assertIn(marker, payload)
            header, archive = payload.split(marker, 1)
            expected = re.search(
                rb"PAYLOAD_SHA256='([0-9a-f]{64})'",
                header,
            )
            self.assertIsNotNone(expected)
            self.assertEqual(
                hashlib.sha256(archive).hexdigest(),
                expected.group(1).decode("ascii"),  # type: ignore[union-attr]
            )
            with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as tar:
                names = set(tar.getnames())
            self.assertIn("global-media-discovery/compose.yaml", names)
            self.assertIn("global-media-discovery/seed/catalog.sqlite3", names)
            self.assertIn("global-media-discovery/web/index.html", names)
            self.assertNotIn(
                "global-media-discovery/seed/tvdb_aug_1_13_2026_extended.json",
                names,
            )
            self.assertFalse(
                any(name.endswith(("-wal", "-shm")) for name in names)
            )
            self.assertFalse(
                any("/__pycache__/" in name or name.endswith(".pyc") for name in names)
            )
            self.assertFalse(any("/node_modules/" in name for name in names))

            tampered = Path(temp) / "tampered.run"
            broken = bytearray(payload)
            broken[-1] ^= 1
            tampered.write_bytes(broken)
            failed = subprocess.run(
                ["bash", str(tampered)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("checksum verification failed", failed.stderr)


if __name__ == "__main__":
    unittest.main()
