# Third-party model and software licence notes

This repository's own code is under `LICENSE-APACHE` and `LICENSE-MIT`. Models and tools it can download carry their own licences.
They are not redistributed here; the pinned revision and the checksum are in the recipe that fetches them.

| Component | Where it is used | Licence | Consequence |
|---|---|---|---|
| `jinaai/jina-embeddings-v4-text-retrieval-GGUF` (Q4_K_M, revision `6c6ba828f6ad`) | `just embed2048-up`, the 2048-dimension embedder for the Auto Ontology spike (#20) | **Qwen Research License** (`LICENSE` in `jinaai/jina-embeddings-v4`; derived from `Qwen2.5-VL-3B-Instruct`). It defines "Non-Commercial" use for research and education. | **Non-commercial spike use only.** Owner decision 2026-11-06: fine for the spike, licence to be noted here. Do not ship it in a product or a commercial service without a licence from the rights holder; read the full licence text first, this note is not a legal summary. |
| NVIDIA Auto Ontology (pinned commit, `just ao-fetch`) | Auto Ontology pod (#13, #20) | Apache-2.0 (its own code); some dependencies carry other licences (psycopg LGPL, mysql-connector GPL-2.0 with FOSS exception, tqdm MPL-2.0): the owner's decision is open on #13 | Built from source at the pinned commit; nothing is redistributed. |
| `Qwen/Qwen3-Embedding-4B-GGUF`, `nomic-embed-text-v1.5`, `bge-reranker-v2-m3` | candidate / current retrieval models | Apache-2.0 (Qwen3-Embedding), Apache-2.0 (nomic-embed-text-v1.5), MIT (bge-reranker-v2-m3): as I recall their model cards; NOT re-verified in this session, check each card before relying on it | |
| sigstore `cosign`, KitOps `kit` | `just cosign-install`, `just kit-install`, the `wattson-kit` image | Apache-2.0 | Binaries are downloaded and checksum-verified, not redistributed. |
