# Adopted dependency licenses

This example does not publish an image. The built image retains the distributions' supplied
license files. Its dependencies are isolated from WattsOn's runtime dependencies.

| Component | Pinned/resolved version | License and primary provenance |
| --- | --- | --- |
| dbt-mcp | 2.10.1 | Apache-2.0, [release LICENSE](https://github.com/dbt-labs/dbt-mcp/blob/v2.10.1/LICENSE) |
| dbt-duckdb | 1.11.1 | Apache-2.0, [PyPI metadata](https://pypi.org/project/dbt-duckdb/1.11.1/) |
| dbt-core | 1.12.6 | Apache-2.0, installed distribution `License-Expression` |
| duckdb | 2.0.0 | MIT, installed distribution classifier/license file |
| mcp Python SDK | 1.30.0 | MIT, installed distribution `License-Expression` |
| dbt-sl-sdk | 0.13.4 | Apache-2.0, installed `dbt_sl_sdk-0.13.4.dist-info/licenses/LICENSE` (metadata omits it) |
| uv | 0.12.23 | MIT OR Apache-2.0, [upstream](https://github.com/astral-sh/uv/tree/0.12.23) |
| Python base | 3.14-slim | PSF-2.0 for Python; Debian OS components have their own licenses, retained in the image |

[dependency-licenses.json](dependency-licenses.json) records metadata from all 102 installed Python
distributions in the Linux aarch64 proof image, not a hand-picked subset. The universal lock also
contains Windows-only `pywin32` and platform-dependent `tzdata`, which were not installed on this host. Long license texts are truncated in this inventory; full files remain installed. Exact
package sources, versions and artifact hashes are recorded in `uv.lock`.

## Unresolved upstream metadata

`dbtlabs-vortex==0.2.1` is a mandatory dbt-mcp dependency used for upstream usage tracking (disabled
in this example). Its installed wheel includes no license file or license declaration, and
[its PyPI metadata](https://pypi.org/project/dbtlabs-vortex/0.2.1/) declares no license. The linked
upstream project is `dbt-labs/vortex`. **Its redistribution license has not been established.**
`uuid==1.30` likewise has no installed license file/declaration and its
[PyPI metadata](https://pypi.org/project/uuid/1.30/) says `UNKNOWN`; its redistribution license also
has not been established. These are inherited upstream dependencies, not claims of permissive
licensing. Review/resolve these two licenses before distributing an image. This integration does
not silently remove or replace upstream dependencies to hide that gap.
