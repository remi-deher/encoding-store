#!/usr/bin/env node
// Plugin d'exemple JavaScript : mêmes deux composants que l'exemple Python, à copier pour écrire un plugin tiers.
//   node plugin.mjs --simulate examplejs-tag-file '{"name":"tag"}' --media 'Show/S01/e1.mkv'
import { basename } from "node:path";
import { Outcome, Plugin, loadManifest } from "../sdk/encoding-plugin-sdk.mjs";

const plugin = new Plugin(loadManifest(import.meta.url));

plugin.component("examplejs-tag-file", (ctx) => {
  const name = ctx.text("name") ?? "tag";
  const value = ctx.text("value") ?? basename(ctx.mediaPath ?? "");
  return Outcome.output("success", `Variable ${name} enregistrée.`, { [name]: value });
});

plugin.component("examplejs-name-matches", (ctx) =>
  Outcome.output((ctx.mediaPath ?? "").toLowerCase().includes((ctx.text("text") ?? "").toLowerCase()) ? "yes" : "no"));

process.exitCode = await plugin.main();
