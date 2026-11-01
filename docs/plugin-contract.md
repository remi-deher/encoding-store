# Contrat des plugins à processus (version 1)

Un plugin est un processus lancé à chaque appel par le worker Encoding, dans un bac à sable Landlock. Le SDK ([Python](../plugins/sdk/encoding_plugin_sdk.py), [JavaScript](../plugins/sdk/encoding-plugin-sdk.mjs)) implémente ce protocole ; les exemples sont dans [`plugins/example`](../plugins/example) et [`plugins/example-js`](../plugins/example-js).

## Protocole

Une requête JSON d'une ligne sur stdin (1 Mo maximum), une réponse JSON d'une ligne sur stdout.

| `type` | Réponse |
|---|---|
| `describe` | le manifeste du plugin |
| `execute` | `outputId` (une sortie déclarée) ou un résultat terminal (`unchanged`, `success`, `failed`, `deferred`), plus `variableChanges` et éventuellement un artefact candidat |

Toute requête porte `contractVersion: 1`. Code de sortie 0 : réponse valide écrite. Code 1 : échec d'infrastructure (service injoignable, authentification refusée, contrat violé) ; l'hôte considère l'effet incertain. Un résultat métier attendu (élément introuvable) est une sortie déclarée, jamais un échec. Ne jamais écrire de secret sur stdout ou stderr.

## Manifeste

Voir [`schemas/plugin-package.schema.json`](../schemas/plugin-package.schema.json) pour l'enveloppe publiée (`plugin.json`, avec `runtime`) et `python3 plugins/sdk/encoding_plugin_sdk.py validate manifest.json` pour les règles complètes (identifiants, sorties, schéma de configuration, `timeoutSeconds`, `mediaEffect`, catégories). Les `requiredTools` référencent des paquets d'outils de ce catalogue.

## Outils d'auteur

```sh
python3 plugin.py --simulate <composant> '{"cle":"valeur"}' [--plugin-config '{...}'] [--media 'Dossier/e1.mkv']
```

## Utilitaires du SDK

`remote_path(ctx)` (chemin du fichier tel que le service le voit, via `pathMappings`), `longest_prefix_match`, `same_path`, `JsonApi` (HTTP JSON sans redirection, délai et taille bornés).

## Publication

Voir [CONTRIBUTING.md](../CONTRIBUTING.md).
