# Security

## Reporting a vulnerability

Please do not open a public issue for anything exploitable. Use GitHub's private
reporting: the **Security** tab, then **Report a vulnerability**. That opens a
private advisory visible only to you and the maintainer.

Include what you found, how to reproduce it, and what it lets an attacker do.
A proof of concept helps, a clear description is enough. You will get an
acknowledgement within a few days and an update while it is being fixed, and
credit on the fix if you want it.

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

## Supported versions

One active line of development on `main`. Fixes land there, and there are no
separately maintained older releases to back-port to.
