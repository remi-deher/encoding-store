"""Tests des plugins Python : protocole du SDK, plugin d'exemple, plugins Sonarr/Radarr contre un faux service.

Exécution : python3 -m unittest discover -s plugins/tests -v   (depuis la racine du dépôt)
Chaque plugin est lancé comme processus réel, exactement comme le fait le worker.
"""
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGINS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PLUGINS, "sdk"))
sys.path.insert(0, os.path.join(PLUGINS, "arr"))

import encoding_plugin_sdk as sdk  # noqa: E402
import plugin as arr  # noqa: E402,F401

ARR = os.path.join(PLUGINS, "arr", "plugin.py")
PLEX = os.path.join(PLUGINS, "plex", "plugin.py")
JELLYFIN = os.path.join(PLUGINS, "jellyfin", "plugin.py")
EXAMPLE = os.path.join(PLUGINS, "example", "plugin.py")
LIBRARY = str(uuid.uuid4())


def run_process(script, args, request):
    process = subprocess.run([sys.executable, "-B", script, *args], input=request, capture_output=True, text=True, timeout=60)
    return process.returncode, process.stdout, process.stderr


def invocation(manifest, component_id, config=None, plugin_config=None, library=LIBRARY, media_path=None, epoch=3):
    component = next(c for c in manifest["components"] if c["id"] == component_id)
    return {
        "contractVersion": 1, "invocationId": str(uuid.uuid4()), "executionId": str(uuid.uuid4()), "attempt": 1,
        "workerNodeId": str(uuid.uuid4()), "leaseEpoch": epoch, "nodeId": "node-1",
        "component": {"pluginId": manifest["id"], "pluginVersion": manifest["version"], "componentId": component["id"],
                      "componentVersion": component["version"], "configurationSchemaVersion": component["configurationSchema"]["version"]},
        "configuration": config or {},
        "context": {"currentArtifactId": "current.mkv", "variables": {}, "mediaPath": media_path, "libraryId": library},
        "deadline": "2099-01-01T00:00:00+00:00", "pluginConfiguration": plugin_config,
    }


def execute(script, args, inv):
    return run_process(script, args, json.dumps({"type": "execute", "contractVersion": 1, "invocation": inv}) + "\n")


class FakeArr:
    """Faux service Sonarr/Radarr : table de routage (méthode, chemin) -> (statut, JSON), requêtes enregistrées."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode() if length else ""
                fake.requests.append({"method": method, "path": self.path, "body": body, "key": self.headers.get("X-Api-Key") or self.headers.get("X-Plex-Token") or self.headers.get("X-Emby-Token")})
                status, payload = fake.routes.get((method, self.path), (404, None))
                data = b"" if payload is None else json.dumps(payload).encode()
                self.send_response(status)
                if status in (301, 302):
                    self.send_header("Location", "http://127.0.0.1:1/")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                self._answer("GET")

            def do_POST(self):  # noqa: N802
                self._answer("POST")

            def do_PUT(self):  # noqa: N802
                self._answer("PUT")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def commands(self):
        return [json.loads(r["body"]) for r in self.requests if r["method"] == "POST"]


def sonarr_routes():
    return {
        ("GET", "/api/v5/system/status"): (404, None),
        ("GET", "/api/v3/system/status"): (200, {}),
        ("GET", "/api/v3/series"): (200, [{"id": 8, "path": "/tv/Show Extra"}, {"id": 7, "path": "/tv/Show"}]),
        ("GET", "/api/v3/episodefile?seriesId=7"): (200, [{"path": "/tv/Show/Season 1/e1.mkv"}]),
        ("GET", "/api/v3/episode/41"): (200, {}),
        ("GET", "/api/v3/episode/42"): (404, None),
        ("POST", "/api/v3/command"): (201, {}),
    }


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(PLUGINS, "example", "manifest.json"), encoding="utf-8") as handle:
            self.manifest = json.load(handle)

    def test_describe_returns_the_manifest(self):
        code, out, _ = run_process(EXAMPLE, [], '{"type":"describe","contractVersion":1}\n')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), self.manifest)

    def test_requests_that_break_the_contract_fail_without_output(self):
        good = invocation(self.manifest, "example-tag-file", {"name": "episode"}, media_path="Shows/e1.mkv")
        bad_version = dict(good, component=dict(good["component"], pluginVersion="9.9.9"))
        bad_component = dict(good, component=dict(good["component"], componentId="inconnu"))
        for request in ('{"type":"describe","contractVersion":2}\n', '{"type":"nope","contractVersion":1}\n', "", "pas du json\n",
                        json.dumps({"type": "execute", "contractVersion": 1, "invocation": bad_version}) + "\n",
                        json.dumps({"type": "execute", "contractVersion": 1, "invocation": bad_component}) + "\n"):
            code, out, _ = run_process(EXAMPLE, [], request)
            self.assertEqual((code, out), (1, ""), request)

    def test_example_components_execute_as_a_process(self):
        code, out, err = execute(EXAMPLE, [], invocation(self.manifest, "example-tag-file", {"name": "episode"}, media_path="Shows/Show/e1.mkv", epoch=7))
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["outputId"], result["leaseEpoch"], result["variableChanges"]), ("success", 7, {"episode": "e1.mkv"}))
        self.assertIsNone(result["terminalResult"])
        code, out, _ = execute(EXAMPLE, [], invocation(self.manifest, "example-name-matches", {"text": "SHOW"}, media_path="Shows/Show/e1.mkv"))
        self.assertEqual(json.loads(out)["outputId"], "yes")
        code, out, _ = execute(EXAMPLE, [], invocation(self.manifest, "example-name-matches", {"text": "film"}, media_path="Shows/Show/e1.mkv"))
        self.assertEqual(json.loads(out)["outputId"], "no")

    def test_sdk_rejects_undeclared_outputs_missing_handlers_and_double_results(self):
        def run(plugin, component_id):
            out, err = io.StringIO(), io.StringIO()
            inv = invocation(self.manifest, component_id, {"name": "x", "text": "x"}, media_path="a.mkv")
            code = plugin.run(io.StringIO(json.dumps({"type": "execute", "contractVersion": 1, "invocation": inv}) + "\n"), out, err)
            return code, out.getvalue()

        plugin = sdk.Plugin(self.manifest)
        plugin.component("example-tag-file")(lambda ctx: sdk.Outcome.output("inconnue"))
        plugin.component("example-name-matches")(lambda ctx: sdk.Outcome.output("yes"))
        self.assertEqual(run(plugin, "example-tag-file"), (1, ""), "sortie non déclarée")
        incomplete = sdk.Plugin(self.manifest)
        incomplete.component("example-tag-file")(lambda ctx: sdk.Outcome.output("success"))
        self.assertEqual(run(incomplete, "example-tag-file"), (1, ""), "composant du manifeste sans gestionnaire")
        both = sdk.Plugin(self.manifest)
        both.component("example-tag-file")(lambda ctx: sdk.Outcome(output_id="success", terminal="success"))
        both.component("example-name-matches")(lambda ctx: sdk.Outcome.output("yes"))
        self.assertEqual(run(both, "example-tag-file"), (1, ""), "sortie et résultat terminal simultanés")
        with self.assertRaises(sdk.ContractError):
            sdk.Plugin(self.manifest).component("absent")(lambda ctx: None)

    def test_infrastructure_errors_exit_without_output(self):
        plugin = sdk.Plugin(self.manifest)

        def explode(ctx):
            raise sdk.InfrastructureError("service injoignable")

        plugin.component("example-tag-file")(explode)
        plugin.component("example-name-matches")(lambda ctx: sdk.Outcome.output("yes"))
        out, err = io.StringIO(), io.StringIO()
        inv = invocation(self.manifest, "example-tag-file", {"name": "x"}, media_path="a.mkv")
        code = plugin.run(io.StringIO(json.dumps({"type": "execute", "contractVersion": 1, "invocation": inv}) + "\n"), out, err)
        self.assertEqual((code, out.getvalue(), err.getvalue().strip()), (1, "", "service injoignable"))

    def test_sdk_exposes_metadata_and_reports_an_explicit_candidate(self):
        plugin = sdk.Plugin(self.manifest)
        plugin.component("example-tag-file")(lambda ctx: sdk.Outcome.output("success", candidate="result.mkv"))
        plugin.component("example-name-matches")(lambda ctx: sdk.Outcome.output("yes"))
        result = plugin.simulate("example-tag-file")
        self.assertEqual("result.mkv", result["candidateArtifactId"])
        context = sdk.Context({"context": {"currentArtifactId": None, "currentMetadata": {"streams": []}, "beforeMetadata": {"source": True}}})
        self.assertIsNone(context.current_file)
        self.assertEqual({"streams": []}, context.current_metadata)
        self.assertEqual({"source": True}, context.before_metadata)


class PathMappingTests(unittest.TestCase):
    """Mêmes vecteurs que les tests C# de LibraryPathMapping : les deux implémentations doivent concorder."""

    def setUp(self):
        with open(os.path.join(HERE, "path-mapping-vectors.json"), encoding="utf-8") as handle:
            self.vectors = json.load(handle)
        self.libraries = self.vectors["libraries"]

    def fill(self, text):
        for name, value in self.libraries.items():
            text = text.replace("{" + name + "}", value)
        return text

    def test_parse_and_map_to_remote(self):
        mappings = sdk.parse_mappings(self.fill(self.vectors["mappings"]))
        self.assertEqual(mappings, {self.libraries[name]: root for name, root in self.vectors["roots"].items()})
        for case in self.vectors["toRemote"]:
            self.assertEqual(sdk.to_remote(mappings, self.libraries[case["library"]], case["relative"]), case["remote"])
        self.assertIsNone(sdk.to_remote(mappings, str(uuid.uuid4()), "a.mkv"))

    def test_invalid_mappings_are_refused(self):
        for text in self.vectors["invalid"]:
            with self.assertRaises(ValueError, msg=text):
                sdk.parse_mappings(self.fill(text))


class SonarrTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeArr(sonarr_routes())
        self.addCleanup(self.fake.close)
        self.manifest = sdk.load_manifest(ARR, "sonarr")

    def plugin_config(self, **overrides):
        config = {"baseUrl": self.fake.url, "apiKey": "secret-api-key", "pathMappings": "%s=/tv" % LIBRARY}
        config.update(overrides)
        return config

    def run_component(self, component, config=None, media="Show/Season 1/e1.mkv", library=LIBRARY, plugin_config=None):
        inv = invocation(self.manifest, component, config, plugin_config or self.plugin_config(), library, media)
        code, out, err = execute(ARR, ["sonarr"], inv)
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["invocationId"], result["leaseEpoch"], result["contractVersion"]), (inv["invocationId"], inv["leaseEpoch"], 1))
        return result

    def test_manifest_is_the_one_served_to_the_backend(self):
        code, out, _ = run_process(ARR, ["sonarr"], '{"type":"describe","contractVersion":1}\n')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), self.manifest)

    def test_refresh_rescans_the_series_containing_the_file(self):
        result = self.run_component("sonarr-refresh-series")
        self.assertEqual(result["outputId"], "success")
        self.assertEqual(self.fake.commands(), [{"name": "RescanSeries", "seriesId": 7}], "pas la série voisine au même préfixe")
        self.assertTrue(all(r["key"] == "secret-api-key" for r in self.fake.requests), "clé envoyée en en-tête")
        self.assertTrue(all("secret-api-key" not in r["path"] + r["body"] for r in self.fake.requests), "clé jamais dans l'URL ni le corps")

    def test_search_uses_explicit_id_or_the_file(self):
        self.run_component("sonarr-search-missing-episodes", {"seriesId": 9})
        self.assertEqual(self.fake.commands(), [{"name": "MissingEpisodeSearch", "seriesId": 9}])
        self.assertNotIn("/api/v3/series", [r["path"] for r in self.fake.requests], "pas de résolution de chemin avec un identifiant")
        self.run_component("sonarr-search-missing-episodes")
        self.assertEqual(self.fake.commands()[-1], {"name": "MissingEpisodeSearch", "seriesId": 7})

    def test_business_failures_take_the_error_output_without_a_command(self):
        unmapped = self.run_component("sonarr-refresh-series", library=str(uuid.uuid4()))
        self.assertEqual(unmapped["outputId"], "error")
        self.assertEqual(self.fake.requests, [], "pas d'appel sans correspondance de chemin")
        outside = self.run_component("sonarr-refresh-series", media="Autre/e1.mkv")
        self.assertEqual(outside["outputId"], "error")
        self.assertEqual(self.fake.commands(), [])
        invalid = self.run_component("sonarr-refresh-series", plugin_config=self.plugin_config(pathMappings="n'importe quoi"))
        self.assertEqual(invalid["outputId"], "error")

    def test_check_by_file_and_by_id(self):
        self.assertEqual(self.run_component("sonarr-check-episode-indexed")["outputId"], "found")
        self.assertEqual(self.run_component("sonarr-check-episode-indexed", media="Show/Season 1/e2.mkv")["outputId"], "notFound")
        self.assertEqual(self.run_component("sonarr-check-episode-indexed", media="Autre/e1.mkv")["outputId"], "notFound")
        self.assertEqual(self.run_component("sonarr-check-episode-indexed", {"episodeId": 41})["outputId"], "found")
        self.assertEqual(self.run_component("sonarr-check-episode-indexed", {"episodeId": 42})["outputId"], "notFound")

    def test_infrastructure_failures_stay_failures_and_never_leak_the_key(self):
        def failing(status):
            fake = FakeArr({("GET", "/api/v5/system/status"): (status, None), ("GET", "/api/v3/system/status"): (status, None)})
            self.addCleanup(fake.close)
            return fake

        for status in (401, 403, 500, 302):
            fake = failing(status)
            inv = invocation(self.manifest, "sonarr-refresh-series", None, self.plugin_config(baseUrl=fake.url), LIBRARY, "Show/e1.mkv")
            code, out, err = execute(ARR, ["sonarr"], inv)
            self.assertEqual((code, out), (1, ""), "HTTP %d" % status)
            self.assertNotIn("secret-api-key", err)
        for broken in ({"baseUrl": "http://127.0.0.1:1"}, {"baseUrl": "http://user:pass@127.0.0.1:1"}, {"baseUrl": "ftp://hote"}, {"baseUrl": None}, {"apiKey": None}):
            config = {k: v for k, v in self.plugin_config(**broken).items() if v is not None}
            code, out, err = execute(ARR, ["sonarr"], invocation(self.manifest, "sonarr-refresh-series", None, config, LIBRARY, "Show/e1.mkv"))
            self.assertEqual((code, out), (1, ""), broken)
            self.assertNotIn("secret-api-key", err)
            self.assertNotIn("pass", err)

    def test_the_program_needs_a_flavor(self):
        self.assertEqual(run_process(ARR, [], "")[0], 2)
        self.assertEqual(run_process(ARR, ["plex"], "")[0], 2)


class RadarrTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeArr({
            ("GET", "/api/v3/system/status"): (200, {}),
            ("GET", "/api/v3/movie"): (200, [
                {"id": 3, "path": "/movies/Film (2020)", "movieFile": {"path": "/movies/Film (2020)/Film.mkv"}},
                {"id": 4, "path": "/movies/Autre"}]),
            ("POST", "/api/v3/command"): (201, {}),
        })
        self.addCleanup(self.fake.close)
        self.manifest = sdk.load_manifest(ARR, "radarr")

    def run_component(self, component, media):
        config = {"baseUrl": self.fake.url, "apiKey": "k", "pathMappings": "%s=/movies" % LIBRARY}
        code, out, err = execute(ARR, ["radarr"], invocation(self.manifest, component, None, config, LIBRARY, media))
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_commands_use_id_lists(self):
        self.assertEqual(self.run_component("radarr-refresh-movie", "Film (2020)/Film.mkv")["outputId"], "success")
        self.assertEqual(self.fake.commands(), [{"name": "RescanMovie", "movieIds": [3]}])
        self.run_component("radarr-search-movie", "Film (2020)/Film.mkv")
        self.assertEqual(self.fake.commands()[-1], {"name": "MoviesSearch", "movieIds": [3]})

    def test_check_matches_the_exact_known_file(self):
        self.assertEqual(self.run_component("radarr-check-movie-indexed", "Film (2020)/Film.mkv")["outputId"], "found")
        self.assertEqual(self.run_component("radarr-check-movie-indexed", "Autre/Autre.mkv")["outputId"], "notFound")


PLEX_SECTIONS = {"MediaContainer": {"Directory": [
    {"key": "1", "type": "movie", "Location": [{"path": "/movies"}]},
    {"key": "2", "type": "show", "Location": [{"path": "/tv"}, {"path": "/tv/Anime"}]},
]}}


class PlexTests(unittest.TestCase):
    def setUp(self):
        self.routes = {("GET", "/library/sections"): (200, PLEX_SECTIONS)}
        self.fake = FakeArr(self.routes)
        self.addCleanup(self.fake.close)
        self.manifest = sdk.load_manifest(PLEX, "plex")

    def run_component(self, component, config=None, media="Show/Season 1/e1.mkv", mappings=True, deadline=None):
        plugin_config = {"baseUrl": self.fake.url, "apiKey": "plex-secret-token"}
        if mappings:
            plugin_config["pathMappings"] = "%s=/tv" % LIBRARY
        inv = invocation(self.manifest, component, config, plugin_config, LIBRARY, media)
        if deadline:
            inv["deadline"] = deadline
        code, out, err = execute(PLEX, [], inv)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def paths(self):
        return [r["path"] for r in self.fake.requests]

    def test_describe_is_the_served_manifest(self):
        code, out, _ = run_process(PLEX, [], '{"type":"describe","contractVersion":1}\n')
        self.assertEqual((code, json.loads(out)), (0, self.manifest))

    def test_refresh_limits_the_scan_to_the_folder_of_the_file(self):
        self.routes[("GET", "/library/sections/2/refresh?path=%2Ftv%2FShow%2FSeason+1")] = (200, None)
        result = self.run_component("plex-refresh-library")
        self.assertEqual(result["outputId"], "success")
        self.assertIn("/library/sections/2/refresh?path=%2Ftv%2FShow%2FSeason+1", self.paths())
        self.assertTrue(all(r["key"] == "plex-secret-token" for r in self.fake.requests))
        self.assertTrue(all("plex-secret-token" not in p for p in self.paths()), "jeton jamais dans l'URL")

    def test_refresh_without_mapping_refreshes_every_section_or_the_given_one(self):
        self.run_component("plex-refresh-library", mappings=False)
        self.assertEqual([p for p in self.paths() if "refresh" in p], ["/library/sections/1/refresh", "/library/sections/2/refresh"])
        self.fake.requests.clear()
        self.run_component("plex-refresh-library", {"sectionId": 1}, mappings=False)
        self.assertEqual(self.paths(), ["/library/sections/1/refresh"])

    def test_file_outside_every_section_is_a_business_error(self):
        # racine /movies2 : préfixe voisin de /movies, refusé (frontière de dossier)
        inv = invocation(self.manifest, "plex-refresh-library", None,
                         {"baseUrl": self.fake.url, "apiKey": "k", "pathMappings": "%s=/movies2" % LIBRARY}, LIBRARY, "a.mkv")
        code, out, _ = execute(PLEX, [], inv)
        self.assertEqual((code, json.loads(out)["outputId"]), (0, "error"))
        self.assertEqual([p for p in self.paths() if "refresh" in p], [])

    def test_wait_polls_until_the_section_is_idle(self):
        busy = {"MediaContainer": {"Directory": [{"key": "2", "type": "show", "refreshing": True, "Location": [{"path": "/tv"}]}]}}
        calls = {"n": 0}
        original = self.fake.routes

        class Sequence(dict):
            def get(inner, key, default=None):  # noqa: N805
                if key == ("GET", "/library/sections"):
                    calls["n"] += 1
                    return (200, busy) if calls["n"] < 3 else (200, PLEX_SECTIONS)
                return original.get(key, default)

        self.fake.routes = Sequence()
        result = self.run_component("plex-wait-library-scan")
        self.assertEqual(result["outputId"], "success")
        self.assertGreaterEqual(calls["n"], 3)

    def test_wait_gives_up_before_the_invocation_deadline(self):
        busy = {"MediaContainer": {"Directory": [{"key": "2", "type": "show", "refreshing": True, "Location": [{"path": "/tv"}]}]}}
        self.routes[("GET", "/library/sections")] = (200, busy)
        soon = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=6)).isoformat()
        result = self.run_component("plex-wait-library-scan", deadline=soon)
        self.assertEqual(result["outputId"], "error")

    def test_check_by_rating_key_and_by_file(self):
        self.routes[("GET", "/library/metadata/10")] = (200, {})
        self.assertEqual(self.run_component("plex-check-media-indexed", {"ratingKey": "10"})["outputId"], "found")
        self.assertEqual(self.run_component("plex-check-media-indexed", {"ratingKey": "11"})["outputId"], "notFound")
        listing = {"MediaContainer": {"Metadata": [{"Media": [{"Part": [{"file": "/tv/Show/Season 1/e1.mkv"}]}]}]}}
        for path in self.paths_for_check():
            self.routes[("GET", path)] = (200, listing)
        self.assertEqual(self.run_component("plex-check-media-indexed")["outputId"], "found")
        self.assertEqual(self.run_component("plex-check-media-indexed", media="Show/Season 1/e2.mkv")["outputId"], "notFound",
                         "un filtre ignoré par Plex ne doit pas valoir « trouvé »")

    def test_playback_by_file_or_globally(self):
        sessions = {"MediaContainer": {"Metadata": [{"Media": [{"Part": [{"file": "/tv/Show/Season 1/e1.mkv"}]}]}]}}
        self.routes[("GET", "/status/sessions")] = (200, sessions)
        self.assertEqual(self.run_component("plex-check-playback")["outputId"], "playing")
        self.assertEqual(self.run_component("plex-check-playback", media="Show/Season 1/e2.mkv")["outputId"], "idle")
        self.assertEqual(self.run_component("plex-check-playback", {"scope": "global"}, media="Show/Season 1/e2.mkv")["outputId"], "playing")
        self.routes[("GET", "/status/sessions")] = (200, {"MediaContainer": {"size": 0}})
        self.assertEqual(self.run_component("plex-check-playback", {"scope": "global"})["outputId"], "idle")
        self.assertEqual(self.run_component("plex-check-playback")["outputId"], "idle")

    EPISODE_E1 = {"ratingKey": "101", "type": "episode", "grandparentRatingKey": "9", "parentIndex": 1, "index": 1,
                  "Media": [{"Part": [{"file": "/tv/Show/Season 1/e1.mkv"}]}]}
    EPISODE_E2 = {"ratingKey": "102", "type": "episode", "grandparentRatingKey": "9", "parentIndex": 1, "index": 2,
                  "Media": [{"Part": [{"file": "/tv/Show/Season 1/e2.mkv"}]}]}

    def route_episodes(self, *items):
        for name, item in (("Show/Season 1/e1.mkv", self.EPISODE_E1), ("Show/Season 1/e2.mkv", self.EPISODE_E2)):
            query = urllib.parse.urlencode({"file": "/tv/" + name, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 50, "type": 4})
            self.routes[("GET", "/library/sections/2/all?" + query)] = (200, {"MediaContainer": {"Metadata": [item]}})

    def session(self, item, remaining_seconds):
        duration = 40 * 60 * 1000
        return {"MediaContainer": {"Metadata": [dict(item, duration=duration, viewOffset=duration - remaining_seconds * 1000)]}}

    def test_next_episode_counts_only_when_requested_and_within_the_margin(self):
        self.route_episodes()
        self.routes[("GET", "/status/sessions")] = (200, self.session(self.EPISODE_E1, 5 * 60))
        e2 = "Show/Season 1/e2.mkv"
        self.assertEqual(self.run_component("plex-check-playback", media=e2)["outputId"], "idle", "sans option, seul le fichier compte")
        both = {"includeNextEpisode": True}
        self.assertEqual(self.run_component("plex-check-playback", both, media=e2)["outputId"], "playing", "marge 0 : tout l'épisode précédent compte")
        self.assertEqual(self.run_component("plex-check-playback", dict(both, nextEpisodeMarginMinutes=10), media=e2)["outputId"], "playing")
        self.assertEqual(self.run_component("plex-check-playback", dict(both, nextEpisodeMarginMinutes=3), media=e2)["outputId"], "idle",
                         "il reste 5 minutes, au-delà de la marge de 3")
        self.assertEqual(self.run_component("plex-check-playback", both, media="Show/Season 1/e1.mkv")["outputId"], "playing", "le fichier lui-même compte toujours")

    def test_next_episode_ignores_other_series_and_following_episodes(self):
        self.route_episodes()
        other = dict(self.EPISODE_E1, grandparentRatingKey="77")
        self.routes[("GET", "/status/sessions")] = (200, self.session(other, 60))
        self.assertEqual(self.run_component("plex-check-playback", {"includeNextEpisode": True}, media="Show/Season 1/e2.mkv")["outputId"], "idle")
        self.routes[("GET", "/status/sessions")] = (200, self.session(self.EPISODE_E2, 60))
        self.assertEqual(self.run_component("plex-check-playback", {"includeNextEpisode": True}, media="Show/Season 1/e1.mkv")["outputId"], "idle",
                         "lire l'épisode suivant ne retarde pas le précédent")

    def test_blocks_playback_rules(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("plex_plugin", PLEX)
        plex_plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plex_plugin)
        sessions = [{"files": ["/tv/a/e1.mkv"], "episode": ("9", 1, 1), "remaining": None}]
        self.assertTrue(plex_plugin.blocks_playback(sessions, "/tv/a/e2.mkv", ("9", 1, 2), True, 10), "durée restante inconnue : on attend")
        self.assertFalse(plex_plugin.blocks_playback(sessions, "/tv/a/e2.mkv", ("9", 1, 2), False, 10))
        self.assertFalse(plex_plugin.blocks_playback(sessions, "/tv/a/e3.mkv", ("9", 1, 3), True, 0), "seul l'épisode immédiatement précédent compte")
        self.assertFalse(plex_plugin.blocks_playback(sessions, "/tv/a/e2.mkv", None, True, 0), "épisode inconnu : seul le fichier compte")

    def test_analyze_by_path_or_key(self):
        self.route_episodes()
        self.routes[("PUT", "/library/metadata/102/analyze")] = (200, None)
        result = self.run_component("plex-analyze-media", media="Show/Season 1/e2.mkv")
        self.assertEqual(result["outputId"], "success")
        self.assertEqual([r["path"] for r in self.fake.requests if r["method"] == "PUT"], ["/library/metadata/102/analyze"])
        self.assertTrue(all(r["key"] == "plex-secret-token" for r in self.fake.requests))
        self.assertEqual(self.run_component("plex-analyze-media", media="Show/Season 1/inconnu.mkv")["outputId"], "notFound")
        self.routes[("GET", "/library/metadata/55")] = (200, {})
        self.routes[("PUT", "/library/metadata/55/analyze")] = (200, None)
        self.assertEqual(self.run_component("plex-analyze-media", {"ratingKey": "55"})["outputId"], "success")
        self.assertEqual(self.run_component("plex-analyze-media", {"ratingKey": "56"})["outputId"], "notFound")

    @staticmethod
    def paths_for_check():
        for name in ("Show/Season 1/e1.mkv", "Show/Season 1/e2.mkv"):
            query = urllib.parse.urlencode({"file": "/tv/" + name, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 50, "type": 4})
            yield "/library/sections/2/all?" + query

    def test_infrastructure_failures_exit_without_output_or_key(self):
        for status in (401, 500, 302):
            fake = FakeArr({("GET", "/library/sections"): (status, None)})
            self.addCleanup(fake.close)
            inv = invocation(self.manifest, "plex-refresh-library", None,
                             {"baseUrl": fake.url, "apiKey": "plex-secret-token", "pathMappings": "%s=/tv" % LIBRARY}, LIBRARY, "a.mkv")
            code, out, err = execute(PLEX, [], inv)
            self.assertEqual((code, out), (1, ""), status)
            self.assertNotIn("plex-secret-token", err)


class JellyfinTests(unittest.TestCase):
    ITEM = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"

    def setUp(self):
        self.fake = FakeArr({})
        self.addCleanup(self.fake.close)
        self.manifest = sdk.load_manifest(JELLYFIN, "jellyfin")

    def route(self, method, path, status=204, payload=None):
        self.fake.routes[(method, path)] = (status, payload)

    def run_component(self, component, config=None, media="Show/Season 1/e1.mkv", mappings=True):
        plugin_config = {"baseUrl": self.fake.url, "apiKey": "jf-secret-key"}
        if mappings:
            plugin_config["pathMappings"] = "%s=/media/tv" % LIBRARY
        code, out, err = execute(JELLYFIN, [], invocation(self.manifest, component, config, plugin_config, LIBRARY, media))
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def items_path(self):
        query = urllib.parse.urlencode({"Recursive": "true", "Path": "/media/tv/Show/Season 1/e1.mkv", "Fields": "Path",
                                        "IncludeItemTypes": "Movie,Episode,Video,Audio"})
        return "/Items?" + query

    def test_describe_is_the_served_manifest(self):
        code, out, _ = run_process(JELLYFIN, [], '{"type":"describe","contractVersion":1}\n')
        self.assertEqual((code, json.loads(out)), (0, self.manifest))

    def test_refresh_item_by_file_resolves_the_exact_path(self):
        listing = {"Items": [{"Id": self.ITEM, "Path": "/media/tv/Show/Season 1/e1.mkv"}, {"Id": "other", "Path": "/media/tv/Show/Season 1/e1.mkv.bak"}]}
        self.route("GET", self.items_path(), 200, listing)
        refresh = "/Items/%s/Refresh?Recursive=false&MetadataRefreshMode=FullRefresh&ImageRefreshMode=FullRefresh&ReplaceAllMetadata=false" % self.ITEM
        self.route("POST", refresh)
        self.assertEqual(self.run_component("jellyfin-refresh-item")["outputId"], "success")
        self.assertIn(("POST", refresh), [(r["method"], r["path"]) for r in self.fake.requests])
        self.assertTrue(all(r["key"] == "jf-secret-key" for r in self.fake.requests))
        self.assertTrue(all("jf-secret-key" not in r["path"] for r in self.fake.requests))

    def test_refresh_item_unknown_file_is_a_business_error(self):
        self.route("GET", self.items_path(), 200, {"Items": []})
        self.assertEqual(self.run_component("jellyfin-refresh-item")["outputId"], "error")
        self.assertEqual([r for r in self.fake.requests if r["method"] == "POST"], [])

    def test_refresh_item_with_explicit_id_needs_no_mapping(self):
        refresh = "/Items/abc/Refresh?Recursive=false&MetadataRefreshMode=FullRefresh&ImageRefreshMode=FullRefresh&ReplaceAllMetadata=false"
        self.route("POST", refresh)
        self.assertEqual(self.run_component("jellyfin-refresh-item", {"itemId": "abc"}, mappings=False)["outputId"], "success")

    def test_refresh_library_notifies_the_file_or_rescans_everything(self):
        self.route("POST", "/Library/Media/Updated")
        self.assertEqual(self.run_component("jellyfin-refresh-library")["outputId"], "success")
        self.assertEqual(self.fake.commands(), [{"Updates": [{"Path": "/media/tv/Show/Season 1/e1.mkv", "UpdateType": "Modified"}]}])
        self.route("POST", "/Library/Refresh")
        self.run_component("jellyfin-refresh-library", mappings=False)
        self.assertEqual(self.fake.requests[-1]["path"], "/Library/Refresh")

    def test_check_by_id_and_by_file(self):
        self.route("GET", "/Items?ids=" + self.ITEM, 200, {"Items": [{"Id": self.ITEM}]})
        self.route("GET", "/Items?ids=missing", 200, {"Items": []})
        self.assertEqual(self.run_component("jellyfin-check-item-indexed", {"itemId": self.ITEM})["outputId"], "found")
        self.assertEqual(self.run_component("jellyfin-check-item-indexed", {"itemId": "missing"})["outputId"], "notFound")
        self.route("GET", self.items_path(), 200, {"Items": [{"Id": self.ITEM, "Path": "/media/tv/Show/Season 1/e1.mkv"}]})
        self.assertEqual(self.run_component("jellyfin-check-item-indexed")["outputId"], "found")
        self.route("GET", self.items_path(), 200, {"Items": [{"Id": "x", "Path": "/media/tv/autre.mkv"}]})
        self.assertEqual(self.run_component("jellyfin-check-item-indexed")["outputId"], "notFound",
                         "un filtre ignoré par Jellyfin ne doit pas valoir « trouvé »")

    def test_playback_by_file_or_globally(self):
        playing = [{"NowPlayingItem": {"Id": "a", "Path": "/media/tv/Show/Season 1/e1.mkv"}}, {"Id": "idle-session"}]
        self.route("GET", "/Sessions", 200, playing)
        self.assertEqual(self.run_component("jellyfin-check-playback")["outputId"], "playing")
        self.assertEqual(self.run_component("jellyfin-check-playback", media="Show/Season 1/e2.mkv")["outputId"], "idle")
        self.assertEqual(self.run_component("jellyfin-check-playback", {"scope": "global"}, media="x.mkv")["outputId"], "playing")
        self.route("GET", "/Sessions", 200, [{"Id": "idle-session"}])
        self.assertEqual(self.run_component("jellyfin-check-playback", {"scope": "global"})["outputId"], "idle")

    def test_infrastructure_failures_exit_without_output_or_key(self):
        for status in (401, 500, 302):
            self.fake.routes = {("POST", "/Library/Refresh"): (status, None)}
            code, out, err = execute(JELLYFIN, [], invocation(self.manifest, "jellyfin-refresh-library", None,
                                     {"baseUrl": self.fake.url, "apiKey": "jf-secret-key"}, LIBRARY, "a.mkv"))
            self.assertEqual((code, out), (1, ""), status)
            self.assertNotIn("jf-secret-key", err)



class AuthoringToolsTests(unittest.TestCase):
    """Outils d'auteur : validation du manifeste, simulation locale, SDK JavaScript."""

    def load(self, *parts):
        with open(os.path.join(PLUGINS, *parts), encoding="utf-8") as handle:
            return json.load(handle)

    def test_shipped_manifests_validate(self):
        catalog = os.path.join(PLUGINS, "..", "src", "Encoding.Api", "Flows", "Catalog")
        for name in ("sonarr", "radarr", "plex", "jellyfin"):
            self.assertEqual([], sdk.validate_manifest(self.load("..", "src", "Encoding.Api", "Flows", "Catalog", name + ".json")), name)
        self.assertEqual([], sdk.validate_manifest(self.load("example", "manifest.json")))
        self.assertEqual([], sdk.validate_manifest(self.load("example-js", "manifest.json")))
        self.assertTrue(os.path.isdir(catalog))

    def test_validation_names_each_problem(self):
        manifest = self.load("example", "manifest.json")
        manifest["id"] = "../evil"
        manifest["version"] = "latest"
        manifest["components"][0]["outputs"] = [{"id": "a", "label": "A"}, {"id": "a", "label": "B", "description": "d"}]
        manifest["components"][0]["configurationSchema"]["fields"][0]["type"] = "telepathy"
        manifest["components"][1]["timeoutSeconds"] = 0
        manifest["components"][1]["mediaEffect"] = "disableIntegrity"
        manifest["components"][1]["category"] = "miscellaneous"
        problems = " | ".join(sdk.validate_manifest(manifest))
        for expected in ("id :", "version :", "dupliqué", "n'a pas de description", "telepathy", "timeoutSeconds", "mediaEffect", "catégorie fonctionnelle"):
            self.assertIn(expected, problems)
        self.assertEqual(["Le manifeste doit être un objet JSON."], sdk.validate_manifest([]))

    def test_validate_command_exit_codes(self):
        good = subprocess.run([sys.executable, "-B", os.path.join(PLUGINS, "sdk", "encoding_plugin_sdk.py"), "validate", os.path.join(PLUGINS, "example", "manifest.json")], capture_output=True, text=True)
        self.assertEqual(0, good.returncode, good.stdout)
        missing = subprocess.run([sys.executable, "-B", os.path.join(PLUGINS, "sdk", "encoding_plugin_sdk.py"), "validate", "/nonexistent.json"], capture_output=True, text=True)
        self.assertEqual(1, missing.returncode)

    def test_simulate_runs_a_component_without_a_cluster(self):
        code, out, err = run_process(EXAMPLE, ["--simulate", "example-tag-file", '{"name":"n"}', "--media", "Show/e1.mkv"], "")
        self.assertEqual(0, code, err)
        response = json.loads(out)
        self.assertEqual("success", response["outputId"])
        self.assertEqual({"n": "e1.mkv"}, response["variableChanges"])
        code, _, err = run_process(EXAMPLE, ["--simulate", "nope"], "")
        self.assertEqual(1, code)
        self.assertIn("absent du manifeste", err)

    @unittest.skipUnless(shutil.which("node"), "Node.js absent")
    def test_javascript_sdk_reports_candidates_and_validates_media_effects(self):
        script = """
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
const sdkUrl = pathToFileURL(process.argv[1]);
process.argv[1] = 'sdk-contract-test';
const { Plugin, Outcome, Context, validateManifest } = await import(sdkUrl);
const manifest = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const plugin = new Plugin(manifest);
for (const component of manifest.components) plugin.component(component.id, () => Outcome.output(component.outputs[0].id, '', {}, 'result.mkv'));
const result = await plugin.simulate(manifest.components[0].id);
const context = new Context({ context: { currentMetadata: { streams: [] } } });
manifest.components[0].mediaEffect = 'disableIntegrity';
manifest.components[0].category = 'miscellaneous';
console.log(JSON.stringify({ candidate: result.candidateArtifactId, metadata: context.currentMetadata,
  current: context.currentFile, problems: validateManifest(manifest) }));
"""
        process = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script,
                                  os.path.join(PLUGINS, "sdk", "encoding-plugin-sdk.mjs"),
                                  os.path.join(PLUGINS, "example-js", "manifest.json")], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, process.returncode, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual("result.mkv", result["candidate"])
        self.assertEqual({"streams": []}, result["metadata"])
        self.assertIsNone(result["current"])
        self.assertIn("mediaEffect", " | ".join(result["problems"]))
        self.assertIn("catégorie fonctionnelle", " | ".join(result["problems"]))

    @unittest.skipUnless(shutil.which("node"), "Node.js absent")
    def test_javascript_sdk_speaks_the_same_protocol(self):
        script = os.path.join(PLUGINS, "example-js", "plugin.mjs")
        manifest = self.load("example-js", "manifest.json")
        node = shutil.which("node")
        process = subprocess.run([node, script], input='{"type":"describe","contractVersion":1}\n', capture_output=True, text=True, timeout=60)
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertEqual("example-js", json.loads(process.stdout)["id"])
        inv = invocation(manifest, "examplejs-tag-file", config={"name": "t"}, media_path="A/b.mkv")
        process = subprocess.run([node, script], input=json.dumps({"type": "execute", "contractVersion": 1, "invocation": inv}) + "\n", capture_output=True, text=True, timeout=60)
        self.assertEqual(0, process.returncode, process.stderr)
        response = json.loads(process.stdout)
        self.assertEqual((inv["invocationId"], 3, "success", {"t": "b.mkv"}), (response["invocationId"], response["leaseEpoch"], response["outputId"], response["variableChanges"]))
        bad = dict(inv, component=dict(inv["component"], pluginId="autre"))
        process = subprocess.run([node, script], input=json.dumps({"type": "execute", "contractVersion": 1, "invocation": bad}) + "\n", capture_output=True, text=True, timeout=60)
        self.assertEqual(1, process.returncode)
        self.assertEqual("", process.stdout)
        process = subprocess.run([node, os.path.join(PLUGINS, "sdk", "encoding-plugin-sdk.mjs"), "validate", os.path.join(PLUGINS, "example-js", "manifest.json")], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, process.returncode, process.stdout)
        process = subprocess.run([node, script, "--simulate", "examplejs-name-matches", '{"text":"b"}', "--media", "A/b.mkv"], capture_output=True, text=True, timeout=60)
        self.assertEqual("yes", json.loads(process.stdout)["outputId"], process.stderr)

if __name__ == "__main__":
    unittest.main()
