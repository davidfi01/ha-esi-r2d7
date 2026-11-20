# ESI R2D7 Serial — Home Assistant custom integration

Home Assistant integration for ESI R2D7 RS-232 blind controllers. It follows the configuration, connection and entity design of Home Assistant's [Denon RS-232 integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/denon_rs232). It works with the **stock Home Assistant Connect AUX-2 ESPHome serial proxy** through the Home Assistant serial port selector, and with a local serial port. **No AUX-2 firmware change is required**; Bluetooth proxy, audio, Z-Wave proxy and other AUX-2 functions remain intact.

**Requires Home Assistant 2026.11.0 or newer.** The integration uses the serialx version that Home Assistant itself ships; it does not pin its own.

## CRITICAL: STOP IS SYSTEM-WIDE

Each blind entity provides **Open, Close and Stop**. Open and Close address the selected channel. **Pressing Stop on ANY blind stops ALL moving blinds belonging to that R2D7 system**, not only that blind. The [published R2D7 v4.1 protocol](https://www.seymourscreenexcellence.com/documents/r2d7_v4.1_protocol.pdf), footnote 15, says STOP is universal and has no channel; a suffix such as `*1s05;` is ignored. This integration sends the documented `*1s;`. Do not use per-cover Stop if other blinds on the system must keep moving.

Because Stop is system-wide, each R2D7 system also gets a **Stop ALL blinds** button, every blind shows a `stop_scope` attribute saying so, and stops requested together are sent as a single command. Confirmed on real hardware (November 2026): both `*1s;` and the per-blind form `*1sNN;` stop every moving blind.

## Install

### From the Home Assistant Marketplace

1. Open **Settings → Marketplace** and search for **ESI R2D7 Serial**. Until the integration is in the default catalog, add this repository as a custom repository: `https://github.com/davidfi01/ha-esi-r2d7`, category **Integration**.
2. Install it, then restart Home Assistant.

### Manual

1. Copy the directory `custom_components/esi_r2d7` from this repository to Home Assistant's `/config/custom_components/esi_r2d7`.
2. Restart Home Assistant.

## Set up

1. Connect the ESI R2D7's RS-232 interface to **one** serial port, for example an AUX-2 RS-232 port. Check the pinout and signal direction. Do not connect two active controllers to the same R2D7 serial input at the same time.
2. If you use an AUX-2: add it through Home Assistant's **ESPHome integration**, with its official firmware unchanged. Open **Settings → Connectivity → Serial** and confirm that the AUX-2 RS-232 proxy port is listed.
3. Go to **Settings → Devices & services → Add integration → ESI R2D7 Serial**. Select the port. Choose the R2D7 **system** number (1–7) and the first and last **channel** (1–60). The link runs at **9600 baud, 8 data bits, no parity, 1 stop bit, no flow control**.
4. Rename the `Channel NN` blinds for your rooms after you confirm which channel drives which blind. Test one blind's Open, Close and Stop while no other blind is moving.

## Capabilities and limitations

- Every configured channel has `cover.open_cover`, `cover.close_cover` and `cover.stop_cover` actions.
- Commands on System 1 are `*1o01;`, `*1c01;`, etc.; STOP always sends `*1s;` for System 1.
- The serial connection is shared and command writes are serialized, so simultaneous requests cannot interleave bytes.
- The R2D7 reports nothing back, so each blind shows the **last command sent**, not a measured position: Open after Open, Closed after Close. Home Assistant has no "Stopped" state for blinds, so after a Stop the blind shows Open and its `last_command` attribute reads `stopped`. No percent-open value is ever invented.
- A Stop marks as stopped the blind it was pressed on plus any blind sent Open or Close in the previous 2 minutes (`RECENT_MOVE_SECONDS` in `const.py`). The last command is remembered across restarts.
- The shown state will be wrong if a blind is moved by a wall switch, a remote or another controller, or if it does not respond. Until a first command is sent, a blind shows Unknown.
- Initial port-open failures are reported as integration-not-ready and Home Assistant retries them.
- The integration watches the serial link. When it drops (AUX-2 reboot, network loss) the blinds show as unavailable, and the entry reloads itself until the link is back.
- If a write fails, Stop is sent again once after reconnecting. Open and Close are not resent; you get an error instead, because a repeated Open or Close can act as a stop on some motor controls.
- Replies from the controller (`U` = command not understood, `O` = buffer overflow, X-off) are written to the Home Assistant log. They are not yet acted on.
- Commands are not paced. The controller has a 256-byte buffer and discards everything on overflow, so avoid moving more than about 20 blinds in one action.
- Setup only confirms that the serial port opens. It cannot confirm that an R2D7 is attached.
- If a command fails, the error says the command may not have reached the controller. A write can fail after some bytes went out, so the integration cannot know. Check the blind before you send Open or Close again.
- Tested with Home Assistant 2026.11.2 and an AUX-2 on stock firmware. Open, Close and Stop are confirmed working, and Stop is confirmed universal.
- Not yet exercised on hardware: the Stop ALL button, recovery after the AUX-2 drops off the network, and actions that move many blinds at once.

## Known limitations (open items)

- No command pacing yet (see the 256-byte buffer note above).
- After the link drops and the integration reloads, it forgets which blinds moved in the last 2 minutes. A Stop then marks only the blind it was pressed on as stopped. The blinds themselves still stop.
- The GitHub checks validate the files only; they do not run the tests.

## Files

`manifest.json` declares a serial integration; `translations/en.json` holds the UI text (generated from `strings.json` by `build_package.py`); `config_flow.py` uses `SerialPortSelector()`; `__init__.py` manages the serial connection; `protocol.py` implements the command grammar using `serialx`, which Home Assistant extends for `esphome-hass://` URLs; `cover.py` builds the blind entities; and `button.py` builds the Stop ALL button.

## Tests

`tests/` holds protocol unit tests. `tests_review/` holds Home Assistant integration tests; they need Python 3.14.2 or newer, `homeassistant==2026.11.2` and the matching `pytest-homeassistant`. They use loopback sockets only, with no hardware.

## Issues

Report problems at <https://github.com/davidfi01/ha-esi-r2d7/issues>.

## References

- [Official serial connectivity](https://www.home-assistant.io/connectivity/serial/)
- [Denon RS-232 reference implementation](https://github.com/home-assistant/core/tree/dev/homeassistant/components/denon_rs232)
- [Home Assistant ESPHome serial proxy URI handler](https://github.com/home-assistant/core/blob/dev/homeassistant/components/esphome/serial_proxy.py)
- [R2D7 v4.1 protocol specification](https://www.seymourscreenexcellence.com/documents/r2d7_v4.1_protocol.pdf)

## License

Apache License 2.0. See `LICENSE`.
