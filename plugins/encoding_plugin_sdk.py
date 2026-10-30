"""SDK Encoding pour écrire un plugin de flow en Python 3 (bibliothèque standard uniquement).

Un plugin est un processus lancé à chaque appel par le worker, dans le bac à sable Landlock. Il reçoit une
requête JSON sur une ligne de stdin ({"type":"describe"} ou {"type":"execute"}) et répond par une ligne JSON
sur stdout. Ce module implémente ce protocole (contrat 1) : version du contrat, identité du plugin, composant
déclaré, sortie déclarée. Un composant ne contient que sa logique métier.

    from encoding_plugin_sdk import Plugin, Outcome, load_manifest

    plugin = Plugin(load_manifest(__file__, "mon-plugin"))

    @plugin.component("mon-composant")
    def run(ctx):
        return Outcome.output("success", "terminé", variables={"nom": "valeur"})

    if __name__ == "__main__":
        raise SystemExit(plugin.run())

Un échec d'infrastructure (service injoignable, authentification refusée…) se signale en levant
InfrastructureError : le processus se termine en erreur et l'hôte considère l'effet incertain, sans jamais le
transformer en sortie métier. Un résultat métier attendu (élément introuvable…) se rapporte par une sortie
déclarée dans le descripteur du composant. N'écrivez ni secret ni donnée sensible dans stdout ou stderr.

Outils d'auteur (aucun cluster nécessaire) :

    python3 encoding_plugin_sdk.py validate manifest.json      # mêmes règles que le serveur
    python3 plugin.py --simulate mon-composant '{"cle":"valeur"}' [--plugin-config '{...}'] [--media 'Dossier/e1.mkv']
"""
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

CONTRACT_VERSION = 1
MAX_REQUEST_CHARACTERS = 1024 * 1024
TERMINAL_RESULTS = ("unchanged", "success", "failed", "deferred")


class InfrastructureError(Exception):
    """Échec technique : le processus se termine en erreur (code 1) et l'effet est considéré comme incertain."""


class ContractError(Exception):
    """Requête ou réponse incompatible avec le contrat plugin."""


class Outcome:
    """Résultat d'un composant : exactement une sortie déclarée ou un résultat terminal."""

    def __init__(self, output_id=None, terminal=None, detail="", variables=None, candidate=None):
        self.output_id = output_id
        self.terminal = terminal
        self.detail = detail
        self.variables = variables or {}
        self.candidate = candidate

    @staticmethod
    def output(output_id, detail="", variables=None, candidate=None):
        return Outcome(output_id=output_id, detail=detail, variables=variables, candidate=candidate)

    @staticmethod
    def finish(terminal, detail=""):
        if terminal not in TERMINAL_RESULTS:
            raise ValueError("Résultat terminal inconnu : %s" % terminal)
        return Outcome(terminal=terminal, detail=detail)


class Context:
    """Invocation reçue par un composant, avec accès typé à sa configuration et à celle du plugin."""

    def __init__(self, invocation):
        self.invocation = invocation
        self.config = invocation.get("configuration") or {}
        self.plugin_config = invocation.get("pluginConfiguration") or {}
        context = invocation.get("context") or {}
        self.media_path = context.get("mediaPath")
        self.library_id = context.get("libraryId")
        self.variables = context.get("variables") or {}
        self.current_file = context.get("currentArtifactId")
        self.before_metadata = context.get("beforeMetadata")
        self.current_metadata = context.get("currentMetadata")

    @staticmethod
    def _text(values, key):
        value = values.get(key)
        return value if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _number(values, key):
        value = values.get(key)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def text(self, key):
        """Paramètre texte de l'instance du composant dans le flow, ou None."""
        return self._text(self.config, key)

    def number(self, key):
        return self._number(self.config, key)

    def flag(self, key, default=False):
        value = self.config.get(key)
        return value if isinstance(value, bool) else default

    def plugin_text(self, key):
        """Configuration effective du plugin (valeurs globales fusionnées avec les dérogations du flow)."""
        return self._text(self.plugin_config, key)

    def plugin_number(self, key):
        return self._number(self.plugin_config, key)


def load_manifest(script_path, plugin_id):
    """Charge le manifeste du plugin.

    Le serveur et le plugin utilisent le même JSON : il est cherché dans manifests/<id>.json à côté du script
    (image worker), puis dans le catalogue du dépôt (développement).
    """
    here = os.path.dirname(os.path.abspath(script_path))
    candidates = [
        os.path.join(here, "manifests", plugin_id + ".json"),
        os.path.join(here, plugin_id + ".json"),
        os.path.join(here, "..", "..", "src", "Encoding.Api", "Flows", "Catalog", plugin_id + ".json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
    raise ContractError("Manifeste introuvable pour le plugin %s." % plugin_id)


class Plugin:
    def __init__(self, manifest):
        if manifest.get("contractVersion") != CONTRACT_VERSION or CONTRACT_VERSION not in manifest.get("supportedHostContractVersions", []):
            raise ContractError("Le manifeste doit déclarer le contrat hôte %d." % CONTRACT_VERSION)
        self.manifest = manifest
        self._handlers = {}

    def component(self, component_id):
        """Décorateur : associe une fonction ctx -> Outcome à un composant du manifeste."""
        def register(handler):
            if component_id not in {c["id"] for c in self.manifest["components"]}:
                raise ContractError("Composant absent du manifeste : %s." % component_id)
            if component_id in self._handlers:
                raise ContractError("Composant déjà géré : %s." % component_id)
            self._handlers[component_id] = handler
            return handler
        return register

    def run(self, stdin=None, stdout=None, stderr=None):
        """Traite une requête. Retourne 0 si une réponse valide a été écrite, 1 pour un échec."""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        stderr = stderr or sys.stderr
        try:
            missing = [c["id"] for c in self.manifest["components"] if c["id"] not in self._handlers]
            if missing:
                raise ContractError("Composants sans gestionnaire : %s." % ", ".join(missing))
            line = stdin.readline(MAX_REQUEST_CHARACTERS + 1)
            if not line.strip():
                raise ContractError("Requête absente.")
            if len(line) > MAX_REQUEST_CHARACTERS:
                raise ContractError("Requête trop volumineuse.")
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("contractVersion") != CONTRACT_VERSION:
                raise ContractError("Version du contrat non prise en charge.")
            kind = request.get("type")
            if kind == "describe":
                response = self.manifest
            elif kind == "execute":
                response = self._execute(request.get("invocation"))
            else:
                raise ContractError("Type de requête inconnu.")
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
            return 0
        except (InfrastructureError, ContractError, ValueError) as error:
            # Les messages de ces exceptions ne contiennent jamais de secret : les gestionnaires doivent le garantir.
            stderr.write("%s\n" % error)
            return 1
        except Exception as error:  # noqa: BLE001 - toute erreur inattendue est un échec d'infrastructure
            stderr.write("Erreur inattendue (%s).\n" % type(error).__name__)
            return 1

    def simulate(self, component_id, config=None, plugin_config=None, media_path=None, library_id=None, variables=None):
        """Exécute un composant sans processus ni cluster, avec une invocation fabriquée. Retourne la réponse du contrat."""
        component = next((c for c in self.manifest["components"] if c["id"] == component_id), None)
        if component is None:
            raise ContractError("Composant absent du manifeste : %s." % component_id)
        return self._execute({
            "contractVersion": CONTRACT_VERSION, "invocationId": str(uuid.uuid4()), "leaseEpoch": 1,
            "component": {"pluginId": self.manifest["id"], "pluginVersion": self.manifest["version"],
                          "componentId": component["id"], "componentVersion": component["version"],
                          "configurationSchemaVersion": component["configurationSchema"]["version"]},
            "configuration": config or {}, "pluginConfiguration": plugin_config or {},
            "context": {"mediaPath": media_path, "libraryId": library_id, "variables": variables or {}, "currentArtifactId": "current"},
        })

    def main(self, argv=None):
        """Point d'entrée d'un plugin : protocole sur stdin/stdout, ou --simulate pour le développement local."""
        argv = list(sys.argv[1:] if argv is None else argv)
        if not argv or argv[0] != "--simulate":
            return self.run()
        try:
            component_id = argv[1]
            options = {"config": json.loads(argv[2]) if len(argv) > 2 and not argv[2].startswith("--") else {}}
            rest = argv[3:] if len(argv) > 2 and not argv[2].startswith("--") else argv[2:]
            for flag, value in zip(rest[::2], rest[1::2]):
                if flag == "--plugin-config":
                    options["plugin_config"] = json.loads(value)
                elif flag == "--media":
                    options["media_path"] = value
                elif flag == "--library":
                    options["library_id"] = value
                else:
                    raise ValueError("Option inconnue : %s" % flag)
            print(json.dumps(self.simulate(component_id, **options), ensure_ascii=False, indent=2))
            return 0
        except (IndexError, ValueError, ContractError, InfrastructureError) as error:
            sys.stderr.write("simulate : %s\n" % (error or "arguments invalides"))
            return 1

    def _execute(self, invocation):
        if not isinstance(invocation, dict):
            raise ContractError("Invocation absente.")
        reference = invocation.get("component") or {}
        if (invocation.get("contractVersion") != CONTRACT_VERSION
                or reference.get("pluginId") != self.manifest["id"]
                or reference.get("pluginVersion") != self.manifest["version"]):
            raise ContractError("Invocation destinée à un autre plugin ou à une autre version.")
        component = next((c for c in self.manifest["components"]
                          if c["id"] == reference.get("componentId")
                          and c["version"] == reference.get("componentVersion")
                          and c["configurationSchema"]["version"] == reference.get("configurationSchemaVersion")), None)
        if component is None:
            raise ContractError("Composant inconnu de ce plugin.")
        outcome = self._handlers[component["id"]](Context(invocation))
        if not isinstance(outcome, Outcome) or (outcome.output_id is None) == (outcome.terminal is None):
            raise ContractError("Un composant doit retourner exactement une sortie ou un résultat terminal.")
        if outcome.output_id is not None and outcome.output_id not in {o["id"] for o in component["outputs"]}:
            raise ContractError("Sortie non déclarée par le composant : %s." % outcome.output_id)
        return {
            "contractVersion": CONTRACT_VERSION,
            "invocationId": invocation.get("invocationId"),
            "leaseEpoch": invocation.get("leaseEpoch"),
            "outputId": outcome.output_id,
            "terminalResult": outcome.terminal,
            "variableChanges": outcome.variables,
            "candidateArtifactId": outcome.candidate,
            "detail": outcome.detail,
        }


# --- correspondance de chemins (même format et mêmes règles que Encoding.Contracts LibraryPathMapping) -------------

def _normalize_remote(path):
    normalized = path.strip().replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized.rstrip("/") if len(normalized) > 1 else normalized


def parse_mappings(text):
    """Parse « identifiant-bibliothèque=/chemin/vu/par/le/service », une ligne par bibliothèque.

    Retourne {identifiant: racine}. Lève ValueError si le format est invalide.
    """
    result = {}
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        separator = line.find("=")
        if separator <= 0:
            raise ValueError("Format de correspondance invalide.")
        library = str(uuid.UUID(line[:separator].strip()))
        root = _normalize_remote(line[separator + 1:])
        if not root or library in result or root in result.values():
            raise ValueError("Racine vide ou déclarée deux fois.")
        result[library] = root
    return result


def to_remote(mappings, library_id, relative_path):
    """Chemin d'un fichier de la bibliothèque tel que le service le voit, ou None si elle n'est pas associée."""
    root = mappings.get(str(uuid.UUID(str(library_id))))
    return None if root is None else root.rstrip("/") + "/" + relative_path.replace("\\", "/")


def remote_path(ctx):
    """(chemin du fichier traité vu par le service, message d'erreur métier) d'après la configuration du plugin."""
    if not ctx.library_id or not ctx.media_path:
        return None, "Le fichier traité n'est pas rattaché à une bibliothèque."
    try:
        mappings = parse_mappings(ctx.plugin_text("pathMappings"))
    except ValueError:
        return None, "La correspondance des chemins du plugin est invalide."
    path = to_remote(mappings, ctx.library_id, ctx.media_path)
    if path is None:
        return None, "Cette bibliothèque n'a pas de correspondance de chemin dans la configuration du plugin."
    return path, None


# --- client HTTP JSON ---------------------------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class JsonApi:
    """Client HTTP JSON pour un service externe.

    Pas de redirection, délai et taille de réponse bornés. 404 est rendu à l'appelant ((404, None)) car c'est
    un résultat métier fréquent ; toute autre erreur est une InfrastructureError dont le message ne contient ni
    l'adresse complète ni les en-têtes (donc pas la clé).
    """

    MAX_RESPONSE_BYTES = 64 * 1024 * 1024
    TIMEOUT_SECONDS = 30

    def __init__(self, name, base_url, headers, opener=None):
        parts = urllib.parse.urlsplit(base_url or "")
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise InfrastructureError("L'adresse de %s doit être une URL HTTP ou HTTPS sans identifiant intégré." % name)
        self.name = name
        self.base = base_url if base_url.endswith("/") else base_url + "/"
        self.headers = dict(headers)
        self.opener = opener or urllib.request.build_opener(_NoRedirect)

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Accept", "application/json")
        for key, value in self.headers.items():
            request.add_header(key, value)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(request, timeout=self.TIMEOUT_SECONDS) as response:
                payload = response.read(self.MAX_RESPONSE_BYTES + 1)
                if len(payload) > self.MAX_RESPONSE_BYTES:
                    raise InfrastructureError("La réponse de %s dépasse la limite." % self.name)
                return response.status, (json.loads(payload) if payload.strip() else None)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return 404, None
            if error.code in (401, 403):
                raise InfrastructureError("%s a refusé la clé API." % self.name)
            raise InfrastructureError("%s a répondu avec le statut HTTP %d." % (self.name, error.code))
        except (urllib.error.URLError, socket.timeout, ConnectionError):
            raise InfrastructureError("Impossible de joindre %s depuis ce worker." % self.name)
        except ValueError:
            raise InfrastructureError("%s a répondu avec un contenu illisible." % self.name)


# --- validation d'un manifeste (mêmes règles que FlowComponentRegistry côté serveur) ------------------------------

_PLUGIN_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*(\.[a-z0-9]+(-[a-z0-9]+)*)?$")
_COMPONENT_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_PLUGIN_FIELD_TYPES = ("text", "secret", "number", "boolean", "select")
_COMPONENT_FIELD_TYPES = ("text", "number", "boolean", "select", "condition-tree", "script-reference", "encoding-profile", "video-encoder", "record-list")
_FLOW_COMPONENT_CATEGORIES = {
    "inputs", "files", "discovery", "metadata", "encoding", "video", "audio", "images", "text",
    "containers", "media-rules", "validation", "conditions", "variables", "scripts", "arr",
    "media-servers", "communications", "cluster", "orchestration", "reporting", "utilities",
}


def validate_manifest(manifest):
    """Retourne la liste des problèmes d'un manifeste (vide si le serveur l'acceptera). Ne lève jamais."""
    problems = []
    if not isinstance(manifest, dict):
        return ["Le manifeste doit être un objet JSON."]
    plugin_id = manifest.get("id")
    if not isinstance(plugin_id, str) or len(plugin_id) > 64 or not _PLUGIN_ID.match(plugin_id):
        problems.append("id : minuscules, chiffres et tirets, avec un espace de noms optionnel « espace.nom ».")
    if not isinstance(manifest.get("version"), str) or not _SEMVER.match(manifest["version"]):
        problems.append("version : forme X.Y.Z attendue.")
    if manifest.get("contractVersion") != CONTRACT_VERSION:
        problems.append("contractVersion : %d attendu." % CONTRACT_VERSION)
    if CONTRACT_VERSION not in (manifest.get("supportedHostContractVersions") or []):
        problems.append("supportedHostContractVersions : doit contenir %d." % CONTRACT_VERSION)
    if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
        problems.append("name : requis.")

    def check_fields(owner, fields, allowed):
        seen = set()
        if not isinstance(fields, list):
            problems.append("%s : « fields » doit être une liste." % owner)
            return
        for field in fields:
            field_id = field.get("id") if isinstance(field, dict) else None
            if not field_id:
                problems.append("%s : un champ n'a pas d'id." % owner)
                continue
            if field_id in seen:
                problems.append("%s : champ « %s » dupliqué." % (owner, field_id))
            seen.add(field_id)
            if field.get("type") not in allowed:
                problems.append("%s : champ « %s » de type « %s » non pris en charge (%s)." % (owner, field_id, field.get("type"), ", ".join(allowed)))
            if not field.get("label"):
                problems.append("%s : champ « %s » sans label." % (owner, field_id))
            if field.get("scope") not in (None, "server", "worker"):
                problems.append("%s : champ « %s » : scope « server » ou « worker »." % (owner, field_id))

    schema = manifest.get("configurationSchema")
    if schema is not None:
        if schema.get("version") != 1:
            problems.append("configurationSchema.version : 1 attendu.")
        check_fields("configurationSchema", schema.get("fields", []), _PLUGIN_FIELD_TYPES)
    components = manifest.get("components")
    if not isinstance(components, list) or not components:
        problems.append("components : au moins un composant est requis.")
        components = []
    seen_components = set()
    for component in components:
        cid = component.get("id") if isinstance(component, dict) else None
        if not isinstance(cid, str) or not _COMPONENT_ID.match(cid):
            problems.append("Composant « %s » : identifiant invalide (minuscules, chiffres, tirets)." % cid)
            continue
        if cid in seen_components:
            problems.append("Composant « %s » déclaré deux fois." % cid)
        seen_components.add(cid)
        if not _SEMVER.match(str(component.get("version", ""))):
            problems.append("Composant « %s » : version X.Y.Z attendue." % cid)
        if component.get("kind") not in ("action", "condition"):
            problems.append("Composant « %s » : kind « action » ou « condition »." % cid)
        if not str(component.get("description", "")).strip():
            problems.append("Composant « %s » : description vide (l'éditeur et les assistants IA s'y fient)." % cid)
        if component.get("category") is not None and (
            not isinstance(component["category"], str) or component["category"] not in _FLOW_COMPONENT_CATEGORIES
        ):
            problems.append("Composant « %s » : catégorie fonctionnelle inconnue." % cid)
        outputs = component.get("outputs") or []
        if not outputs:
            problems.append("Composant « %s » : aucune sortie déclarée." % cid)
        ids = [o.get("id") for o in outputs if isinstance(o, dict)]
        if len(ids) != len(set(ids)):
            problems.append("Composant « %s » : identifiant de sortie dupliqué." % cid)
        for output in outputs:
            if isinstance(output, dict) and not str(output.get("description", "")).strip():
                problems.append("Composant « %s » : la sortie « %s » n'a pas de description." % (cid, output.get("id")))
        component_schema = component.get("configurationSchema") or {}
        if component_schema.get("version") != 1:
            problems.append("Composant « %s » : configurationSchema.version 1 attendu." % cid)
        check_fields("Composant « %s »" % cid, component_schema.get("fields", []), _COMPONENT_FIELD_TYPES)
        timeout = component.get("timeoutSeconds")
        if component.get("mediaEffect") not in (None, "readOnly", "producesOutput", "modifiesCurrent"):
            problems.append("Composant « %s » : mediaEffect inconnu." % cid)
        if timeout is not None and not (isinstance(timeout, int) and 1 <= timeout <= 21600):
            problems.append("Composant « %s » : timeoutSeconds entre 1 et 21600." % cid)
    return problems


def _cli(argv):
    if len(argv) == 2 and argv[0] == "validate":
        try:
            with open(argv[1], encoding="utf-8") as handle:
                problems = validate_manifest(json.load(handle))
        except (OSError, ValueError) as error:
            print("Manifeste illisible : %s" % error, file=sys.stderr)
            return 1
        for problem in problems:
            print("✗ " + problem)
        print("Manifeste valide." if not problems else "%d problème(s)." % len(problems))
        return 1 if problems else 0
    print("Usage : python3 encoding_plugin_sdk.py validate manifest.json", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
