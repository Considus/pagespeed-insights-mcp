# Contributing

## Ground rules

Three things this project holds to. A change that fits them is worth proposing,
one that does not will be turned down however well written it is.

**Standard library only.** Nothing in `pagespeed_insights/` imports from outside
Python's stdlib, and there is no optional-dependency escape hatch either. This
is what makes the package installable by people who do not use a terminal,
download it and run it, no virtual environment, no pip, no compiled wheel. It is
the reason the service-account credential was dropped in favour of an API key,
and it is not up for trade.

**No number without its uncertainty.** A single Lighthouse run is noise. Every
figure this tool reports is a median across distinct analyses with its min-max
spread beside it, and runs that PSI served from cache are dropped and counted.
Anything that would present one run as a measurement is out of scope by design.

**Lab and field stay separate.** Lighthouse is a simulation, the Chrome UX
Report is what real people experienced. They routinely disagree by an order of
magnitude, and merging them into one figure would destroy the only honest thing
here.

Beyond those: small diffs. The smallest change that fixes the thing, rather than
a rewrite alongside it. If you spot something else, say so separately.

## Running the tests

None of them need an API key or a network connection.

```bash
python3 -m unittest discover -s tests -v
```

CI runs the same tests on Python 3.9 and 3.12 across Linux, macOS and Windows,
plus a compile check. A pull request needs those green.

Windows is in the matrix on purpose. Being installable there without a compiled
dependency is a property worth protecting, and it would regress silently
otherwise.

## Testing against the real APIs

Set `PAGESPEED_CONFIG_DIR` to a scratch directory first, so you cannot overwrite
your own saved key:

```bash
PAGESPEED_CONFIG_DIR=/tmp/psi-test PAGESPEED_API_KEY=... python3 -m pagespeed_insights --runs 2 https://example.com/
```

Never commit a key, and never paste one into an issue or a pull request.

## Building the bundle

`manifest.json` is what the MCP Registry and the Connectors Directory list, and it has to keep describing the server. The tool list and the version live in the package, so the build copies them across and refuses to run if the two have drifted.

```bash
./build-mcpb.py --check
```

`--sync` writes the tool list and version into `manifest.json` from the package, which is what you want after adding or renaming a tool. With no argument it checks, packs `dist/pagespeed-insights-mcp-<version>.mcpb`, and stamps the artifact's SHA-256 into `server.json`.

The bundle carries `assets/` as well as the package. `brand.py` resolves that folder as a sibling of `pagespeed_insights` and reads the icon and the four woff2 faces at run time to inline them into the HTML report. A missing face falls back to the system stack rather than failing, so leaving them out would not break anything loudly, it would just quietly produce reports that stopped looking like the product. `OFL.txt` travels because the font licence requires it to.

`setup.py` is not in the bundle, because a bundle is configured through the manifest's `user_config`. Unlike the Proton server, setup here writes to the platform config directory rather than beside the server, so a key saved by a cloned copy is picked up by a bundle install too. They share one settings file.

Every tool needs a title and the right `readOnlyHint` or `destructiveHint`, and a directory submission is rejected without them. `report` is the one that is not read-only, because it writes a file when given a directory or a filename. Tests cover this.

Releasing, and the order matters. Build once, upload the artifact that build produced, then publish `server.json` from the same run. The archive carries timestamps, so the build is not reproducible and a second build of identical sources hashes differently. Rebuild after stamping and `server.json` points at a hash no published file has, which a client reads as a corrupted download rather than a mistake in the listing.

## Proposing a change

Open a pull request against `main`. Say what it changes and why. If it touches
the parsing of an API response, include the shape of the response you tested
against. The two bugs found so far were both silent, producing a plausible
number rather than an error.

## Found a security hole?

Please do not open a public issue. There is a private path in
[SECURITY.md](SECURITY.md).
