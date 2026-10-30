#!/usr/bin/env python3
"""Plugins Sonarr et Radarr (un même programme, déclaré deux fois sur le worker).

Usage : plugin.py <sonarr|radarr>

Composants : relire les fichiers de la série/du film qui contient le fichier traité, lancer la recherche des
éléments manquants, vérifier que le fichier est connu du service. Le fichier traité est retrouvé par la
correspondance de chemins du plugin (bibliothèque Encoding -> chemin vu par le service). Le déclencheur d'import
(webhook) est côté serveur Encoding : voir docs/architecture/0029-integration-plugins-and-sdk.md.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for candidate in (HERE, os.path.join(HERE, "..", "sdk")):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

from encoding_plugin_sdk import InfrastructureError, JsonApi, Outcome, Plugin, load_manifest, remote_path  # noqa: E402,F401

FLAVORS = {
    "sonarr": {
        "name": "Sonarr", "api_versions": ("v5", "v3"), "collection": "series", "item_key": "seriesId",
        "refresh": "sonarr-refresh-series", "search": "sonarr-search-missing-episodes", "check": "sonarr-check-episode-indexed",
        "refresh_command": "RescanSeries", "search_command": "MissingEpisodeSearch", "id_list": False,
        "check_id_key": "episodeId", "check_id_path": "episode/%d",
    },
    "radarr": {
        "name": "Radarr", "api_versions": ("v3",), "collection": "movie", "item_key": "movieId",
        "refresh": "radarr-refresh-movie", "search": "radarr-search-movie", "check": "radarr-check-movie-indexed",
        "refresh_command": "RescanMovie", "search_command": "MoviesSearch", "id_list": True,
        "check_id_key": "movieId", "check_id_path": "movie/%d",
    },
}


# --- client HTTP -------------------------------------------------------------------------------------------------

class ArrClient:
    """Client minimal de l'API Sonarr/Radarr (API v5 puis v3 pour Sonarr)."""

    def __init__(self, flavor, base_url, api_key, opener=None):
        self.flavor = flavor
        self.api = JsonApi(flavor["name"], base_url, {"X-Api-Key": api_key}, opener)
        self.api_root = None

    def _root(self):
        if self.api_root is None:
            for version in self.flavor["api_versions"]:
                status, _ = self.api.request("GET", "api/%s/system/status" % version)
                if status != 404:
                    self.api_root = "api/%s/" % version
                    break
            else:
                raise InfrastructureError("L'API de %s est introuvable. Vérifiez l'adresse et la version du service." % self.flavor["name"])
        return self.api_root

    def get(self, path):
        return self.api.request("GET", self._root() + path)

    def command(self, name, item_id):
        key = self.flavor["item_key"]
        body = {"name": name, key + "s": [item_id]} if self.flavor["id_list"] else {"name": name, key: item_id}
        self.api.request("POST", self._root() + "command", body)

    def items(self):
        """(id, dossier, fichier connu pour un film ou None) de chaque série ou film."""
        _, payload = self.get(self.flavor["collection"])
        result = []
        for item in payload or []:
            if isinstance(item, dict) and isinstance(item.get("id"), int) and isinstance(item.get("path"), str):
                movie_file = item.get("movieFile")
                known = movie_file.get("path") if isinstance(movie_file, dict) and isinstance(movie_file.get("path"), str) else None
                result.append((item["id"], item["path"], known))
        return result

    def episode_file_paths(self, series_id):
        _, payload = self.get("episodefile?seriesId=%d" % series_id)
        return [f["path"] for f in payload or [] if isinstance(f, dict) and isinstance(f.get("path"), str)]


def _norm(path):
    return path.replace("\\", "/")


def best_match(items, remote_path):
    """L'élément dont le dossier est le plus long préfixe du fichier (frontière de dossier respectée)."""
    target = _norm(remote_path)
    matches = [item for item in items if target.startswith(_norm(item[1]).rstrip("/") + "/")]
    return max(matches, key=lambda item: len(item[1]), default=None)


# --- composants --------------------------------------------------------------------------------------------------

def build(flavor_id, opener=None):
    flavor = FLAVORS[flavor_id]
    plugin = Plugin(load_manifest(__file__, flavor_id))
    name = flavor["name"]

    def client(ctx):
        base_url = ctx.plugin_text("baseUrl")
        api_key = ctx.plugin_text("apiKey")
        if base_url is None:
            raise InfrastructureError("L'adresse de %s n'est pas configurée." % name)
        if api_key is None:
            raise InfrastructureError("La clé API de %s n'est pas configurée." % name)
        return ArrClient(flavor, base_url, api_key, opener)

    def resolve_item(ctx, api):
        explicit = ctx.number(flavor["item_key"])
        if explicit is not None:
            return int(explicit), None
        path, error = remote_path(ctx)
        if error:
            return None, error
        match = best_match(api.items(), path)
        if match is None:
            return None, "Aucun élément %s ne contient ce fichier." % name
        return match[0], None

    def command_component(command, done):
        def handler(ctx):
            api = client(ctx)
            item_id, error = resolve_item(ctx, api)
            if error:
                return Outcome.output("error", error)
            api.command(command, item_id)
            return Outcome.output("success", "%s %s (identifiant %d)." % (done, name, item_id))
        return handler

    plugin.component(flavor["refresh"])(command_component(flavor["refresh_command"], "La relecture a été demandée à"))
    plugin.component(flavor["search"])(command_component(flavor["search_command"], "La recherche a été demandée à"))

    @plugin.component(flavor["check"])
    def check(ctx):
        api = client(ctx)
        explicit = ctx.number(flavor["check_id_key"])
        if explicit is not None:
            status, _ = api.get(flavor["check_id_path"] % int(explicit))
            return Outcome.output("notFound", "Élément absent du service.") if status == 404 else Outcome.output("found", "Élément connu du service.")
        path, error = remote_path(ctx)
        if error:
            return Outcome.output("error", error)
        items = api.items()
        if flavor_id == "radarr":
            known = any(item[2] is not None and _norm(item[2]) == _norm(path) for item in items)
        else:
            series = best_match(items, path)
            known = series is not None and any(_norm(p) == _norm(path) for p in api.episode_file_paths(series[0]))
        return Outcome.output("found", "Fichier connu de %s." % name) if known else Outcome.output("notFound", "Fichier absent de %s." % name)

    return plugin


def main(argv):
    if len(argv) != 2 or argv[1] not in FLAVORS:
        sys.stderr.write("Argument attendu : sonarr ou radarr.\n")
        return 2
    return build(argv[1]).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
