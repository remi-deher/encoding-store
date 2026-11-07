# Changelog

Les paquets publiés sont immuables : chaque modification de sources passe par une nouvelle version (voir [CONTRIBUTING.md](CONTRIBUTING.md)).

## Non publié

### Outillage du dépôt
- Publication automatisée jusqu'à la décision humaine : `store.py pending`, `build --all-pending`, `release-prep` (archives en attente, `verify` depuis le tag précédent (seuls les tags `vAAAA.MM.JJ` comptent : un tag parasite plus proche ne fait plus sauter le contrôle), section `## vAAAA.MM.JJ` du CHANGELOG) et `changelog <tag>` ; workflow manuel « Prepare release » qui ouvre la PR `release/vAAAA.MM.JJ`. Le workflow « Release » ne publie qu'un tag `vAAAA.MM.JJ` d'un commit de `main` ayant sa section de CHANGELOG, contrôle l'immuabilité depuis le tag précédent, atteste aussi `SHA256SUMS`, et sépare contrôle (lecture seule) et publication (aucun paquet installé).
- CI : actions épinglées par SHA, dépendances Python épinglées par hash (`requirements/`), Dependabot, plugins en attente d'archive affichés dans le résumé du job.
- `verify` : les sources de chaque plugin doivent correspondre à sa dernière version publiée (retour arrière détecté), tout fichier du répertoire d'un plugin doit être déclaré dans `plugins/packages.json`, archives ZIP sans doublon, lien symbolique ni taille excessive, orphelins cherchés à toute profondeur de `packages/`.
- `verify --base` : compare au point de divergence (plus de faux positifs sur une branche en retard) et protège aussi les entrées de `repository.json` des versions publiées (seul l'épinglage d'un lien `main` vers un SHA de commit est admis). Sur push, `--require-ancestor` fait échouer la CI si l'historique a été réécrit (push forcé).
- CI : déclenchée aussi sur push `dev`, immuabilité contrôlée sur push, contrôles d'intégrité et tests exécutés même si le lint ou le typage échoue.
- `store.py show <id> [version]` affiche le manifeste interne indenté d'une archive publiée.
- `scripts/store.py` : construction reproductible des paquets de plugins, contrôle complet du catalogue (`verify`), synchronisation des métadonnées.
- `plugins/packages.json` : source de vérité du contenu des paquets et des métadonnées de l'index.
- CI GitHub Actions (lint, intégrité, immuabilité, tests) et workflow de release avec attestation de provenance.
- Schéma `schemas/repository.schema.json`.
- Typage complet (mypy, `disallow_untyped_defs`) du SDK Python, des plugins et de `scripts/store.py`, vérifié en CI.
- SDK JavaScript typé (JSDoc, `tsc --strict` via `npm run typecheck`, types `Manifest`, `Invocation`, `Handler`…), vérifié en CI.
- SDK : `norm_path`, `same_path`, `longest_prefix_match` ; suppression de la copie dupliquée `plugins/encoding_plugin_sdk.py` et du chemin de repli vers le dépôt du moteur.

### Plugins
- plex 1.1.2, jellyfin 1.0.2, sonarr 1.0.2, radarr 1.0.2 : déclarent `"capabilities": ["network"]` dans leur manifeste (ADR 0042 d'encoding-central, PR #12). Sans cette capacité, un worker récent coupe le réseau du plugin. Un central plus ancien ignore le champ : aucun changement de comportement. Le schéma `plugin-package` décrit désormais `capabilities` (seule valeur : `network`).
- plex 1.1.1, jellyfin 1.0.1, sonarr 1.0.1, radarr 1.0.1 : même comportement, utilisent les nouveaux utilitaires de chemin du SDK.
- base 1.10.3, subtitles 1.5.0 : publication des archives de ces versions, dont les sources étaient déjà dans `main` (commit c2cd86f) sans archive ; contenu des sources inchangé.
- Plugins natifs ai 1.0.3, audio 1.1.3, base 1.10.2, conditions 1.2.2, images 1.0.3, mkv 1.1.3, nodes 1.8.2, probe 1.1.3, subtitles 1.3.3, video 1.3.3 : contenu identique, `plugin.json` désormais indenté donc lisible.
- Résumés du catalogue plus explicites pour tous les plugins.
