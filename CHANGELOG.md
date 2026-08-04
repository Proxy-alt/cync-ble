# Changelog

Version history for the Home Assistant `cync_ble` custom_component
(`custom_components/cync_ble/manifest.json`'s `version` field). Independent of
the [`cync-lan`](https://github.com/Proxy-alt/cync-lan-lib) core protocol
library's own version scheme, which this integration depends on to do the
actual protocol work, and of the
[`cync-lan`](https://github.com/Proxy-alt/cync-lan) integration's.

### 0.2.1

**A barren node no longer consumes the whole harvest cycle.** Caught on the
first real run of 0.2.0, which is exactly what it was meant to surface: the
cycle reached a `78:6D:EB` node, correctly demoted it, and then stopped — with
44 candidates untried — because the loop broke on a successful *connection*
rather than a successful sweep. Those nodes connect and authenticate perfectly
and hand back nothing, so the walk now continues to the next candidate within
the same cycle.

Note the interaction with `MAX_CONNECT_ATTEMPTS` (3): a barren node still costs
a full connect plus a window, so it spends budget either way. What changed is
that the budget now buys up to three *sweeps attempted* instead of one, and
demotion means those nodes sink after a single cycle regardless.

### 0.2.0

Two fixes, both about the same thing: the integration was preferring nodes that
could never do the job, and discarding readings it should have kept.

**A proven node is one that delivers a sweep, not one that authenticates.**

The firmware answers Home Assistant's subscribe in two different ways depending
on which OUI a node carries, measured across all 46 nodes of a real mesh:

| family | nodes | `start_notify` | sweep |
| :--- | ---: | :--- | :--- |
| `F4:BC:DA` | 10 | hangs, no ATT response | 22 notifications, 38 of 46 devices, every time |
| `78:6D:EB` | 3 | refused instantly, `WRITE_NOT_PERMITTED` | 0, 0, and once 14 |

Ten out of ten and three out of three, at overlapping signal strengths, across
four device types on the refusing OUI — so it tracks the silicon, not the
product or the radio conditions.

It matters because of ordering: bleak registers the notification callback and
*then* writes the descriptor. When the write hangs the callback stays
registered and the sweep arrives during the window; when it is refused, bleak
raises and the callback is discarded before anything comes back. So a
`78:6D:EB` node cannot harvest — not "harvests unreliably".

Both kinds authenticate identically, which is exactly why authentication was
the wrong thing to remember. On the development account two of the three
persisted proven nodes were `78:6D:EB`: tried first, guaranteed to return
nothing, and with `MAX_CONNECT_ATTEMPTS` at 3 they could consume the whole
budget while 23 working nodes sat untried.

- Promotion moved from `_authenticate` to a new `_record_sweep`, driven by what
  the harvest actually collected.
- A node that authenticates but delivers nothing is recorded as **barren**.
  That is a statement about harvesting only — such a node commands the mesh
  perfectly well — so `_candidate_macs` now takes `for_harvest`: barren nodes
  sink below never-tried ones for a harvest, and outrank them for a command.
- The persisted cap sorted by MAC, and `78:6D:EB` sorts before `F4:BC:DA`. It
  was systematically evicting the working nodes. It now keeps the largest
  sweeps.
- `_candidate_macs`' docstring claimed `F4:BC:DA` nodes "consistently refuse
  connections". On this mesh they are the family that works.

Entries stored under the old meaning migrate by attrition: the first harvest
through a barren node collects nothing and demotes it. Nothing hard-won is
thrown away on upgrade.

**An unreachable device's last-known level no longer overwrites fresh state.**

`parse_status` in `cync-lan` 0.7.0 surfaces the mesh's own online flag, which
had previously been mistaken for a presence marker in both directions — six of
44 slots on the development mesh are offline at any moment and were being
dropped entirely. An offline reading is real information but not a fresh one,
so it now fills a gap and never displaces something newer.

Best paired with `cync-lan` **0.7.0**, which is where the underlying status
parsing was corrected. The manifest floor stays at `>=0.6.0` deliberately —
raising it to a version that is not yet on PyPI would stop the integration
loading for everyone, which has happened here before. The new field is read
defensively so 0.6.0 still works.

### 0.1.0

Initial release. Local Bluetooth control of Cync / C by GE devices — no DNS
redirection and no cloud at runtime, using Home Assistant's own Bluetooth stack
and ESPHome Bluetooth proxies.

`iot_class: local_polling`: each refresh takes one deliberately sacrificial
connection that subscribes, collects the status sweep the mesh emits, and loses
the link when the firmware refuses the CCCD write. That refusal is measured,
invariant behaviour rather than something to retry around — and the attempt is
what makes the mesh report, so a full picture of the house arrives in the
seconds before the rejection lands.
