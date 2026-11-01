# Encoding Store

Public static catalogue for Encoding plugins and shared media tool packages.

The application downloads versioned archives listed in [`repository.json`](repository.json). Published versions are immutable. The index includes a SHA-256 for each archive; tool manifests also include SHA-256 hashes for every executable.

## Layout

- `packages/<id>/<version>/<version>.zip`: the exact archive served to Encoding nodes.
- `plugins/<id>/`: maintained source, manifests, SDK, and tests for process plugins.
- `plugins/packages.json`: contents and index metadata of each plugin package (source of truth for the build).
- `schemas/`: JSON schemas for the index and package manifests.
- `scripts/store.py`: reproducible package build and full catalogue verification.
- `docs/plugin-contract.md`: process plugin contract; see also [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`CHANGELOG.md`](CHANGELOG.md).

Process plugins run in a separate Landlock confined process. Native media component packages contain only declarative manifests; their implementations remain in the Encoding engine and are restricted to its allowlisted component families. No package is preinstalled by the engine.

Current process integrations are Plex, Sonarr, Radarr, and Jellyfin. Native component manifests for base flows, node placement, conditions, video 1.3.2, audio 1.1.2, subtitles 1.3.2, MKV 1.1.2, images 1.0.2, probing 1.1.2, and AI 1.0.2 are also published here. Linux x64 packages are published for FFmpeg/FFprobe, MKVToolNix, MediaInfo, and ImageMagick, built from Ubuntu 24.04 Noble amd64 packages. Their source package versions and Debian archive hashes are recorded in [`tools/ubuntu-noble-lock.json`](tools/ubuntu-noble-lock.json). The engine image retains the compatible runtime libraries, while the executable packages are installed from this Store. Other platforms are not published yet.

Run the checks (the same as CI; requires `pip install fastjsonschema ruff`) with:

```sh
ruff check .
python3 scripts/store.py verify
python3 -m unittest discover -s scripts/tests -v
python3 -m unittest discover -s plugins/tests -v
```

`verify` checks every archive hash, the package manifests against the schemas, that each published archive matches its sources, and that no archive is orphaned. CI also rejects any change to an already published archive.

## Trust

Hashes detect changed or incomplete artifacts; they do not identify the publisher. Configure this repository as the official source only when its account and release permissions are controlled by the project owner. Tagged releases (`vYYYY.MM.DD`) publish `SHA256SUMS` and a Sigstore build provenance attestation for `repository.json` and every archive (`gh attestation verify <file> --repo remi-deher/encoding-store`). Download URLs still follow `main` for existing versions; new versions can be pinned to a tag with `store.py build --ref`.
