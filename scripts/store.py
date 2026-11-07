#!/usr/bin/env python3
"""Outils du dépôt Encoding Store : construction reproductible des paquets de plugins et contrôle du catalogue.

    python3 scripts/store.py verify [--base REF]     contrôle complet (CI) ; --base REF refuse toute modification d'un paquet déjà publié
    python3 scripts/store.py build <id> [--ref REF]  construit packages/<id>/<version>/<version>.zip depuis plugins/ et l'ajoute à repository.json
    python3 scripts/store.py build --all-pending      construit tous les plugins dont la version des sources n'a pas d'archive
    python3 scripts/store.py pending                  liste ces plugins (rien n'est écrit)
    python3 scripts/store.py release-prep [--tag vAAAA.MM.JJ]
                                                      prépare un instantané : build --all-pending, verify, section du CHANGELOG
    python3 scripts/store.py changelog <tag>          affiche la section du CHANGELOG d'un instantané (notes de release)
    python3 scripts/store.py show <id> [version]     affiche le manifeste interne (plugin.json ou tool.json) d'une archive publiée, indenté
    python3 scripts/store.py sync-index              recopie nom, résumé, catégorie et outils requis de plugins/packages.json vers repository.json

Dépendance : fastjsonschema (pip install fastjsonschema), pour valider les manifestes contre schemas/.
"""
from __future__ import annotations

import argparse
import datetime
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
CHANGELOG_PATH = os.path.join(ROOT, "CHANGELOG.md")
DOWNLOAD_BASE = "https://raw.githubusercontent.com/remi-deher/encoding-store"
FIXED_DATE = (1980, 1, 1, 0, 0, 0)
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
MAX_UNCOMPRESSED = 512 * 1024 * 1024
S_IFMT, S_IFLNK = 0o170000, 0o120000
RELEASE_TAG = re.compile(r"^v([0-9]{4})\.([0-9]{2})\.([0-9]{2})$")
# Même motif pour git describe --match (glob) : un tag parasite plus proche (v1.0, v2-test) ne doit pas masquer l'instantané précédent.
RELEASE_TAG_GLOB = "v[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]"
UNRELEASED = "## Non publié"


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
        import fastjsonschema  # type: ignore[import-untyped,unused-ignore]
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
    write_index(index)
    print("repository.json synchronisé.")
    return 0


def write_index(index: Json) -> None:
    with open(INDEX_PATH, "w", encoding="utf-8") as handle:
        handle.write(dump_json(index))


def pending_plugins(index: Json, spec: Json) -> list[tuple[str, str]]:
    """(id, version) des plugins dont la version déclarée par les sources n'a pas encore d'archive dans l'index."""
    published = {plugin["id"]: {entry["version"] for entry in plugin["versions"]} for plugin in index["plugins"]}
    pending = []
    for package_id in sorted(key for key in spec if not key.startswith("_")):
        manifest, _ = expected_entries(spec, package_id)
        if manifest["version"] not in published.get(package_id, set()):
            pending.append((package_id, manifest["version"]))
    return pending


def build_plugin(index: Json, spec: Json, package_id: str, ref: str) -> tuple[str, str]:
    """Construit l'archive de la version des sources et l'ajoute à l'index (en mémoire). Retourne (version, sha256)."""
    if package_id not in spec or package_id.startswith("_"):
        sys.exit("Plugin inconnu dans plugins/packages.json : %s" % package_id)
    manifest, entries = expected_entries(spec, package_id)
    version = manifest["version"]
    if manifest["id"] != package_id or not SEMVER.match(version):
        sys.exit("Manifeste incohérent : id %r, version %r." % (manifest["id"], version))
    target = archive_path(package_id, version)
    if os.path.exists(target):
        sys.exit("%s %s est déjà publié et immuable : incrémentez la version du manifeste." % (package_id, version))
    data = build_zip(entries)
    os.makedirs(os.path.dirname(target))
    with open(target, "wb") as handle:
        handle.write(data)
    sync_metadata(index, spec)
    plugin = next(item for item in index["plugins"] if item["id"] == package_id)
    plugin["versions"].append({"version": version, "downloadUrl": download_url(ref, package_id, version), "sha256": sha256(data)})
    plugin["versions"].sort(key=lambda item: semver_key(item["version"]))
    plugin["latestVersion"] = plugin["versions"][-1]["version"]
    return version, sha256(data)


def build_pending(ref: str) -> list[tuple[str, str]]:
    """Construit chaque plugin en attente ; idempotent (rien à faire = rien d'écrit). Retourne les (id, version) construits."""
    index, spec = read_json(INDEX_PATH), read_json(SPEC_PATH)
    built = []
    for package_id, _version in pending_plugins(index, spec):
        version, digest = build_plugin(index, spec, package_id, ref)
        print("%s %s construit (%s)." % (package_id, version, digest))
        built.append((package_id, version))
    if built:
        write_index(index)
    return built


def command_build(args: argparse.Namespace) -> int:
    if getattr(args, "all_pending", False):
        if args.id:
            sys.exit("build : donnez un identifiant ou --all-pending, pas les deux.")
        if not build_pending(args.ref):
            print("Aucun plugin en attente de publication.")
        return 0
    if not args.id:
        sys.exit("build : identifiant de plugin ou --all-pending requis.")
    index, spec = read_json(INDEX_PATH), read_json(SPEC_PATH)
    version, digest = build_plugin(index, spec, args.id, args.ref)
    write_index(index)
    print("%s %s construit (%s)." % (args.id, version, digest))
    return 0


def command_pending(_args: argparse.Namespace) -> int:
    pending = pending_plugins(read_json(INDEX_PATH), read_json(SPEC_PATH))
    for package_id, version in pending:
        print("%s %s" % (package_id, version))
    if pending:
        print("%d plugin(s) en attente : python3 scripts/store.py build --all-pending" % len(pending))
    else:
        print("Aucun plugin en attente de publication.")
    return 0


# --- instantané du catalogue (release) --------------------------------------------------------------------------

def default_tag() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("v%Y.%m.%d")


def check_tag(tag: str) -> None:
    match = RELEASE_TAG.match(tag)
    try:
        if match is None:
            raise ValueError
        datetime.date(*(int(part) for part in match.groups()))
    except ValueError:
        sys.exit("Tag invalide %r : format attendu vAAAA.MM.JJ (date réelle)." % tag)


def previous_tag(rev: str = "HEAD") -> str | None:
    """Dernier tag d'instantané atteignable depuis rev (rev exclu s'il est lui-même tagué : passer rev^)."""
    result = git("describe", "--tags", "--abbrev=0", "--match", RELEASE_TAG_GLOB, rev)
    tag = result.stdout.strip()
    return tag if result.returncode == 0 and RELEASE_TAG.match(tag) else None


def published_versions(index: Json) -> set[tuple[str, str, str]]:
    return {(kind, item["id"], entry["version"]) for kind in ("plugins", "tools") for item in index.get(kind, []) for entry in item["versions"]}


def snapshot_lines(since: str | None) -> tuple[list[str], int]:
    """Lignes listant les versions que l'instantané ajoute depuis le tag précédent (toutes les dernières versions s'il n'y en a
    pas), et leur nombre."""
    index = read_json(INDEX_PATH)
    labels = {"plugins": "plugin", "tools": "outil"}
    if since is None:
        lines = ["Premier instantané signé du catalogue : dernière version de chaque paquet.", ""]
        lines += ["- %s %s %s" % (labels[kind], item["id"], item["latestVersion"]) for kind in ("plugins", "tools") for item in index[kind]]
        return lines, len(lines) - 2
    old = git("show", since + ":repository.json")
    if old.returncode != 0:
        sys.exit("repository.json illisible au tag %s : %s" % (since, old.stderr.strip()))
    added = sorted(published_versions(index) - published_versions(json.loads(old.stdout)), key=lambda key: (key[0], key[1], semver_key(key[2])))
    if not added:
        return ["Aucune nouvelle version de paquet depuis %s." % since], 0
    lines = ["- %s %s %s" % (labels[kind], package_id, version) for kind, package_id, version in added]
    return ["Nouvelles versions depuis %s :" % since, ""] + lines, len(lines)


def changelog_section(text: str, header: str) -> tuple[int, int] | None:
    """Bornes [début, fin) des lignes de la section « header » (titre compris), ou None."""
    lines = text.split("\n")
    if header not in lines:
        return None
    start = lines.index(header)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def section_body(text: str, header: str) -> str | None:
    bounds = changelog_section(text, header)
    if bounds is None:
        return None
    return "\n".join(text.split("\n")[bounds[0] + 1:bounds[1]]).strip("\n")


def release_changelog(text: str, tag: str, snapshot: list[str]) -> str:
    """Renomme « Non publié » en « ## <tag> », y ajoute la liste des versions publiées et rouvre une section « Non publié » vide."""
    bounds = changelog_section(text, UNRELEASED)
    if bounds is None:
        sys.exit("CHANGELOG.md : section « %s » introuvable." % UNRELEASED)
    lines = text.split("\n")
    body = section_body(text, UNRELEASED) or ""
    section = [UNRELEASED, "", "## " + tag, "", "### Paquets de l'instantané", "", *snapshot, ""]
    if body:
        section += [body, ""]
    return "\n".join(lines[:bounds[0]] + section + lines[bounds[1]:])


def command_release_prep(args: argparse.Namespace) -> int:
    tag = args.tag or default_tag()
    check_tag(tag)
    if git("rev-parse", "-q", "--verify", "refs/tags/" + tag).returncode == 0:
        sys.exit("Le tag %s existe déjà : choisissez une autre date (--tag)." % tag)
    with open(CHANGELOG_PATH, encoding="utf-8") as handle:
        changelog = handle.read()
    if changelog_section(changelog, "## " + tag) is not None:
        sys.exit("CHANGELOG.md contient déjà la section %s : instantané déjà préparé." % tag)
    since = previous_tag()
    built = build_pending(args.ref)
    # Contrôle complet avant d'écrire le journal : un échec laisse le CHANGELOG intact et la commande peut être relancée.
    print("Contrôle du catalogue%s :" % (" (immuabilité depuis %s)" % since if since else ""))
    if command_verify(argparse.Namespace(base=since)) != 0:
        print("Préparation interrompue : corrigez les erreurs puis relancez release-prep (les archives déjà construites sont conservées).")
        return 1
    snapshot, count = snapshot_lines(since)
    if count == 0 and not section_body(changelog, UNRELEASED):
        sys.exit("Rien à publier depuis %s : aucune nouvelle version et section « Non publié » vide." % since)
    with open(CHANGELOG_PATH, "w", encoding="utf-8") as handle:
        handle.write(release_changelog(changelog, tag, snapshot))
    print("CHANGELOG.md : section %s créée." % tag)
    print("Instantané %s prêt%s. Étapes suivantes (humaines) : commit, PR, fusion dans main, puis workflow « Release » avec tag=%s."
          % (tag, " (%d archive(s) construite(s))" % len(built) if built else "", tag))
    return 0


def command_changelog(args: argparse.Namespace) -> int:
    with open(CHANGELOG_PATH, encoding="utf-8") as handle:
        body = section_body(handle.read(), "## " + args.tag)
    if not body:
        print("CHANGELOG.md : aucune section « ## %s » (lancez release-prep avant de publier)." % args.tag, file=sys.stderr)
        return 1
    print(body)
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
    names = archive.namelist()
    for name in names:
        if name.startswith("/") or ".." in name.split("/") or "\\" in name:
            problems.append("%s : nom d'entrée dangereux %r." % (label, name))
    # Un nom dupliqué laisse deux extracteurs voir deux contenus différents : verify ne lit que le dernier.
    for name in sorted({name for name in names if names.count(name) > 1}):
        problems.append("%s : entrée dupliquée %r." % (label, name))
    for info in archive.infolist():
        if (info.external_attr >> 16) & S_IFMT == S_IFLNK:
            problems.append("%s : lien symbolique interdit (%s)." % (label, info.filename))
    if sum(info.file_size for info in archive.infolist()) > MAX_UNCOMPRESSED:
        problems.append("%s : taille décompressée supérieure à %d Mio." % (label, MAX_UNCOMPRESSED // (1024 * 1024)))


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


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


def pinned_url_change(old: str, new: str) -> bool:
    """Seule évolution admise d'un lien publié : de la branche main vers un SHA de commit, même chemin (ticket #5)."""
    prefix = DOWNLOAD_BASE + "/main/"
    match = re.fullmatch(re.escape(DOWNLOAD_BASE) + r"/[0-9a-f]{40}/(.+)", new)
    return old.startswith(prefix) and match is not None and match.group(1) == old[len(prefix):]


def check_published_entries(old_index: Json, new_index: Json, base: str, problems: Problems) -> None:
    """Chaque version présente dans l'index de la base y reste, avec le même hash et le même lien (hors épinglage)."""
    for kind in ("plugins", "tools"):
        current = {(item["id"], entry["version"]): entry for item in new_index.get(kind, []) for entry in item["versions"]}
        for item in old_index.get(kind, []):
            for old in item["versions"]:
                label = "%s %s %s" % (kind[:-1], item["id"], old["version"])
                new = current.get((item["id"], old["version"]))
                if new is None:
                    problems.append("%s : version publiée retirée de repository.json (présente sur %s)." % (label, base))
                    continue
                for key in sorted(set(old) | set(new)):
                    if old.get(key) == new.get(key):
                        continue
                    if key == "downloadUrl" and pinned_url_change(old[key], new[key]):
                        continue
                    problems.append("%s : %s modifié dans repository.json, une version publiée est immuable." % (label, key))


def check_immutability(base: str, problems: Problems, require_ancestor: bool = False) -> None:
    # Sur push, la base est le commit précédent de la branche : s'il n'est plus ancêtre de HEAD, l'historique a été réécrit
    # (push forcé) et le point de divergence recule avant les archives retirées ou republiées. On refuse au lieu de comparer.
    if require_ancestor and git("merge-base", "--is-ancestor", base, "HEAD").returncode != 0:
        problems.append("historique réécrit depuis %s : ce commit n'est plus un ancêtre de HEAD (push forcé ?), "
                        "l'immuabilité des paquets publiés ne peut pas être garantie." % base)
        return
    # Point de divergence et non pointe de la base : une branche en retard ne doit pas voir les ajouts de la base comme des suppressions.
    merge_base = git("merge-base", base, "HEAD")
    if merge_base.returncode != 0:
        problems.append("git merge-base %s HEAD a échoué : %s" % (base, merge_base.stderr.strip()))
        return
    fork = merge_base.stdout.strip()
    # Comparaison avec l'arbre de travail : une modification non validée est vue aussi.
    result = git("diff", "--name-status", "--no-renames", fork, "--", "packages")
    if result.returncode != 0:
        problems.append("git diff %s a échoué : %s" % (fork, result.stderr.strip()))
        return
    for line in result.stdout.splitlines():
        status, _, path = line.partition("\t")
        if status != "A":
            problems.append("%s : un paquet publié est immuable (statut %s)." % (path, status))
    old_index = git("show", fork + ":repository.json")
    if old_index.returncode != 0:
        problems.append("repository.json illisible à %s : %s" % (fork, old_index.stderr.strip()))
        return
    check_published_entries(json.loads(old_index.stdout), read_json(INDEX_PATH), base, problems)


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
    on_disk = {os.path.relpath(path, ROOT) for path in glob.glob(os.path.join(ROOT, "packages", "**", "*"), recursive=True)
               if not os.path.isdir(path)}
    for orphan in sorted(on_disk - published):
        problems.append("%s : archive absente de repository.json." % orphan)
    check_licenses(problems)
    if args.base:
        check_immutability(args.base, problems, getattr(args, "require_ancestor", False))
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
    check_undeclared_sources(spec, problems)


def check_undeclared_sources(spec: Json, problems: Problems) -> None:
    """Tout fichier du répertoire d'un plugin (celui de son manifeste) doit entrer dans un paquet : sinon il manquerait à l'archive."""
    plugins_dir = os.path.join(ROOT, "plugins")
    entries = [entry for key, entry in spec.items() if not key.startswith("_")]
    declared = {entry["manifest"] for entry in entries} | {source for entry in entries for source in entry.get("files", {}).values()}
    for home in sorted({entry["manifest"].split("/")[0] for entry in entries}):
        for path in sorted(glob.glob(os.path.join(plugins_dir, home, "**", "*"), recursive=True)):
            relative = os.path.relpath(path, plugins_dir).replace(os.sep, "/")
            if os.path.isdir(path) or "__pycache__" in relative.split("/") or relative in declared:
                continue
            problems.append("plugins/%s : fichier source déclaré par aucun paquet dans plugins/packages.json (il manquerait à l'archive publiée)." % relative)


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
    if version != plugin["latestVersion"]:
        problems.append("plugin %s : les sources sont en %s mais la dernière version publiée est %s (sources perdues ou retour arrière)."
                        % (package_id, version, plugin["latestVersion"]))
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
    verify.add_argument("--require-ancestor", action="store_true",
                        help="avec --base : échoue si la base n'est pas un ancêtre de HEAD (push forcé, historique réécrit)")
    verify.set_defaults(handler=command_verify)
    build = commands.add_parser("build")
    build.add_argument("id", nargs="?")
    build.add_argument("--all-pending", action="store_true", help="construit tous les plugins dont la version des sources n'a pas d'archive")
    build.add_argument("--ref", default="main", help="branche, tag ou commit servi dans downloadUrl (défaut : main)")
    build.set_defaults(handler=command_build)
    commands.add_parser("pending").set_defaults(handler=command_pending)
    prep = commands.add_parser("release-prep")
    prep.add_argument("--tag", help="vAAAA.MM.JJ (défaut : date du jour, UTC)")
    prep.add_argument("--ref", default="main", help="ref servie dans downloadUrl des archives construites (défaut : main)")
    prep.set_defaults(handler=command_release_prep)
    changelog = commands.add_parser("changelog")
    changelog.add_argument("tag")
    changelog.set_defaults(handler=command_changelog)
    show = commands.add_parser("show")
    show.add_argument("id")
    show.add_argument("version", nargs="?", help="défaut : la dernière version")
    show.set_defaults(handler=command_show)
    commands.add_parser("sync-index").set_defaults(handler=command_sync_index)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
