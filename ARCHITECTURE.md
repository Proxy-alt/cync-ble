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

### `iot_class` is `local_push`

This started as `local_polling`, on the belief that notifications were refused
outright. That was wrong, and the correction is the reason this section changed:
what fails is **BlueZ's `StartNotify`**, not reporting itself.

The device does expose a `0x2902` CCCD (handle 19) and still answers the
subscribe with GATT `Unlikely Error`. But the CCCD is not how this protocol
enables reporting — writing `0x01` to the notification characteristic's *value*
is, which is what `google/python-dimond` does while never writing a CCCD at all.
With the enable-write first, **16 status packets arrived and decrypted correctly**
on a connection whose `StartNotify` had just been rejected.

`cync_lan.ble_mesh.BleMeshSession.subscribe()` does the enable-write first and
treats a refused subscribe as survivable, so this integration gets pushed state.
Polling a forty-node mesh — which was the weakest part of this design — is no
longer necessary.

One caveat carried forward: the inbound `0xDC` slot layout is decoded on the
strength of a single capture, and its presence rule contradicts acync's. See
`parse_status`. State updates should be treated as best-effort until a second
mesh confirms the layout.

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
| status notifications | **received and decrypted** — BlueZ's `StartNotify` is refused, but the vendor's own enable-write works |
| inbound slot layout | plausible only — one capture, and it contradicts acync |

Colour temperature and RGB ride the same `0xF0` family whose brightness member
works, so they are better founded than a guess — but nobody has moved either
over this transport, and the first release should not imply otherwise.

## Not claimed yet, on purpose

Two things are switched off rather than faked, because CI should mean something:

- **`config_flow` is `false` in the manifest.** Hassfest correctly rejects a
  declared config flow with no `config_flow.py` behind it. It flips to `true`
  in the same commit that adds a working one — step 3 below.
- **HACS validation is not in CI.** It fails on `<Validation brands>` until the
  integration is listed in `home-assistant/brands`, and opening that pull
  request for something which cannot yet control a device would be premature.
  The job returns when there is something installable to validate.

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
