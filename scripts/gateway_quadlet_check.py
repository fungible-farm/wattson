#!/usr/bin/env python3
"""Reboot survival for the gateway (#24), checked without installing anything.

Renders gateway/wattson-gateway.container (image and port from the justfile defaults, this checkout's path), asks podman's
user generator for a dry run, and asserts the generated systemd service runs the same container as `just gateway-up`:
the digest-pinned image, loopback-only publish, read-only root, dropped capabilities, the pasta host-loopback map, the
config directory mounted read-only, restart-on-failure, and enablement from default.target (what makes it start at boot).
The generator output is the observation; no unit is written to ~/.config and nothing is started.
Usage: python3 scripts/gateway_quadlet_check.py [--print]   (--print: print the rendered Quadlet and the install commands)
"""
import os
import re
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATOR = "/usr/lib/systemd/user-generators/podman-user-generator"


def justfile_default(name):
    text = open(os.path.join(REPO, "justfile")).read()
    m = re.search(rf'^{name}\s*:=\s*env\("[A-Z_]+",\s*"([^"]+)"\)', text, re.M)
    if not m:
        raise SystemExit(f"FAIL gateway-quadlet: could not read {name} from the justfile")
    return m.group(1)


def render():
    text = open(os.path.join(REPO, "gateway", "wattson-gateway.container")).read()
    return (text.replace("@IMAGE@", justfile_default("gw_image")).replace("@PORT@", justfile_default("gw_port"))
            .replace("@REPO@", REPO))


def main():
    rendered = render()
    if "--print" in sys.argv:
        print(rendered)
        print("# Install (you run these; nothing here does):")
        print("#   mkdir -p ~/.config/containers/systemd && cp <the file above> ~/.config/containers/systemd/wattson-gateway.container")
        print("#   systemctl --user daemon-reload && systemctl --user start wattson-gateway.service")
        print("# Stop the hand-started one first: just gateway-down. Linger must be on: loginctl show-user $USER -p Linger")
        return
    if not os.path.exists(GENERATOR):
        raise SystemExit("FAIL gateway-quadlet: podman user generator missing (podman >= 4.4 with Quadlet needed)")
    with tempfile.TemporaryDirectory(prefix="ql-") as d:
        with open(os.path.join(d, "wattson-gateway.container"), "w") as f:
            f.write(rendered)
        out = subprocess.run([GENERATOR, "--dryrun"], capture_output=True, text=True,
                             env={**os.environ, "QUADLET_UNIT_DIRS": d})
    service = out.stdout
    print(f"generator: rc={out.returncode} bytes={len(service)}")
    image = justfile_default("gw_image")
    port = justfile_default("gw_port")
    wants = {
        "service generated": "wattson-gateway.service" in service,
        "digest-pinned image": image in service,
        "loopback-only publish": f"127.0.0.1:{port}:4000" in service,
        "read-only root": "--read-only" in service,
        "capabilities dropped": "--cap-drop all" in service or "--cap-drop=all" in service,
        "pasta host-loopback map": "pasta:--map-host-loopback,169.254.1.2" in service,
        "config dir mounted ro": f"{REPO}/gateway:/etc/agentgateway:ro" in service,
        "restart on failure": "Restart=on-failure" in service,
        "enabled from default.target": "WantedBy=default.target" in service,
    }
    for name, ok in wants.items():
        print(f"{'ok  ' if ok else 'MISS'} {name}")
    if out.returncode or not all(wants.values()):
        print(service[-1500:] + out.stderr[-500:])
        print("FAIL gateway-quadlet: the generated service does not match gateway-up")
        sys.exit(1)
    print("PASS gateway-quadlet: the Quadlet generates a systemd service equivalent to gateway-up, enabled from default.target")


if __name__ == "__main__":
    main()
