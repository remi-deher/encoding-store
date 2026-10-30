#!/usr/bin/env python3
"""Plugin d'exemple « example » : deux composants sans service externe, à copier pour écrire un plugin tiers.

  - example-tag-file     (action)    : écrit une variable de flow (par défaut le nom du fichier traité).
  - example-name-matches (condition) : aiguille selon que le chemin relatif du média contient un texte.

Le manifeste (manifest.json, à côté de ce fichier) est le même JSON que celui chargé par le serveur.
Voir plugins/README.md pour l'installation côté serveur et côté worker.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for candidate in (HERE, os.path.join(HERE, "..", "sdk")):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

from encoding_plugin_sdk import Outcome, Plugin  # noqa: E402

with open(os.path.join(HERE, "manifest.json"), encoding="utf-8") as handle:
    plugin = Plugin(json.load(handle))


@plugin.component("example-tag-file")
def tag_file(ctx):
    name = ctx.text("name") or "tag"
    value = ctx.text("value") or os.path.basename(ctx.media_path or "")
    return Outcome.output("success", "Variable %s enregistrée." % name, variables={name: value})


@plugin.component("example-name-matches")
def name_matches(ctx):
    text = (ctx.text("text") or "").lower()
    return Outcome.output("yes" if text in (ctx.media_path or "").lower() else "no")


if __name__ == "__main__":
    sys.exit(plugin.main())
