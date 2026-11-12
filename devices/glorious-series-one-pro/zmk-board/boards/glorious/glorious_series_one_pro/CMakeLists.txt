# SPDX-License-Identifier: MIT
# Compx bootloader re-entry: boot chord + &compx_bl behavior. Check that
# glorious_enter_compx_bootloader is in zmk.elf (see ../../../README.md).
zephyr_library()
zephyr_library_sources(bootloader_reentry.c)
zephyr_library_include_directories(${APPLICATION_SOURCE_DIR}/include)
