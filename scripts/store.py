#!/usr/bin/env python3
"""Outils du dépôt Encoding Store : construction reproductible des paquets de plugins et contrôle du catalogue.

    python3 scripts/store.py verify [--base REF]     contrôle complet (CI) ; --base REF refuse toute modification d'un paquet déjà publié
    python3 scripts/store.py build <id> [--ref REF]  construit packages/<id>/<version>/<version>.zip depuis plugins/ et l'ajoute à repository.json
    python3 scripts/store.py show <id> [version]     affiche le manifeste interne (plugin.json ou tool.json) d'une archive publiée, indenté
    python3 scripts/store.py sync-index              recopie nom, résumé, catégorie et outils requis de plugins/packages.json vers repository.json

Dépendance : fastjsonschema (pip install fastjsonschema), pour valider les manifestes contre schemas/.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from typing import Any, Callable

Json = Any
Problems = list[str]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(ROOT, "repository.json")
SPEC_PATH = os.path.join(ROOT, "plugins", "packages.json")
DOWNLOAD_BASE = "https://raw.githubusercontent.com/remi-deher/encoding-store"
FIXED_DATE = (1980, 1, 1, 0, 0, 0)
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def semver_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def read_json(path: str) -> Json:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(value: Json) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_path(package_id: str, version: str) -> str:
    return os.path.join(ROOT, "packages", package_id, version, version + ".zip")


def download_url(ref: str, package_id: str, version: str) -> str:
    return "%s/%s/packages/%s/%s/%s.zip" % (DOWNLOAD_BASE, ref, package_id, version, version)


def load_validator(name: str) -> Callable[[Json], Json]:
    try:
        import fastjsonschema  # type: ignore[import-untyped]
    except ImportError:
        sys.exit("fastjsonschema est requis : pip install fastjsonschema")
    return fastjsonschema.compile(read_json(os.path.join(ROOT, "schemas", name)))


# --- contenu attendu d'un paquet de plugin --------------------------------------------------------------------------

def expected_entries(spec: Json, package_id: str) -> tuple[Json, dict[str, bytes]]:
    """{nom dans l'archive: octets} d'un paquet de plugin, d'après ses sources."""
    entry = spec[package_id]
    manifest_path = os.path.join(ROOT, "plugins", entry["manifest"])
    with open(manifest_path, "rb") as handle:
        manifest_bytes = handle.read()
    manifest = json.loads(manifest_bytes)
    entries = {"plugin.json": dump_json(dict(manifest, runtime=entry["runtime"])).encode("utf-8")}
    if "manifestInArchive" in entry:
        entries[entry["manifestInArchive"]] = manifest_bytes
    for name, source in entry.get("files", {}).items():
        with open(os.path.join(ROOT, "plugins", source), "rb") as handle:
            entries[name] = handle.read()
    for name, content in entry.get("inline", {}).items():
        entries[name] = content.encode("utf-8")
    return manifest, entries


def build_zip(entries: dict[str, bytes]) -> bytes:
    """Archive déterministe : ordre trié, horodatage et permissions fixes, plugin.json en premier."""
    buffer = io.BytesIO()
    names = ["plugin.json"] + sorted(name for name in entries if name != "plugin.json")
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            info = zipfile.ZipInfo(name, FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, entries[name], compresslevel=9)
    return buffer.getvalue()


def same_entry(name: str, left: bytes, right: bytes) -> bool:
    if name.endswith(".json"):
        return json.loads(left) == json.loads(right)
    return left == right


# --- index ------------------------------------------------------------------------------------------------------

def sync_metadata(index: Json, spec: Json) -> None:
    plugins = {plugin["id"]: plugin for plugin in index["plugins"]}
    for package_id, entry in spec.items():
        if package_id.startswith("_"):
            continue
        plugin = plugins.get(package_id)
        if plugin is None:
            plugin = {"id": package_id, "versions": []}
            index["plugins"].append(plugin)
            index["plugins"].sort(key=lambda item: item["id"])
        plugin.update(name=entry["name"], summary=entry["summary"], category=entry["category"], requiredTools=entry.get("requiredTools", []))
        ordered = {key: plugin[key] for key in ("id", "name", "summary", "category", "requiredTools", "latestVersion", "versions") if key in plugin}
        plugin.clear()
        plugin.update(ordered)


def command_sync_index(_args: argparse.Namespace) -> int:
    index, spec = read_json(INDEX_PATH), read_json(SPEC_PATH)
    sync_metadata(index, spec)
    with open(INDEX_PATH, "w", encoding="utf-8") as handle:
        handle.write(dump_json(index))
    print("repository.json synchronisé.")
    return 0


def command_build(args: argparse.Namespace) -> int:
    index, spec = read_json(INDEX_PATH), read_json(SPEC_PATH)
    if args.id not in spec or args.id.startswith("_"):
        sys.exit("Plugin inconnu dans plugins/packages.json : %s" % args.id)
    manifest, entries = expected_entries(spec, args.id)
    version = manifest["version"]
    if manifest["id"] != args.id or not SEMVER.match(version):
        sys.exit("Manifeste incohérent : id %r, version %r." % (manifest["id"], version))
    target = archive_path(args.id, version)
    if os.path.exists(target):
        sys.exit("%s %s est déjà publié et immuable : incrémentez la version du manifeste." % (args.id, version))
    data = build_zip(entries)
    os.makedirs(os.path.dirname(target))
    with open(target, "wb") as handle:
        handle.write(data)
    sync_metadata(index, spec)
    plugin = next(item for item in index["plugins"] if item["id"] == args.id)
    plugin["versions"].append({"version": version, "downloadUrl": download_url(args.ref, args.id, version), "sha256": sha256(data)})
    plugin["versions"].sort(key=lambda item: semver_key(item["version"]))
    plugin["latestVersion"] = plugin["versions"][-1]["version"]
    with open(INDEX_PATH, "w", encoding="utf-8") as handle:
        handle.write(dump_json(index))
    print("%s %s construit (%s)." % (args.id, version, sha256(data)))
    return 0


def command_show(args: argparse.Namespace) -> int:
    index = read_json(INDEX_PATH)
    item = next((entry for kind in ("plugins", "tools") for entry in index[kind] if entry["id"] == args.id), None)
    if item is None:
        sys.exit("Paquet inconnu : %s" % args.id)
    version = args.version or item["latestVersion"]
    path = archive_path(args.id, version)
    if not os.path.isfile(path):
        sys.exit("Archive absente : %s %s" % (args.id, version))
    with zipfile.ZipFile(path) as archive:
        name = "plugin.json" if "plugin.json" in archive.namelist() else "tool.json"
        sys.stdout.write(dump_json(json.loads(archive.read(name))))
    return 0


# --- contrôle ---------------------------------------------------------------------------------------------------

def check_zip_names(archive: zipfile.ZipFile, label: str, problems: Problems) -> None:
    for name in archive.namelist():
        if name.startswith("/") or ".." in name.split("/") or "\\" in name:
            problems.append("%s : nom d'entrée dangereux %r." % (label, name))


def check_versions(kind: str, item: Json, problems: Problems) -> None:
    versions = item["versions"]
    label = "%s %s" % (kind, item["id"])
    if not versions:
        problems.append("%s : aucune version." % label)
        return
    numbers = [entry["version"] for entry in versions]
    if numbers != sorted(set(numbers), key=semver_key):
        problems.append("%s : versions non triées ou dupliquées." % label)
    if item["latestVersion"] != max(numbers, key=semver_key):
        problems.append("%s : latestVersion %s n'est pas la plus récente (%s)." % (label, item["latestVersion"], max(numbers, key=semver_key)))


def check_archives(kind: str, item: Json, validate_manifest: Callable[[Json], Json], problems: Problems,
                   published: set[str]) -> dict[str, dict[str, bytes]]:
    """Contrôle chaque archive listée : fichier, SHA-256, URL, manifeste interne. Retourne {version: {nom: octets}}."""
    contents: dict[str, dict[str, bytes]] = {}
    for entry in item["versions"]:
        version = entry["version"]
        label = "%s %s %s" % (kind, item["id"], version)
        path = archive_path(item["id"], version)
        published.add(os.path.relpath(path, ROOT))
        if not os.path.isfile(path):
            problems.append("%s : archive absente." % label)
            continue
        with open(path, "rb") as handle:
            data = handle.read()
        if sha256(data) != entry["sha256"]:
            problems.append("%s : SHA-256 différent de repository.json." % label)
        if not re.fullmatch(re.escape(DOWNLOAD_BASE) + r"/[A-Za-z0-9._/-]+/packages/%s/%s/%s\.zip" % (re.escape(item["id"]), re.escape(version), re.escape(version)), entry["downloadUrl"]):
            problems.append("%s : URL de téléchargement inattendue (%s)." % (label, entry["downloadUrl"]))
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            problems.append("%s : archive illisible." % label)
            continue
        check_zip_names(archive, label, problems)
        members = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
        contents[version] = members
        manifest_name = "plugin.json" if kind == "plugin" else "tool.json"
        if manifest_name not in members:
            problems.append("%s : %s absent." % (label, manifest_name))
            continue
        manifest = json.loads(members[manifest_name])
        try:
            # Les archives natives historiques (antérieures à runtime) restent valides : elles sont immuables.
            validate_manifest(manifest if kind != "plugin" or "runtime" in manifest or version == item["latestVersion"] else dict(manifest, runtime={"type": "native", "entrypoint": ""}))
        except Exception as error:  # noqa: BLE001 - fastjsonschema.JsonSchemaException
            problems.append("%s : %s non conforme au schéma (%s)." % (label, manifest_name, error))
        if manifest.get("id") != item["id"] or manifest.get("version") != version:
            problems.append("%s : id/version du manifeste interne différents de l'index." % label)
        if kind == "tool":
            for executable in manifest.get("executables", []):
                body = members.get(executable["path"])
                if body is None:
                    problems.append("%s : exécutable absent (%s)." % (label, executable["path"]))
                elif sha256(body) != executable["sha256"].lower():
                    problems.append("%s : SHA-256 de %s différent de tool.json." % (label, executable["path"]))
            if entry.get("platform") != manifest.get("platform"):
                problems.append("%s : plateforme de l'index différente de tool.json." % label)
    return contents


def check_licenses(problems: Problems) -> None:
    lock = read_json(os.path.join(ROOT, "tools", "ubuntu-noble-lock.json"))
    for name, package in lock["packages"].items():
        if package["sha256_expected_from_Ubuntu_Packages"] != package["sha256_downloaded"]:
            problems.append("verrou %s : hash téléchargé différent du hash attendu." % name)
        if not os.path.isfile(os.path.join(ROOT, package["copyrightPath"])):
            problems.append("verrou %s : fichier de licence absent (%s)." % (name, package["copyrightPath"]))


def check_immutability(base: str, problems: Problems) -> None:
    result = subprocess.run(["git", "diff", "--name-status", "--no-renames", base, "--", "packages"], cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        problems.append("git diff %s a échoué : %s" % (base, result.stderr.strip()))
        return
    for line in result.stdout.splitlines():
        status, _, path = line.partition("\t")
        if status != "A":
            problems.append("%s : un paquet publié est immuable (statut %s)." % (path, status))


def command_verify(args: argparse.Namespace) -> int:
    index, spec = read_json(INDEX_PATH), read_json(SPEC_PATH)
    problems: Problems = []
    try:
        load_validator("repository.schema.json")(index)
    except Exception as error:  # noqa: BLE001
        problems.append("repository.json non conforme au schéma (%s)." % error)
    categories = {category["id"] for category in index["categories"]}
    published: set[str] = set()
    contents: dict[str, dict[str, dict[str, bytes]]] = {}
    plugin_validator, tool_validator = load_validator("plugin-package.schema.json"), load_validator("tool-package.schema.json")
    for plugin in index["plugins"]:
        if plugin["category"] not in categories:
            problems.append("plugin %s : catégorie inconnue %s." % (plugin["id"], plugin["category"]))
        check_versions("plugin", plugin, problems)
        contents[plugin["id"]] = check_archives("plugin", plugin, plugin_validator, problems, published)
    for tool in index["tools"]:
        check_versions("outil", tool, problems)
        check_archives("tool", tool, tool_validator, problems, published)
    check_plugin_sources(index, spec, contents, problems)
    on_disk = {os.path.relpath(path, ROOT) for path in glob.glob(os.path.join(ROOT, "packages", "*", "*", "*"))}
    for orphan in sorted(on_disk - published):
        problems.append("%s : archive absente de repository.json." % orphan)
    check_licenses(problems)
    if args.base:
        check_immutability(args.base, problems)
    for problem in problems:
        print("ERREUR", problem)
    print("%d erreur(s)." % len(problems) if problems else "Catalogue valide.")
    return 1 if problems else 0


def check_plugin_sources(index: Json, spec: Json, contents: dict[str, dict[str, dict[str, bytes]]], problems: Problems) -> None:
    plugins = {plugin["id"]: plugin for plugin in index["plugins"]}
    sources = {key for key in spec if not key.startswith("_")}
    for package_id in sorted(sources - set(plugins)):
        problems.append("plugin %s : défini dans plugins/packages.json mais absent de repository.json." % package_id)
    for package_id in sorted(set(plugins) - sources):
        problems.append("plugin %s : sans sources déclarées dans plugins/packages.json." % package_id)
    for package_id in sorted(sources & set(plugins)):
        check_one_plugin(plugins[package_id], spec, contents[package_id], problems)


def check_one_plugin(plugin: Json, spec: Json, published_versions: dict[str, dict[str, bytes]], problems: Problems) -> None:
    package_id = plugin["id"]
    entry = spec[package_id]
    for key in ("name", "summary", "category"):
        if plugin.get(key) != entry[key]:
            problems.append("plugin %s : %s différent de plugins/packages.json (python3 scripts/store.py sync-index)." % (package_id, key))
    if plugin.get("requiredTools", []) != entry.get("requiredTools", []):
        problems.append("plugin %s : requiredTools différent de plugins/packages.json." % package_id)
    manifest, expected = expected_entries(spec, package_id)
    version = manifest["version"]
    published = published_versions.get(version)
    if published is None:
        problems.append("plugin %s : la version %s des sources n'est pas publiée (python3 scripts/store.py build %s)." % (package_id, version, package_id))
        return
    if set(published) != set(expected):
        problems.append("plugin %s %s : entrées de l'archive %s différentes des sources %s." % (package_id, version, sorted(published), sorted(expected)))
        return
    for name in sorted(expected):
        if not same_entry(name, published[name], expected[name]):
            problems.append("plugin %s %s : %s diffère des sources (modifiez la version puis reconstruisez)." % (package_id, version, name))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--base", help="référence git de comparaison : les paquets déjà publiés ne doivent pas changer")
    verify.set_defaults(handler=command_verify)
    build = commands.add_parser("build")
    build.add_argument("id")
    build.add_argument("--ref", default="main", help="branche, tag ou commit servi dans downloadUrl (défaut : main)")
    build.set_defaults(handler=command_build)
    show = commands.add_parser("show")
    show.add_argument("id")
    show.add_argument("version", nargs="?", help="défaut : la dernière version")
    show.set_defaults(handler=command_show)
    commands.add_parser("sync-index").set_defaults(handler=command_sync_index)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
