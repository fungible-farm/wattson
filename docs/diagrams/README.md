# Diagrams

`ecosystem.mmd` is the source of the picture in the README. GitHub draws it directly. To get a stand-alone SVG
(for slides or a report), render it through a local Kroki, which is a beta feature from the kr0ki project:

```bash
(cd ~/promptexecution/kr0ki && just dev-kroki-up)    # once; listens on 127.0.0.1:8010
just diagrams                                        # writes docs/diagrams/ecosystem.svg
```

Diagrams are text in git, so a change to the pod, a recipe or an allowed tool shows up as a reviewable diff. A later step can
generate the picture from `kube/wattson.yaml` itself (kr0ki already renders Kubernetes topology), so the diagram and the
real pod cannot drift apart.
