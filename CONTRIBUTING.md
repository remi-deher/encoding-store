# Contribuer

## Prérequis

```sh
pip install fastjsonschema ruff mypy
```

## Vérifications locales (les mêmes que la CI)

```sh
ruff check .
mypy                                          # SDK, plugins Python et scripts/store.py
npm ci && npm run typecheck                   # SDK JavaScript (JSDoc + tsc strict) et plugin d'exemple
python3 scripts/store.py verify              # hashes, schémas, sources = dernière archive publiée, fichiers non déclarés, orphelins
python3 -m unittest discover -s scripts/tests -v
python3 -m unittest discover -s plugins/tests -v
```

## Principes

- `plugins/packages.json` décrit chaque paquet de plugin : manifeste, runtime, fichiers embarqués, nom, résumé, catégorie. C'est la source de vérité de l'index pour ces champs.
- `plugins/sdk/` contient l'unique SDK. Il est copié dans les paquets Python à la construction : ne jamais éditer une copie.
- Un paquet publié (`packages/**`) ne change jamais : `verify` échoue si les sources d'un plugin diffèrent de sa dernière archive publiée sans nouvelle version, si une version incrémentée n'a pas d'archive, si les sources reviennent à une version antérieure, ou si un fichier du répertoire d'un plugin n'est déclaré par aucun paquet de `plugins/packages.json`. La CI (`verify --base`, sur PR et sur push `main`/`dev`) refuse toute modification ou suppression d'archive publiée et de son entrée dans `repository.json`, comparée au point de divergence avec la base.
- Les paquets d'outils (`packages/ffmpeg`, etc.) viennent des `.deb` Ubuntu listés dans `tools/ubuntu-noble-lock.json` ; ils ne sont pas construits par `store.py` mais sont contrôlés (hash, exécutables, licences).

## Publier une nouvelle version d'un plugin

1. Modifier les sources sous `plugins/`.
2. Incrémenter `version` au sommet du manifeste (`plugins/<id>/manifests/<id>.json`).
3. `python3 scripts/store.py build <id>` : crée `packages/<id>/<version>/<version>.zip` (déterministe) et met à jour `repository.json`.
4. `python3 scripts/store.py verify`, puis ajouter une entrée à `CHANGELOG.md`.
5. Changer le résumé, la catégorie ou le nom : éditer `plugins/packages.json` puis `python3 scripts/store.py sync-index`.

`python3 scripts/store.py show <id> [version]` affiche le `plugin.json` / `tool.json` d'une archive publiée, indenté.

`build --ref <tag>` fait pointer `downloadUrl` vers un tag ou un commit plutôt que `main`.

## Publier un instantané du catalogue

Créer un tag `vAAAA.MM.JJ` : le workflow `Release` rejoue `verify`, calcule `SHA256SUMS`, génère une attestation de provenance (Sigstore, `gh attestation verify <fichier> --repo remi-deher/encoding-store`) et crée la release GitHub.

## Distribution des binaires

Les archives d'outils sont volumineuses (binaires) et alourdissent l'historique git. Pour les prochaines versions, préférer des pièces jointes de release (ou Git LFS) référencées par `downloadUrl` ; `verify` accepte toute URL `https://raw.githubusercontent.com/remi-deher/encoding-store/<ref>/...` et doit être adapté si l'hébergement change.
