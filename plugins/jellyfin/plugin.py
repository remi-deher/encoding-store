#!/usr/bin/env python3
"""Plugin Jellyfin : actualiser une bibliothèque ou un élément, vérifier qu'un élément est connu.

Le fichier traité est retrouvé par la correspondance de chemins du plugin (bibliothèque Encoding -> chemin vu
par Jellyfin). Clé API envoyée en en-tête X-Emby-Token, jamais dans l'URL.
"""
import os
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
for candidate in (HERE, os.path.join(HERE, "..", "sdk")):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

from encoding_plugin_sdk import InfrastructureError, JsonApi, Outcome, Plugin, load_manifest, remote_path  # noqa: E402

REFRESH_QUERY = "Recursive=false&MetadataRefreshMode=FullRefresh&ImageRefreshMode=FullRefresh&ReplaceAllMetadata=false"


def _norm(path):
    return path.replace("\\", "/")


class JellyfinClient:
    def __init__(self, base_url, api_key, opener=None):
        self.api = JsonApi("Jellyfin", base_url, {"X-Emby-Token": api_key}, opener)

    def items(self, query):
        _, payload = self.api.request("GET", "Items?" + urllib.parse.urlencode(query))
        return [i for i in (payload or {}).get("Items") or [] if isinstance(i, dict) and isinstance(i.get("Id"), str)]

    def item_id_for_path(self, path):
        """Identifiant de l'élément dont le fichier est exactement ce chemin (le filtre serveur est revérifié)."""
        for item in self.items({"Recursive": "true", "Path": path, "Fields": "Path", "IncludeItemTypes": "Movie,Episode,Video,Audio"}):
            if isinstance(item.get("Path"), str) and _norm(item["Path"]) == _norm(path):
                return item["Id"]
        return None

    def item_exists(self, item_id):
        return any(i["Id"].replace("-", "").lower() == item_id.replace("-", "").lower() for i in self.items({"ids": item_id}))

    def playing_files(self):
        """Fichiers des sessions qui lisent un contenu (lectures en pause comprises) ; None = fichier inconnu."""
        _, payload = self.api.request("GET", "Sessions")
        result = []
        for session in payload or []:
            item = session.get("NowPlayingItem") if isinstance(session, dict) else None
            if isinstance(item, dict):
                result.append(item.get("Path") if isinstance(item.get("Path"), str) else None)
        return result

    def refresh_item(self, item_id):
        self.api.request("POST", "Items/%s/Refresh?%s" % (urllib.parse.quote(item_id, safe=""), REFRESH_QUERY))

    def media_updated(self, path):
        self.api.request("POST", "Library/Media/Updated", {"Updates": [{"Path": path, "UpdateType": "Modified"}]})

    def refresh_library(self):
        self.api.request("POST", "Library/Refresh")


def build(opener=None):
    plugin = Plugin(load_manifest(__file__, "jellyfin"))

    def client(ctx):
        base_url, key = ctx.plugin_text("baseUrl"), ctx.plugin_text("apiKey")
        if base_url is None:
            raise InfrastructureError("L'adresse de Jellyfin n'est pas configurée.")
        if key is None:
            raise InfrastructureError("La clé API de Jellyfin n'est pas configurée.")
        return JellyfinClient(base_url, key, opener)

    @plugin.component("jellyfin-refresh-item")
    def refresh_item(ctx):
        api = client(ctx)
        item_id = ctx.text("itemId")
        if item_id is None:
            path, error = remote_path(ctx)
            if error:
                return Outcome.output("error", error)
            item_id = api.item_id_for_path(path)
            if item_id is None:
                return Outcome.output("error", "Jellyfin ne connaît pas ce fichier.")
        api.refresh_item(item_id)
        return Outcome.output("success", "L'actualisation de l'élément a été demandée à Jellyfin.")

    @plugin.component("jellyfin-refresh-library")
    def refresh_library(ctx):
        api = client(ctx)
        if ctx.plugin_text("pathMappings") is None:
            api.refresh_library()
            return Outcome.output("success", "Le scan des bibliothèques a été demandé à Jellyfin.")
        path, error = remote_path(ctx)
        if error:
            return Outcome.output("error", error)
        api.media_updated(path)
        return Outcome.output("success", "Jellyfin a été informé de la modification du fichier.")

    @plugin.component("jellyfin-check-item-indexed")
    def check(ctx):
        api = client(ctx)
        item_id = ctx.text("itemId")
        if item_id is not None:
            found = api.item_exists(item_id)
        else:
            path, error = remote_path(ctx)
            if error:
                return Outcome.output("error", error)
            found = api.item_id_for_path(path) is not None
        return Outcome.output("found", "Élément connu de Jellyfin.") if found else Outcome.output("notFound", "Élément absent de Jellyfin.")

    @plugin.component("jellyfin-check-playback")
    def playback(ctx):
        api = client(ctx)
        files = api.playing_files()
        if ctx.text("scope") == "global":
            return Outcome.output("playing", "Jellyfin est en cours de lecture.") if files else Outcome.output("idle", "Aucune lecture en cours dans Jellyfin.")
        path, error = remote_path(ctx)
        if error:
            return Outcome.output("error", error)
        if any(f is not None and _norm(f) == _norm(path) for f in files):
            return Outcome.output("playing", "Le fichier est en cours de lecture dans Jellyfin.")
        return Outcome.output("idle", "Le fichier n'est pas en cours de lecture dans Jellyfin.")

    return plugin


if __name__ == "__main__":
    sys.exit(build().run())
