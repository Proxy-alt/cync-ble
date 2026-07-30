# Architecture

Why this repository exists separately, and the decisions already made. Written
before the integration, so the reasoning is on record rather than reconstructed
from the code later.

## What this is

A Home Assistant integration that controls Cync / C by GE devices over
**Bluetooth mesh**, using the `cync-lan` protocol library. Sibling to
[`cync-lan`](https://github.com/Proxy-alt/cync-lan), which controls the same
devices over TCP by impersonating the vendor's cloud.

The two are not rivals; they suit different people.

| | `cync-lan` (HACS) | `cync_ble` (this) |
|---|---|---|
| Transport | TCP, devices connect to HA | BLE mesh, HA connects out |
| DNS redirection | **required** | not needed |
| Cloud at setup | account login | account login |
| Cloud at runtime | none | none |
| Needs a mains-powered bridge device | yes | no |
| Bluetooth adapter in range | no | **yes** (or an ESPHome proxy) |
| Feature coverage | broader — 9 platforms | narrower to begin with |
| Distribution | HACS | aiming for `home-assistant/core` |

## Why a separate repository

HACS permits **one integration per repository**: *"There must only be one
integration per repository... If multiple subdirectories exist, only the first
will be managed by HACS."* Shipping both from `cync-lan` would silently give
users whichever sorted first.

They would also enumerate the same physical devices. Installed together, every
bulb and switch would appear twice, as two device entries, with two sets of
entities disagreeing about state. Separate repositories, separate domains, and
the user picks.

## Why `cync_ble` and not `cync`

`cync` is taken. `home-assistant/core` already ships a cloud-based `cync`
integration (`iot_class: cloud_push`, codeowner @Kinachi249, one platform:
`light`). So the brand name is claimed, and the `_ble` suffix is exactly the
convention core already uses for this situation:

| domain | iot_class | quality | name |
|---|---|---|---|
| `switchbot` | local_push | **gold** | SwitchBot Bluetooth |
| `switchbot_cloud` | cloud_polling | – | SwitchBot Cloud |
| `yalexs_ble` | local_push | – | Yale Access Bluetooth |
| `august` | cloud_push | – | August |

Two integrations per brand, both accepted, and in SwitchBot's case the
*Bluetooth* one is the gold-tier flagship. The suffix falls to whichever arrived
second, which here is us.

A reviewer may still ask why this is not folded into `cync`. The answer: an
entirely different protocol and library (Telink BLE mesh with its own crypto, vs
a cloud REST client), a different `iot_class`, a different discovery path, and
the precedents above. Worth raising with @Kinachi249 *before* opening anything,
rather than arriving with a finished pull request into someone else's brand.

## Decisions already made

### One session, not one per device

Mesh relay is **confirmed on hardware**: a command addressed to one device and
sent over a connection to a *different* device is relayed and acted on. So the
coordinator holds a single `BleMeshSession` and addresses every device through
it. At ~40 nodes, a connection per device would be unworkable; this is the fact
that makes the integration practical.

### The library must never construct the BLE client

`cync_lan.ble_mesh.BleMeshSession` accepts a `GattClient` (a
`typing.Protocol`) rather than creating one. This integration must therefore
pass a connection obtained from **Home Assistant's own Bluetooth stack**.

That is not a style preference. It is what makes **ESPHome Bluetooth proxies**
work, and proxies are the difference between reaching a whole house and reaching
whatever happens to be near the HA box. An integration that reached for `bleak`
directly would forfeit that silently.

Hence `"dependencies": ["bluetooth_adapters"]` in the manifest, and
`bleak_retry_connector` / `async_ble_device_from_address` at the call site.

### Credentials come from the cloud export, once

The Telink mesh name and password are the home's `mac` and `access_key` from the
Cync cloud API — **confirmed on hardware**, and available via
`cync_lan.ble_mesh.mesh_credentials_from_home()`.

So the config flow logs into the Cync account once, retrieves them, and stores
them in the config entry. After that there is no cloud in the loop. That shape —
cloud-assisted setup, local control — is ordinary and accepted in core; it is
also what makes this submittable where `cync-lan` is not.

They do **not** come from the hub. `cync-lan`'s `query_mesh_credentials` button
implies otherwise and cannot work, because hub commands currently get no reply
at all.

### Setup talks to the cloud through its own client, not `cync_lan.cloud_api`

The first version reused `cync_lan.cloud_api.CyncCloudAPI`, since it already
spoke every endpoint setup needs. That was wrong, and it took a real install to
show why: `CyncCloudAPI` is a **process-wide singleton** configured entirely
through **environment variables that `cync_lan.const` reads once, at import
time**. Both are reasonable for the standalone add-on it was written for — one
account, one process — and cync-lan's own integration documents the resulting
single-account limit openly.

They do not survive a *second* integration in the same Home Assistant process,
which is exactly what this is. With cync-lan installed alongside and setting up
first, reproduced directly:

- `CyncCloudAPI()` returns cync-lan's live instance, cached token and all. That
  is what produced the reported symptom: `check_token()` found cync-lan's
  *expired* token — something a fresh install could never reach — and tried to
  refresh it, down a code path that turned out to be broken in cync-lan itself
  (its refresh could never have succeeded; fixed there separately). The
  exception escaped a method declared to return `bool`, hit a broad
  `except Exception` here, and was reported as **"could not reach the Cync
  cloud API"** — sending the user to check their network for a bug that was in
  neither the network nor, originally, this integration;
- `CYNC_CONFIG_DIR` no longer has any effect either, so this integration's
  writes landed in cync-lan's directory while its reads looked in its own;
- `CYNC_ACCOUNT_USERNAME`/`_PASSWORD` likewise, so credentials typed into this
  integration's wizard were **silently ignored** and cync-lan's account used
  instead — the worst of the three, because it looks like success.

So setup now uses `cloud.py`, a small client of this integration's own: four
endpoints, every input an argument, no module state, nothing written to disk.
That also removes the YAML round-trip, the on-disk token cache, and the Fernet
secret those needed — none of which this integration ever wanted, and the last
of which was writing `0777` files.

The general lesson is worth keeping: **a library configured by import-time
globals cannot be shared by two integrations**, however convenient its API
looks. Sibling projects sharing protocol code is right; sharing process-global
configuration is not.

### `iot_class` is `local_polling` — via `local_push` and back again

This section has been wrong twice, so the reasoning is laid out rather than the
conclusion alone.

It started as `local_polling`, on the belief that notifications were refused
outright. That was wrong: what fails is **BlueZ's `StartNotify`**, not reporting.
So it became `local_push`. That was also wrong, and it is back to
`local_polling`, because of what testing the remaining orderings showed:

| | result |
|---|---|
| never subscribe | sending works, no inbound status |
| enable-write only, no subscribe | **no notifications at all** |
| anything calling `StartNotify` | packets arrive, then the link **drops** |

BlueZ will not route notifications without `StartNotify`, and this firmware
refuses the CCCD write `StartNotify` performs — fatally. **So on a local adapter
you can send or receive, not both.** For an integration that has to do both,
that means polling, or reconnecting between the two, and polling is the honest
manifest claim.

`google/python-dimond` has neither problem because bluepy never uses BlueZ's
GATT API. That route is not available here.

**This makes proxy compatibility decisive, not a nicety.** An ESPHome Bluetooth
proxy implements its own GATT client instead of going through BlueZ, so it may
not trip this at all — in which case `local_push` becomes correct again, for
proxy users. Nobody has tested it. It is now the highest-value unknown in this
repository, ahead of any code.

Two further caveats carried forward. The inbound `0xDC` slot layout is decoded
from a single capture and its presence rule contradicts acync's — see
`parse_status` — so state updates are best-effort until a second mesh confirms
it. And `subscribe()` raises rather than returning quietly, because a refused
subscribe leaves a dead session and a caller needs to know that.

### State comes from a deliberately sacrificial connection

Subscribing to the notify characteristic is refused by this firmware and takes
the link down with it. That was measured rather than assumed: **14 attempts,
every one refused with GATT `UNLIKELY_ERROR`, never once accepted.** So
imitating the retry loop the vendor's own iOS app ships (see
`ble_ios_app_subscribe_confirmed.md`) buys nothing here — persistence is not
the missing ingredient.

What the failed attempt *does* buy is the state. The vendor's enable-write
starts the device reporting and BlueZ registers the callback locally, so for
the ~30 seconds before the rejection lands the mesh streams status: **17-19
notifications decoding to 34-38 of this mesh's 46 devices, on every single
try.** A full picture of the house for the price of one connection.

So each refresh takes a **harvest** — one connection that subscribes on
purpose, collects the sweep, and is thrown away — while commands use a
separate connection that never subscribes and therefore stays healthy. That
is what makes `local_polling` honest here: entities report what the mesh said
about itself, not merely what was last sent to it, and a physically-operated
switch is picked up on the next cycle.

The decode behind this is confirmed, not assumed. `parse_status`'s presence
rule contradicted acync's and rested on a single capture; driving a device to
60, then 25, then off and harvesting after each produced exactly 60, 25 and 0
across a 38-device sweep.

One ordering subtlety, learned by getting it wrong on hardware: a harvest
taken immediately after a command can still report the *previous* value. So a
command issued since the last harvest wins, and the mesh's own account takes
over once a harvest lands after it. Without that rule every toggle visibly
snaps back.

## Protocol status

Everything here is inherited from `cync-lan`, where the confidence markers live.
Repeated because an integration should not promise more than the transport has
demonstrated.

| | over BLE |
|---|---|
| session handshake, mutual auth | **confirmed** |
| mesh relay | **confirmed** |
| `set_power` (`0xD0`) | **confirmed** |
| brightness (`0xF0` and `0xD2`) | **confirmed**, both forms |
| colour temperature | not confirmed |
| RGB | not confirmed |
| status notifications | **received and decrypted** — subscribe is always refused, but the sweep arrives first; this is what the harvest exploits |
| inbound slot layout | **confirmed** — set 60/25/off read back as 60/25/0 across a 38-device sweep |

Colour temperature and RGB ride the same `0xF0` family whose brightness member
works, so they are better founded than a guess — but nobody has moved either
over this transport, and the first release should not imply otherwise.

## Not claimed yet, on purpose — both since resolved

Two things were switched off rather than faked, because CI should mean something:

- **`config_flow` was `false` in the manifest.** Hassfest correctly rejects a
  declared config flow with no `config_flow.py` behind it. It flipped to `true`
  in the same commit that added a working one — step 3 below.
- **HACS validation was not in CI.** The assumption at the time was that its
  `<Validation brands>` check required listing this integration in
  `home-assistant/brands`, which felt premature for something that couldn't
  yet control a device. That assumption was wrong: HACS's own documentation
  says a committed `custom_components/cync_ble/brand/` directory with an
  `icon.png` satisfies the check on its own, no external listing needed. Once
  that directory existed (see the brand-asset commits), the job was added and
  confirmed passing against a live run — not just inferred from the docs —
  before being left in CI for good.

## Build order

1. `const.py`, `manifest.json`, `hacs.json` — settle the domain and dependencies.
2. Coordinator holding one `BleMeshSession`, obtained from HA's Bluetooth stack.
3. Config flow: account login → mesh credentials → device enumeration.
4. `switch` and `light` only. They cover what is confirmed.
5. Tests, especially the config flow, which core requires at full coverage.
6. Then, and only then, talk to @Kinachi249 and open an architecture discussion
   about the second-integration question.

Colour temperature and RGB wait for hardware confirmation. Inbound state can be
built on now, but the slot layout deserves a second capture before anything
depends on the details.
