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
        cls.app = (cls.root / "frontend/src/App.tsx").read_text(encoding="utf-8")

    def test_requested_frontend_stack_is_exact_and_strict(self) -> None:
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["dependencies"]["react"], "19.2.8")
        self.assertEqual(package["devDependencies"]["typescript"], "7.0.2")
        self.assertEqual(package["devDependencies"]["vite"], "8.2.2")
        for dependency in (
            "@tanstack/react-query",
            "@tanstack/react-router",
            "@tanstack/react-virtual",
        ):
            self.assertIn(dependency, package["dependencies"])
        tsconfig = json.loads((self.root / "tsconfig.json").read_text(encoding="utf-8"))
        self.assertTrue(tsconfig["compilerOptions"]["strict"])
        self.assertTrue(tsconfig["compilerOptions"]["noUncheckedIndexedAccess"])

    def test_country_and_reset_are_controlled_by_url_state(self) -> None:
        self.assertIn("value={search.country}", self.app)
        self.assertIn("onChange={(country) => setSearch({ country })}", self.app)
        self.assertIn("navigate({ search: { view: 'day', date: today } })", self.app)
        self.assertNotIn("selected={", self.app)

    def test_go_serves_frontend_and_read_only_api(self) -> None:
        source = (self.root / "cmd/gmd-server/main.go").read_text(encoding="utf-8")
        compose = (self.root / "compose.yaml").read_text(encoding="utf-8")
        caddy = (self.root / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn("//go:embed all:dist", source)
        self.assertIn('method_not_allowed', source)
        self.assertIn('mode=ro', source)
        self.assertIn("Dockerfile.api", compose)
        self.assertIn("./data:/data:ro", compose)
        self.assertNotIn("./web:/srv/web", compose)
        self.assertIn("reverse_proxy api:8080", caddy)

    def test_public_version_references_are_synchronized(self) -> None:
        version = (self.root / "VERSION").read_text(encoding="utf-8").strip()
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        project = tomllib.loads((self.root / "pyproject.toml").read_text(encoding="utf-8"))
        module = (self.root / "src/gmd/__init__.py").read_text(encoding="utf-8")
        compose = (self.root / "compose.yaml").read_text(encoding="utf-8")
        example = (self.root / ".env.example").read_text(encoding="utf-8")
        self.assertEqual(package["version"], version)
        self.assertEqual(project["project"]["version"], version)
        self.assertIn(f'__version__ = "{version}"', module)
        self.assertEqual(compose.count(f"${{GMD_VERSION:-{version}}}"), 2)
        self.assertIn(f"GMD_VERSION={version}", example)

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
            header, archive = payload.split(marker, 1)
            expected = re.search(rb"PAYLOAD_SHA256='([0-9a-f]{64})'", header)
            self.assertIsNotNone(expected)
            self.assertEqual(
                hashlib.sha256(archive).hexdigest(),
                expected.group(1).decode("ascii"),  # type: ignore[union-attr]
            )
            with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as tar:
                names = set(tar.getnames())
            for required in (
                "global-media-discovery/compose.yaml",
                "global-media-discovery/seed/catalog.sqlite3",
                "global-media-discovery/Dockerfile.api",
                "global-media-discovery/cmd/gmd-server/main.go",
                "global-media-discovery/frontend/src/App.tsx",
                "global-media-discovery/package-lock.json",
            ):
                self.assertIn(required, names)
            self.assertFalse(any("/node_modules" in name for name in names))
            self.assertFalse(any(name.endswith(("-wal", "-shm")) for name in names))


if __name__ == "__main__":
    unittest.main()
