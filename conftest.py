"""Root conftest.

`pytest_plugins` must live in the rootdir conftest - pytest refuses to
collect a suite that declares it in a nested one. The plugin itself is
`pytest-homeassistant-custom-component`, a test-only dependency; see
tests/conftest.py for the fixtures built on top of it.
"""

pytest_plugins = "pytest_homeassistant_custom_component"
