# Changelog

Les paquets publiés sont immuables : chaque modification de sources passe par une nouvelle version (voir [CONTRIBUTING.md](CONTRIBUTING.md)).

## Non publié

### Outillage du dépôt
- `store.py show <id> [version]` affiche le manifeste interne indenté d'une archive publiée.
- `scripts/store.py` : construction reproductible des paquets de plugins, contrôle complet du catalogue (`verify`), synchronisation des métadonnées.
- `plugins/packages.json` : source de vérité du contenu des paquets et des métadonnées de l'index.
- CI GitHub Actions (lint, intégrité, immuabilité, tests) et workflow de release avec attestation de provenance.
- Schéma `schemas/repository.schema.json`.
- Typage complet (mypy, `disallow_untyped_defs`) du SDK Python, des plugins et de `scripts/store.py`, vérifié en CI.
- SDK JavaScript typé (JSDoc, `tsc --strict` via `npm run typecheck`, types `Manifest`, `Invocation`, `Handler`…), vérifié en CI.
- SDK : `norm_path`, `same_path`, `longest_prefix_match` ; suppression de la copie dupliquée `plugins/encoding_plugin_sdk.py` et du chemin de repli vers le dépôt du moteur.

### Plugins
- plex 1.1.1, jellyfin 1.0.1, sonarr 1.0.1, radarr 1.0.1 : même comportement, utilisent les nouveaux utilitaires de chemin du SDK.
- Plugins natifs ai 1.0.3, audio 1.1.3, base 1.10.2, conditions 1.2.2, images 1.0.3, mkv 1.1.3, nodes 1.8.2, probe 1.1.3, subtitles 1.3.3, video 1.3.3 : contenu identique, `plugin.json` désormais indenté donc lisible.
- Résumés du catalogue plus explicites pour tous les plugins.
