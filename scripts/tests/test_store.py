"""Tests de scripts/store.py : intégrité du catalogue, reproductibilité des archives, détection des altérations.

Exécution : python3 -m unittest discover -s scripts/tests -v   (depuis la racine du dépôt ; nécessite fastjsonschema)
"""
import argparse
import contextlib
import glob
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import store  # noqa: E402


@contextlib.contextmanager
def repository_copy():
    """Copie jetable du dépôt (sans .git) sur laquelle store.py travaille."""
    saved = (store.ROOT, store.INDEX_PATH, store.SPEC_PATH, store.CHANGELOG_PATH)
    with tempfile.TemporaryDirectory() as directory:
        for name in ("packages", "plugins", "schemas", "tools", "licenses"):
            shutil.copytree(os.path.join(saved[0], name), os.path.join(directory, name), ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(saved[1], os.path.join(directory, "repository.json"))
        shutil.copy(saved[3], os.path.join(directory, "CHANGELOG.md"))
        store.ROOT, store.INDEX_PATH, store.SPEC_PATH, store.CHANGELOG_PATH = (
            directory, os.path.join(directory, "repository.json"), os.path.join(directory, "plugins", "packages.json"), os.path.join(directory, "CHANGELOG.md"))
        try:
            yield directory
        finally:
            store.ROOT, store.INDEX_PATH, store.SPEC_PATH, store.CHANGELOG_PATH = saved


def verify(base=None):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = store.command_verify(argparse.Namespace(base=base))
    return code, output.getvalue()


GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test@example.invalid", GIT_COMMITTER_NAME="test",
               GIT_COMMITTER_EMAIL="test@example.invalid", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def git(directory, *args):
    return subprocess.run(["git", *args], cwd=directory, env=GIT_ENV, capture_output=True, text=True, check=True).stdout.strip()


def commit_all(directory, message):
    git(directory, "add", "-A")
    git(directory, "commit", "-q", "-m", message)
    return git(directory, "rev-parse", "HEAD")


def init_git(directory):
    git(directory, "init", "-q", "-b", "main")
    return commit_all(directory, "catalogue initial")


def edit_json(path, change):
    value = store.read_json(path)
    change(value)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(store.dump_json(value))


def bump_patch(directory, manifest):
    """Incrémente la version d'un manifeste de plugins/ sans construire l'archive. Retourne la nouvelle version."""
    path = os.path.join(directory, "plugins", manifest)
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    current = json.loads(text)["version"]
    major, minor, patch = current.split(".")
    new_version = "%s.%s.%d" % (major, minor, int(patch) + 1)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text.replace('"version": "%s"' % current, '"version": "%s"' % new_version, 1))
    return new_version


def plugin_entry(index, package_id):
    return next(plugin for plugin in index["plugins"] if plugin["id"] == package_id)


def zip_problems(build):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        build(archive)
    problems = []
    store.check_zip_names(zipfile.ZipFile(io.BytesIO(buffer.getvalue())), "test", problems)
    return problems


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

    def test_native_manifest_change_without_new_version_is_detected(self) -> None:
        with repository_copy() as directory:
            edit_json(os.path.join(directory, "plugins", "base", "manifests", "base.json"), lambda manifest: manifest.update(description="modifiée"))
            code, output = verify()
            self.assertEqual(1, code)
            self.assertIn("plugin base", output)
            self.assertIn("plugin.json diffère des sources", output)

    def test_shared_sdk_change_without_new_version_is_detected_for_each_plugin(self) -> None:
        with repository_copy() as directory:
            with open(os.path.join(directory, "plugins", "sdk", "encoding_plugin_sdk.py"), "a", encoding="utf-8") as handle:
                handle.write("\n# modifié\n")
            code, output = verify()
            self.assertEqual(1, code)
            for package_id in ("jellyfin", "plex", "radarr", "sonarr"):
                self.assertIn("plugin %s " % package_id, output)
                self.assertRegex(output, "plugin %s [0-9.]+ : encoding_plugin_sdk.py diffère des sources" % package_id)

    def test_new_version_without_archive_is_detected(self) -> None:
        # Le précédent base 1.10.3 / subtitles 1.5.0 : version incrémentée, archive jamais construite.
        with repository_copy() as directory:
            new_version = bump_patch(directory, "base/manifests/base.json")
            code, output = verify()
            self.assertEqual(1, code)
            self.assertIn("plugin base : la version %s des sources n'est pas publiée" % new_version, output)

    def test_sources_rolled_back_to_an_older_published_version_are_detected(self) -> None:
        with repository_copy() as directory:
            subtitles = plugin_entry(store.read_json(store.INDEX_PATH), "subtitles")
            latest = subtitles["latestVersion"]
            previous = sorted((entry["version"] for entry in subtitles["versions"]), key=store.semver_key)[-2]
            with zipfile.ZipFile(store.archive_path("subtitles", previous)) as archive:
                old_manifest = json.loads(archive.read("plugin.json"))
            old_manifest.pop("runtime", None)
            with open(os.path.join(directory, "plugins", "subtitles", "manifests", "subtitles.json"), "w", encoding="utf-8") as handle:
                handle.write(store.dump_json(old_manifest))
            code, output = verify()
            self.assertEqual(1, code)
            self.assertIn("plugin subtitles : les sources sont en %s mais la dernière version publiée est %s" % (previous, latest), output)

    def test_undeclared_source_file_is_detected(self) -> None:
        with repository_copy() as directory:
            with open(os.path.join(directory, "plugins", "plex", "helpers.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            os.makedirs(os.path.join(directory, "plugins", "plex", "__pycache__"))
            with open(os.path.join(directory, "plugins", "plex", "__pycache__", "plugin.cpython-312.pyc"), "wb") as handle:
                handle.write(b"ignored")
            code, output = verify()
            self.assertEqual(1, code)
            self.assertIn("plugins/plex/helpers.py : fichier source déclaré par aucun paquet", output)
            self.assertNotIn("__pycache__", output)

    def test_orphan_archive_is_detected(self) -> None:
        with repository_copy() as directory:
            os.makedirs(os.path.join(directory, "packages", "plex", "9.9.9"))
            with open(os.path.join(directory, "packages", "plex", "9.9.9", "9.9.9.zip"), "wb") as handle:
                handle.write(b"PK")
            code, output = verify()
            self.assertEqual(1, code)
            self.assertIn("absente de repository.json", output)

    def test_stray_file_at_any_depth_of_packages_is_detected(self) -> None:
        with repository_copy() as directory:
            for relative in (("plex", "notes.txt"), ("plex", "1.1.1", "extra", "payload.zip")):
                os.makedirs(os.path.join(directory, "packages", *relative[:-1]), exist_ok=True)
                with open(os.path.join(directory, "packages", *relative), "wb") as handle:
                    handle.write(b"x")
            code, output = verify()
            self.assertEqual(1, code)
            self.assertIn(os.path.join("packages", "plex", "notes.txt"), output)
            self.assertIn(os.path.join("packages", "plex", "1.1.1", "extra", "payload.zip"), output)

    def test_duplicate_zip_entries_are_rejected(self) -> None:
        def build(archive: zipfile.ZipFile) -> None:
            archive.writestr("plugin.json", "{}")
            with self.assertWarns(UserWarning):
                archive.writestr("plugin.json", "{}")
        self.assertEqual(["test : entrée dupliquée 'plugin.json'."], zip_problems(build))

    def test_symlink_zip_entries_are_rejected(self) -> None:
        def build(archive: zipfile.ZipFile) -> None:
            info = zipfile.ZipInfo("plugin.py")
            info.external_attr = 0o120777 << 16
            archive.writestr(info, "/etc/passwd")
        self.assertEqual(["test : lien symbolique interdit (plugin.py)."], zip_problems(build))

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



@unittest.skipUnless(shutil.which("git"), "git est requis")
class ImmutabilityTests(unittest.TestCase):
    """verify --base : rien de ce qui est publié à la base ne change, ni l'archive ni son entrée d'index."""

    def test_unchanged_catalogue_passes(self) -> None:
        with repository_copy() as directory:
            base = init_git(directory)
            code, output = verify(base)
            self.assertEqual(0, code, output)

    def test_new_version_passes(self) -> None:
        with repository_copy() as directory:
            base = init_git(directory)
            bump_patch(directory, "plex/manifests/plex.json")
            with contextlib.redirect_stdout(io.StringIO()):
                store.command_build(argparse.Namespace(id="plex", ref="main"))
            code, output = verify(base)
            self.assertEqual(0, code, output)

    def test_republished_archive_with_matching_hash_is_rejected(self) -> None:
        # verify seul ne voit rien : archive et hash d'index changent ensemble.
        with repository_copy() as directory:
            base = init_git(directory)
            path = store.archive_path("plex", "1.1.1")
            with zipfile.ZipFile(path) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            entries["plugin.py"] += b"\n# altered\n"
            data = store.build_zip(entries)
            with open(path, "wb") as handle:
                handle.write(data)
            edit_json(store.INDEX_PATH, lambda index: next(v for v in plugin_entry(index, "plex")["versions"] if v["version"] == "1.1.1").update(sha256=store.sha256(data)))
            code, output = verify(base)
            self.assertEqual(1, code)
            self.assertIn("packages/plex/1.1.1/1.1.1.zip : un paquet publié est immuable (statut M)", output)
            self.assertIn("plugin plex 1.1.1 : sha256 modifié dans repository.json", output)

    def test_removed_version_is_rejected(self) -> None:
        with repository_copy() as directory:
            base = init_git(directory)
            shutil.rmtree(os.path.join(directory, "packages", "plex", "1.1.0"))
            edit_json(store.INDEX_PATH, lambda index: plugin_entry(index, "plex").update(versions=[v for v in plugin_entry(index, "plex")["versions"] if v["version"] != "1.1.0"]))
            code, output = verify(base)
            self.assertEqual(1, code)
            self.assertIn("(statut D)", output)
            self.assertIn("plugin plex 1.1.0 : version publiée retirée de repository.json", output)

    def test_published_download_url_cannot_move_to_another_ref(self) -> None:
        with repository_copy() as directory:
            base = init_git(directory)

            def repoint(index: dict) -> None:
                entry = next(v for v in plugin_entry(index, "plex")["versions"] if v["version"] == "1.1.1")
                entry["downloadUrl"] = entry["downloadUrl"].replace("/main/", "/some-unmerged-branch/")
            edit_json(store.INDEX_PATH, repoint)
            code, output = verify(base)
            self.assertEqual(1, code)
            self.assertIn("plugin plex 1.1.1 : downloadUrl modifié", output)

    def test_published_download_url_may_be_pinned_to_a_commit(self) -> None:
        with repository_copy() as directory:
            base = init_git(directory)

            def pin(index: dict) -> None:
                entry = next(v for v in plugin_entry(index, "plex")["versions"] if v["version"] == "1.1.1")
                entry["downloadUrl"] = entry["downloadUrl"].replace("/main/", "/%s/" % base)
            edit_json(store.INDEX_PATH, pin)
            code, output = verify(base)
            self.assertEqual(0, code, output)

    def test_branch_behind_its_base_has_no_false_positive(self) -> None:
        # La base a publié une version après le départ de la branche : ce n'est pas une suppression côté branche.
        with repository_copy() as directory:
            init_git(directory)
            git(directory, "switch", "-q", "-c", "feature")
            git(directory, "switch", "-q", "main")
            bump_patch(directory, "plex/manifests/plex.json")
            with contextlib.redirect_stdout(io.StringIO()):
                store.command_build(argparse.Namespace(id="plex", ref="main"))
            commit_all(directory, "plex suivant")
            git(directory, "switch", "-q", "feature")
            code, output = verify("main")
            self.assertEqual(0, code, output)

    def test_unknown_base_is_reported(self) -> None:
        with repository_copy() as directory:
            init_git(directory)
            code, output = verify("does-not-exist")
            self.assertEqual(1, code)
            self.assertIn("git merge-base does-not-exist HEAD a échoué", output)


if __name__ == "__main__":
    unittest.main()


def run(handler, **kwargs):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = handler(argparse.Namespace(**kwargs))
    return code, output.getvalue()


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


SAMPLE_CHANGELOG = "# Changelog\n\nIntroduction.\n\n## Non publié\n\n### Plugins\n- un changement\n\n## v2000.01.01\n\n- ancien\n"


def seed_changelog():
    """CHANGELOG connu : les tests ne dépendent pas de l'état du vrai journal (par exemple juste après un release-prep)."""
    with open(store.CHANGELOG_PATH, "w", encoding="utf-8") as handle:
        handle.write(SAMPLE_CHANGELOG)


def read_changelog():
    with open(store.CHANGELOG_PATH, encoding="utf-8") as handle:
        return handle.read()


class PendingBuildTests(unittest.TestCase):
    """pending et build --all-pending : toutes les versions des sources sans archive, en une commande, sans toucher au publié."""

    def test_nothing_pending_on_a_valid_catalogue(self) -> None:
        with repository_copy():
            self.assertEqual([], store.pending_plugins(store.read_json(store.INDEX_PATH), store.read_json(store.SPEC_PATH)))
            code, output = run(store.command_build, id=None, all_pending=True, ref="main")
            self.assertEqual(0, code)
            self.assertIn("Aucun plugin en attente", output)

    def test_all_pending_builds_every_bumped_plugin_and_leaves_published_archives_alone(self) -> None:
        with repository_copy() as directory:
            before = {path: read_bytes(path) for path in glob.glob(os.path.join(directory, "packages", "**", "*.zip"), recursive=True)}
            sonarr = bump_patch(directory, "arr/manifests/sonarr.json")
            radarr = bump_patch(directory, "arr/manifests/radarr.json")
            code, output = run(store.command_pending)
            self.assertIn("radarr %s" % radarr, output)
            self.assertIn("sonarr %s" % sonarr, output)
            code, output = run(store.command_build, id=None, all_pending=True, ref="main")
            self.assertEqual(0, code, output)
            index = store.read_json(store.INDEX_PATH)
            self.assertEqual(sonarr, plugin_entry(index, "sonarr")["latestVersion"])
            self.assertEqual(radarr, plugin_entry(index, "radarr")["latestVersion"])
            for path, data in before.items():
                self.assertEqual(data, read_bytes(path), path)
            self.assertEqual(0, verify()[0])
            code, output = run(store.command_build, id=None, all_pending=True, ref="main")
            self.assertIn("Aucun plugin en attente", output)


@unittest.skipUnless(shutil.which("git"), "git est requis")
class ReleasePrepTests(unittest.TestCase):
    """release-prep : construit, contrôle puis écrit la section du CHANGELOG ; ne tague ni ne publie rien."""

    def setUp(self) -> None:
        self.copy = repository_copy()
        self.directory = self.copy.__enter__()
        self.addCleanup(self.copy.__exit__, None, None, None)
        seed_changelog()

    def test_first_snapshot_lists_latest_versions_and_reopens_unreleased(self) -> None:
        directory = self.directory
        init_git(directory)
        unreleased = store.section_body(read_changelog(), store.UNRELEASED)
        code, output = run(store.command_release_prep, tag="v2099.11.07", ref="main")
        self.assertEqual(0, code, output)
        text = read_changelog()
        self.assertLess(text.index("## Non publié"), text.index("## v2099.11.07"))
        self.assertEqual("", store.section_body(text, store.UNRELEASED))
        notes = store.section_body(text, "## v2099.11.07")
        self.assertIn("Premier instantané", notes)
        self.assertIn("- plugin plex ", notes)
        self.assertIn("- outil ffmpeg ", notes)
        self.assertIn(unreleased, notes)
        code, output = run(store.command_changelog, tag="v2099.11.07")
        self.assertEqual(0, code)
        self.assertEqual(notes, output.strip("\n"))
        self.assertIn("## v2000.01.01\n\n- ancien", text)
        self.assertEqual("", git(directory, "tag"))

    def test_snapshot_builds_pending_plugins_and_lists_only_new_versions(self) -> None:
        directory = self.directory
        init_git(directory)
        run(store.command_release_prep, tag="v2099.01.01", ref="main")
        commit_all(directory, "instantané")
        git(directory, "tag", "v2099.01.01")
        version = bump_patch(directory, "plex/manifests/plex.json")
        code, output = run(store.command_release_prep, tag="v2099.02.01", ref="main")
        self.assertEqual(0, code, output)
        self.assertTrue(os.path.isfile(store.archive_path("plex", version)))
        notes = store.section_body(read_changelog(), "## v2099.02.01")
        self.assertEqual("### Paquets de l'instantané\n\nNouvelles versions depuis v2099.01.01 :\n\n- plugin plex %s" % version, notes)

    def test_nothing_to_publish_is_refused(self) -> None:
        directory = self.directory
        init_git(directory)
        run(store.command_release_prep, tag="v2099.01.01", ref="main")
        commit_all(directory, "instantané")
        git(directory, "tag", "v2099.01.01")
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(io.StringIO()):
            store.command_release_prep(argparse.Namespace(tag="v2099.02.01", ref="main"))
        self.assertIn("Rien à publier", str(raised.exception))

    def test_existing_tag_or_section_and_invalid_tags_are_refused(self) -> None:
        directory = self.directory
        init_git(directory)
        git(directory, "tag", "v2099.01.01")
        for tag, message in (("v2099.01.01", "existe déjà"), ("v2099.13.01", "Tag invalide"), ("2026.01.02", "Tag invalide"), ("v2099.1.2", "Tag invalide")):
            with self.assertRaises(SystemExit) as raised:
                store.command_release_prep(argparse.Namespace(tag=tag, ref="main"))
            self.assertIn(message, str(raised.exception))
        run(store.command_release_prep, tag="v2099.03.01", ref="main")
        with self.assertRaises(SystemExit) as raised:
            store.command_release_prep(argparse.Namespace(tag="v2099.03.01", ref="main"))
        self.assertIn("déjà la section", str(raised.exception))

    def test_failed_verification_leaves_changelog_untouched(self) -> None:
        directory = self.directory
        init_git(directory)
        before = read_changelog()
        path = store.archive_path("plex", plugin_entry(store.read_json(store.INDEX_PATH), "plex")["latestVersion"])
        with open(path, "ab") as handle:
            handle.write(b"x")
        code, output = run(store.command_release_prep, tag="v2099.11.07", ref="main")
        self.assertEqual(1, code)
        self.assertIn("Préparation interrompue", output)
        self.assertEqual(before, read_changelog())

    def test_missing_changelog_section_fails(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, run(store.command_changelog, tag="v1999.01.01")[0])
