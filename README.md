# PageSpeed Insights MCP

Measures a web page with Google PageSpeed Insights and reports the median of
several runs with the spread beside it, so a number arrives with its
uncertainty. Works from an assistant that speaks MCP, or from a terminal.

Standard library only. No virtual environment, no `pip`, no compiled
dependency. Clone it and run it.

## Why another one

There are already several PageSpeed MCP servers. As far as we can tell, every
one of them only runs Lighthouse once and hands the model the number.

A single Lighthouse run is noise. Total Blocking Time routinely swings
threefold between runs on a page that has not changed, and the headline
performance score is a weighted blend that inherits every bit of that. Ask once
and you get a figure with no error bars, and no way to tell a real regression
from the instrument moving. That is not a small imprecision, it is the
difference between measuring something and guessing at it confidently.

There is a second problem underneath, and it is worse because it is invisible.
PageSpeed caches its result per URL and replays it. Ask five times and you can
be handed one analysis five times, which quietly turns a median into a vote for
whatever Google happened to have cached. Minutes after a deploy on 30 July 2026
that is exactly what happened here, 2 of 3 runs came back with a byte-identical
timestamp from *before* the deploy, dragging the average back to the pre-fix
score while the one fresh run showed the fix working. A tool that reports one
run would have reported the stale number as current, with nothing to indicate
anything was wrong.

This one takes the median of 5 runs by default, prints the min-max spread next
to every figure, drops runs that were cached replays, and tells you how many it
dropped.

## The lab is not your users

Both of these are true, measured on the same page on the same afternoon.

| | LCP | Verdict |
|---|---|---|
| Lighthouse lab, mobile | 21.36 s | performance **27 / 100** |
| Real Chrome users, 28-day p75 | 1.20 s | **FAST** |

That is the BBC home page. The lab says the site is catastrophically slow. Real
people are getting it in 1.2 seconds.

Neither number is wrong, they answer different questions. Lighthouse is a
simulation on throttled hardware, useful because it is repeatable and you can
run it against a page nobody has visited yet. Field data is what actually
happened to real Chrome users, and it is the only thing Google ranks on. A tool
that shows you one and not the other is misleading you, whichever one it picks.

So this reports both, separately, and never merges them into a single figure.

## What it does

- Median of N **distinct** analyses, with the min-max spread beside every score
  and metric.
- Keeps asking until it has N genuinely different analyses, because Google
  re-analyses a URL about once a minute and replays the cached result to
  everything that asks in between. Five requests in a row is one measurement
  five times over.
- Says what that cost in calls and seconds, and reports fewer analyses honestly
  rather than padding the count when time runs out.
- Real-user data from the Chrome UX Report, current and up to 6 months of
  weekly history, when Google has it.
- Says plainly when Google has none, rather than letting a lab score stand in
  for evidence about real visitors.
- Tells an exhausted quota apart from a broken page, and a misconfigured key
  apart from both.

## Things to actually ask it

Four that exercise different parts of it, and none need you to know a tool name.

**"How fast is considus.com on mobile, and how confident are you in that
number?"** The whole reason this exists. You get the median of several genuinely
distinct analyses and the spread around it, not one run quoted as fact.

**"Is there real user data for this site, or only lab numbers?"** Reads the
Chrome UX Report, which is the only evidence here about actual visitors and the
only thing Google ranks on. Plenty of small sites have none, and it says so
rather than letting a lab score stand in.

**"What's actually holding up the LCP on that page?"** Splits one number into
the four phases it is made of, so you find out whether it is the server, the
discovery, the download or the paint. Fast, one call, no averaging needed.

**"Did that change help?"** Records a baseline on the first call, then compares
after you have made the change. It only calls a difference real when the two
ranges do not overlap, which is what stops you celebrating the instrument
moving.

## Before you start

You need an API key from Google Cloud. It is free, it takes about a minute, and
there is one decision to make, at step 3.

1. Open [console.cloud.google.com](https://console.cloud.google.com/) and pick
   your account.
2. **APIs & Services**.
3. **API Library** in the sidebar. Search for **PageSpeed Insights API**,
   select it, press **Enable**.
   > **Decide here, while you are on this screen.** If you also want real-user
   > data, meaning what actual Chrome visitors experienced and how it has moved
   > over the last six months, search for **Chrome UX Report API** and enable
   > that as well before moving on. Same key, no second credential, one more
   > search on the screen you are already looking at. Skip it and everything
   > else still works, you just get lab measurements only. Adding it later means
   > coming back to this screen and to step 7, which is the only reason it is
   > worth deciding now.
4. **Credentials**, **from the sidebar, not from the page you are on**.
5. **Create credentials** at the top, then **API key**.
6. Give it a name.
7. Under **API restrictions**, tick **PageSpeed Insights API**, and tick
   **Chrome UX Report API** too if you enabled it at step 3.
8. Leave **Authenticate API calls through a service account** unticked.
9. Leave **Application restrictions** on **None**.
10. **Create**, then copy the key.

Step 4 is the one that defeats people, and it defeated me. There are two routes
to a credentials screen and only one of them offers an API key. Reach it from
the page you are already on and the button gives you OAuth clients and service
accounts, no API key anywhere, and you reasonably conclude PageSpeed does not
support them. It does. The sidebar entry is a different screen, and it sits just
below API Library.

Step 9 looks wrong and is not. An HTTP-referrer restriction is the safe-looking
choice on that form, and it makes the key unusable from a program like this
one, because there is no referring web page. The API restriction at step 7 is
the one that limits what the key can reach.

## Install

Whichever route you take, clone it somewhere permanent, a folder in your home
directory is right. Your assistant's config will point at that exact path, so a
folder that later moves is a connection that breaks. Not Downloads, not a temp
folder.

Both routes finish the same way. `setup.py` opens a small page in your browser,
served from your own machine on a random port behind a single-use link. It
shuts itself down when you are finished and it never writes down anything you
type. Paste the key in. It checks the key against Google before saving a thing,
and tells you whether real-user data is available.

Run it again any time. It notices you have set it up before, and a blank key
field means keep the one you have.

### Have an assistant do it

Paste this into an AI assistant that runs shell commands **on this computer**.
Read what it proposes before you let it run.

```
Please install the PageSpeed Insights MCP server from
https://github.com/Considus/pagespeed-insights-mcp on this computer, following the
Install section of its README exactly. Clone it into a permanent folder in my home
directory, then run setup.py and tell me the local link it prints so I can finish
setup in my browser. Show me each command before you run it.
```

### Or run the commands yourself

Python 3.9 or newer, which macOS and most Linux machines already have. On
Windows, install it from python.org or run `winget install Python.Python.3.12`.

```bash
git clone https://github.com/Considus/pagespeed-insights-mcp.git
cd pagespeed-insights-mcp
python3 setup.py
```

## Connect it to your assistant

When setup finishes it hands you a second prompt. Paste that into whichever
assistant you want measuring your pages, Claude, Cursor, Windsurf, Zed, Codex
CLI, VS Code Copilot, anything that speaks MCP. Restart the app afterwards, MCP
servers load at startup.

It works this way round on purpose. Every client keeps its MCP config somewhere
different, under a different key, and those locations move. An assistant already
knows where its own config lives, so asking it beats shipping a list of paths
that quietly rots. The prompt carries no key, only the name, the command and the
path.

### Tools

| Tool | What it does |
|---|---|
| `report` | Everything in one call, and the one most people want. Scores with their spread, real-user data if Google has any, and what is failing ranked by what fixing it is worth. Returns a self-contained HTML page as well, to save or forward. |
| `check_pagespeed` | Scores only. Median of N distinct analyses with the spread. `urls`, `strategy` (mobile, desktop or both), `runs` (1-10, default 5). |
| `diagnose_page` | What is failing, ranked. Only reports a fault that failed in every analysis, because audits are as noisy as scores. |
| `field_data` | Real-user data from the Chrome UX Report. `urls`, and `history` for the weekly p75 series. |
| `explain_lcp` | Which of four phases owns a slow Largest Contentful Paint: server response, the wait before the browser starts fetching the largest image, the download, then the wait before it is painted. One call, answers at once. |
| `compare` | Did the change actually help. Measures now and compares against a saved baseline, giving a verdict only where the two ranges do not overlap. |
| `diagnose` | Whether the key works, whether the Chrome UX Report is reachable, and which baselines are held, without disclosing the key. |

Two things about `explain_lcp` are worth knowing before you read one, and both
are printed in every answer. The four phases do **not** add up to the LCP, and
the gap is not rounding. Across twelve real origins the sum missed by anything
from 40ms under to 2.6 seconds over. Each phase is its own 75th percentile, and
percentiles do not add. And the phases are measured only over visits where the
largest element was an image, which on some sites is a small minority, so the
answer always says what share of visits it is describing.

That second point is the useful part as often as it is the caveat. On one large
site the LCP looks a comfortable 1.5 seconds, and the eighth of visits with an
image LCP are waiting 3.3 seconds before the image so much as starts
downloading. Nothing in the headline number shows that.

`compare` answers the question the findings leave open, which is whether the
change you made did anything. The first call on a URL records a baseline and
compares nothing, because there is nothing to compare against yet. Make the
change, call it again, and it measures afresh and reports what moved.

It gives a verdict only where the two min-max ranges do not overlap at all.
That is deliberately conservative and it will miss small real improvements. The
reason is that the two possible mistakes are not equally bad. Telling you
nothing moved when something did costs you a little confidence, while telling
you something improved when it was noise is a claim you might repeat to a
client. Where a change is real it reports both the difference in medians and
the smaller figure the ranges actually guarantee, and the guaranteed one is the
number to quote.

Baselines are held in `baselines.json` beside the settings, keyed by URL and
strategy, and are never moved unless you ask. Field data is deliberately not
compared, because a 28-day rolling window cannot show a change made this week.

### The skill

`skills/pagespeed-insights-read/` teaches an assistant how to read the results.
The server refuses to state a number without its uncertainty. It cannot stop an
assistant dropping that uncertainty on the way to an answer, and the most common
way that goes wrong is adding four savings estimates together and promising nine
seconds.

It is optional. Everything works without it, and the tools carry the same
warnings in their own descriptions. The skill is what stops those warnings being
paraphrased away on the journey to an answer.

Every client keeps skills somewhere different, some sync them from an account
rather than reading your disk, and some have no such thing at all. So rather than
printing one client's path, ask yours. Paste this in, with the folder path filled
in from wherever you cloned this.

```
I have a skill folder on this computer and I would like you to install it into
whichever client you are running inside, if that client supports skills.

  Skill folder = /path/to/pagespeed-insights-mcp/skills/pagespeed-insights-read

Please:
1. Work out whether this client supports skills, and where it keeps them on this
   machine.
2. Install the folder there, keeping its folder name unchanged. Most clients take
   the name of the folder as the name of the skill, so renaming it renames the
   skill.
3. Prefer linking to the folder above rather than copying it, if this client
   follows links, so it updates when I next pull. Copy it if not.
4. If this client syncs skills from an account rather than reading this disk, do
   not guess at a path. Tell me where to add it instead.
5. Tell me what you did and whether I need to restart the app.

If this client has no concept of skills, say so plainly and do nothing.
Everything works without it.
```

Setup prints the same prompt with your actual path already in it.

A 5-analysis check on 2 URLs takes several minutes, and asking harder will not
speed it up. Google re-analyses a URL roughly once a minute whatever you do, so
the time is spent waiting for genuinely new measurements rather than queuing
requests. The server sends a progress update each time a new analysis lands,
which is what stops a client giving up on it. If your assistant offers to use
`runs=1` to be quicker, the answer is no, that is the thing this exists to
stop.

Start with `diagnose` if anything looks wrong. It separates a configuration
problem from a slow page in about 2 seconds.

## From a terminal

From the folder you cloned into it runs as a module, `python3 -m
pagespeed_insights`. For a `pagespeed` command that works from anywhere, link
the launcher onto your PATH.

```bash
mkdir -p ~/.local/bin
ln -s "$PWD/pagespeed" ~/.local/bin/pagespeed
```

If `~/.local/bin` is not on your PATH, add `export PATH="$HOME/.local/bin:$PATH"`
to your shell profile. On Windows there is no symlink step, add the folder to
your PATH or keep using `python -m pagespeed_insights` from inside it.

Setup will show you that command but will not run it. It writes nothing outside
its own settings, and a tool that quietly drops executables into a PATH
directory is the thing that rule exists to prevent.

```bash
pagespeed https://example.com/
pagespeed --runs 3 --strategy both https://example.com/
pagespeed --field --history https://example.com/
pagespeed --lcp https://example.com/
pagespeed --findings https://example.com/
pagespeed --compare https://example.com/
pagespeed --baselines
pagespeed --field --report report.html https://example.com/
pagespeed --json https://example.com/
```

`--report` writes one self-contained HTML page with no scripts, no network and
nothing external. It opens from a file and survives being emailed, which matters because
the largest finding is often a hosting or third-party decision belonging to
somebody other than whoever ran the check.

Your assistant can save the same page. Ask it for a report and it will ask where
you want the file. The server writes only to a folder you name and only when you
ask, it will not create one, and it never overwrites an existing report, because
somebody comparing a before against an after wants both. Name no folder and it
goes to a `reports` folder beside the settings, and the full path comes back so
you can find it.

Saved files keep their embedded fonts and run about 150KB. The copy that comes
back in conversation drops them, because 145KB of base64 is most of a context
window spent on typography. Same renderer either way.

With no URL it uses whatever you saved during setup.

Exit codes are split by what went wrong, so something running this in CI can
tell a broken site apart from a bad afternoon at Google.

| Code | Meaning |
|---|---|
| 0 | fine |
| 1 | something else went wrong |
| 2 | bad arguments |
| 3 | quota exhausted, infrastructure rather than the site |
| 4 | credential rejected, configuration rather than the site |
| 5 | page unreachable, **this one is the site** |
| 6 | could not reach Google, network rather than the site |

Only 5 means the page is at fault. Failing a build on 3 or 6 is failing it
because Google was busy.

## When something goes wrong

Every failure carries the sentence that fixes it rather than a stack trace, and
most of them are not about your site at all. Knowing which is which saves you
changing something that was never broken.

**A 429 and no key.** You are on the shared anonymous pool, which everyone else
is also using and which is routinely spent by the middle of the day. It says
nothing whatsoever about the page you were measuring. Run setup and add a key.

**A 429 with a key.** Your own project quota is spent for the day. It resets at
midnight Pacific time.

**A 400 or 403 from PageSpeed Insights.** The key is missing, wrong, or carrying
an HTTP-referrer restriction. That last one is the usual answer, and it is step
9 of [Before you start](#before-you-start) coming back to bite, because a
program has no referring web page, so a key restricted that way is refused every
single time. Use API restrictions instead, or none. While you are on that
screen, check the PageSpeed Insights API is enabled on the project at all.

**No field data for the origin.** This is not a failure and the tool won't
report it as one. Google publishes real-user data only for origins with enough
traffic to stay anonymous, so a small, new or pre-launch site has none, and
won't until it has visitors. What's left is the lab, which is a simulation and
not evidence about anybody's real experience.

**The Chrome UX Report says it has not been used in this project.** Either the
API isn't enabled, or you enabled it a minute ago and it hasn't finished
propagating. Those two are indistinguishable from out here, and this one has
already been retried before you see it, so wait a couple of minutes and run it
again before changing anything.

**The Chrome UX Report refuses the key.** The key itself is fine, its API
restrictions just don't include Chrome UX Report API. Add it in the Google Cloud
console, under Credentials, editing the key's API restrictions.

**Lighthouse could not load the page.** Unlike everything above it, this one
really is about your site. Check the URL serves a 200 to an anonymous visitor,
with no login in the way and no geographic block.

**It could not reach Google at all.** Network, DNS or a timeout, already retried
with backoff by the time it says so.

When you can't tell which of these you have, `diagnose` answers it. Whether the
key works, whether the Chrome UX Report is reachable, and which baselines are
held, none of it disclosing the key.

## Updating

There's no package and no installer, so there's nothing to download. The server
runs as `mcp_server.py` out of the directory you cloned into, which makes an
update a pull and a restart.

Your key and your saved URLs aren't in that directory, they sit in
`settings.json` in your platform's config directory, so an update leaves them
alone and you won't be asked for the key again.

The restart is the part that catches people out. A stdio MCP server is a
long-running process, and it reads `mcp_server.py` once, when the app starts it.
Changing the file underneath a server that's already running does nothing at
all, so quit the app properly and open it again. Closing the window isn't enough
on macOS, and neither is closing the last tab on Windows if it leaves the app in
the tray.

Releases are tagged, and the releases page on GitHub says what changed in each
one. `pagespeed --version` tells you which one you're on, and your assistant can
read the same number out of the server's handshake. `git pull` puts you on the
latest `main`, which is sometimes ahead of the newest tag.

### Have an assistant do it

Paste this into an AI assistant that runs shell commands **on this computer**. It
can do the pull, but it can't restart the app it's running inside, so the last
step stays yours.

```
Please update my PageSpeed Insights MCP server. Find where it's installed by
reading the path out of this app's MCP config rather than guessing it, run git
pull in that folder, and tell me what changed. Don't run setup.py and don't edit
my settings.json, my key and saved URLs are already in it. Then remind me to quit
this app completely and open it again, because the server only reads
mcp_server.py at startup.
```

### Or run the commands yourself

In the folder you cloned into.

```bash
cd pagespeed-insights-mcp
git pull
```

## Where things live

Your key and your saved URLs go in `settings.json` in your platform's config
directory.

- macOS, `~/Library/Application Support/pagespeed-insights-mcp/`
- Linux, `~/.config/pagespeed-insights-mcp/`
- Windows, `%APPDATA%\pagespeed-insights-mcp\`

To add, change or remove those saved URLs, run `python3 setup.py` again from the
folder you cloned into. The box comes back with whatever you saved last time, so
edit the list and submit it. Empty it and you are back to naming a site every
time. Your key is kept unless you type a new one over it. There is no CLI flag
for this. The file is small and plain, so editing it by hand works just as well,
and deleting it removes the key from the machine.

On macOS and Linux `settings.json` is written owner-readable only. On Windows it is
not, because Windows has no POSIX file modes and the request is quietly
ignored. There the protection comes from `%APPDATA%` living inside your user
profile, which is restricted to you by default. That is a real protection, but
it is inherited rather than set by this tool, so it is worth knowing which one
you are relying on.

`PAGESPEED_CONFIG_DIR` moves that wherever you like, and `PAGESPEED_API_KEY`
overrides the stored key for anyone who would rather keep it in their own secret
manager.

The key is a file rather than an entry in your system keychain, and that is a
deliberate trade worth being straight about. Reaching the keychain portably
needs an installed package, an installed package needs a virtual environment and
a `pip` step, and that is the entire "clone it and run it" advantage gone for
the people who need it most. Weigh that against what the credential actually is.
A PageSpeed key, restricted as step 7 instructs, is read-only access to public
measurements of public web pages. It holds no personal data, unlocks no account,
and cannot be billed against, because the API is free. The worst a stolen one
does is spend a quota of 25,000 calls a day, and you fix that by deleting the
key. That is a nuisance, not a breach.

## What it will not do

It will not report a single run as a measurement. There is no flag for it and
there is not going to be.

It will not merge lab and field numbers into one figure. They disagree by an
order of magnitude on real sites, and averaging them would destroy the only
honest thing here.

It will not call a change a regression when the change is inside the spread.
The spread is printed so you can see for yourself, and a movement inside it is
not evidence of anything.

It does not touch Google Search Console. Search Console needs OAuth and
per-property authorisation, because it serves private data about a property you
own, where PageSpeed and the Chrome UX Report serve public data about public
pages that anyone may measure. That is a different kind of tool with a different
kind of credential, and bolting it on here would drag a consent flow into
something that currently needs one string.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

No API key and no network needed. CI runs them on Python 3.9 and 3.12 across
Linux, macOS and Windows.

Two of the tests pin bugs found while building this, both of which produced a
plausible number rather than an error. PageSpeed reports its embedded CLS
multiplied by 100, so a healthy 0.08 reads as 8.0 if you take it at face value,
which is a catastrophic score on the one metric where those two numbers are the
whole story. And the Chrome UX Report returns CLS as a string while every other
metric is a number, so the obvious comparison raises a TypeError. Neither
announces itself. In a measurement tool, a wrong number that looks right is the
only kind of bug that matters.

## Privacy Policy

This one is not local-only, and pretending otherwise would be the wrong thing to write. Measuring a page means asking Google to measure it, so the address you check goes to Google. That is the tool working rather than a leak, but it is worth stating first rather than buried.

### What leaves this machine, and where it goes

The URL you are measuring, sent to the PageSpeed Insights API, and to the Chrome UX Report API when you ask for field data. Your API key travels with those requests if you have one. Nothing else. Google then loads that page from their own infrastructure and measures it, so the page owner sees a visit from Google rather than from you.

Google's handling of those requests is Google's, governed by their privacy policy and their API terms, and it is outside anything Considus can promise for them.

### What does not leave

There is no Considus server in this. No account, no telemetry, no crash reporting, no usage counting, and no licence check. We never see what you measured, when, or that you ran it at all. There is nothing on our side to see it from.

### What it writes, and where

`settings.json` holds your API key and any URLs you saved, in your platform's config directory, and `PAGESPEED_CONFIG_DIR` moves it wherever you like.

Baselines, so a later run can tell you whether a change is real or noise, sit beside it.

HTML reports go to the reports folder next to those settings, or to a folder you named in the conversation. Nothing writes to a folder that does not already exist, and nothing is ever overwritten.

All of it is on your own disk and all of it is yours to delete. Deleting `settings.json` removes the key from the machine.

### About the key being in a file

On macOS and Linux it is written owner-readable only. On Windows that request is ignored, because Windows has no POSIX file modes, and the protection there comes from `%APPDATA%` sitting inside your user profile instead. That is real, and it is inherited rather than set by this tool, which is a weaker promise and worth knowing.

It is a file rather than a keychain entry because reaching the keychain portably needs an installed package, which needs a virtual environment and a `pip` step, which is the whole "clone it and run it" advantage gone. Weigh that against what the credential is. A PageSpeed key restricted as the setup instructs is read-only access to public measurements of public pages. It holds no personal data, unlocks no account, and cannot be billed against, because the API is free. The worst a stolen one does is spend a quota you can reset by deleting the key. Anyone who disagrees with that trade sets `PAGESPEED_API_KEY` from their own secret manager and no key is stored at all.

### Retention, and who else gets it

We hold nothing, so there is nothing for us to keep, and nothing for us to delete on your behalf. Nothing is shared with anyone, because nothing reaches us to share. Google is the only third party involved, and only for the measurement requests above.

### If you want to ask about any of this

Write to privacy@considus.com, or open an issue at [github.com/Considus/pagespeed-insights-mcp/issues](https://github.com/Considus/pagespeed-insights-mcp/issues). The policy covering considus.com itself is at [considus.com/privacy](https://considus.com/privacy/), and it is a separate document because it covers a separate thing.

## Support

This is free and stays that way. Apache 2.0 means you can take it, build on it,
and ship it commercially without owing anything back, which is deliberate.

Something broken or a number you don't believe,
[open an issue](https://github.com/Considus/pagespeed-insights-mcp/issues).
Anything exploitable goes through GitHub's private reporting instead, described
in [SECURITY.md](SECURITY.md), rather than a public issue. For anything that
fits neither, including press and licensing, it's <support@considus.com>, and
the other ways to reach us are at
[considus.com/support](https://considus.com/support/).

If it stopped you chasing a regression that was never there, then consider
[buying me a coffee](https://buymeacoffee.com/considus). If it didn't, telling
me what it got wrong is worth more than the coffee, and a bug report about a
number that looked plausible and wasn't is the most useful thing anyone can
send.

## Licence

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Unofficial community project. Not affiliated with, endorsed by, or sponsored by
Google LLC. It calls two public Google APIs, bundles no Google code, and
redistributes no Google data.
