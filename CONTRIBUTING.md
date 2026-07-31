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

## Proposing a change

Open a pull request against `main`. Say what it changes and why. If it touches
the parsing of an API response, include the shape of the response you tested
against. The two bugs found so far were both silent, producing a plausible
number rather than an error.

## Found a security hole?

Please do not open a public issue. There is a private path in
[SECURITY.md](SECURITY.md).
