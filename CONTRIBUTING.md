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

`build <id>` construit un seul plugin.

### Liens de téléchargement

Chaque nouvelle version est servie depuis un **tag d'instantané** et non depuis `main` : `https://raw.githubusercontent.com/remi-deher/encoding-store/vAAAA.MM.JJ/packages/<id>/<version>/<version>.zip`. Un déplacement ou une réécriture de `main` ne peut donc ni modifier ni casser un lien publié.

- `build` épingle par défaut au tag du jour (UTC) ; `release-prep` réaligne ensuite sur son propre tag tout lien qui vise un tag n'ayant jamais servi son archive : tag jamais créé, ou créé sans elle (version construite le jour D, instantané vD préparé sans elle, fusionnée ensuite). Un tel lien n'a jamais répondu, aucun moteur n'a pu s'en servir : c'est le seul changement de lien admis avec l'épinglage `main` vers un SHA de commit. Un lien vers un tag qui sert déjà l'archive est immuable, et un tag publié ne se déplace jamais (pas de `git tag -f`). Pour un `release-prep` local, récupérer d'abord les tags (`git fetch --tags`).
- `build --ref <ref>` choisit la ref : tag `vAAAA.MM.JJ`, SHA de commit (40 caractères) ou `main` (déconseillé, `verify --base` le signale). Toute autre ref, `..` compris, est refusée par `build` comme par `verify`.
- Un lien épinglé répond dès que la release qui porte son tag est publiée : fusionner jusqu'à `main`, puis lancer **Release** sans attendre, avec le tag exact de la PR d'instantané (le workflow refuse tout lien vers un autre tag absent).
- `verify` contrôle hors ligne, par git, que chaque lien épinglé sert exactement l'archive de l'index (tag ou commit existant). `verify --online` télécharge chaque lien et compare son SHA-256 à l'index ; les liens vers un tag pas encore créé sont signalés et sautés.
- Les 52 versions publiées avant ce changement gardent leur lien `main`, inchangé : les repointer serait une décision du mainteneur, à documenter (l'audit `docs/audit-fiabilite.md`, H4, décrit l'épinglage par SHA du commit d'introduction, que `verify --base` admet déjà).

Changer le résumé, la catégorie ou le nom : éditer `plugins/packages.json` puis `python3 scripts/store.py sync-index`.

`python3 scripts/store.py show <id> [version]` affiche le `plugin.json` / `tool.json` d'une archive publiée, indenté.

## Publier un instantané du catalogue (release `vAAAA.MM.JJ`)

La préparation est automatisée ; la publication reste une décision humaine en deux clics. Aucun outil ni agent ne pousse de tag ni ne crée de release de lui-même.

### 1. Préparer : workflow « Prepare release » (ou une commande locale)

GitHub : **Actions > Prepare release > Run workflow**, sur `dev` (ou `main`), champ `tag` vide pour la date du jour (UTC) ou `vAAAA.MM.JJ`.

Le workflow, avec l'environnement épinglé de la CI :

1. construit toutes les archives en attente (`build --all-pending`) : seules de **nouvelles** versions sont ajoutées, aucune archive existante n'est touchée ;
2. épingle au tag de l'instantané les liens des nouvelles versions, puis rejoue `verify --base <tag précédent> --release-tag <tag>` (catalogue, immuabilité depuis le dernier instantané, liens) ; en cas d'échec, rien n'est poussé ;
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

Le workflow refuse un tag mal formé ou déjà existant, un commit absent de `main`, et un tag sans section dans `CHANGELOG.md`. Il rejoue `verify --base <tag précédent> --release-tag <tag> --online` (les liens déjà servis répondent avec les octets de l'index), calcule `SHA256SUMS`, génère une attestation de provenance Sigstore pour `repository.json`, `SHA256SUMS` et chaque archive, puis crée le tag et la release GitHub (notes = section du CHANGELOG, pièces jointes `repository.json` et `SHA256SUMS`). Le job de contrôle n'a aucun droit d'écriture ; le job qui signe et publie n'installe aucun paquet. Un dernier job, en lecture seule, retélécharge ensuite tous les liens, dont ceux du nouveau tag.

Le workflow **Download links** (hebdomadaire, ou manuel sur la branche choisie) rejoue `verify --online`.

Vérifier un fichier : `gh attestation verify <fichier> --repo remi-deher/encoding-store`.

## Distribution des binaires

Les archives d'outils sont volumineuses (binaires) et alourdissent l'historique git. Pour les prochaines versions, préférer des pièces jointes de release (ou Git LFS) référencées par `downloadUrl` ; `verify` n'accepte que `https://raw.githubusercontent.com/remi-deher/encoding-store/<vAAAA.MM.JJ|SHA|main>/packages/...` et doit être adapté si l'hébergement change. Une archive suivie par Git LFS serait servie par `raw.githubusercontent.com` sous forme de pointeur : ne jamais migrer une version publiée.
