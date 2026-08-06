#!/usr/bin/env python3
"""Build the MCPB bundle.

An MCPB bundle is what the MCP Registry and the Connectors Directory can
actually list. Clone-and-run is fine for developers and is not a package type,
so without this the server is locked out of both.

    ./build-mcpb.py --check     verify the manifest agrees with the server
    ./build-mcpb.py --sync      copy the tool list and version across
    ./build-mcpb.py             build dist/pagespeed-insights-mcp-<version>.mcpb

The tool list and the version live in the package. This script copies them into
manifest.json and refuses to build if the two have drifted, so the file a
reviewer reads cannot quietly stop describing the server they are reviewing.

Releasing, and the order matters. Build once, upload the artifact that build
produced, then publish server.json from the same run. The archive carries
timestamps so the build is not reproducible, and a second build of identical
sources hashes differently. A rebuild after stamping leaves server.json
pointing at a hash no published file has, which clients read as a corrupted
download rather than as a mistake in the listing.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "manifest.json")
REGISTRY = os.path.join(HERE, "server.json")
DIST = os.path.join(HERE, "dist")
MCPB_CLI = ["npx", "-y", "@anthropic-ai/mcpb@2.1.2"]

# What travels. setup.py is deliberately absent: a bundle is configured through
# the manifest's user_config. Unlike the Proton server, setup.py here writes to
# the platform config directory rather than beside the server, so a key saved
# by a cloned copy is found by a bundle install too — they share one settings
# file. The CLI, the tests and the skill are not part of the server.
INCLUDE = ["manifest.json", "mcp_server.py", "README.md", "LICENSE", "NOTICE"]

# brand.py resolves assets as a sibling of the package, and reads the icon and
# the four woff2 faces at run time to inline them into the HTML report. A
# missing face degrades to the system stack rather than failing, so leaving
# these out would not break anything loudly, it would just quietly produce
# reports that stopped looking like the product. OFL.txt travels because the
# font licence requires it to.
INCLUDE_TREES = ["pagespeed_insights", "assets"]

# Nothing to vendor. The package is standard library only, which is the whole
# reason it installs without a virtual environment.
VENDOR = []

_PRUNE = ("__pycache__", ".pyc", ".pyo", ".DS_Store")


def _server():
    sys.path.insert(0, HERE)
    from pagespeed_insights import mcp, __version__
    return mcp, __version__


def check(fix=False):
    """True if manifest.json agrees with the package."""
    mcp, version = _server()
    tools = [{"name": d["name"], "description": d["description"]}
             for d in mcp.TOOL_DEFS]
    with open(MANIFEST, encoding="utf-8") as f:
        man = json.load(f)

    problems = []
    if man.get("tools") != tools:
        problems.append("tools: manifest lists %d, server exposes %d"
                        % (len(man.get("tools") or []), len(tools)))
    if man.get("version") != version:
        problems.append("version: manifest %s, package %s"
                        % (man.get("version"), version))

    if problems and fix:
        man["tools"] = tools
        man["version"] = version
        with open(MANIFEST, "w", encoding="utf-8") as f:
            json.dump(man, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print("manifest.json updated from the package")
        return True

    for p in problems:
        print("drift: " + p, file=sys.stderr)
    return not problems


def _prune(root):
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE]
        for name in filenames:
            if name.endswith(_PRUNE) or name in _PRUNE:
                os.remove(os.path.join(dirpath, name))


def build():
    if not check():
        print("\nRun ./build-mcpb.py --sync to copy the tool list and version "
              "into manifest.json.", file=sys.stderr)
        return 1

    with open(MANIFEST, encoding="utf-8") as f:
        version = json.load(f)["version"]

    stage = tempfile.mkdtemp(prefix="mcpb-")
    try:
        for rel in INCLUDE:
            shutil.copy2(os.path.join(HERE, rel), os.path.join(stage, rel))
        for rel in INCLUDE_TREES:
            shutil.copytree(os.path.join(HERE, rel), os.path.join(stage, rel))
        _prune(stage)

        os.makedirs(DIST, exist_ok=True)
        out = os.path.join(DIST, "pagespeed-insights-mcp-%s.mcpb" % version)
        subprocess.run(MCPB_CLI + ["pack", stage, out], check=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    with open(out, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()

    _stamp_registry(version, os.path.basename(out), digest)

    print("\n%s" % out)
    print("  size        %.1f KB" % (os.path.getsize(out) / 1024.0))
    print("  fileSha256  %s" % digest)
    print("\nserver.json now carries that hash. Clients check it before they")
    print("install, so the release asset has to be this exact file.")
    print("\nThe build is not reproducible, the archive carries timestamps, so")
    print("a rebuild produces a different hash from identical sources. Upload")
    print("the file above, do not rebuild between stamping and uploading, and")
    print("publish server.json from the same build that made the artifact.")
    return 0


def _stamp_registry(version, filename, digest):
    """Put the hash of the file we just built into server.json.

    Doing this by hand is how a registry entry ends up pointing at a hash that
    was true one build ago, and a client that validates the hash then refuses
    to install with nothing to say about why."""
    with open(REGISTRY, encoding="utf-8") as f:
        reg = json.load(f)
    reg["version"] = version
    pkg = reg["packages"][0]
    pkg["identifier"] = (
        "https://github.com/Considus/pagespeed-insights-mcp/releases/download/v%s/%s"
        % (version, filename))
    pkg["fileSha256"] = digest
    with open(REGISTRY, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--check":
        sys.exit(0 if check() else 1)
    if arg == "--sync":
        sys.exit(0 if check(fix=True) else 1)
    sys.exit(build())
