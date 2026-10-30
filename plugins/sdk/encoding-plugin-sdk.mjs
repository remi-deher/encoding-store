// SDK Encoding pour écrire un plugin de flow en JavaScript (Node.js, modules ES, aucune dépendance).
//
// Même contrat et même API que le SDK Python (encoding_plugin_sdk.py) : le plugin est un processus lancé à chaque appel,
// qui lit une requête JSON d'une ligne sur stdin et répond par une ligne JSON sur stdout (contrat 1).
//
//   import { Plugin, Outcome, loadManifest } from "./encoding-plugin-sdk.mjs";
//   const plugin = new Plugin(loadManifest(import.meta.url));
//   plugin.component("mon-composant", (ctx) => Outcome.output("success", "terminé", { nom: "valeur" }));
//   process.exitCode = await plugin.main();
//
// Un échec d'infrastructure (service injoignable, authentification refusée) se signale en levant InfrastructureError :
// le processus se termine en erreur et l'hôte considère l'effet incertain. Un résultat métier attendu se rapporte par une
// sortie déclarée. N'écrivez ni secret ni donnée sensible sur stdout ou stderr.
//
// Outils d'auteur :
//   node encoding-plugin-sdk.mjs validate manifest.json
//   node plugin.mjs --simulate mon-composant '{"cle":"valeur"}' [--plugin-config '{...}'] [--media 'Dossier/e1.mkv']
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { randomUUID } from "node:crypto";

export const CONTRACT_VERSION = 1;
const MAX_REQUEST_CHARACTERS = 1024 * 1024;
const TERMINAL_RESULTS = ["unchanged", "success", "failed", "deferred"];

export class InfrastructureError extends Error {}
export class ContractError extends Error {}

export class Outcome {
  constructor(outputId = null, terminal = null, detail = "", variables = {}, candidate = null) {
    Object.assign(this, { outputId, terminal, detail, variables, candidate });
  }
  static output(outputId, detail = "", variables = {}, candidate = null) { return new Outcome(outputId, null, detail, variables, candidate); }
  static finish(terminal, detail = "") {
    if (!TERMINAL_RESULTS.includes(terminal)) throw new RangeError(`Résultat terminal inconnu : ${terminal}`);
    return new Outcome(null, terminal, detail);
  }
}

const text = (values, key) => (typeof values[key] === "string" && values[key].trim() ? values[key] : null);
const number = (values, key) => (typeof values[key] === "number" ? values[key] : null);

export class Context {
  constructor(invocation) {
    this.invocation = invocation;
    this.config = invocation.configuration ?? {};
    this.pluginConfig = invocation.pluginConfiguration ?? {};
    const context = invocation.context ?? {};
    this.mediaPath = context.mediaPath ?? null;
    this.libraryId = context.libraryId ?? null;
    this.variables = context.variables ?? {};
    this.currentFile = context.currentArtifactId ?? null;
    this.beforeMetadata = context.beforeMetadata ?? null;
    this.currentMetadata = context.currentMetadata ?? null;
  }
  text(key) { return text(this.config, key); }
  number(key) { return number(this.config, key); }
  flag(key, fallback = false) { return typeof this.config[key] === "boolean" ? this.config[key] : fallback; }
  pluginText(key) { return text(this.pluginConfig, key); }
  pluginNumber(key) { return number(this.pluginConfig, key); }
}

/** Charge manifest.json à côté du script (import.meta.url). */
export function loadManifest(scriptUrl, fileName = "manifest.json") {
  return JSON.parse(readFileSync(join(dirname(fileURLToPath(scriptUrl)), fileName), "utf8"));
}

export class Plugin {
  constructor(manifest) {
    if (manifest.contractVersion !== CONTRACT_VERSION || !(manifest.supportedHostContractVersions ?? []).includes(CONTRACT_VERSION))
      throw new ContractError(`Le manifeste doit déclarer le contrat hôte ${CONTRACT_VERSION}.`);
    this.manifest = manifest;
    this.handlers = new Map();
  }

  component(componentId, handler) {
    if (!this.manifest.components.some((c) => c.id === componentId)) throw new ContractError(`Composant absent du manifeste : ${componentId}.`);
    if (this.handlers.has(componentId)) throw new ContractError(`Composant déjà géré : ${componentId}.`);
    this.handlers.set(componentId, handler);
    return this;
  }

  async #execute(invocation) {
    if (!invocation || typeof invocation !== "object") throw new ContractError("Invocation absente.");
    const reference = invocation.component ?? {};
    if (invocation.contractVersion !== CONTRACT_VERSION || reference.pluginId !== this.manifest.id || reference.pluginVersion !== this.manifest.version)
      throw new ContractError("Invocation destinée à un autre plugin ou à une autre version.");
    const component = this.manifest.components.find((c) => c.id === reference.componentId && c.version === reference.componentVersion
      && c.configurationSchema.version === reference.configurationSchemaVersion);
    if (!component) throw new ContractError("Composant inconnu de ce plugin.");
    const outcome = await this.handlers.get(component.id)(new Context(invocation));
    if (!(outcome instanceof Outcome) || (outcome.outputId === null) === (outcome.terminal === null))
      throw new ContractError("Un composant doit retourner exactement une sortie ou un résultat terminal.");
    if (outcome.outputId !== null && !component.outputs.some((o) => o.id === outcome.outputId))
      throw new ContractError(`Sortie non déclarée par le composant : ${outcome.outputId}.`);
    return {
      contractVersion: CONTRACT_VERSION, invocationId: invocation.invocationId, leaseEpoch: invocation.leaseEpoch,
      outputId: outcome.outputId, terminalResult: outcome.terminal, variableChanges: outcome.variables,
      candidateArtifactId: outcome.candidate, detail: outcome.detail,
    };
  }

  /** Exécute un composant sans processus ni cluster, avec une invocation fabriquée. */
  simulate(componentId, { config = {}, pluginConfig = {}, mediaPath = null, libraryId = null, variables = {} } = {}) {
    const component = this.manifest.components.find((c) => c.id === componentId);
    if (!component) throw new ContractError(`Composant absent du manifeste : ${componentId}.`);
    return this.#execute({
      contractVersion: CONTRACT_VERSION, invocationId: randomUUID(), leaseEpoch: 1,
      component: { pluginId: this.manifest.id, pluginVersion: this.manifest.version, componentId, componentVersion: component.version,
        configurationSchemaVersion: component.configurationSchema.version },
      configuration: config, pluginConfiguration: pluginConfig,
      context: { mediaPath, libraryId, variables, currentArtifactId: "current" },
    });
  }

  async #readRequest(stdin) {
    let data = "";
    for await (const chunk of stdin) {
      data += chunk;
      if (data.length > MAX_REQUEST_CHARACTERS) throw new ContractError("Requête trop volumineuse.");
      const newline = data.indexOf("\n");
      if (newline >= 0) { data = data.slice(0, newline); break; }
    }
    if (!data.trim()) throw new ContractError("Requête absente.");
    return JSON.parse(data);
  }

  /** Traite une requête du worker. Retourne 0 si une réponse valide a été écrite, 1 sinon. */
  async run(stdin = process.stdin, stdout = process.stdout, stderr = process.stderr) {
    try {
      const missing = this.manifest.components.filter((c) => !this.handlers.has(c.id)).map((c) => c.id);
      if (missing.length) throw new ContractError(`Composants sans gestionnaire : ${missing.join(", ")}.`);
      stdin.setEncoding?.("utf8");
      const request = await this.#readRequest(stdin);
      if (!request || request.contractVersion !== CONTRACT_VERSION) throw new ContractError("Version du contrat non prise en charge.");
      let response;
      if (request.type === "describe") response = this.manifest;
      else if (request.type === "execute") response = await this.#execute(request.invocation);
      else throw new ContractError("Type de requête inconnu.");
      stdout.write(JSON.stringify(response) + "\n");
      return 0;
    } catch (error) {
      if (error instanceof InfrastructureError || error instanceof ContractError || error instanceof SyntaxError) stderr.write(`${error.message}\n`);
      else stderr.write(`Erreur inattendue (${error?.constructor?.name ?? "Error"}).\n`);
      return 1;
    }
  }

  /** Point d'entrée : protocole sur stdin/stdout, ou --simulate pour le développement local. */
  async main(argv = process.argv.slice(2)) {
    if (argv[0] !== "--simulate") return this.run();
    try {
      const [, componentId, ...rest] = argv;
      const options = { config: rest[0] && !rest[0].startsWith("--") ? JSON.parse(rest.shift()) : {} };
      for (let i = 0; i < rest.length; i += 2) {
        if (rest[i] === "--plugin-config") options.pluginConfig = JSON.parse(rest[i + 1]);
        else if (rest[i] === "--media") options.mediaPath = rest[i + 1];
        else if (rest[i] === "--library") options.libraryId = rest[i + 1];
        else throw new ContractError(`Option inconnue : ${rest[i]}`);
      }
      console.log(JSON.stringify(await this.simulate(componentId, options), null, 2));
      return 0;
    } catch (error) {
      process.stderr.write(`simulate : ${error.message}\n`);
      return 1;
    }
  }
}

// --- validation d'un manifeste (mêmes règles que le SDK Python et que le serveur) ------------------------------------

const PLUGIN_ID = /^[a-z0-9]+(-[a-z0-9]+)*(\.[a-z0-9]+(-[a-z0-9]+)*)?$/;
const COMPONENT_ID = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const SEMVER = /^\d+\.\d+\.\d+$/;
const PLUGIN_FIELD_TYPES = ["text", "secret", "number", "boolean", "select"];
const COMPONENT_FIELD_TYPES = ["text", "number", "boolean", "select", "condition-tree", "script-reference", "encoding-profile", "video-encoder", "record-list"];
const FLOW_COMPONENT_CATEGORIES = new Set([
  "inputs", "files", "discovery", "metadata", "encoding", "video", "audio", "images", "text",
  "containers", "media-rules", "validation", "conditions", "variables", "scripts", "arr",
  "media-servers", "communications", "cluster", "orchestration", "reporting", "utilities",
]);

export function validateManifest(manifest) {
  const problems = [];
  if (!manifest || typeof manifest !== "object") return ["Le manifeste doit être un objet JSON."];
  if (typeof manifest.id !== "string" || manifest.id.length > 64 || !PLUGIN_ID.test(manifest.id)) problems.push("id : minuscules, chiffres et tirets, avec un espace de noms optionnel « espace.nom ».");
  if (typeof manifest.version !== "string" || !SEMVER.test(manifest.version)) problems.push("version : forme X.Y.Z attendue.");
  if (manifest.contractVersion !== CONTRACT_VERSION) problems.push(`contractVersion : ${CONTRACT_VERSION} attendu.`);
  if (!(manifest.supportedHostContractVersions ?? []).includes(CONTRACT_VERSION)) problems.push(`supportedHostContractVersions : doit contenir ${CONTRACT_VERSION}.`);
  if (typeof manifest.name !== "string" || !manifest.name.trim()) problems.push("name : requis.");
  const checkFields = (owner, fields, allowed) => {
    const seen = new Set();
    for (const field of fields ?? []) {
      if (!field?.id) { problems.push(`${owner} : un champ n'a pas d'id.`); continue; }
      if (seen.has(field.id)) problems.push(`${owner} : champ « ${field.id} » dupliqué.`);
      seen.add(field.id);
      if (!allowed.includes(field.type)) problems.push(`${owner} : champ « ${field.id} » de type « ${field.type} » non pris en charge (${allowed.join(", ")}).`);
      if (!field.label) problems.push(`${owner} : champ « ${field.id} » sans label.`);
      if (field.scope != null && !["server", "worker"].includes(field.scope)) problems.push(`${owner} : champ « ${field.id} » : scope « server » ou « worker ».`);
    }
  };
  if (manifest.configurationSchema) {
    if (manifest.configurationSchema.version !== 1) problems.push("configurationSchema.version : 1 attendu.");
    checkFields("configurationSchema", manifest.configurationSchema.fields, PLUGIN_FIELD_TYPES);
  }
  if (!Array.isArray(manifest.components) || !manifest.components.length) problems.push("components : au moins un composant est requis.");
  const seenComponents = new Set();
  for (const component of Array.isArray(manifest.components) ? manifest.components : []) {
    const id = component?.id;
    if (typeof id !== "string" || !COMPONENT_ID.test(id)) { problems.push(`Composant « ${id} » : identifiant invalide (minuscules, chiffres, tirets).`); continue; }
    if (seenComponents.has(id)) problems.push(`Composant « ${id} » déclaré deux fois.`);
    seenComponents.add(id);
    if (!SEMVER.test(String(component.version ?? ""))) problems.push(`Composant « ${id} » : version X.Y.Z attendue.`);
    if (!["action", "condition"].includes(component.kind)) problems.push(`Composant « ${id} » : kind « action » ou « condition ».`);
    if (!String(component.description ?? "").trim()) problems.push(`Composant « ${id} » : description vide (l'éditeur et les assistants IA s'y fient).`);
    if (component.category != null && !FLOW_COMPONENT_CATEGORIES.has(component.category)) problems.push(`Composant « ${id} » : catégorie fonctionnelle inconnue.`);
    const outputs = component.outputs ?? [];
    if (!outputs.length) problems.push(`Composant « ${id} » : aucune sortie déclarée.`);
    if (new Set(outputs.map((o) => o.id)).size !== outputs.length) problems.push(`Composant « ${id} » : identifiant de sortie dupliqué.`);
    for (const output of outputs) if (!String(output.description ?? "").trim()) problems.push(`Composant « ${id} » : la sortie « ${output.id} » n'a pas de description.`);
    if (component.configurationSchema?.version !== 1) problems.push(`Composant « ${id} » : configurationSchema.version 1 attendu.`);
    checkFields(`Composant « ${id} »`, component.configurationSchema?.fields, COMPONENT_FIELD_TYPES);
    const timeout = component.timeoutSeconds;
    if (component.mediaEffect != null && !['readOnly', 'producesOutput', 'modifiesCurrent'].includes(component.mediaEffect)) problems.push(`Composant « ${id} » : mediaEffect inconnu.`);
    if (timeout != null && !(Number.isInteger(timeout) && timeout >= 1 && timeout <= 21600)) problems.push(`Composant « ${id} » : timeoutSeconds entre 1 et 21600.`);
  }
  return problems;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  if (process.argv[2] === "validate" && process.argv[3]) {
    let problems;
    try { problems = validateManifest(JSON.parse(readFileSync(process.argv[3], "utf8"))); }
    catch (error) { console.error(`Manifeste illisible : ${error.message}`); process.exit(1); }
    for (const problem of problems) console.log(`✗ ${problem}`);
    console.log(problems.length ? `${problems.length} problème(s).` : "Manifeste valide.");
    process.exit(problems.length ? 1 : 0);
  }
  console.error("Usage : node encoding-plugin-sdk.mjs validate manifest.json");
  process.exit(2);
}
