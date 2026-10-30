# Encoding Store

Public static catalogue for Encoding plugins and shared media tool packages.

The application downloads versioned archives listed in [`repository.json`](repository.json). Published versions are immutable. The index includes a SHA-256 for each archive; tool manifests also include SHA-256 hashes for every executable.

## Layout

- `packages/<id>/<version>/<version>.zip`: the exact archive served to Encoding nodes.
- `plugins/<id>/`: maintained source, manifests, SDK, and tests for process plugins.
- `schemas/`: package manifest schemas.

Process plugins run in a separate Landlock confined process. Native media component packages contain only declarative manifests; their implementations remain in the Encoding engine and are restricted to its allowlisted component families. No package is preinstalled by the engine.

Current process integrations are Plex, Sonarr, Radarr, and Jellyfin. Native component manifests for base flows, node placement, conditions, video, audio, subtitles, MKV, images, probing, and AI are also published here. Linux x64 packages are published for FFmpeg/FFprobe, MKVToolNix, MediaInfo, and ImageMagick, built from Ubuntu 24.04 Noble amd64 packages. Their source package versions and Debian archive hashes are recorded in [`tools/ubuntu-noble-lock.json`](tools/ubuntu-noble-lock.json). The engine image retains the compatible runtime libraries, while the executable packages are installed from this Store. Other platforms are not published yet.

Run the plugin source checks with:

```sh
python3 -m unittest discover -s plugins/tests -v
```

## Trust

Hashes detect changed or incomplete artifacts; they do not identify the publisher. Configure this repository as the official source only when its account and release permissions are controlled by the project owner. Signing and provenance attestations are future work.
