"""Tests for the setup-time Cync cloud client.

The device-ID arithmetic here is protocol behaviour reimplemented from
cync-lan's exporter rather than imported, so it is pinned against the real
shapes a live export produces.
"""

from __future__ import annotations

import aiohttp
import pytest

from custom_components.cync_ble.cloud import (
    CyncAuthError,
    CyncCloud,
    CyncCloudError,
    _parse_devices,
)


def test_mesh_id_is_the_tail_of_device_id():
    """A real deviceID is <9-digit home><device>; the mesh address commands
    are addressed to is the last three digits of the remainder."""
    devices = _parse_devices(
        [
            {
                "deviceID": "169573386007",
                "displayName": "Kitchen",
                "mac": "AA:BB:CC:DD:EE:01",
                "deviceType": 1,
            }
        ]
    )
    assert devices == [
        {"id": 7, "name": "Kitchen", "type": 1, "mac": "AA:BB:CC:DD:EE:01"}
    ]


def test_multi_endpoint_sub_devices_are_skipped():
    """Sub-devices share their parent's mesh address (remainder longer than
    four digits). Emitting them would create duplicate entities aimed at the
    same target."""
    devices = _parse_devices(
        [
            {
                "deviceID": "169573386007",
                "displayName": "Parent",
                "mac": "AA:BB:CC:DD:EE:01",
                "deviceType": 67,
            },
            {
                "deviceID": "169573386001007",
                "displayName": "Sub-device",
                "mac": "AA:BB:CC:DD:EE:01",
                "deviceType": 67,
            },
        ]
    )
    assert [d["name"] for d in devices] == ["Parent"]


def test_incomplete_entries_are_skipped_not_fatal():
    """Real exports carry entries missing fields; one bad row must not lose
    the whole home."""
    devices = _parse_devices(
        [
            {"displayName": "No device id", "mac": "A", "deviceType": 1},
            {"deviceID": "169573386008", "mac": "A", "deviceType": 1},  # no name
            {
                "deviceID": "169573386009",
                "displayName": "Good",
                "mac": "AA:BB:CC:DD:EE:02",
                "deviceType": 1,
            },
            "not-a-dict",
        ]
    )
    assert [d["name"] for d in devices] == ["Good"]


class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        return self._body


class _FakeSession:
    def __init__(self, status, body):
        self._status, self._body = status, body
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _FakeResponse(self._status, self._body)


class _RoutedSession:
    """Answers per URL substring, so the two-request homes flow (device
    list, then per-home properties) can be exercised."""

    def __init__(self, routes: dict[str, tuple[int, object]]):
        self._routes = routes
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append(url)
        for fragment, (status, body) in self._routes.items():
            if fragment in url:
                return _FakeResponse(status, body)
        raise AssertionError(f"unexpected request to {url}")


async def _logged_in(session) -> CyncCloud:
    cloud = CyncCloud(session)
    cloud._access_token, cloud._user_id = "tok", 42
    return cloud


async def test_homes_carry_mesh_credentials_and_devices():
    """The home's own `mac`/`access_key` are the Telink mesh name and
    password - confirmed on hardware, and the whole reason setup touches the
    cloud at all."""
    session = _RoutedSession(
        {
            "subscribe/devices": (
                200,
                [
                    {
                        "name": "My Home",
                        "mac": "meshname1",
                        "access_key": "meshpass1",
                        "product_id": "p1",
                        "id": "h1",
                        "properties": {
                            "bulbsArray": [
                                {
                                    "deviceID": "169573386007",
                                    "displayName": "Kitchen",
                                    "mac": "AA:BB:CC:DD:EE:01",
                                    "deviceType": 38,
                                }
                            ]
                        },
                    }
                ],
            )
        }
    )
    cloud = await _logged_in(session)
    homes = await cloud.async_get_homes()
    assert homes == [
        {
            "name": "My Home",
            "mesh_name": "meshname1",
            "mesh_password": "meshpass1",
            "devices": [
                {"id": 7, "name": "Kitchen", "type": 38, "mac": "AA:BB:CC:DD:EE:01"}
            ],
        }
    ]


async def test_placeholder_homes_are_skipped():
    """Real accounts carry unnamed placeholder entries - one per Wi-Fi hub -
    plus homes with no devices. Neither is a usable choice."""
    session = _RoutedSession(
        {
            "subscribe/devices": (
                200,
                [
                    {"mac": "x", "access_key": "y"},  # no name
                    {"name": "No keys", "product_id": "p", "id": "i"},
                    {
                        "name": "Empty",
                        "mac": "m",
                        "access_key": "k",
                        "product_id": "p",
                        "id": "i",
                        "properties": {"bulbsArray": []},
                    },
                ],
            )
        }
    )
    cloud = await _logged_in(session)
    assert await cloud.async_get_homes() == []


async def test_properties_are_fetched_when_not_inlined():
    """The device list endpoint often omits `properties`; they come from a
    second per-home request."""
    session = _RoutedSession(
        {
            "subscribe/devices": (
                200,
                [
                    {
                        "name": "Lazy Home",
                        "mac": "meshname2",
                        "access_key": "meshpass2",
                        "product_id": "p9",
                        "id": "h9",
                    }
                ],
            ),
            "product/p9/device/h9/property": (
                200,
                {
                    "bulbsArray": [
                        {
                            "deviceID": "169573386011",
                            "displayName": "Lamp",
                            "mac": "AA:BB:CC:DD:EE:11",
                            "deviceType": 5,
                        }
                    ]
                },
            ),
        }
    )
    cloud = await _logged_in(session)
    homes = await cloud.async_get_homes()
    assert [h["name"] for h in homes] == ["Lazy Home"]
    assert homes[0]["devices"][0]["id"] == 11
    assert any("property" in url for url in session.calls)


async def test_a_home_whose_properties_error_is_dropped_not_fatal():
    """A home with no devices answers the properties endpoint with an error
    body. That drops the home; it must not abort the whole account."""
    session = _RoutedSession(
        {
            "subscribe/devices": (
                200,
                [
                    {
                        "name": "Broken",
                        "mac": "m",
                        "access_key": "k",
                        "product_id": "p9",
                        "id": "h9",
                    }
                ],
            ),
            "product/p9/device/h9/property": (
                404,
                {"error": {"msg": "no properties", "code": 4041009}},
            ),
        }
    )
    cloud = await _logged_in(session)
    assert await cloud.async_get_homes() == []


async def test_api_error_body_is_an_auth_error_not_a_transport_error():
    """The API answers an unknown account with HTTP 404 *and* an error body.
    That is the cloud saying no, not the cloud being unreachable - conflating
    the two is what made the original failure read as 'cloud unreachable'."""
    session = _FakeSession(404, {"error": {"msg": "user not exists", "code": 4041011}})
    cloud = CyncCloud(session)
    with pytest.raises(CyncAuthError, match="user not exists"):
        await cloud.request_otp("nobody@example.com")


async def test_plain_http_error_is_a_cloud_error():
    session = _FakeSession(500, {"anything": True})
    cloud = CyncCloud(session)
    with pytest.raises(CyncCloudError):
        await cloud.request_otp("someone@example.com")


async def test_login_stores_the_token_and_homes_use_it():
    session = _FakeSession(200, {"access_token": "tok", "user_id": 42})
    cloud = CyncCloud(session)
    await cloud.login("someone@example.com", "pw", 123456)
    assert cloud._auth_headers == {"Access-Token": "tok"}


async def test_device_list_before_login_is_an_error_not_a_crash():
    cloud = CyncCloud(_FakeSession(200, []))
    with pytest.raises(CyncCloudError, match="login"):
        await cloud.async_get_homes()


class _ExplodingSession:
    def __init__(self, exc):
        self._exc = exc

    async def request(self, method, url, **kwargs):
        raise self._exc


@pytest.mark.parametrize(
    "exc",
    [aiohttp.ClientConnectionError("no route"), TimeoutError()],
    ids=["connection-refused", "timeout"],
)
async def test_transport_failures_are_cloud_errors_not_auth_errors(exc):
    """A genuinely unreachable cloud must be distinguishable from a rejected
    account - the config flow shows a different message for each, and
    reporting the wrong one is what sent this integration's first real user
    looking at their network instead of at the bug."""
    cloud = CyncCloud(_ExplodingSession(exc))
    with pytest.raises(CyncCloudError) as excinfo:
        await cloud.request_otp("someone@example.com")
    assert not isinstance(excinfo.value, CyncAuthError)


async def test_login_response_missing_fields_is_a_cloud_error():
    session = _FakeSession(200, {"unexpected": "shape"})
    cloud = CyncCloud(session)
    with pytest.raises(CyncCloudError):
        await cloud.login("someone@example.com", "pw", 123456)
