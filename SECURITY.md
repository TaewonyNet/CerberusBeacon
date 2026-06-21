# Security Policy

## Supported Versions

Only the latest release is actively maintained.

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report via GitHub Issues: https://github.com/TaewonyNet/CerberusBeacon/issues

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

You will receive a response within 72 hours. If the issue is confirmed, a patch will be released as soon as possible.

## Security Model

| Mechanism | Details |
|-----------|---------|
| Authentication | TOTP (RFC 6238), SHA-1, 30s step, 6 digits |
| Brute-force | IP-based lockout after N failures (configurable); expired counters auto-pruned |
| Transport | All external traffic via Cloudflare TLS — server binds `127.0.0.1` only |
| Agent API & WS | `X-API-Token` + `X-OTP` 2FA on every `/api/agent/*` and `/ws/*` (token-only is rejected) |
| Agent OTP secret | Separate from browser login secret — agent compromise does not expose browser login |
| Macro write | `POST /api/macros` is session-cookie only (agents cannot modify macros) |
| File API | `/api/tree`·`/api/download`·`/api/upload`·`/api/settings` are session-cookie only |
| Session | Signed secure cookie, configurable expiry; signing key in memory only |
| Tunnel exposure | Idle tunnels auto-close (`CB_IDLE_TIMEOUT`); on server idle-shutdown, ⏱ tunnels are closed to avoid unmonitored exposure |
| Secrets storage | `~/.cerberus/` (mode 0700), individual secret files mode 0600 |
| State integrity | `tunnels.json` written atomically (tmp + `os.replace`) to survive concurrent server/bot writes |

## Scope

In scope: authentication bypass, privilege escalation, secret disclosure, RCE via the web interface.  
Out of scope: issues requiring physical access to the server, Cloudflare infrastructure vulnerabilities.
