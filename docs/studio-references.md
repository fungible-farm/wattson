# Studio references: pinned commits and licences (#26)

What we borrow ideas from and what we could reuse, before building the studio-equivalent (#25). Every row below comes from a command whose output is quoted. Surveyed 2026-11-16 from shallow clones in `/tmp/dojo/refs` (read-only) and public APIs. Nothing here was run or installed.

This is an engineering record, not legal advice. Licence names come from the repos' own files and metadata; a partner who redistributes images should have counsel read the full texts.

## Pinned commits and licences

| Project | Pinned commit | Commit date | Licence | Command that produced the row |
|---|---|---|---|---|
| Open Notebook | `5e285b7a2e435895c4ee99ad7e4b8d2af64c3239` | 2026-11-03 | MIT (`Copyright (c) 2024 Luis Novo`) | `git -C open-notebook rev-parse HEAD`; `head -3 open-notebook/LICENSE` |
| Eigent | `4958c94bd91faf4e0bfff270a971d4d1810fb6db` | 2026-11-02 | Apache-2.0 (`Copyright 2026 @ Eigent.AI`) | `git -C eigent rev-parse HEAD`; `head -3 eigent/LICENSE` shows the Apache License 2.0 header |
| Portable AI Studio | `a55c42c028f44dd1d08e75ce81a28d53d92ab24d` | 2026-11-05 | MIT (`Copyright (c) 2026 Tech Jarves`) | `git -C Portable-Local-Studio rev-parse HEAD`; `head -3 Portable-Local-Studio/LICENSE` |
| llama.cpp | release `b11418` (what Portable AI Studio pins); repo head seen `de32b268c4ad55d45f3b7b16c029f4e077f4ba74` | n/a | MIT | `cat Portable-Local-Studio/scripts/config/llm-backends.json` gives `"llamaRelease": "b11418"`; `gh api repos/ggml-org/llama.cpp --jq .license.spdx_id` gives `MIT` |
| stable-diffusion.cpp | release `master-685-19bdfe2` (Portable AI Studio's `SD_RELEASE`); repo head seen `532d2c15c430a785a712b2d94281f04e733008a4` | n/a | MIT | `grep SD_RELEASE= scripts/setup/setup.sh`; `gh api repos/leejet/stable-diffusion.cpp --jq .license.spdx_id` gives `MIT` |
| whisper.cpp | release `v1.9.1` (Portable AI Studio's `WHISPER_RELEASE`); repo head seen `5922450fbce1ed9d1648babd0d5ac4520055b436` | n/a | MIT | `grep RELEASE= scripts/setup/setup-whisper.sh`; `gh api repos/ggml-org/whisper.cpp --jq .license.spdx_id` gives `MIT` |
| kokoro-js (the Kokoro runtime Portable AI Studio installs) | npm `1.2.1` (`"kokoro-js":"^1.2.1"` in `scripts/setup/setup-tts.sh`) | n/a | Apache-2.0 | `curl -s https://registry.npmjs.org/kokoro-js/latest` gives `kokoro-js 1.2.1 Apache-2.0` |
| Kokoro-82M weights | HF `hexgrad/Kokoro-82M` sha `f3ff3571791e39611d31c381e3a41a3af07b4987` | n/a | apache-2.0 (model card) | `curl -s https://huggingface.co/api/models/hexgrad/Kokoro-82M` |
| Kokoro-82M ONNX weights | HF `onnx-community/Kokoro-82M-v1.0-ONNX` sha `1939ad2a8e416c0acfeecc08a694d14ef25f2231` | n/a | apache-2.0 (model card) | `curl -s https://huggingface.co/api/models/onnx-community/Kokoro-82M-v1.0-ONNX` |

Two differences between what the studio pins and what is current, so nobody reads the head SHAs as the pins: whisper.cpp's latest release is `v1.9.5` against the studio's `v1.9.1`, and stable-diffusion.cpp's latest is `master-1104-5dcfa79` against the studio's `master-685-19bdfe2` (`gh api repos/<repo>/releases/latest --jq .tag_name`). The README for Portable AI Studio says of the engines: "Bundles stable-diffusion.cpp (MIT License). Model weights are subject to their respective creators' licenses." (`sed -n 365,367p Portable-Local-Studio/README.md`).

## What each configures, and where

| | Open Notebook | Eigent | Portable AI Studio |
|---|---|---|---|
| Models | Provider and model are DB records via the `esperanto` library; `openai_compatible` takes a custom `base_url` (`api/credentials_service.py`). | CAMEL `ModelFactory.create(...)` with `model_platform`, `model_type`, `api_key`, `api_url` (`backend/app/agent/agent_model.py`). | Weights under `app/models`, `app/llm-models`, `app/speech-models`, `app/tts-models`; selection is a field of `POST /api/llm/start`. |
| Tools | None found in the app. | `MCPToolkit` plus about 40 toolkits registered by name (`backend/app/agent/tools.py`). | None; no MCP in the source. |
| Skills | None found. | `SKILL.md` files (`backend/app/service/skill_service.py`: `SKILL_FILE = "SKILL.md"`). | None. |
| Prompts | `prompts/` with 9 Jinja templates. | All system prompts in `backend/app/agent/prompt.py`. | One editable default system prompt in the UI. |
| Launch | Compose: SurrealDB plus app; UI 8502 and REST 5055. | Electron spawns a FastAPI backend on 5001, loopback by default. | One Node file, `scripts/server/serve.cjs`, bootstrapped by `linux.sh`, `mac.sh`, `windows.bat`. |

These rows were checked by a read-only survey of the clones and spot-checked by the coordinator on #26; the quoted file names are where to look. Open Notebook's prompt template purposes are inferred from names (files not opened). Eigent's route-level auth is unconfirmed. Portable AI Studio's management API request schemas are unverified.

## Third-party notices and redistribution

What I found, and what I did not look at:

- **Notice files in the repos.** `gh api repos/ggml-org/llama.cpp/contents/licenses --jq '.[].name'` lists one file, `LICENSE-jsonhpp`. llama.cpp also has `LICENSE` and `AUTHORS` at its root. stable-diffusion.cpp has a root `LICENSE` and no other notice file at its root. Eigent's `licenses/` directory holds only licence-header templates (`license_template_py.txt`, `license_template_ts.txt`, `update_license.js`), not third-party notices. Open Notebook has no notice file at its root (`ls open-notebook | grep -i -E "notice|third"` printed nothing).
- **The studio's own tree.** Its README names only stable-diffusion.cpp as bundled. It fetches llama.cpp, whisper.cpp, Kokoro and Node at first run (`scripts/setup/*.sh`), so it ships none of them itself; our images would, and then need their notices.
- **Dependency licences are not surveyed.** Open Notebook (Python and Next.js), Eigent (Python and Electron), kokoro-js's dependencies (`phonemizer`, `@huggingface/transformers`) and every transitive package were not audited. A redistributed image carries all of them. This is the open item that could block redistribution.
- **Model weights are separate from engine licences.** Kokoro's two weight sets above are apache-2.0 on their cards. The weights the studio's catalogue offers for chat, image and speech (hard-coded Hugging Face GGUF URLs; whisper models) were not checked and each has its own licence. For the same reason the LFM Open License question on `docs/tool-selection-plan.md` stays separate.
- **Nothing found that blocks reuse of the three studio-type projects' own code.** MIT and Apache-2.0 both allow redistribution with the licence text and copyright notice kept; Apache-2.0 also asks for changed files to be marked and a NOTICE file to be carried if the project has one (Eigent has none at the root). That is the reading of the licence text, not a legal opinion.

## What this means for the studio plan

- Reuse of code is permitted for all three projects and all four engines under MIT or Apache-2.0, subject to carrying notices. The ideas to borrow (see #25) do not need reuse.
- The unresolved piece is a dependency and weight licence audit for any image we ship. It is a separate task from this survey and is not started.
