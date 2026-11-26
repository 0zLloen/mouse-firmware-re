# SPDX-License-Identifier: MIT
# Compx bootloader re-entry (boot chord + &compx_bl behavior) and PAW3370 sensor driver.
# Check that recovery and sensor symbols are in zmk.elf (see ../../../README.md).
zephyr_library()
zephyr_library_sources(bootloader_reentry.c paw3370.c)
zephyr_library_include_directories(${APPLICATION_SOURCE_DIR}/include)
