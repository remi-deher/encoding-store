# Changelog

Les paquets publiés sont immuables : chaque modification de sources passe par une nouvelle version (voir [CONTRIBUTING.md](CONTRIBUTING.md)).

## Non publié

### Outillage du dépôt
- `scripts/store.py` : construction reproductible des paquets de plugins, contrôle complet du catalogue (`verify`), synchronisation des métadonnées.
- `plugins/packages.json` : source de vérité du contenu des paquets et des métadonnées de l'index.
- CI GitHub Actions (lint, intégrité, immuabilité, tests) et workflow de release avec attestation de provenance.
- Schéma `schemas/repository.schema.json`.
- SDK : `norm_path`, `same_path`, `longest_prefix_match` ; suppression de la copie dupliquée `plugins/encoding_plugin_sdk.py` et du chemin de repli vers le dépôt du moteur.

### Plugins
- plex 1.1.1, jellyfin 1.0.1, sonarr 1.0.1, radarr 1.0.1 : même comportement, utilisent les nouveaux utilitaires de chemin du SDK.
- Résumés du catalogue plus explicites pour tous les plugins.
