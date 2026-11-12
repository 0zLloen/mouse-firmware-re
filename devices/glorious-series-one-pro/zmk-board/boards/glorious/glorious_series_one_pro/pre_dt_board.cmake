# SPDX-License-Identifier: MIT
# Same as ZMK nRF52840 boards: silence duplicate unit-address warnings from nrf52840.dtsi
list(APPEND EXTRA_DTC_FLAGS "-Wno-unique_unit_address_if_enabled")
