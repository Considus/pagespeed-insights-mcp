---
name: reading-pagespeed
description: How to read what the PageSpeed Insights MCP returns without overclaiming. Use whenever answering a question about site speed, Core Web Vitals, Lighthouse scores, or what to fix on a page, and whenever the pagespeed-insights MCP tools are called. Covers when a change is real, why savings must not be added together, why lab and field figures differ, and what to do when a site has no real-user data.
---

# Reading PageSpeed results

The MCP server refuses to state a number without its uncertainty. It cannot
stop an assistant discarding that uncertainty on the way to an answer, which is
the failure this skill exists to prevent.

Every rule below came from measuring, and the measurements are quoted so they
can be checked rather than believed.

## The one that matters most: a change smaller than the spread is not a change

Every score and metric comes back as a median with the range across distinct
analyses. That range is the instrument moving on a page that did not change.

Measured on one unchanged page: performance ran 27 to 37 across three analyses,
Total Blocking Time 824ms to 3.05s, and estimated savings for one fix ranged
1450 to 2750ms.

So:

- Do not report "performance improved from 33 to 36". That is inside the noise.
- Do not compare today's median against yesterday's median without both
  spreads. Two medians that differ by less than the ranges overlap tell you
  nothing.
- If asked whether something got better, say what the spreads were and whether
  they overlap. If they overlap, the honest answer is "no measurable change",
  not a hedged yes.

## Never add savings together

Findings carry Google's estimate of what each fix returns, per metric. **These
do not compose.** Two fixes claiming 6750ms and 1200ms off LCP do not return
7950ms. They overlap, and Lighthouse models no interaction between them.

- Never sum them into a total.
- Never promise a resulting score. There is no way to get from a saving to a
  score, because the curve is not linear.
- The ordering is the useful part. Say "fix this first", not "this will save
  you nine seconds".

## Lab and field answer different questions

Two sets of numbers come back and they routinely disagree by an order of
magnitude. On one real site the lab reported a 21-second Largest Contentful
Paint and a score of 27, while real visitors were getting the page in 1.2
seconds. Both were true.

- **Lab** is a simulation on deliberately slow hardware. It is repeatable and
  it works on a page nobody has visited. It is not what your visitors
  experienced.
- **Field**, from the Chrome UX Report, is what actually happened to real
  Chrome users over 28 days. It is the only one Google ranks on.

Never average them, never present one as a correction of the other, and always
say which one a number came from.

## No field data is normal

Google only publishes field data for sites with enough traffic to keep visitors
anonymous. Most sites have none.

- This is not a fault in the site or the tool.
- Do not treat a good lab score as evidence about real visitors when field data
  is absent. Say plainly that there is none.
- Do not suggest the user "enable" it. There is nothing to enable, it is a
  traffic threshold.

## Some findings are not the reader's to fix

The tool marks findings that are not changes to the page. On one real site the
single largest fault was a DNS redirect, and the next three were third-party
scripts.

When the biggest finding is off the page, say so and say who it belongs to:
hosting, DNS, or whoever owns the embedded script. Offering a code change for a
DNS problem wastes the reader's afternoon.

## What the tool cannot tell you

Be straight about this when asked "how do I get to 100":

- Savings do not compose, so there is no route to a target score.
- Several fixes are hosting or business decisions rather than code.
- The score curve is not linear, so the last ten points cost far more than the
  first ten.
- Lighthouse reports symptoms of an architecture, not the architecture.

The tool produces a prioritised list of real faults with what each is roughly
worth. That is genuinely most of the work. It is not a plan to a number, and
saying otherwise sets up a failure the reader will discover later.

## Which tool to call

- **`report`** for "how is my site" or "what should I fix". Everything in one
  call, and it returns an HTML page. Save that page to a file when the user
  might forward it, which is often, because the findings frequently belong to
  someone else.
- **`diagnose_page`** when the scores are already known and only the faults are
  wanted.
- **`check_pagespeed`** for scores alone.
- **`field_data`** for real users only, with `history` for six months of weekly
  figures.
- **`diagnose`** when anything looks wrong. It separates a configuration
  problem from a slow page in about two seconds.

These take minutes, not seconds, because Google only re-analyses a page about
once a minute and replays a cached result in between. That is the tool
collecting genuinely distinct measurements. Do not offer to speed it up by
reducing `runs` below the default and then quote the result as though it were
the same thing.

## Phrases to avoid

- "improved by N points" without both spreads
- "this will save X seconds" from summed estimates
- "your site is slow" when only lab data says so and field data disagrees
- "you should enable CrUX"
- "fix these and you will reach 90"
