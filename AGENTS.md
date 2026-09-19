# Agent notes

For a coding agent working in this repo. `CONTRIBUTING.md` is the human document and it is not duplicated here; read it first for the ground rules and the release order. This file is the part an agent needs and a person already knows.

Every task moves through four beats: isolate on a branch, build, prove with evidence, ship a PR carrying that evidence.

## Isolate

Branch from `origin/main` by name, never from wherever `HEAD` is sitting, and check nobody is already on the work:

```bash
git fetch origin
gh pr list -R Considus/pagespeed-insights-mcp
git checkout -b <type>/<short-name> origin/main
```

If an open PR touches the same files, build on it or say so and stop. Stage by explicit path. Never `git add -A`.

## Build

**Read `CONTRIBUTING.md` before writing.** Three things there are constraints rather than preferences, and a change that breaks one is rejected however good the rest is.

- **Standard library only**, with no optional-dependency escape hatch. This is what lets someone who does not use a terminal download the package and run it, and it is why the service-account credential was dropped for an API key.
- **No number without its uncertainty.** Every figure is a median across distinct analyses with its min-max spread beside it, and runs PSI served from cache are dropped and counted.
- **Lab and field stay separate.** Lighthouse is a simulation, CrUX is what real people got. They disagree by an order of magnitude and merging them destroys the only honest thing here.

**Python 3.9 is the floor.** CI runs 3.9 and 3.12 on Linux, macOS and Windows. A 3.10-only construct (`match`, a runtime-evaluated `X | Y` annotation, `zip(strict=)`, `itertools.pairwise`, `tomllib`, `dataclass(slots=)`) passes on your interpreter and fails only in the 3.9 leg. Windows is in the matrix on purpose: being installable there without a compiled dependency is a property that would regress silently.

**`manifest.json` is generated.** The version lives in `pagespeed_insights/__init__.py` and the tool list lives in the package. `./build-mcpb.py --sync` writes both across. Never hand-edit the manifest. Every tool needs a `title` and the right `readOnlyHint` or `destructiveHint`; a directory submission is rejected without them, and `report` is the one tool that is not read-only because it writes a file.

Extract shared logic only when two callers need it. One caller is a layer for nothing.

## Prove

```bash
python3 -m compileall -q pagespeed_insights setup.py mcp_server.py tests
python3 -m unittest discover -s tests -v
./build-mcpb.py --check
```

No test needs an API key or a network connection. A PR needs all of that green, and the test **count** is the thing to read, not the word "passed" — a collection error reports zero failures.

Against the real API, point the config directory somewhere scratch first so you cannot overwrite the saved key:

```bash
PAGESPEED_CONFIG_DIR=/tmp/psi-test PAGESPEED_API_KEY=... \
  python3 -m pagespeed_insights --runs 2 https://example.com/
```

Never commit a key and never paste one into an issue or a PR.

Capture the **before** while you are still reproducing the problem, which is when it is cheapest, and the **after** once the change works. For this repo that is usually a pair of command outputs rather than a screenshot.

**What cannot be checked locally:** the Windows and Linux CI legs, the 3.9 leg unless you have 3.9 installed, anything about how a client renders the tool list, and whether the MCP Registry accepts a listing (`mcp-publisher validate` is the only thing that answers that before a release is cut).

## Ship

Open the PR with the evidence in the body: what changed, how it was tested, the risks. The title and body take no house standard (owner, 2026-09-19): git mechanics are not read as writing, even here where the repo is public. The documents in this repo are a different matter, and `.claude/rules/writing-public-copy.md` governs them.

**Greptile costs a credit and the account has 30 a month.** A review runs only on a PR carrying the `greptile` label, set in `.greptile/config.json`. Label a change to behaviour. Leave a docs fix, a version bump or a dependency-free tidy unlabelled. Do not run a loop that re-reviews until it scores 5/5; each pass is another credit.

Present the PR URL and stop. Merging is a separate decision.

## Releasing

`CONTRIBUTING.md` has the order and the order matters, because the archive is not reproducible and a rebuild after stamping leaves `server.json` pointing at a hash no published file has. The one thing worth repeating here: **nothing happens on its own.** The MCP Registry does not watch this repo, its tags or its releases. Skip the publish step and the registry keeps describing the previous bundle, silently, for as long as you leave it.
