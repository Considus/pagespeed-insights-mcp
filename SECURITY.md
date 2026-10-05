# Security

## Reporting a vulnerability

Please report security issues **privately**, and don't open a public issue or a pull request.

Email **security@considus.com**, or, if you'd rather keep it on GitHub, use the **Report a vulnerability** button on this repository's [Security tab](https://github.com/Considus/pagespeed-insights-mcp/security), which opens GitHub's private reporting. GitHub asks you to sign in first, and only you and I can see the report.

Tell me what you found, how to reproduce it, which version or commit it affects, and what it lets an attacker do. A proof of concept helps, but a clear description is plenty.

I'll acknowledge your report within **3 business days** and keep you posted while I look into it. This is coordinated disclosure, so please give me a reasonable amount of time to ship a fix before you make it public. You're welcome to the credit once it's out, or to stay anonymous, whichever you'd prefer.

The same terms cover every Considus project and both websites, catchlight.app and considus.com, and the policy page for this project is at [considus.com/security](https://considus.com/security/).

## What is in scope

`pagespeed_insights/`, `setup.py` and `mcp_server.py`.

Worth reporting:

- An API key reaching anywhere other than the user's config directory, whether that is stdout,
  a log, a tool result, an error message, the setup page, or a URL recorded
  somewhere.
- A way to make the setup server accept a request without the session token, or
  to reach it from another machine.
- A URL or argument that causes a request to somewhere other than the Google
  APIs this tool is documented to call.
- Anything that writes outside the config directory.
- A crafted API response that escapes into a shell, a file path, or the
  JSON-RPC stream.

Out of scope: the behaviour of Google's APIs themselves, quota exhaustion on a
key you control, and the fact that an API key stored on disk is readable by
anything already running as that user. That last one is a deliberate trade,
explained in `pagespeed_insights/config.py`, the credential is read-only access
to public measurements of public pages, and the alternative costs every user a
dependency.

## Threat model, briefly

This tool holds one low-value credential and talks to two Google APIs over
HTTPS. It never fetches the pages it measures, Google does that, so hostile
page content never reaches this machine. The API responses it does parse are
treated as untrusted input and never interpolated into a shell command, a file
path, or a raw stdout write.

The setup server binds to `127.0.0.1` only, on a random port, behind a
single-session token compared with `hmac.compare_digest`, and shuts down after
the form is submitted or after fifteen idle minutes.

## Safe harbour

You won't face legal action from me or from Considus for research done in good faith, so long as you avoid violating anyone's privacy, avoid destroying data, and follow this policy.

## Supported versions

One active line of development on `main`. Fixes land there, and there are no
separately maintained older releases to back-port to.
