# Encoding Store

Public, static catalogue for Encoding plugins and shared media tool packages.

The application downloads archives listed in [`repository.json`](repository.json). Plugin and tool versions are immutable. Every archive has a SHA-256 digest; tool manifests also declare the SHA-256 digest of each executable.

## Package layout

- `plugins/<id>/<version>.zip`: plugin archive with `plugin.json` at its root.
- Tool archives are attached to versioned GitHub releases because platform binaries can be large. The catalogue records their release URL, platform, and archive SHA-256.

The catalogue starts empty. Packages will be added as they are migrated and their artifacts are ready. See the application repository's `backend/plugins/README.md` for the package contracts and authoring examples.

## Trust

The hashes detect changed or incomplete artifacts; they do not identify the publisher. Configure this repository as an official source only when its account and release permissions are controlled by the project owner. Signing and provenance attestations are future work.
