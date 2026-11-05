# Diagrams

`ecosystem.d2` is the source of the picture in the README; `ecosystem.svg` is rendered from it and committed, so the README
draws anywhere. To re-render, you need a Kroki server. [kr0ki](https://github.com/promptexecution/kr0ki) is our sibling project in
the PromptExecution ecosystem and runs one for you (beta):

```bash
git clone https://github.com/promptexecution/kr0ki && (cd kr0ki && just dev-kroki-up)   # x86_64 only for now: kr0ki#150
WATTSON_KROKI_URL=http://127.0.0.1:8010 just diagrams                                    # rewrites docs/diagrams/*.svg
```

On an arm64 machine, run Kroki on an x86_64 peer and set `WATTSON_KROKI_URL` to it. Any Kroki works. The diagram text is sent only
to the server you choose. Use `.d2` (or `.dot`) sources: Mermaid needs a Kroki companion service that kr0ki does not run.

Diagrams are text in git, so a change to the pod, a recipe or an allowed tool shows up as a reviewable diff. A later step can
generate the picture from `kube/wattson.yaml` itself (kr0ki already renders Kubernetes topology), so the diagram and the
real pod cannot drift apart.
