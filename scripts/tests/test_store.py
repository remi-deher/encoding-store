"""Tests de scripts/store.py : intégrité du catalogue, reproductibilité des archives, détection des altérations.

Exécution : python3 -m unittest discover -s scripts/tests -v   (depuis la racine du dépôt ; nécessite fastjsonschema)
"""
import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import store  # noqa: E402


@contextlib.contextmanager
def repository_copy():
    """Copie jetable du dépôt (sans .git) sur laquelle store.py travaille."""
    saved = (store.ROOT, store.INDEX_PATH, store.SPEC_PATH)
    with tempfile.TemporaryDirectory() as directory:
        for name in ("packages", "plugins", "schemas", "tools", "licenses"):
            shutil.copytree(os.path.join(saved[0], name), os.path.join(directory, name), ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(saved[1], os.path.join(directory, "repository.json"))
        store.ROOT, store.INDEX_PATH, store.SPEC_PATH = directory, os.path.join(directory, "repository.json"), os.path.join(directory, "plugins", "packages.json")
        try:
            yield directory
        finally:
            store.ROOT, store.INDEX_PATH, store.SPEC_PATH = saved


def verify():
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = store.command_verify(argparse.Namespace(base=None))
    return code, output.getvalue()


class StoreTests(unittest.TestCase):
    def test_repository_is_valid(self) -> None:
        code, output = verify()
        self.assertEqual(0, code, output)

    def test_archives_are_reproducible(self) -> None:
        spec = store.read_json(store.SPEC_PATH)
        _, entries = store.expected_entries(spec, "plex")
        first = store.build_zip(entries)
        self.assertEqual(first, store.build_zip(dict(reversed(list(entries.items())))))
        self.assertEqual("plugin.json", zipfile.ZipFile(io.BytesIO(first)).namelist()[0])

    def test_tampered_archive_is_detected(self) -> None:
        with repository_copy() as directory:
            path = os.path.join(directory, "packages", "plex", "1.1.1", "1.1.1.zip")
            with open(path, "ab") as handle:
                handle.write(b"x")
            code, output = verify()
            self.assertEqual(1, code)
            self.assertIn("plex 1.1.1", output)

    def test_source_change_without_new_version_is_detected(self) -> None:
        with repository_copy() as directory:
            with open(os.path.join(directory, "plugins", "plex", "plugin.py"), "a", encoding="utf-8") as handle:
                handle.write("\n# modifié\n")
            code, output = verify()
            self.assertEqual(1, code)
            self.assertIn("plugin.py diffère des sources", output)

    def test_orphan_archive_is_detected(self) -> None:
        with repository_copy() as directory:
            os.makedirs(os.path.join(directory, "packages", "plex", "9.9.9"))
            with open(os.path.join(directory, "packages", "plex", "9.9.9", "9.9.9.zip"), "wb") as handle:
                handle.write(b"PK")
            code, output = verify()
            self.assertEqual(1, code)
            self.assertIn("absente de repository.json", output)

    def test_show_prints_indented_manifest_of_a_published_archive(self) -> None:
        for package, version in (("ffmpeg", None), ("plex", "1.1.0"), ("base", None)):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                store.command_show(argparse.Namespace(id=package, version=version))
            text = output.getvalue()
            self.assertGreater(text.count("\n"), 5, package)
            self.assertEqual(package, json.loads(text)["id"])

    def test_published_version_cannot_be_rebuilt(self) -> None:
        with repository_copy():
            with self.assertRaises(SystemExit) as raised:
                store.command_build(argparse.Namespace(id="plex", ref="main"))
            self.assertIn("immuable", str(raised.exception))

    def test_build_publishes_a_new_version(self) -> None:
        with repository_copy() as directory:
            manifest_path = os.path.join(directory, "plugins", "plex", "manifests", "plex.json")
            with open(manifest_path, encoding="utf-8") as handle:
                text = handle.read()
            current = json.loads(text)["version"]
            major, minor, patch = current.split(".")
            new_version = "%s.%s.%d" % (major, minor, int(patch) + 1)
            with open(manifest_path, "w", encoding="utf-8") as handle:
                handle.write(text.replace('"version": "%s"' % current, '"version": "%s"' % new_version, 1))
            with contextlib.redirect_stdout(io.StringIO()):
                store.command_build(argparse.Namespace(id="plex", ref="v2026.11"))
            index = store.read_json(store.INDEX_PATH)
            plex = next(plugin for plugin in index["plugins"] if plugin["id"] == "plex")
            self.assertEqual(new_version, plex["latestVersion"])
            self.assertIn("/v2026.11/packages/plex/%s/%s.zip" % (new_version, new_version), plex["versions"][-1]["downloadUrl"])
            code, output = verify()
            self.assertEqual(0, code, output)


if __name__ == "__main__":
    unittest.main()
