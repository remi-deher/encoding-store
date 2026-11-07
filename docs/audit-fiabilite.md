# Audit de fiabilité du Store

- **Date** : 2026-11-07
- **Commit audité** : `02ccef8` (`dev` = `main` au moment de l'audit)
- **Périmètre** : `scripts/store.py`, `.github/workflows/ci.yml` et `release.yml`, `schemas/`, `plugins/packages.json`, `repository.json`, `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `docs/plugin-contract.md`, configuration GitHub du dépôt (protections, Actions, historique des exécutions).
- **Méthode** : lecture du code et des workflows, interrogation de l'API GitHub (`gh api`, `gh run`), puis reproduction de chaque scénario dans un worktree jetable hors du dépôt, avec un environnement Python isolé (`fastjsonschema`, `ruff`, `mypy`). Aucun fichier du dépôt n'a été modifié en dehors de ce rapport, et aucune archive sous `packages/` n'a été touchée.
- **Contrainte respectée par tous les correctifs proposés** : une archive publiée et son SHA-256 ne changent jamais ; `repository.json` reste lisible par les moteurs actuels (même schéma, mêmes champs).

## Synthèse priorisée

| ID | Gravité | Axe | Constat | Ticket qui le traite |
|----|---------|-----|---------|----------------------|
| H1 | Haute | 1, 4 | Aucune barrière avant publication : pas de protection de branche, CI non requise, pas de CI sur push `dev` | #3, #4 |
| H2 | Haute | 1, 2 | Publication manuelle en 4 à 5 étapes humaines ; un oubli de `build` a produit le précédent base 1.10.3 / subtitles 1.5.0 | #4 |
| H3 | Haute | 1, 4 | La CI s'arrête au premier échec : mypy (non épinglé) a masqué `verify` sur le commit fautif | #3 |
| H4 | Haute | 3 | Les 52 liens de téléchargement suivent `main` : l'immuabilité « sur le fil » ne repose que sur le processus | #5 |
| M1 | Moyenne | 1 | Retour arrière des sources sous la dernière version publiée non détecté | #3 |
| M2 | Moyenne | 1 | Fichiers source non déclarés dans `plugins/packages.json` jamais contrôlés | #3 |
| M3 | Moyenne | 2, 4 | Release jamais exécutée ; workflow de release trop permissif | #4 |
| M4 | Moyenne | 1, 3, 4 | Contrôle d'immuabilité incomplet : entrées de `repository.json` non couvertes, diff à deux points (faux positifs) | #3, #5 |
| M5 | Moyenne | 3, 4 | La validation de `downloadUrl` accepte `..` et donc une URL qui sort du dépôt | #5 |
| M6 | Moyenne | 4 | Chaîne d'approvisionnement de la CI : actions non épinglées par SHA, `pip` sans version ni hash, pas de Dependabot | #4 |
| M7 | Moyenne | 4 | Provenance des paquets d'outils invérifiable : le contrôle du verrou Ubuntu est tautologique | (hors mission) |
| B1 | Basse | 4 | Contrôles ZIP incomplets : noms dupliqués, liens symboliques, taille, profondeur des orphelins | #3 |
| B2 | Basse | 4 | Tests manquants sur `--base`, la publication oubliée, le retour arrière et les URL | #3, #5 |
| B3 | Basse | 2 | Documentation désynchronisée (README, CHANGELOG, contrat) | #4 |
| I1 | Info | 4 | Reproductibilité octet pour octet dépendante de zlib ; `verify` compare le contenu, pas les octets | — |
| I2 | Info | 3 | Binaires dans l'historique git ; une migration LFS casserait tous les liens actuels | #5 |
| I3 | Info | 3 | Hypothèse non vérifiable ici : le moteur contrôle le SHA-256 avant installation | — |

Ordre de traitement recommandé : H1 et H3 d'abord (ils rendent les autres contrôles effectifs), puis H2 avec M1, M2 et M4 (détection et automatisation, tickets #3 et #4), puis H4 avec M5 (liens, ticket #5). M3, M6 et M7 peuvent suivre en parallèle.

Les scores CVSS ne sont donnés que pour les constats exploitables par un tiers (M5, M6) : les autres sont des défauts de processus, que le score CVSS décrit mal. Tous les scénarios d'attaque supposent un accès en écriture au dépôt ou la compromission d'un compte ou d'un jeton qui l'a, puisque le dépôt n'accepte de contributions que par ce biais.

---

## Axe 1 : sources modifiées sans archive publiée

### Le précédent, reconstitué

1. `c2cd86f` (2026-11-02 04:30, « feat(store): add configurable subtitle profiles ») modifie `plugins/base/manifests/base.json` (1.10.2 vers 1.10.3) et `plugins/subtitles/manifests/subtitles.json` (1.3.3 vers 1.5.0), **sans archive ni mise à jour de `repository.json`**. Il a été **poussé directement sur `main`** : son parent est `3ce6f89` et aucune PR ne le porte.
2. L'exécution CI sur ce push (`gh run` 41383464366, `main`, `push`) a **échoué en 15 s à l'étape mypy** :
   `scripts/store.py:63: error: Unused "type: ignore" comment [unused-ignore]`.
   Cause : `pip install fastjsonschema ruff mypy` sans version. Une nouvelle version de mypy (ou des stubs de fastjsonschema) a rendu le commentaire inutile. **L'étape `verify` n'a jamais tourné.**
3. Rejoué sur ce commit, `verify` détecte bien le défaut :
   ```
   ERREUR plugin base : la version 1.10.3 des sources n'est pas publiée (python3 scripts/store.py build base).
   ERREUR plugin subtitles : la version 1.5.0 des sources n'est pas publiée (python3 scripts/store.py build subtitles).
   2 erreur(s).
   ```
4. Correction un jour et demi plus tard dans `cf81166` (2026-11-03 22:31, PR #2), avec `aa944ff` qui rend mypy tolérant.

Pendant ce délai, l'index restait cohérent avec les archives : les moteurs voyaient base 1.10.2 et subtitles 1.3.3, donc rien n'a cassé chez eux. En revanche, `main` contenait des sources testées que personne ne pouvait installer, et la CI était rouge pour une raison qui n'avait rien à voir. **Conclusion : l'outil de détection existe et fonctionne ; c'est son exécution, sa visibilité et son caractère bloquant qui ont fait défaut.**

### Ce que `verify` détecte, et ce que la CI en voit

Chaque ligne a été reproduite dans un worktree jetable.

| Scénario | `verify` | `verify --base` | La CI le voit-elle ? |
|----------|----------|-----------------|----------------------|
| Version incrémentée sans `build` (le précédent) | ✅ « n'est pas publiée » | ✅ | PR : oui, si une étape antérieure n'échoue pas (H3). Push `main` : après coup, sans bloquer. Push `dev` : **non** (H1) |
| Source modifiée sans incrément (`plugin.py`, manifeste, `inline`, `runtime`) | ✅ « diffère des sources » | ✅ | Idem |
| SDK Python modifié sans incrément | ✅ 4 erreurs (plex, jellyfin, sonarr, radarr) | ✅ | Idem |
| Sources ramenées à une version antérieure (base.json revenu à 1.10.2) | ❌ **« Catalogue valide »** | ❌ | **Non** (M1) |
| Nouveau fichier importé mais non déclaré dans `packages.json` | ❌ (seule la modification de `plugin.py` est vue) | ❌ | **Non** (M2) |
| SDK JavaScript, plugins `example*` | ❌ (non embarqués, attendu) | ❌ | Non (sans conséquence aujourd'hui) |
| Suppression d'une version publiée (archive et entrée d'index) | ❌ « Catalogue valide » | ✅ statut `D` | PR seulement (H1, M4) |
| `downloadUrl` d'une version publiée repointée vers une autre ref, ou vers `../../autre/depot` | ❌ | ❌ | **Non** (M4, M5) |
| Branche en retard sur sa base (`verify --base` depuis `3ce6f89` contre `02ccef8`) | — | ⚠️ **6 faux positifs** « immuable (statut D) » | PR bloquée à tort (M4) |

---

## Constats détaillés

### H1. Aucune barrière avant publication (Haute)

**Preuve**
- `gh api repos/remi-deher/encoding-store/branches/main/protection` : `404 Branch not protected`. Même réponse pour `dev`. `rulesets` : `[]`.
- `ci.yml` se déclenche sur `push: branches: [main]` et `pull_request`. La branche par défaut est **`dev`** (`gh repo view`) : un push direct sur `dev` n'exécute **aucune** vérification.
- `verify --base` (immuabilité) ne s'exécute que `if: github.event_name == 'pull_request'`.
- Le précédent montre un push direct sur `main` avec une CI rouge, resté en l'état.

**Impact** : un push direct, une fusion malgré une CI rouge ou un force-push sur `main` modifie ce que servent les liens `raw.githubusercontent.com/.../main/...` (voir H4). Pour une version publiée, il suffit de changer à la fois l'archive et son `sha256` dans `repository.json` pour que le contrôle de hash côté moteur passe. Un plugin Python s'exécute dans le bac à sable Landlock, mais les exécutables d'outils (ffmpeg, mkvmerge...) tournent dans le moteur. Le README (section *Trust*) reconnaît que la confiance repose sur le contrôle du compte : aujourd'hui, aucun garde-fou ne vient doubler ce contrôle.

**Correctif proposé**
1. Déclencher la CI sur les deux branches longues et exécuter l'immuabilité sur push aussi :
   ```yaml
   on:
     push:
       branches: [main, dev]
     pull_request:
   # ...
         - name: Published packages are immutable
           run: |
             base="${{ github.event_name == 'pull_request' && format('origin/{0}', github.base_ref) || github.event.before }}"
             python3 scripts/store.py verify --base "$base"
   ```
   (Sur un premier push de branche, `github.event.before` vaut `0000…` : prévoir un repli sur `origin/main`.)
2. Créer un ruleset sur `main` et `dev` : PR obligatoire, statut `checks` requis, force-push et suppression interdits, tags `v*` protégés.
   ```sh
   gh api -X POST repos/remi-deher/encoding-store/rulesets --input - <<'EOF'
   {"name":"branches-publiees","target":"branch","enforcement":"active",
    "conditions":{"ref_name":{"include":["refs/heads/main","refs/heads/dev"],"exclude":[]}},
    "rules":[{"type":"deletion"},{"type":"non_fast_forward"},
             {"type":"pull_request","parameters":{"required_approving_review_count":0,"dismiss_stale_reviews_on_push":true,
               "require_code_owner_review":false,"require_last_push_approval":false,"required_review_thread_resolution":false}},
             {"type":"required_status_checks","parameters":{"strict_required_status_checks_policy":true,
               "required_status_checks":[{"context":"checks"}]}}]}
   EOF
   ```
   Le mainteneur étant seul, `required_approving_review_count: 0` garde le passage par PR et CI sans exiger de second relecteur. Une liste de contournement réduite au propriétaire reste possible, de façon explicite.

### H2. Publication manuelle en plusieurs étapes humaines (Haute)

**Preuve** : `CONTRIBUTING.md`, « Publier une nouvelle version d'un plugin ». Un humain doit enchaîner : (1) modifier les sources, (2) incrémenter `version` à la main, (3) lancer `store.py build <id>` en local et committer le ZIP produit sur son poste, (4) lancer `verify` et écrire `CHANGELOG.md`, (5) lancer `sync-index` si les métadonnées changent. Les sources sont partagées : `arr/plugin.py` alimente sonarr **et** radarr, `sdk/encoding_plugin_sdk.py` alimente 4 plugins. Une seule modification impose donc 2 à 4 incréments et 2 à 4 `build`. Le précédent est exactement l'oubli de l'étape 3.

**Impact** : sources en avance sur le catalogue, CI rouge sur `main`, publications groupées après coup (`cf81166`), et risque de publier un ZIP construit avec un environnement local différent (voir I1).

**Correctif proposé** (ticket #4)
- Ajouter à `store.py` une commande `pending` qui liste les plugins dont la version des sources n'a pas d'archive, et un `build --all-pending` idempotent :
  ```python
  def pending_plugins(index: Json, spec: Json) -> list[tuple[str, str]]:
      published = {p["id"]: {v["version"] for v in p["versions"]} for p in index["plugins"]}
      out = []
      for package_id in sorted(k for k in spec if not k.startswith("_")):
          manifest, _ = expected_entries(spec, package_id)
          if manifest["version"] not in published.get(package_id, set()):
              out.append((package_id, manifest["version"]))
      return out
  ```
- En CI de PR, afficher un message actionnable (« lancez `store.py build --all-pending` ») dans le résumé du job (`$GITHUB_STEP_SUMMARY`).
- Facultatif : un workflow sur PR, déclenché par une étiquette `publish`, qui exécute `build --all-pending` et pousse le commit sur la branche de la PR. Il tourne avec l'environnement épinglé de la CI (I1), et la fusion reste manuelle.
- Pour les sources partagées, faire échouer `verify` avec la liste complète des plugins à incrémenter dans un seul message, au lieu de N erreurs séparées.

### H3. L'arrêt au premier échec masque `verify`, et les outils ne sont pas épinglés (Haute)

**Preuve** : run 41383464366. Le job unique `checks` enchaîne lint, mypy, tsc, `verify`, immuabilité et tests. mypy échoue, les étapes suivantes sont sautées. `pip install fastjsonschema ruff mypy` n'épingle aucune version : le même commit peut passer un jour et échouer le lendemain.

**Impact** : le contrôle d'intégrité, le plus important pour le Store, dépend de la réussite d'un contrôle de style. Le signal utile est noyé dans un échec sans rapport.

**Correctif proposé** (ticket #3)
- Séparer l'intégrité dans son propre job, ou exécuter les étapes d'intégrité même après un échec :
  ```yaml
  jobs:
    integrity:            # verify + immuabilité + tests du Store, sans dépendre du lint
      runs-on: ubuntu-24.04
      steps: [...]
    quality:              # ruff, mypy, tsc, tests des plugins
      runs-on: ubuntu-24.04
      steps: [...]
  ```
  Ou, a minima, ajouter `if: ${{ !cancelled() }}` aux étapes `verify`, immuabilité et tests.
- Épingler les outils Python dans `requirements-dev.txt` avec leurs hashes (`pip-compile --generate-hashes`), puis `pip install --require-hashes -r requirements-dev.txt`, et laisser Dependabot proposer les montées de version (M6).

### H4. Les liens de téléchargement suivent `main` (Haute)

**Preuve** : les 52 entrées de `repository.json` (14 plugins, 4 outils) utilisent `https://raw.githubusercontent.com/remi-deher/encoding-store/main/packages/...`. `store.py build` a `--ref main` par défaut. Le README le reconnaît : « Download URLs still follow `main` for existing versions ».

**Impact**
- **Immuabilité sur le fil conditionnelle** : les octets servis pour une version « immuable » sont ceux de `main` à l'instant de la requête. Seul le processus (H1) les protège, et il est aujourd'hui sans barrière. Si le moteur contrôle le SHA-256 (I3), une altération devient au mieux un déni de service (installation refusée), au pire une compromission quand l'index est modifié en même temps.
- **Fenêtre de 404** : une version construite sur une branche (`--ref main` par défaut) est inaccessible tant que la PR n'est pas fusionnée dans `main`. Si un moteur lit l'index d'une autre branche (`dev` est la branche par défaut), ses liens sont cassés.
- **Évolutions d'hébergement bloquées** : renommer `main`, réécrire l'historique pour alléger les binaires, ou migrer vers Git LFS (piste suggérée par `CONTRIBUTING.md`) casse tous les liens existants (I2).
- **Traçabilité** : les attestations de release portent sur un tag, mais aucun lien ne pointe vers ce tag. Le seul rapprochement possible passe par le hash.

**Correctif proposé** (ticket #5), sans toucher aux archives ni à leur hash :
- Repointer chaque `downloadUrl` vers le **SHA (40 caractères) du commit qui a introduit l'archive**. Faisabilité vérifiée : les 52 archives ont un commit d'introduction présent sur `origin/main`, et il n'y en a que 8 distincts (`94ef673`, `35d88df`, `78a2e94`, `8c002a9`, `a3a8f25`, `aec1433`, `af0c518`, `cf81166`). Le champ reste une chaîne `https://`, le schéma et les moteurs actuels sont inchangés.
  ```python
  def introducing_commit(rel_path: str) -> str:
      out = subprocess.run(["git", "log", "origin/main", "--diff-filter=A", "--format=%H", "-1", "--", rel_path],
                           cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
      if not re.fullmatch(r"[0-9a-f]{40}", out):
          raise SystemExit("%s : archive absente de origin/main, impossible d'épingler." % rel_path)
      return out
  ```
- Dans `verify`, exiger une ref en SHA de 40 caractères (en gardant `main` toléré seulement pendant la transition) et contrôler que l'archive existe à cette ref avec les mêmes octets (`git cat-file blob <sha>:packages/<id>/<v>/<v>.zip`, puis SHA-256).
- Pour les nouvelles versions : construire avec `--ref main`, puis épingler après la fusion via une commande `store.py pin-urls` (manuelle ou lancée par un workflow qui ouvre une PR). Le lien `main` reste valide entre-temps puisque l'archive y est déjà présente.
- Le ruleset de H1 (force-push interdit) garantit que les commits référencés restent atteignables.

### M1. Retour arrière des sources non détecté (Moyenne)

**Preuve** : `check_one_plugin` ne contrôle que la version déclarée par le manifeste des sources. Le test suivant renvoie `Catalogue valide.` : remettre `plugins/base/manifests/base.json` et `subtitles.json` à leur état de `3ce6f89` (1.10.2 et 1.3.3) alors que 1.10.3 et 1.5.0 sont publiées.

**Impact** : une fusion maladroite ou un `git checkout` d'un vieux fichier fait perdre les sources de la dernière version publiée sans alerte. La version suivante repartirait de sources anciennes, ce qui fait régresser les fonctions publiées entre-temps.

**Correctif proposé** :
```python
if version != plugin["latestVersion"]:
    problems.append("plugin %s : les sources sont en %s mais la dernière version publiée est %s "
                    "(sources perdues ou retour arrière)." % (package_id, version, plugin["latestVersion"]))
```
Placé avant le contrôle « n'est pas publiée », il couvre aussi une version des sources supérieure à `latestVersion` (le précédent), avec un message plus explicite.

### M2. Fichiers source non déclarés jamais contrôlés (Moyenne)

**Preuve** : `expected_entries` ne lit que `manifest`, `files` et `inline` de `plugins/packages.json`. Un module ajouté à `plugins/plex/` et importé par `plugin.py` n'entre pas dans l'archive, et `verify` ne le signale pas : seule la modification de `plugin.py` est vue, le fichier manquant ne l'est jamais. Les tests de `plugins/tests` importent depuis les sources et passent.

**Impact** : un plugin publié qui échoue à l'import chez les utilisateurs alors que la CI est verte.

**Correctif proposé** : déclarer pour chaque plugin le répertoire de sources et refuser tout fichier non déclaré ni explicitement ignoré.
```python
def undeclared_files(entry: Json) -> list[str]:
    declared = {entry["manifest"], *entry.get("files", {}).values()}
    root = os.path.join(ROOT, "plugins", entry["sourceDir"])           # ex. "plex", "arr"
    ignored = set(entry.get("ignore", []))
    found = {os.path.relpath(p, os.path.join(ROOT, "plugins")) for p in glob.glob(os.path.join(root, "**", "*"), recursive=True)
             if os.path.isfile(p) and "__pycache__" not in p}
    return sorted(found - declared - ignored)
```
`sourceDir` et `ignore` sont de nouveaux champs de `plugins/packages.json`, fichier interne au dépôt : aucun impact sur `repository.json`.

### M3. Release jamais exécutée, workflow trop permissif (Moyenne)

**Preuve**
- `git tag` est vide et `gh release list` aussi : **aucune release n'a jamais été publiée**. Il n'existe donc ni `SHA256SUMS` ni attestation Sigstore, alors que le README présente ces garanties comme acquises (« Tagged releases (`vYYYY.MM.DD`) publish `SHA256SUMS` and a Sigstore build provenance attestation »).
- `release.yml` :
  - `on: push: tags: ["v*"]` accepte n'importe quel tag `v…`, pas seulement `vAAAA.MM.JJ`.
  - Rien ne vérifie que le commit tagué est sur `main`.
  - `workflow_dispatch` permet d'attester des artefacts depuis **n'importe quelle branche**, avec une identité signée du dépôt.
  - `permissions: contents: write, id-token: write, attestations: write` s'appliquent à tout le job, y compris l'installation non épinglée de `fastjsonschema`.
  - `verify` est rejoué sans `--base` : un tag posé sur un commit qui a modifié une archive publiée n'est pas bloqué.
  - `SHA256SUMS` n'est pas un sujet de l'attestation, et les archives ne sont pas jointes à la release.

**Impact** : les garanties de traçabilité annoncées n'existent pas encore, et le premier tag essuiera les plâtres d'un workflow jamais exécuté. Une attestation produite depuis une branche non relue reste signée au nom du dépôt.

**Correctif proposé** (ticket #4) :
```yaml
on:
  push:
    tags: ["v[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]"]
permissions:
  contents: read
jobs:
  release:
    permissions: { contents: write, id-token: write, attestations: write }
    steps:
      - uses: actions/checkout@<sha>   # fetch-depth: 0
      - name: Le tag est sur main
        run: git merge-base --is-ancestor "$GITHUB_SHA" origin/main
      - run: python3 scripts/store.py verify --base "$(git describe --tags --abbrev=0 HEAD^ 2>/dev/null || git rev-list --max-parents=0 HEAD)"
      # ... SHA256SUMS ajouté aux subject-path de l'attestation
```
Supprimer `workflow_dispatch`, ou le restreindre par `if: github.ref == 'refs/heads/main'`. Protéger les tags `v*` par ruleset (H1). Publier une première release pour valider le workflow, puis corriger le README (B3).

### M4. Contrôle d'immuabilité incomplet (Moyenne)

**Preuve** (`check_immutability`) :
- Seul `packages/` est comparé. Une entrée de `repository.json` pour une version déjà publiée peut voir son `downloadUrl` changé : le test qui repointe plex 1.1.1 vers `some-unmerged-branch` renvoie `Catalogue valide.` avec et sans `--base HEAD`.
- `git diff <base> -- packages` compare la **pointe** de la base à l'arbre de travail, pas le point de divergence. Une branche partie de `3ce6f89` et comparée à `02ccef8` donne **6 erreurs** « immuable (statut D) » : ce sont les archives ajoutées entre-temps sur la base.
- Ce contrôle ne tourne ni sur push ni en release (H1, M3).

**Impact** : faux positifs qui poussent à contourner le contrôle ; aucune protection des liens des versions publiées, ce qui comptera dès que H4 les aura épinglés.

**Correctif proposé** :
```python
result = subprocess.run(["git", "diff", "--name-status", "--no-renames", base + "...HEAD", "--", "packages"], ...)
# Puis, pour l'index : chaque (id, version) présent à la base garde le même sha256, et son downloadUrl
# ne peut évoluer que de .../main/... vers .../<sha40>/... (même chemin), jamais l'inverse.
old = json.loads(subprocess.run(["git", "show", base + ":repository.json"], cwd=ROOT, capture_output=True, text=True, check=True).stdout)
```

### M5. `downloadUrl` peut sortir du dépôt par `..` (Moyenne, CVSS 3.1 4.3 `AV:N/AC:H/PR:H/UI:N/S:U/C:N/I:H/A:N`)

**Preuve** : l'expression de `check_archives` autorise `[A-Za-z0-9._/-]+` comme ref. Avec
`https://raw.githubusercontent.com/remi-deher/encoding-store/../../attacker/fork/main/packages/plex/1.1.0/1.1.0.zip`,
`verify` renvoie `Catalogue valide.`. Un client HTTP qui normalise le chemin télécharge depuis `attacker/fork`. Le schéma se limite à exiger `^https://`.

**Impact** : `verify` est censé garantir que chaque lien pointe vers ce dépôt. Cette garantie tombe : un attaquant (avec accès en écriture, voir H1) peut héberger hors du dépôt une archive qui ne passe pas par le contrôle d'immuabilité de `packages/`. Le contrôle de hash côté moteur reste la dernière barrière (I3).

**Correctif proposé** :
```python
REF = r"(?:main|[0-9a-f]{40}|v[0-9]{4}\.[0-9]{2}\.[0-9]{2})"
pattern = re.escape(DOWNLOAD_BASE) + "/" + REF + r"/packages/%s/%s/%s\.zip" % (re.escape(item["id"]), re.escape(version), re.escape(version))
```
Le schéma `repository.schema.json` peut rester tel quel pour ne pas toucher aux lecteurs : le contrôle strict vit dans `verify`.

### M6. Chaîne d'approvisionnement de la CI (Moyenne, CVSS 3.1 5.9 `AV:N/AC:H/PR:N/UI:N/S:U/C:N/I:H/A:N`)

**Preuve** :
- Actions référencées par tag mobile (`actions/checkout@v4`, `setup-python@v5`, `setup-node@v4`, `attest-build-provenance@v2`).
- `gh api .../actions/permissions` : `allowed_actions: all`, `sha_pinning_required: false`.
- `pip install` sans version ni hash, y compris dans le job de release qui dispose de `contents: write` et `id-token: write`.
- Ni `.github/dependabot.yml`, ni `CODEOWNERS`, ni `SECURITY.md`.
- Côté npm, `npm ci` avec `package-lock.json` est correct.

**Impact** : un tag d'action déplacé ou un paquet PyPI compromis s'exécute avec le jeton du dépôt. En release, il peut publier une release ou attester des artefacts. Instabilité démontrée par H3.

**Correctif proposé** : épingler chaque action par SHA (avec la version en commentaire), passer `sha_pinning_required` à `true` et `allowed_actions` à `selected`, utiliser `requirements-dev.txt` avec hashes (H3), et ajouter :
```yaml
# .github/dependabot.yml
version: 2
updates:
  - { package-ecosystem: github-actions, directory: "/", schedule: { interval: weekly } }
  - { package-ecosystem: pip,            directory: "/", schedule: { interval: weekly } }
  - { package-ecosystem: npm,            directory: "/", schedule: { interval: weekly } }
```

### M7. Provenance des paquets d'outils invérifiable (Moyenne)

**Preuve** : `check_licenses` compare `sha256_expected_from_Ubuntu_Packages` et `sha256_downloaded`, deux chaînes écrites dans le même fichier `tools/ubuntu-noble-lock.json` : le contrôle est tautologique. Aucun script du dépôt ne reconstruit les archives d'outils à partir des `.deb`. Les SHA-256 des exécutables dans `tool.json` ne garantissent que la cohérence interne de l'archive.

**Impact** : rien dans la CI ne relie les binaires servis (exécutés dans le moteur) aux paquets Ubuntu annoncés. Le verrou documente la provenance sans la prouver.

**Correctif proposé** : un workflow planifié (hebdomadaire, hors chemin critique) qui télécharge chaque `.deb` depuis l'archive Ubuntu, vérifie son SHA-256 contre `Packages` signé, extrait les exécutables et compare leurs hashes à ceux de `tool.json`. Supprimer le champ `sha256_downloaded`, ou le recalculer dans ce workflow.

### B1. Contrôles ZIP incomplets (Basse)

**Preuve** (`check_archives`, `check_zip_names`) :
- `members = {name: archive.read(name) ...}` garde **la dernière** entrée d'un nom dupliqué. Un extracteur côté moteur qui prend la première verrait un autre `plugin.json` que celui validé par `verify` (écart entre deux analyseurs).
- Aucun rejet des liens symboliques (`external_attr`), aucune limite de taille décompressée.
- La détection des orphelins (`glob("packages/*/*/*")`) ignore les fichiers à une autre profondeur.

**Correctif proposé** :
```python
names = archive.namelist()
if len(names) != len(set(names)):
    problems.append("%s : entrées dupliquées." % label)
for info in archive.infolist():
    if (info.external_attr >> 16) & 0o170000 == 0o120000:
        problems.append("%s : lien symbolique interdit (%s)." % (label, info.filename))
if sum(i.file_size for i in archive.infolist()) > 512 * 1024 * 1024:
    problems.append("%s : taille décompressée excessive." % label)
# orphelins : glob(os.path.join(ROOT, "packages", "**", "*"), recursive=True) filtré sur les fichiers
```

### B2. Tests manquants (Basse)

`scripts/tests/test_store.py` couvre : index valide, archive altérée, source modifiée sans incrément, orphelin, reconstruction refusée, publication. **Aucun test** ne couvre `verify --base` (immuabilité), la version incrémentée sans `build` (le précédent), le retour arrière (M1), un fichier non déclaré (M2), le format des URL (M5) ni les ZIP piégés (B1). À ajouter avec chaque correctif, en réutilisant `repository_copy()`.

### B3. Documentation désynchronisée (Basse)

- `README.md` annonce « video 1.3.2, audio 1.1.2, subtitles 1.3.2, MKV 1.1.2, images 1.0.2, probing 1.1.2, AI 1.0.2 », alors que les dernières versions sont 1.3.3, 1.1.3, 1.5.0, 1.1.3, 1.0.3, 1.1.3 et 1.0.3. Il présente aussi les releases signées comme existantes (M3).
- `CHANGELOG.md` range sous « Non publié » des versions servies depuis `main` (base 1.10.3, plex 1.1.2...) : aucun lien entre une entrée du journal et un instantané.
- `docs/plugin-contract.md` ne dit ni d'où le moteur lit l'index, ni qu'il doit vérifier `sha256` avant d'extraire (I3).

**Correctif** : générer la liste des versions du README depuis `repository.json` (ou la supprimer), créer une section de CHANGELOG par tag `vAAAA.MM.JJ` au moment de la release (ticket #4), et documenter le contrat de téléchargement.

### I1. Reproductibilité octet pour octet dépendante de zlib (Info)

`build_zip` est déterministe pour une implémentation de zlib donnée. Avec zlib 1.3.2 et Python 3.14 en local, les 14 dernières versions de plugins sont reproduites **à l'identique** (hash égal à l'index). En revanche, `verify` compare le contenu décompressé et non les octets : un ZIP construit ailleurs (zlib-ng, autre niveau) passerait `verify` avec un hash différent. Construire les archives en CI (H2) avec un environnement épinglé supprime ce risque.

### I2. Binaires dans l'historique git (Info)

`packages/` pèse 5,3 Mo, dont 4,6 Mo pour mkvtoolnix. `CONTRIBUTING.md` envisage Git LFS ou des pièces jointes de release. Pour une archive suivie par LFS, `raw.githubusercontent.com` sert le **fichier pointeur**, pas le contenu : tous les liens actuels échoueraient au contrôle de hash. Toute migration doit donc suivre l'épinglage de H4, ne concerner que les **nouvelles** versions, et ne jamais réécrire l'historique.

### I3. Hypothèse côté moteur (Info)

Toutes les estimations d'impact ci-dessus supposent que le moteur Encoding vérifie le `sha256` de l'index **avant** d'extraire ou d'exécuter quoi que ce soit, et qu'il lit l'index depuis `main`. Ce dépôt ne permet pas de le vérifier, et `docs/plugin-contract.md` ne le spécifie pas. Il faut le confirmer dans le dépôt du moteur. Si ce n'est pas le cas, H1, H4 et M5 deviennent critiques.

---

## Axe 2 : récapitulatif des étapes manuelles

| Étape | Qui | Automatisable | Constat |
|-------|-----|---------------|---------|
| Incrémenter `version` dans le manifeste (×2 pour `arr/`, ×4 pour le SDK) | humain | contrôlée par `verify`, pas automatisée | H2 |
| `store.py build <id>` en local et commit du ZIP | humain | oui (H2) | oubli = précédent |
| `store.py sync-index` après un changement de métadonnées | humain | oui (contrôlé par `verify`) | — |
| Entrée de `CHANGELOG.md` | humain | partiellement | B3 |
| Fusion dans `main` | humain | — | sans barrière (H1) |
| Tag `vAAAA.MM.JJ` | humain | oui (workflow planifié ou manuel contrôlé) | jamais fait (M3) |
| Épinglage des liens | n'existe pas | oui (H4) | — |

## Axe 3 : récapitulatif des liens qui suivent `main`

52 liens sur 52 suivent `main`. Ce choix rend l'immuabilité dépendante du processus (H1), ouvre une fenêtre de 404 avant fusion, bloque toute évolution d'hébergement (I2) et laisse les attestations sans lien direct avec les téléchargements (M3). Épinglage par SHA de commit d'introduction faisable sans toucher aux archives ni aux hashes, avec un format de `repository.json` inchangé (H4) ; il faut le compléter par M4 et M5 pour que les liens épinglés ne puissent plus régresser.
