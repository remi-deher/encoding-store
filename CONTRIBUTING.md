# Contribuer

## Prérequis

```sh
pip install --require-hashes -r requirements/dev.txt   # fastjsonschema, ruff, mypy : versions et hashes épinglés, comme en CI
```

Les dépendances Python de la CI sont épinglées par hash dans `requirements/*.txt`, générés depuis `requirements/*.in` (commande en tête de chaque `.in`). Les actions GitHub sont épinglées par SHA de commit. Dependabot propose les montées de version des deux.

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
- Un paquet publié (`packages/**`) ne change jamais : `verify` échoue si les sources d'un plugin diffèrent de sa dernière archive publiée sans nouvelle version, si une version incrémentée n'a pas d'archive, si les sources reviennent à une version antérieure, ou si un fichier du répertoire d'un plugin n'est déclaré par aucun paquet de `plugins/packages.json`. La CI (`verify --base`, sur PR et sur push `main`/`dev`) refuse toute modification ou suppression d'archive publiée et de son entrée dans `repository.json`, comparée au point de divergence avec la base. Sur push, le commit précédent de la branche doit rester un ancêtre du nouveau (`verify --require-ancestor`) : un push forcé qui réécrit l'historique de `main` ou `dev` fait échouer la CI.
- Les paquets d'outils (`packages/ffmpeg`, etc.) viennent des `.deb` Ubuntu listés dans `tools/ubuntu-noble-lock.json` ; ils ne sont pas construits par `store.py` mais sont contrôlés (hash, exécutables, licences).

## Publier une nouvelle version d'un plugin

1. Modifier les sources sous `plugins/`.
2. Incrémenter `version` au sommet du manifeste de chaque plugin concerné (`plugins/<id>/manifests/<id>.json`). Une source partagée (`plugins/arr/plugin.py`, `plugins/sdk/`) impose d'incrémenter chacun des plugins qui l'embarquent : `verify` les liste tous.
3. Décrire le changement sous « Non publié » dans `CHANGELOG.md`.
4. Construire les archives : soit `python3 scripts/store.py build --all-pending` dans la PR, soit laisser le workflow **Prepare release** le faire (étape suivante). `python3 scripts/store.py pending` liste ce qui attend une archive ; la CI l'affiche aussi dans le résumé du job.

`build <id>` construit un seul plugin. `build --ref <ref>` fait pointer `downloadUrl` vers un tag ou un commit plutôt que `main`.

Changer le résumé, la catégorie ou le nom : éditer `plugins/packages.json` puis `python3 scripts/store.py sync-index`.

`python3 scripts/store.py show <id> [version]` affiche le `plugin.json` / `tool.json` d'une archive publiée, indenté.

## Publier un instantané du catalogue (release `vAAAA.MM.JJ`)

La préparation est automatisée ; la publication reste une décision humaine en deux clics. Aucun outil ni agent ne pousse de tag ni ne crée de release de lui-même.

### 1. Préparer : workflow « Prepare release » (ou une commande locale)

GitHub : **Actions > Prepare release > Run workflow**, sur `dev` (ou `main`), champ `tag` vide pour la date du jour (UTC) ou `vAAAA.MM.JJ`.

Le workflow, avec l'environnement épinglé de la CI :

1. construit toutes les archives en attente (`build --all-pending`) : seules de **nouvelles** versions sont ajoutées, aucune archive existante n'est touchée ;
2. rejoue `verify --base <tag précédent>` (catalogue et immuabilité depuis le dernier instantané) ; en cas d'échec, rien n'est poussé ;
3. renomme « Non publié » de `CHANGELOG.md` en `## vAAAA.MM.JJ`, y ajoute la liste des versions de paquets nouvelles depuis le tag précédent, et rouvre une section « Non publié » vide ;
4. rejoue les tests du Store, pousse la branche `release/vAAAA.MM.JJ`, ouvre une PR vers la branche de lancement et y lance la CI.

Équivalent local, une commande (puis commit, push et PR à la main) :

```sh
python3 scripts/store.py release-prep [--tag vAAAA.MM.JJ]
```

`release-prep` refuse un tag mal formé ou existant, une section déjà présente dans le CHANGELOG, et un instantané vide (aucune nouvelle version et « Non publié » vide). Après un échec de `verify`, le CHANGELOG est intact et la commande peut être relancée.

Si la PR n'est pas créée (réglage *Settings > Actions > General > Allow GitHub Actions to create and approve pull requests* désactivé), le résumé du run donne le lien pour l'ouvrir à la main.

### 2. Relire et fusionner jusqu'à `main`

La PR `release/vAAAA.MM.JJ` contient les archives, `repository.json` et le CHANGELOG. Une fois la CI verte, la fusionner, puis porter `dev` dans `main` par la PR habituelle.

### 3. Publier : workflow « Release »

GitHub : **Actions > Release > Run workflow**, **sur `main`**, tag `vAAAA.MM.JJ`. Pousser soi-même le tag sur un commit de `main` déclenche le même workflow.

Le workflow refuse un tag mal formé ou déjà existant, un commit absent de `main`, et un tag sans section dans `CHANGELOG.md`. Il rejoue `verify --base <tag précédent>`, calcule `SHA256SUMS`, génère une attestation de provenance Sigstore pour `repository.json`, `SHA256SUMS` et chaque archive, puis crée le tag et la release GitHub (notes = section du CHANGELOG, pièces jointes `repository.json` et `SHA256SUMS`). Le job de contrôle n'a aucun droit d'écriture ; le job qui signe et publie n'installe aucun paquet.

Vérifier un fichier : `gh attestation verify <fichier> --repo remi-deher/encoding-store`.

## Distribution des binaires

Les archives d'outils sont volumineuses (binaires) et alourdissent l'historique git. Pour les prochaines versions, préférer des pièces jointes de release (ou Git LFS) référencées par `downloadUrl` ; `verify` accepte toute URL `https://raw.githubusercontent.com/remi-deher/encoding-store/<ref>/...` et doit être adapté si l'hébergement change.
