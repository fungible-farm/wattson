# Dependency and fixture licenses

This integration's own code follows the repository's MIT OR Apache-2.0 terms.
No PLEXOS solver or commercial model data is included. PLEXOS is Energy
Exemplar's trademark; this integration does not imply endorsement.

The public fixture is copied unchanged from NatLabRockies/plexosdb commit
`73e7fabe5e9f951231b6120c139cec1e52d12463`, path
`tests/data/numeric_object_names.xml`, under its repository's **BSD-3-Clause**
license. [The original notice and full license](fixtures/LICENSE.txt) are
retained; [manifest.json](fixtures/manifest.json) records source and checksum.

Installed package license identifiers below were checked against the locked
wheel distribution metadata in the built image. Package license texts remain
in their installed `*.dist-info` directories. The lock is the complete version
and artifact-hash register. `pre-commit` and its dependencies are upstream
PLEXOSdb runtime requirements, retained without patching upstream metadata.

| Locked dependency | License |
|---|---|
| plexosdb 1.7.0 | BSD-3-Clause |
| plexos2duckdb 0.1.0b16 | BSD-3-Clause |
| duckdb 2.0.0 | MIT |
| loguru 0.7.3 | MIT |
| pre-commit 4.7.0 | MIT |
| cfgv 3.5.0 | MIT |
| identify 2.6.20 | MIT |
| nodeenv 1.12.0 | BSD (metadata); BSD-3-Clause in bundled license |
| PyYAML 6.0.3 | MIT |
| virtualenv 21.15.5 | MIT |
| distlib 0.4.4 | PSF-2.0 |
| filelock 4.2.9 | MIT |
| platformdirs 4.14.5 | MIT |
| python-discovery 1.9.4 | MIT |
| packaging 26.3 | Apache-2.0 OR BSD-2-Clause |
| rich 15.0.0 | MIT |
| markdown-it-py 4.2.0 | MIT |
| mdurl 0.1.2 | MIT |
| Pygments 2.21.0 | BSD-2-Clause |

Container components:

- CPython: PSF-2.0; Debian base OS packages retain their individual licenses.
- uv 0.12.23: MIT OR Apache-2.0.
- Optional Microsoft Azure CLI: MIT; its official Azure Linux image contains
  independently licensed OS/Python packages and is a separately pulled artifact.

Container image digests are recorded in the Containerfile and justfile rather
than treating mutable tags as immutable versions. No image is published by
these recipes.
