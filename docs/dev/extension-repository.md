# Extension repository

The extension can be served from a [dynamic extensions repository](https://docs.blender.org/manual/en/latest/advanced/extensions/creating_repository/dynamic_repository.html):
Blender asks the server for a listing and passes `blender_version` and `platform`, and the server answers with only the
newest compatible version of each extension.

## Run the server

```
uv run python scripts/build.py          # builds dist/zonal-<ver>.zip
uv run python scripts/serve_repo.py     # http://127.0.0.1:8000/api/v1/extensions/
```

| Option | Meaning |
| --- | --- |
| `--token SECRET` | Require `Authorization: Bearer SECRET` (Blender's *access token* field) |
| `--host 0.0.0.0 --port 8080` | Make it reachable from other machines |
| `--dir PATH` | Folder with the zips (default `dist/`) |
| `--no-generate` | Reuse the existing `dist/index.json` |
| `--blender PATH` | Blender used for `extension server-generate` |

On start the script runs `blender --command extension server-generate --repo-dir dist`, which writes `dist/index.json`
(the same listing format as a static repository). Rebuild the zip and restart to publish a new version.

## Add it to Blender

*Preferences > Get Extensions > Repositories > + > Add Remote Repository*, URL
`http://127.0.0.1:8000/api/v1/extensions/`, tick *Use Access Token* and paste the token if you set one.

## Check it by hand

```
curl -H "Authorization: Bearer SECRET" "http://127.0.0.1:8000/api/v1/extensions/?blender_version=5.0.0&platform=windows-x64"
```

Blender 4.5 (older than `blender_version_min = 5.0.0`) or another platform gets an empty `data` list.

!!! note
    The Blender manual recommends a [static repository](https://docs.blender.org/manual/en/latest/advanced/extensions/creating_repository/static_repository.html)
    for small or personal repositories: `dist/index.json` plus the zip on any web server is enough. The dynamic server
    only pays off with several versions of several extensions. Each per-platform zip is 60-110 MB because it bundles the OpenStudio wheels.

## Static repository for publishing

`scripts/make_repo.py` builds a static repository in `repo/` (zip, `index.json`, `index.html`) after validating the manifest:

```
uv run python scripts/make_repo.py --base-url https://github.com/OWNER/REPO/releases/download/v0.1.0
```

`--base-url` rewrites `archive_url` to absolute Release URLs, so the zips (one per platform, built with `--split-platforms`)
and `index.json` can all live on the same GitHub Release. The tag-triggered workflow `.github/workflows/release.yml` does
this automatically and attaches both. The repository URL for Blender is then
`https://github.com/OWNER/REPO/releases/latest/download/index.json`: GitHub redirects `latest` to the newest release, and each
`index.json` points at the zips of its own tag. GitHub Pages is not used for the listing (it hosts the docs only).

!!! note
    `latest` ignores draft and pre-release releases; don't tick *pre-release* on the GitHub Release.
