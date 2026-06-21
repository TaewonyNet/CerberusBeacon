# Third-Party Licenses

CerberusBeacon is licensed under the [MIT License](LICENSE).
It depends on the following third-party components. None of them are bundled in
this repository — they are installed via `pip`/`uv` or downloaded/loaded at
runtime, so each remains under its own license.

## Required Python dependencies

| Package | License | Notes |
|---------|---------|-------|
| [tornado](https://github.com/tornadoweb/tornado) | Apache-2.0 | Web server / IOLoop |
| [pyotp](https://github.com/pyauth/pyotp) | MIT | TOTP |
| [psutil](https://github.com/giampaolo/psutil) | BSD-3-Clause | System metrics (optional at runtime) |
| [qrcode](https://github.com/lincolnloop/python-qrcode) | BSD | QR output (optional at runtime) |
| [terminado](https://github.com/jupyter/terminado) | BSD | PTY WebSocket backend |
| [websockets](https://github.com/python-websockets/websockets) | BSD-3-Clause | CLI/bot WS client |

All required dependencies use permissive licenses compatible with MIT
redistribution.

## Optional dependencies

| Package | License | Notes |
|---------|---------|-------|
| [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) | **LGPL-3.0** | Telegram bot only (`--extra telegram`) |
| [slack-bolt](https://github.com/slackapi/bolt-python) | MIT | Slack bot only (`--extra slack`) |
| [slack-sdk](https://github.com/slackapi/python-slack-sdk) | MIT | Slack bot only |

> **python-telegram-bot (LGPL-3.0):** CerberusBeacon uses it as an *optional,
> unmodified, dynamically-imported* dependency and does **not** bundle or modify
> its source. Under LGPL-3.0 this keeps CerberusBeacon itself under the MIT
> License. Users who enable the Telegram bot install it themselves and may
> replace it independently. Do **not** vendor (copy) python-telegram-bot source
> into this repository — that would trigger LGPL obligations.

## Runtime-downloaded / CDN components (not bundled)

| Component | License | How it is obtained |
|-----------|---------|--------------------|
| [cloudflared](https://github.com/cloudflare/cloudflared) | Apache-2.0 | Downloaded at runtime from Cloudflare's GitHub releases to `~/.cerberus/cloudflared`. Use of Cloudflare Tunnel is also subject to Cloudflare's Terms of Service. |
| [xterm.js](https://github.com/xtermjs/xterm.js) `5.3.0` | MIT | Loaded in-browser from jsDelivr CDN |
| [xterm-addon-fit](https://github.com/xtermjs/xterm.js) `0.8.0` | MIT | Loaded in-browser from jsDelivr CDN |

These components are fetched on demand and are not redistributed as part of this
repository.
