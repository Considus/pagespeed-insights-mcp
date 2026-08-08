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

## Releasing

The order matters, because the archive carries timestamps. The build is not
reproducible, a second build of identical sources hashes differently, and a
rebuild after stamping leaves `server.json` pointing at a hash no published file
has. A client reads that as a corrupted download rather than as a mistake in the
listing.

**Nothing here happens on its own.** The MCP Registry does not watch this repo,
its tags or its releases. Cut a release without step 7 and the registry carries
on describing the previous bundle, silently and for as long as you leave it.

1. **Bump the version in `pagespeed_insights/__init__.py`**, not in
   `manifest.json`, which is generated from it. Then `./build-mcpb.py --sync`.
2. **Check the listing before going further.** The registry rejects a
   `description` over 100 characters, and it does so at publish time, long after
   a release has been cut. `mcp-publisher validate` catches it and publishes
   nothing.
3. **Open a PR and merge it.** Everything below assumes `main` is final.
4. **Tag `main` once the listing is settled, not before.** A tag cut earlier
   points at a commit whose `manifest.json` disagrees with the copy inside the
   bundle you are about to ship.
5. **Build once.** `./build-mcpb.py` packs
   `dist/pagespeed-insights-mcp-<version>.mcpb` and stamps its SHA-256 into
   `server.json`. Do not build again after this.
6. **Cut the release with that exact file.**

   ```bash
   gh release create v<version> dist/pagespeed-insights-mcp-<version>.mcpb \
     -R Considus/pagespeed-insights-mcp
   ```

7. **Publish, logging in immediately first.** The registry JWT expires quickly
   enough that a login from earlier in the same sitting will fail.

   ```bash
   SEED=$(openssl pkey -in <key.pem> -outform DER | tail -c 32 | xxd -p -c 64)
   mcp-publisher login dns --domain considus.com --private-key "$SEED"
   mcp-publisher publish
   ```

   The signing key is the only proof of the `com.considus` namespace. Its public
   half is the `v=MCPv1` TXT record on considus.com, so the key can be checked
   against DNS rather than taken on trust.

8. **Verify what was published, not what you built.** Download the release
   asset, hash it, and compare against `fileSha256` in `server.json`.
9. **Deprecate the version this replaces.**

   ```bash
   mcp-publisher status --status deprecated --message "Superseded by <version>." \
     com.considus/pagespeed-insights-mcp <old-version>
   ```

   Read the status back from
   `/v0/servers/com.considus%2Fpagespeed-insights-mcp/versions`. The `?search=`
   listing reports every version as active regardless, and will tell you the
   change failed when it did not.

10. **Rebuild the website if this README changed.** considus.com's product pages
    are generated from it, by `build-product-pages.py` in `Considus-Ops`.

## Proposing a change

Open a pull request against `main`. Say what it changes and why. If it touches
the parsing of an API response, include the shape of the response you tested
against. The two bugs found so far were both silent, producing a plausible
number rather than an error.

## Found a security hole?

Please do not open a public issue. There is a private path in
[SECURITY.md](SECURITY.md).
