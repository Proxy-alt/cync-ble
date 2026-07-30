"""Tests for Bluetooth address normalisation.

Pinned against real values from a live account and a simultaneous BLE scan
of the same mesh - the reversed cases below were confirmed to be genuinely
advertising in their reversed form, so these are observations rather than
guesses.
"""

from __future__ import annotations

from custom_components.cync_ble.address import (
    byte_reversed,
    candidates,
    to_colon_form,
)

# Real entry from the affected account, stored the ordinary way round.
FORWARD_STORED = "F4BCDA32A971"
FORWARD_REAL = "F4:BC:DA:32:A9:71"

# Real entry from the same account, stored byte-reversed. The reversed form
# is the one that was actually on the air during the scan.
REVERSED_STORED = "152232dabcf4"
REVERSED_REAL = "F4:BC:DA:32:22:15"


def test_bare_hex_gains_separators():
    """The cloud never punctuates, and both Home Assistant's address lookup
    and cync_lan's mac_to_address require it."""
    assert to_colon_form(FORWARD_STORED) == FORWARD_REAL


def test_already_punctuated_is_left_alone():
    assert to_colon_form(FORWARD_REAL) == FORWARD_REAL


def test_case_and_separator_style_are_normalised():
    assert to_colon_form("f4-bc-da-32-a9-71") == FORWARD_REAL


def test_byte_reversal_recovers_the_advertising_address():
    assert byte_reversed(REVERSED_STORED) == REVERSED_REAL


def test_reversal_is_its_own_inverse():
    assert byte_reversed(byte_reversed(FORWARD_STORED)) == FORWARD_REAL


def test_candidates_offers_as_stored_first():
    """As-stored is right for the large majority (44 of 46 on the account
    this was found on), so it must be tried first."""
    assert candidates(FORWARD_STORED) == [
        FORWARD_REAL,
        "71:A9:32:DA:BC:F4",
    ]


def test_candidates_covers_the_reversed_case():
    assert REVERSED_REAL in candidates(REVERSED_STORED)


def test_palindromic_address_is_not_offered_twice():
    """A single candidate, not a duplicate - the caller loops over these and
    a repeat would mean a pointless second lookup."""
    assert candidates("AABBCCCCBBAA") == ["AA:BB:CC:CC:BB:AA"]
