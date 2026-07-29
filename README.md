<p align="center">
  <img src="https://raw.githubusercontent.com/Proxy-alt/cync-lan/feature/ha-custom-component/custom_components/cync_lan/brand/logo%402x.png" alt="Cync LAN" width="420">
</p>

# Cync Bluetooth

**Local Bluetooth control of Cync / C by GE devices, as a Home Assistant
integration.** No DNS redirection. No cloud in the loop at runtime.

> [!WARNING]
> **This is a skeleton, not a working integration yet.** The protocol underneath
> it is confirmed on real hardware — see below — but the integration itself is
> being built. Nothing here installs and controls anything today. Watch the repo
> rather than filing bugs.

The sibling of [`cync-lan`](https://github.com/Proxy-alt/cync-lan), which
controls the same devices over TCP by impersonating the vendor's cloud server.
This one talks to them directly over their Bluetooth mesh instead.

## Why a second integration

`cync-lan` requires **DNS redirection**: you point the `xlink.cn` hostnames at
Home Assistant on your router, and devices connect to it thinking it is the
cloud. That works well and keeps everything local, but it is the single biggest
setup obstacle and the cause of most support traffic.

Bluetooth needs none of it. The trade is a Bluetooth adapter within range of
your mesh — or an **ESPHome Bluetooth proxy**, which Home Assistant supports
natively and which this integration is deliberately designed to work with.

| | `cync-lan` | this |
|---|---|---|
| DNS redirection | **required** | not needed |
| Needs a mains-powered bridge device | yes | no |
| Bluetooth in range | no | **yes** (proxies count) |
| Cloud at runtime | none | none |
| Feature coverage | broader | narrower to start |

Neither replaces the other. `cync-lan` covers more device types and more
platforms; this one is far easier to set up.

## Protocol status — honest version

Everything below was verified against real hardware, not inferred from a
decompiled app. Where something is unconfirmed it says so, because this project
inherits a strict rule from `cync-lan`: a plausible-looking wrong opcode is the
worst failure mode available, since it fails **silently**.

| | over Bluetooth |
|---|---|
| session handshake, mutual auth | **confirmed** |
| mesh relay — one connection reaches every device | **confirmed** |
| on / off | **confirmed** |
| brightness | **confirmed** |
| colour temperature | not confirmed |
| RGB colour | not confirmed |
| inbound status updates | received and decrypted — but **at the cost of the connection** |
| the exact meaning of those updates | partially decoded, one capture only |

That second-to-last row is the real limitation, and it is a Linux Bluetooth stack
problem rather than a device one. BlueZ will not hand over notifications unless it
subscribes, and this firmware rejects the subscription in a way that drops the
link. **On a local adapter you can send or receive, not both** — so the manifest
says `local_polling`.

An **ESPHome Bluetooth proxy** uses its own Bluetooth client rather than BlueZ and
may well not have this problem, which would make pushed state available again.
Nobody has tested that yet; it is the most useful thing anyone with a proxy could
report.

## Credentials

Setup asks for your Cync account once, to retrieve the Bluetooth mesh
credentials from the vendor's API. After that there is no cloud involvement —
every command is local.

The mesh password grants control of every device on the mesh. Treat it as a
password: it is stored in the config entry, redacted from diagnostics, and should
never be pasted into an issue.

## Design notes

[`ARCHITECTURE.md`](ARCHITECTURE.md) records the decisions and why they were
made — the single-session model, why the library is never allowed to create its
own Bluetooth client (it is what makes proxies possible), why the domain is
`cync_ble` rather than `cync`, and the intended route to
`home-assistant/core`.

## Credits

The protocol work behind this is a chain of earlier projects, and it would not
exist without them.

- **[juanboro/cync2mqtt](https://github.com/juanboro/cync2mqtt)** — its `acync`
  module is a working Bluetooth mesh implementation, and cross-checking against
  it byte-for-byte is what gave confidence in the crypto here. Apache-2.0,
  itself descended from `google/python-dimond` and `python-tikteck`.
- **[baudneo/cync-lan](https://github.com/baudneo/cync-lan)** — the async
  rewrite and the origin of most of the protocol knowledge.
- **[iburistu/cync-lan](https://github.com/iburistu/cync-lan)** — the original
  demonstration that these devices could be controlled locally at all.

## License

MIT.
