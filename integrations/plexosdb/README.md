# PLEXOS XML in a container

[PLEXOSdb](https://github.com/NatLabRockies/plexosdb) imports PLEXOS XML into
in-memory SQLite. This optional integration packages the real upstream library
and prints an inventory from its database. It does not contain or run the
commercial PLEXOS engine, solve a network, or evaluate referenced CSV files.
It adds no dependencies to WattsOn's runtime.

You need **Podman and just** on the host. Python, uv and PLEXOSdb live inside the
image. Run these commands from this directory:

```bash
just build                     # Build the isolated, uv-locked image
just proof                     # Import/export a licensed upstream fixture offline
just inspect /absolute/model-directory model.xml  # Mount that directory read-only
# Save the JSON inventory on the host, if wanted:
just inspect /absolute/model-directory model.xml > inventory.json
```

The input directory is mounted at `/input:ro`. The image runs as UID 65532 with
no capabilities, a read-only root filesystem, no network, and a 256 MiB `/tmp`
tmpfs. Inputs must be readable by that UID; mount only the directory the job
needs. On an SELinux host, arrange an appropriate container file label on a
**dedicated copy** of the input directory before mounting. The recipes do not
relabel client files. The only normal output is JSON on stdout; upstream library
logs may appear on stderr. The image contains the public proof fixture, never
client data. Its build context excludes everything except enumerated source,
lock and fixture files.

Default run limits are one CPU and 1 GiB RAM; builds are capped at 1 GiB RAM.
For larger trusted inputs, deliberately set `PLEXOSDB_MEMORY=4g` and/or
`PLEXOSDB_CPUS=2`. XML is parsed into memory, so this small example is not a
streaming importer. A killed process or rejected XML fails the command rather
than supplying an invented inventory. The upstream library offers many mutation
APIs; this wrapper exposes only a fixed inventory query. Normal input inspection
never exports or writes a model.

## What the proof establishes

`just proof` imports the unchanged, 522-byte upstream
[`numeric_object_names.xml`](https://github.com/NatLabRockies/plexosdb/blob/73e7fabe5e9f951231b6120c139cec1e52d12463/tests/data/numeric_object_names.xml)
using PLEXOSdb 1.7.0. It checks the source SHA-256 against
[the provenance manifest](fixtures/manifest.json), checks the two generator names
`113_316_1` and `1133161` against actual database query results, exports to tmpfs,
and imports that export again. It also verifies the `/input` mount's kernel
read-only flag, rejects a write descriptor, and checks the original hash again.
An unexpected result exits nonzero. The fixture is a small public regression
input, not an operational power-system model.

The container proof passed on this host (Linux arm64, Podman), with
`objects: 2`, `classes: {"Generator": 2}`, and `proof: "PASS"`.
Client models, large inputs, other architectures, and an actual Azure account
have not been validated. The fixture and upstream license are retained in
[fixtures](fixtures); see [LICENSES.md](LICENSES.md) for dependency licenses.

## Optional Azure Blob input

Azure CLI **downloads blobs; it does not FUSE-mount Blob Storage**. This workflow
stages exactly one XML blob into a named Podman volume, then mounts that volume
read-only into the same offline inspector. No privileged container, FUSE device,
cloud key, or host Azure installation is needed.

```bash
just az-login                  # Interactive device-code login; token cache in its own volume
just az-stage ACCOUNT CONTAINER 'path/to/model.xml'  # One blob into wattson-plexos-input
just az-inspect                # Same inspector; input volume read-only, no Azure credentials/network
```

Use an identity permitted to read the chosen account/container (typically
**Storage Blob Data Reader**). Login occurs only when you invoke `az-login`.
The dedicated `wattson-plexos-az-auth` volume holds credentials: protect it as a
secret, never commit or share it. `az-stage` uses `--auth-mode login`, receives
that volume separately, and prints neither token values nor blob metadata.
The inspector never receives the credential volume.

Staging is intentionally bounded: one blob of at most **256 MiB**, one download
connection, 1 GiB RAM, one CPU, and a **600-second container lifetime**. The
300-second Azure request timeout is separate from that lifetime. Metadata and
download are bound by ETag so an input revision cannot change between the size
check and download. A partial download is not published as `model.xml`; an
existing completed input is never replaced. Stage once per dataset volume;
a failed/interrupted staging may require removing the disposable input volume
before retrying. This downloads only the XML: related CSVs, solution ZIPs and
other model assets require their own deliberate staging process.

The official Azure Linux CLI image (2.92.0) is pinned by digest independently
from the query image. Its local `az version`, download flags and shell tools
were verified without credentials. `AZ_CLI_IMAGE` and `PLEXOSDB_IMAGE` can select reviewed replacement images.
Azure access, tenant policies, device-code permissions and account-specific
RBAC remain unvalidated until run with your identity. No live cloud account is
needed for `just proof`.

```bash
# When finished, remove staging data and credentials explicitly:
podman volume rm wattson-plexos-input
podman volume rm wattson-plexos-az-auth
```

Microsoft documents the
[official CLI container](https://learn.microsoft.com/en-us/cli/azure/run-azure-cli-docker)
and [`az storage blob download`](https://learn.microsoft.com/en-us/cli/azure/storage/blob#az-storage-blob-download).
The separate staging process keeps Azure's network and identity requirements
outside the offline model reader.

## Updating

Update `pyproject.toml`, run `uv lock` **in this directory**, then rebuild and
rerun the proof. The independent lock pins all transitive packages and hashes.
Base Python and uv images are pinned by digest; update those deliberately too.
The lock's Python requirement is 3.14, matching WattsOn. Changes to upstream
versions or fixtures must include renewed provenance and expected results.
