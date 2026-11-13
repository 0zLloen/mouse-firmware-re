## What this adds or changes

<!-- Device/platform folder, which ladder step the findings come from, and anything you are unsure about. -->

## Checklist ([review checklist](https://github.com/0zLloen/mouse-firmware-re/blob/main/CONTRIBUTING.md#review-checklist))

- [ ] Folder name follows `devices/<vendor>-<model>/` (or `platforms/<oem>-<family>/` for shared material).
- [ ] `device.yaml` validates against `schema/device.schema.json` and agrees with the Markdown.
- [ ] Every claim is labelled Verified, Inferred or Unknown, with how it was verified or what would settle it.
- [ ] Firmware addresses name the exact image file and version.
- [ ] Vendor files are referenced by official URL, version and SHA-256; no binaries, dumps or raw captures are added.
- [ ] Serial numbers, Bluetooth/RF addresses, pairing keys, photo metadata and personal paths are removed.
- [ ] Anything not exercised on hardware is marked untested.
- [ ] Write procedures preserve the bootloader and unit-specific pages, never mass-erase, require a backup first, and name the recovery path (and whether it was tested).
- [ ] New tools have an offline test that runs in CI.
- [ ] This is my own work and may be published under the MIT License.
