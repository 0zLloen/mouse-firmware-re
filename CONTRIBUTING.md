# Contributing: reverse-engineer your mouse and publish what you find

This guide is for people who want to find out how their gaming mouse's firmware works (which chip, how it updates, where the bootloader lives, whether it can run open firmware) and add that knowledge here in a form others can build on. You do not need prior firmware experience. You do need patience, a notebook, and the discipline to stop before steps that can brick your mouse.

The worked example is [`devices/glorious-series-one-pro/`](devices/glorious-series-one-pro/): start with its [`README.md`](devices/glorious-series-one-pro/README.md) (status and open questions) and [`device.yaml`](devices/glorious-series-one-pro/device.yaml), then read the Markdown files it lists.

**Contents:** [Ground rules](#ground-rules) · [Evidence rules](#evidence-rules) · [The safety ladder](#the-safety-ladder) · [Submitting](#submitting) · [Review checklist](#review-checklist)

---

## Ground rules

1. **Climb the ladder in order.** Each step below is more invasive than the one before. Most of the useful facts come from the first five steps, in which you never write anything to the mouse yourself (changing settings in the vendor app during step 4 is fine; letting it update firmware is a write, see step 4).
2. **Back up before you write.** Nothing you send writes firmware until there is a verified backup and a known way back (step 7).
3. **Never mass-erase.** No `nrfjprog --recover`, `--eraseall`, OpenOCD `nrf52_recover`, `pyocd erase --chip`, STM32 RDP regression, or any "unlock" that erases the chip. It destroys the bootloader and the per-unit pairing/calibration data, which exist nowhere else. Some tools do it for you when they find a locked chip: pyOCD mass-erases a locked nRF52 on connect unless you pass `-O auto_unlock=false` to every command, and J-Link Commander and nRF Connect offer to "unsecure" or "recover" it. Answer **No**.
4. **Never send bytes you do not understand.** Vendor HID commands that look harmless can erase flash. Only send a command you have seen the vendor software send and that you have good reason to believe is read-only.
5. **Label every claim** as Verified, Inferred or Unknown, and say how you know it.
6. **No vendor binaries, no dumps, no personal data in the repository.** URLs and SHA-256 hashes instead of files; serial numbers and pairing keys redacted.
7. **Keep a lab notebook.** Date, command, output, file hash. Your notebook is your evidence; the `source` fields in `device.yaml` are copied from it.

Practical: keep a second mouse plugged in. A bricked mouse is a bad pointing device for un-bricking itself.

## Evidence rules

Every factual statement in a device folder or platform document carries one label:

| Label | Meaning | What the `source` must say |
| :--- | :--- | :--- |
| **Verified** | Observed directly on a real unit (USB enumeration, a read-back, a measurement), or read literally from an official file or photo (a hash, a record address, a string, a constant, a chip marking). | What you ran or read, and when: command + date, file name + address, photo URL + exhibit. |
| **Inferred** | Your interpretation of disassembly, decompilation, USB captures or photos: what a pin does, what a command means, how a bootloader behaves. | What you inferred it from. |
| **Unknown** | Not established yet. | What would settle it. |

Rules of thumb:

- Label the claim, not the document. In Markdown a row can carry two labels (Verified for the marking, Inferred for the part it implies); `device.yaml` has one label per item, so use the weaker one and name the stronger part in `source`, as the Glorious sensor does.
- A value read from vendor software is Verified as a constant. What it implies about the device ("the bootloader enumerates as 258a:2103") is Inferred until it is seen on the device.
- When unsure, use the weaker label. Upgrading a label needs new evidence, and the edit says what it was.
- "Documented" is not "tested". Anything not yet exercised on hardware says so explicitly.
- If two sources disagree, record both with their sources and mark the point Unknown until one is confirmed.
- Cite firmware locations as `<file name> @ <load address>`, e.g. `CM2856_Mouse_USB_v1.00.00_20220810.hex @ 0x000184EC`. Use load addresses, not file offsets, and name the exact file version.

---

## The safety ladder

| Step | Touches the mouse? | Can brick it? | Typical labels |
| :--- | :--- | :--- | :--- |
| [1. Identify](#1-identify-the-device) | Standard descriptor reads only | No | Verified |
| [2. Open-source intel](#2-open-source-intel) | No | No | Verified (markings), Inferred (parts) |
| [3. Vendor updater analysis](#3-vendor-updater-analysis) | No | No | Verified (hashes, constants), Inferred (behaviour) |
| [4. Passive USB capture](#4-passive-usb-capture) | Only the vendor app talks to it | Only as much as the vendor app | Verified (bytes), Inferred (meaning) |
| [5. Static disassembly](#5-static-disassembly) | No | No | Inferred, Verified for literal constants |
| [6. Read-back and backup](#6-read-back-and-backup) | Yes, read-only commands | Unlikely, but the device is now in a special mode | Verified |
| [7. Writing flash and SWD](#7-writing-flash-and-swd) | Yes, writes | **Yes** | Verified only for what you actually did |

Start `devices/<vendor>-<model>/device.yaml` from [`devices/_template/`](devices/_template/) at step 1 and fill it in as you go. A pull request after step 1 or 2 is welcome; you do not need to finish the ladder.

### 1. Identify the device

Plug the mouse in by cable, then again through its receiver if it has one, and record each identity. These commands only read what the kernel already fetched when the device was plugged in, plus standard USB descriptors.

```bash
lsusb                                    # find it: "Bus 001 Device 036: ID 258a:2018 ..."
sudo lsusb -v -d 258a:2018 > lsusb-258a-2018.txt   # all descriptors and strings; HID report descriptors
                                         # show "** UNAVAILABLE **" while usbhid owns them: use sysfs below
lsusb -t                                 # interfaces, drivers, speed (12M = full-speed)

id=0003:0000258A:00002018                # HID_ID = bus:VID:PID, upper case, zero-padded; bus 0003 = USB
for h in /sys/class/hidraw/hidraw*; do   # one hidraw node per HID interface
  grep -qi "HID_ID=$id" "$h/device/uevent" || continue
  echo "== $h interface $(basename "$(readlink -f "$h/device/..")" | sed 's/.*\.//')"
  echo "report descriptor: $(wc -c < "$h/device/report_descriptor") bytes"
  xxd -p "$h/device/report_descriptor" | tr -d '\n'; echo
  hid-decode "$h/device/report_descriptor"   # pipx install hid-tools
done
```

Windows: Device Manager → the device → Properties → Details → *Hardware Ids* shows `HID\VID_258A&PID_2018&REV_0101&MI_02` (`REV` is bcdDevice, `MI` the interface); [USB Device Tree Viewer](https://www.uwe-sieber.de/usbtreeview_e.html) shows every descriptor including HID report descriptors. macOS: `system_profiler SPUSBDataType` (`SPUSBHostDataType` on recent versions) and `ioreg -p IOUSB -l -w0`.

**Record:** VID:PID and role of every identity (wired application, receiver; the bootloader comes later), `bcdDevice`, the `iManufacturer` and `iProduct` strings exactly, USB speed, each HID interface with its report descriptor length and hex, the firmware version shown by the vendor app, and the FCC ID from the label. Look for a vendor-defined usage page (`0xFF00`–`0xFFFF`) with Feature reports: that is usually the configuration and update channel.

**Do not record** `iSerial`, `HID_UNIQ`, or Bluetooth addresses (`bluetoothctl info` prints one). Replace them with `<redacted>` when you paste output.

**Label:** Verified, with the command and date.

### 2. Open-source intel

Before opening anything, find what others already published.

- **FCC ID** (label, manual or box): search [fccid.io](https://fccid.io/) or the [FCC database](https://apps.fcc.gov/oet/cf/eas/reports/GenericSearch.cfm). The *internal photos* exhibit usually shows both PCB sides; the test report names the radio and modulation. Some exhibits are confidential for the first months after the grant. Other regulators (ISED Canada, NCC Taiwan, KC Korea, MIC Japan) sometimes have better photos.
- **Teardowns:** review sites with PCB photos, teardown videos, enthusiast forums.
- **Chip markings → parts.** Zoom into the photos and copy the package text exactly. Decode it with the manufacturer's datasheet ("ordering information" / "marking" sections). Example: `N52840 QIAAD0 2144MX` is a Nordic nRF52840, package QI (aQFN73), variant AA, build code D0, date code 2021 week 44. Sensors are often custom-marked with the brand's name (the Glorious `BAMF` is one); tie them to a base part through package shape, pin count and the register sequences in the firmware, and label that link Inferred.
- **Existing projects:** search for the VID:PID, the PCB name and the chip (`"258a:2018"`, `"CM2852"`) on GitHub, in ZMK/QMK/Zephyr issues, and in this repository's `platforms/`.

**Record:** the FCC ID and exhibit URLs, PCB silkscreen (name, revision, date), every marking you can read with the photo it came from, the parts you derive, and links to related firmware projects.

**Label:** markings read from a photo are Verified (cite the exhibit); the part a custom marking implies is Inferred; pinouts guessed from traces are Inferred.

### 3. Vendor updater analysis

The official updater is the richest source: it contains the firmware image and the code that talks to the bootloader.

1. **Download it yourself** from the vendor's official site. Record the page URL, the file URL, the date, the version and the size, and hash every file you get or extract:
   `sha256sum *` (Windows: `(Get-FileHash -Algorithm SHA256 <file>).Hash.ToLower()`, macOS: `shasum -a 256 <file>`). Vendors remove old releases (the Glorious Series One Pro package is gone from Glorious' servers): keep your copies outside the repository, and an archive.org snapshot of the download page helps.
   Web drivers (WebHID) have nothing to install: save the page's JavaScript (DevTools → Sources or Network) and note the firmware URLs it fetches, without session tokens or signatures (`token=`, `X-Amz-Signature=`). Never press *Update*.
2. **Extract, don't run.** Work in a throwaway VM or an offline directory. Installers unpack without executing anything:
   - Inno Setup: `innoextract Setup.exe`; NSIS, self-extracting archives, many MSIs: `7z x`; MSI: `msiextract`.
   - Electron apps: `npx @electron/asar extract resources/app.asar app/`, then read the JavaScript. Native Node addons (`*.node`) are DLLs.
   - .NET (`FirmwareUpdate.exe` and friends): decompile with [ILSpy](https://github.com/icsharpcode/ILSpy) / `ilspycmd` or dnSpyEx. Look for VID/PID constants, report sizes, archive passwords, XOR keys, command bytes and the order of operations. If dependencies are embedded (Costura) and a disassembler crashes on missing references, the Python packages `dnfile` + `dncil` still disassemble single methods. Mind the buffer index: in a Windows `HidD_SetFeature` buffer `buf[0]` is the report ID, so `buf[i]` is payload byte `i-1`.
   - Native code: Ghidra (step 5).
3. **Identify the image format.** Intel HEX (text lines starting with `:`, carries load addresses: free memory-map information), Motorola S-record, raw `.bin` (no addresses), DFU, UF2, or a vendor container with a header. A plain ARM Cortex-M image starts with a vector table: a RAM address (`0x2000xxxx`) followed by odd flash addresses. Uniformly high entropy everywhere (`binwalk -E`) suggests encryption or compression. Convert HEX to binary with `objcopy -I ihex -O binary fw.hex fw.bin` (the binary starts at the lowest address; record it). Check the ranges first (`hexinfo.py fw.hex` from the `intelhex` package): if the file also has records far away (nRF52 UICR at `0x10001000`, STM32 option bytes), convert each range separately or `objcopy` fills the gap. Non-ARM images: RISC-V starts with a jump to reset; 8051 with `02 hi lo` (LJMP) at `0x0000`.

**Record:** the full nesting (`Package.zip > Updater.exe > payload.zip (password X) > fw.hex`), each file's name, version, size and SHA-256, the image format, address range and entry point, whether it is encrypted, signed or checksummed, and which parts of flash the update writes. Archive passwords and fixed XOR/obfuscation constants needed to reproduce the extraction may be recorded; firmware encryption or signing keys may not.

**Never commit** the downloaded or extracted files, or full decompiled vendor source. Short excerpts (a few instructions, a method name, a constant) with their location are fine.

**Label:** hashes, sizes, record addresses and literal constants are Verified; what the updater code does is Inferred.

### 4. Passive USB capture

Watch the vendor software talk to the mouse. You send nothing yourself.

**Linux (usbmon + Wireshark):**

```bash
sudo modprobe usbmon
lsusb -d 258a:                           # "Bus 001 Device 036" -> interface usbmon1, address 36
sudo dumpcap -i usbmon1 -w dpi-800-to-1600.pcapng   # Ctrl-C when done
```

Display filters: `usb.device_address == 36` (all traffic of the device), add `&& usb.transfer_type == 0x02` for control transfers (Feature/Output reports via SET_REPORT/GET_REPORT) or `== 0x01` for interrupt reports. Export as text with `tshark -r dpi-800-to-1600.pcapng -Y '<filter>' -x > dpi-800-to-1600.txt`.

Most vendor apps are Windows-only. Either run them in a VM with USB passthrough and capture on the Linux host, or capture on Windows directly. In a VM, turn off automatic firmware updates in the app (or cut the VM's network) before connecting the mouse, and pass the whole USB port through rather than filtering by ID (QEMU: `-device usb-host,hostbus=B,hostport=P`): the bootloader may use another VID and must still reach the VM.

**Windows (USBPcap):** tick USBPcap when installing Wireshark, reboot, capture on the `USBPcapN` root hub the mouse is on, and use the same display filters.

Tips:

- One action per capture, named after it (`read-settings`, `dpi-800-to-1600`, `version-check`). Diffing two captures that differ in one setting is the fastest way to map a protocol.
- The device address changes when a device re-enumerates (e.g. into its bootloader). During an update, filter by bus, not address.
- Measure the polling rate from report timestamps in usbmon or hidraw, while the mouse moves. Never from browser pointer events: browsers merge reports (a browser polling test showed 565 Hz for the Glorious mouse, which sends about 1,000 reports/s).
- Capturing an official update is the best way to learn the bootloader protocol, but the update itself writes flash and may erase unit-specific pages: back up first if step 6 gives you a way to (and record that you could not if it does not), and only do it when the vendor offers it for your mouse, on a direct port with stable power, preferably on bare-metal Windows rather than through VM passthrough.

**Privacy:** usbmon and USBPcap capture *every* device on that bus, including your keyboard. Do not type passwords while capturing, use a bus with only the mouse on it if you can, and **never commit raw captures**. Commit text excerpts of the relevant packets, with serial numbers, Bluetooth/RF addresses and pairing data replaced by `XX`, and say what you redacted.

**Receivers.** A wireless receiver may relay a report to the mouse or answer it itself, depending on a target or routing byte. Find that byte, and list which commands the receiver handles on its own. Treat its own write commands (mode or bootloader entry, pairing ID) like the writes of step 7: one wrong packet can unpair the mouse or put the receiver into its bootloader. [`receiver.md`](devices/glorious-series-one-pro/receiver.md) is the worked example.

**Record:** report IDs, lengths and byte layouts, command bytes, status codes, the order of a transaction, and which capture each comes from.

**Label:** bytes on the wire are Verified (cite the capture name and date); what a field means is Inferred until you cross-check it against code (step 3 or 5).

### 5. Static disassembly

Load the firmware into [Ghidra](https://ghidra-sre.org/) (free; IDA Free, Binary Ninja, rizin work too).

1. **Import.** Intel HEX imports with its addresses. For a raw `.bin`, choose language `ARM:LE:32:Cortex` (Cortex-M) and set the base address to the image's load address. If the base is unknown, use the one that makes the vector table's reset handler land inside the image. 8051 and RISC-V MCUs are supported too; some (e.g. Telink TC32) need a community processor module.
2. **Vector table.** Word 0 is the initial stack pointer (gives you the top of the stack in RAM), word 1 the reset handler (odd = Thumb). Create functions at every handler address.
3. **Memory map.** Add RAM and the peripheral ranges from the MCU datasheet. A CMSIS SVD file and an SVD loader script (e.g. SVD-Loader-Ghidra) name every peripheral register for you.
4. **Peripheral base addresses → pin maps.** Writes of small constants to peripheral pin-select or GPIO configuration registers reveal the wiring. On the nRF52840, `NRF_SPIM2` is at `0x40023000` and `PSEL.SCK` at `+0x508`, so `str r0, [0x40023508]` with `r0 = 12` means the sensor clock is on `P0.12`; `NRF_P0` and `NRF_P1` are at `0x50000000` and `0x50000300`. See [`hardware.md`](devices/glorious-series-one-pro/hardware.md) for a full worked pin map.
5. **Strings and constants.** Search for strings (enable UTF-16 for USB descriptors): SDK source paths, assert messages and version strings identify the SDK and the module of a function. Search for the constants you saw in captures (report lengths, command bytes, status codes) to find the USB command dispatcher.
6. **Flash map.** Erase/write calls and the addresses passed to them show where settings, profiles and pairing data live. These are the regions that must be preserved.

**Record:** the image (file + hash), the memory map, the pin map with the address of each instruction that proves it, flash regions and their users, the bootloader entry mechanism (often a magic word in RAM followed by a reset), and the command table.

**Label:** Inferred, except literal constants, strings and addresses read from the image (Verified). A pin map from disassembly stays Inferred until someone probes the board.

### 6. Read-back and backup

From here on you talk to the device directly. Before anything is ever written, make a backup of everything that is **not** in the vendor package: the bootloader, settings, pairing keys and calibration pages, and configuration words such as UICR or option bytes. The vendor package normally contains only the application, so those regions exist only on your unit.

Ways to read flash, least invasive first:

1. A read or verify command in the bootloader protocol, if step 3/4/5 showed one.
2. A backup or export feature in the vendor tool.
3. SWD/JTAG read-only (step 7 for the hookup), only after a read-only check that the debug port is open.

Example, **Glorious Series One Pro mouse only** (`258a:2018`, bootloader `258a:2103`; Linux hidraw with that device's udev rule; other Compx PIDs and the receiver are not supported). Before you run it, know how to get the device out of its bootloader: the tool sends `Exit` when the read finishes, and [`platforms/compx-nrf52/bootloader-protocol.md`](platforms/compx-nrf52/bootloader-protocol.md) says what to do if it stops early.

```bash
python3 platforms/compx-nrf52/tools/compx_usb_flasher.py dump --out <backup.hex>
```

From application mode it first sends the updater's own "set device mode" packet (read from `FirmwareUpdate.exe` and tested on a retail unit), then reads upward from `0x6000` through the bootloader's `VerifyByAddress` command until the bootloader refuses (at `0x4D800` on this mouse), and never overwrites an existing file. So it cannot reach the bootloader below `0x6000` or the pairing and calibration pages at `0x88000`+: on this mouse only SWD could back those up, which matters before any write that might erase them.

- If a tool for your device does not exist yet, write it the same way: only commands seen in the vendor software, read-only, a mocked offline test.
- Read twice and compare: `sha256sum backup-1.hex backup-2.hex`. Keep the backups somewhere safe outside the repository.
- Compare the application region against the official image. A match is a Verified statement worth recording ("the application region of a unit on v1.0.0.0 matches the official `.hex`").
- **Never commit dumps,** even of your own unit: they contain its serial, pairing keys and calibration.

**Record:** the method and command, which address ranges it covers and which it cannot reach, whether reads below the application base are refused, and the results of comparing against the official image.

**Label:** Verified (the data came off the device).

### 7. Writing flash and SWD

This is the step that can brick a mouse. Do not start it until all of these hold:

1. **A full backup** from step 6, read twice with matching hashes, covering everything you could reach.
2. **A known recovery path that does not depend on what you are about to write**: a resident bootloader you will not touch plus the official image to re-flash, or an SWD connection confirmed open (read-only) on this unit.
3. **A documented flash layout** in `device.yaml` with `preserve: true` on the bootloader, pairing, calibration and any unit-specific regions, and a write plan that stays inside the application region.
4. **A tool that verifies by read-back** before it marks the image bootable, and writes the vector table (or whatever makes the bootloader hand over) last, so an interrupted write leaves the bootloader in charge. [`compx_usb_flasher.py`](platforms/compx-nrf52/tools/compx_usb_flasher.py) shows the pattern.

**SWD/JTAG hookup.** Find the pads with a multimeter on an unpowered board (continuity to ground, diode drop on data lines) before connecting anything. Take `VTref` from the regulated MCU rail, never from the battery or VBUS, and do not power the board from the battery and the probe at the same time. [`flash-layout-and-swd.md`](devices/glorious-series-one-pro/flash-layout-and-swd.md) has a full passive procedure.

**Readback protection.** Check it read-only before anything else:

- **nRF52:** read `APPROTECTSTATUS` from the CTRL-AP (OpenOCD `nrf52.dap apreg 1 0x0c`). On newer silicon revisions (for the nRF52840, build code F0 and later), access-port protection is re-enabled at every reset unless both UICR and the running firmware open it, so SWD can be closed even when UICR says otherwise.
- **STM32:** read the RDP level. Level 1 → 0 regression mass-erases; level 2 is permanent.
- On every family, the "unlock" for a protected chip is a mass erase. **Do not run it, and decline any tool that offers it** (see ground rule 3). A locked chip with a working USB bootloader is still recoverable; an erased one without a bootloader backup is not.

**Writing.** Write only the application region. Leave the bootloader, pairing and calibration pages alone. Verify every byte by read-back. Test recovery (re-flash stock and confirm pairing still works) before you publish instructions others will follow.

**Document before you publish:** the exact commands, the regions written, what was preserved and how you checked, how you restored stock, and what went wrong on the way. A recovery path is Verified only after you have actually recovered a device with it; until then it is Inferred and says "untested on hardware".

---

## Submitting

### Where things go

```text
devices/<vendor>-<model>/      one product: its USB identities, hardware, pin map, flash layout,
  device.yaml                  updater analysis, recovery notes (copy devices/_template/)
  README.md                    or topic files listed under docs: in device.yaml
platforms/<family>/            shared by several products: an OEM/MCU/bootloader family's protocol
  tools/                       spec and the tools that speak it, with an offline test
schema/device.schema.json      the device.yaml schema (CI validates every device folder against it)
```

- **Device folder name:** `<vendor>-<model>`, lower case ASCII, words joined by hyphens, as marketed (`glorious-series-one-pro`). Add a suffix such as `-rev2` only for a hardware revision that differs (another PCB or MCU), and note how to tell them apart.
- **Platform folder name:** `<oem-or-chip-vendor>-<mcu-family>` (`compx-nrf52`). Start in your device folder; move protocol specs and tools to `platforms/` once a second device is shown to share them (same bootloader identity and command set), and set `platform:` in each `device.yaml`.
- **Tools** in `platforms/<family>/tools/` follow the repository rules: Python standard library where possible, read-only unless the subcommand says otherwise, never mass-erase, refuse images that overlap preserved regions, verify by read-back before handing control to the new image, and ship an offline test with a mocked device (like [`test_tools.py`](platforms/compx-nrf52/tools/test_tools.py)) that you add to [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

### Pull request

```bash
gh repo fork 0zLloen/mouse-firmware-re --clone   # or fork on github.com and clone
cd mouse-firmware-re
git switch -c device/<vendor>-<model>
cp -r devices/_template devices/<vendor>-<model>
# fill in device.yaml first, then README.md
pipx run check-jsonschema --schemafile schema/device.schema.json devices/<vendor>-<model>/device.yaml
git add devices/<vendor>-<model>
git commit -m "devices: add <vendor> <model>"
git push -u origin device/<vendor>-<model>
gh pr create --web                       # opens github.com with the PR checklist filled in
```

One device or one topic per pull request. The pull request template carries the [review checklist](#review-checklist). CI validates `device.yaml`, runs the tool tests, and fails if a tracked file matches `.gitignore` (binaries, captures). On a first contribution, CI starts after a maintainer approves the run.

To fix or add to an existing device, edit its files directly and state the new evidence in the pull request.

### No pull request? Open an issue

Use [New device report](https://github.com/0zLloen/mouse-firmware-re/issues/new?template=new-device.yml) for a device that is not here yet (it asks for the same essentials as `device.yaml`), or [Finding or correction](https://github.com/0zLloen/mouse-firmware-re/issues/new?template=finding.yml) for one claim about an existing device. A maintainer turns it into a pull request and credits you. Do not attach binaries, dumps or raw captures to issues either.

### Never commit

- Vendor firmware, updaters or anything extracted from them. Link the official download and record the SHA-256 instead. CI fails if any tracked file matches `.gitignore`, which lists the binary, archive and capture extensions.
- Flash dumps or backups, even of your own unit.
- Raw USB captures (`.pcap`, `.pcapng`): commit redacted text excerpts.
- Full decompiled vendor source or long disassembly listings. Short excerpts with addresses are fine.
- Firmware encryption or signing keys found in vendor software.
- Firmware you built yourself: describe how to build it; CI can build it (the ZMK board in the Glorious folder is built in CI).
- Images that contain vendor code, such as a patched stock image. Do not attach them to releases, issues or discussions either: describe how to build them from the user's own copy of the official image.

### Privacy

Before you push, search your changes for:

- USB serial numbers (`iSerial`, `HID_UNIQ`), Bluetooth and 2.4 GHz addresses, pairing keys and anything else from the pairing page of a dump.
- The serial sticker in your own photos, and their metadata: `exiftool -all= photo.jpg` removes location and camera data.
- Your user name, host name or home path in pasted terminal output.

Anything pushed to GitHub, even to a fork or a pull request you later force-push, stays reachable by commit SHA, so rewriting history does not unpublish it. If you pushed something private, close the pull request without saying what leaked, follow GitHub's "Removing sensitive data from a repository" (it ends with a request to GitHub Support), and replace what can be replaced (re-pair the receiver).

### Licensing

This repository is under the [MIT License](LICENSE). By contributing, you agree that your text, tables, code and your own photos are published under it. Only submit what you have the right to publish: your own writing, measurements and photos. Link to FCC exhibits and third-party teardown photos instead of copying them, and quote vendor material only in short excerpts needed to document a fact.

---

## Review checklist

Reviewers check, and authors tick in the pull request:

- [ ] Folder name follows `devices/<vendor>-<model>/` (or `platforms/<oem>-<family>/` for shared material).
- [ ] `device.yaml` validates against `schema/device.schema.json` and agrees with the Markdown.
- [ ] Every claim is labelled Verified, Inferred or Unknown, with how it was verified (command + date, file + address, photo URL) or what would settle it.
- [ ] Firmware addresses name the exact image file and version.
- [ ] Vendor files are referenced by official URL, version and SHA-256; no binaries, dumps or raw captures are added.
- [ ] Serial numbers, Bluetooth/RF addresses, pairing keys, photo metadata and personal paths are removed.
- [ ] Anything not exercised on hardware is marked untested.
- [ ] Write procedures preserve the bootloader and unit-specific pages, never mass-erase, require a backup first, and name the recovery path (and whether it was tested).
- [ ] New tools have an offline test that runs in CI.
- [ ] The contribution is the author's own work and can be published under the MIT License.
