"""End-to-end checks inside a real Home Assistant 2026.11.2 test instance. No hardware."""
import asyncio, json, pathlib
import pytest, probatio
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed, mock_restore_cache_with_extra_data
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from datetime import timedelta

DOMAIN = "esi_r2d7"
ROOT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
ESPHOME_URL = "esphome-hass://esphome/01JABCDEFAUX2?port_name=RS-232+Port+1"


def _entry(url, first=1, last=16, system=1):
    return MockConfigEntry(domain=DOMAIN, title="ESI R2D7 System 1",
        data={CONF_DEVICE: url, "system": system, "first_channel": first, "last_channel": last})


async def _setup(hass, url, **kw):
    e = _entry(url, **kw); e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id); await hass.async_block_till_done()
    return e


# ---------- packaging / UI text ----------
async def test_translations_reach_the_frontend(hass: HomeAssistant):
    """The universal-STOP warning lives only in strings.json. Is it ever shown?"""
    tr = await async_get_translations(hass, "en", "config", [DOMAIN])
    assert f"component.{DOMAIN}.config.step.user.description" in tr, "no config-flow text is loaded: strings.json is not read for custom integrations (needs translations/en.json)"


def test_manifest_iot_class_matches_behaviour():
    m = json.loads((ROOT / "manifest.json").read_text())
    assert m["iot_class"] == "assumed_state", m["iot_class"]


def test_manifest_has_no_exact_serialx_pin():
    m = json.loads((ROOT / "manifest.json").read_text())
    assert not any(r.startswith("serialx==") for r in m.get("requirements", [])), m["requirements"]


@pytest.mark.xfail(strict=True, reason="REVIEW L4: needs a real repository URL and owner")
def test_manifest_custom_integration_fields():
    m = json.loads((ROOT / "manifest.json").read_text())
    missing = [k for k in ("issue_tracker",) if k not in m]
    assert not missing and m["codeowners"], f"missing={missing} codeowners={m['codeowners']}"


# ---------- config flow ----------
async def test_flow_form_schema_serializes_for_frontend(hass):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.FORM
    fields = probatio.to_field_list(r["data_schema"], custom_serializer=cv.custom_serializer)
    print("FIELDS", fields)
    assert [f["name"] for f in fields] == ["device", "system", "first_channel", "last_channel"]


async def test_flow_happy_path_opens_port_but_sends_nothing(hass, r2d7):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_DEVICE: r2d7.url, "system": 1, "first_channel": 1, "last_channel": 16})
    await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert bytes(r2d7.rx) == b""  # nothing proves an R2D7 is attached


async def test_flow_inverted_range(hass, r2d7):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_DEVICE: r2d7.url, "system": 1, "first_channel": 9, "last_channel": 2})
    assert r["errors"] == {"base": "invalid_channel_range"}


@pytest.mark.parametrize("bad", [{"system": 0}, {"system": 8}, {"first_channel": 0}, {"last_channel": 61}])
async def test_flow_bounds(hass, r2d7, bad):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    data = {CONF_DEVICE: r2d7.url, "system": 1, "first_channel": 1, "last_channel": 16} | bad
    with pytest.raises(Exception) as ei:
        await hass.config_entries.flow.async_configure(r["flow_id"], data)
    assert "Invalid" in type(ei.value).__name__ or "Invalid" in str(type(ei.value).__mro__)


async def test_flow_esphome_not_loaded_shows_form_error(hass):
    """usb stub handler raises ConfigEntryNotReady when ESPHome has not loaded."""
    assert await async_setup_component(hass, "usb", {})
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_DEVICE: ESPHOME_URL, "system": 1, "first_channel": 1, "last_channel": 16})
    assert r.get("errors") == {"base": "cannot_connect"}, r


async def test_flow_serialexception_shows_form_error(hass):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_DEVICE: "nonsense://x", "system": 1, "first_channel": 1, "last_channel": 16})
    assert r.get("errors") == {"base": "cannot_connect"}, r


@pytest.mark.xfail(strict=True, reason="REVIEW M2: multi-system hub not implemented")
async def test_flow_second_system_on_same_bus(hass, r2d7):
    await _setup(hass, r2d7.url)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_DEVICE: r2d7.url, "system": 2, "first_channel": 1, "last_channel": 4})
    assert r["type"] is FlowResultType.CREATE_ENTRY, f"system 2 on the same port is refused: {r['type']} {r.get('reason')}"


@pytest.mark.xfail(strict=True, reason="REVIEW M1: not implemented")
async def test_reconfigure_flow_exists(hass, r2d7):
    from custom_components.esi_r2d7.config_flow import EsiR2D7ConfigFlow
    assert hasattr(EsiR2D7ConfigFlow, "async_step_reconfigure"), "channel range/port cannot be changed without deleting the entry"


# ---------- setup / entities / services ----------
async def test_setup_entities_and_dispatch(hass, r2d7):
    e = await _setup(hass, r2d7.url)
    assert e.state is ConfigEntryState.LOADED
    covers = sorted(hass.states.async_entity_ids("cover"))
    assert len(covers) == 16
    st = hass.states.get(covers[0])
    print("STATE", covers[0], st.state, dict(st.attributes))
    assert st.state == "unknown"
    await hass.services.async_call("cover", "open_cover", {"entity_id": covers[0]}, blocking=True)
    await hass.services.async_call("cover", "close_cover", {"entity_id": covers[15]}, blocking=True)
    await hass.services.async_call("cover", "stop_cover", {"entity_id": covers[5]}, blocking=True)
    await asyncio.sleep(0.05)
    assert bytes(r2d7.rx) == b"*1o01;*1c16;*1s;"
    assert "current_position" not in hass.states.get(covers[0]).attributes  # no position invented
    assert await hass.config_entries.async_unload(e.entry_id)
    assert e.state is ConfigEntryState.NOT_LOADED
    assert await hass.config_entries.async_setup(e.entry_id)
    assert e.state is ConfigEntryState.LOADED
    await hass.config_entries.async_unload(e.entry_id)


async def test_every_channel_sends_the_same_universal_stop(hass, r2d7):
    e = await _setup(hass, r2d7.url, first=3, last=6)
    for c in sorted(hass.states.async_entity_ids("cover")):
        await hass.services.async_call("cover", "stop_cover", {"entity_id": c}, blocking=True)
        await hass.services.async_call("cover", "open_cover", {"entity_id": c}, blocking=True)
    await asyncio.sleep(0.05)
    await hass.config_entries.async_unload(e.entry_id)
    assert bytes(r2d7.rx) == b"*1s;*1o03;*1s;*1o04;*1s;*1o05;*1s;*1o06;"


async def test_stop_all_button_and_stop_scope(hass, r2d7):
    e = await _setup(hass, r2d7.url, first=4, last=5)
    buttons = hass.states.async_entity_ids("button")
    assert buttons == ["button.esi_r2d7_system_1_stop_all_blinds"], buttons
    for c in hass.states.async_entity_ids("cover"):
        assert hass.states.get(c).attributes["stop_scope"] == "all blinds on system 1"
    await hass.services.async_call("button", "press", {"entity_id": buttons[0]}, blocking=True)
    await asyncio.sleep(0.05)
    assert bytes(r2d7.rx) == b"*1s;"
    reg = er.async_get(hass)
    devices = {x.device_id for x in er.async_entries_for_config_entry(reg, e.entry_id)}
    assert len(devices) == 1                       # button sits on the same device as the blinds
    await r2d7.drop_clients(); await asyncio.sleep(0.1); await hass.async_block_till_done()
    assert hass.states.get(buttons[0]).state == "unavailable"
    await hass.config_entries.async_unload(e.entry_id)


def _lc(hass, ch):
    st = hass.states.get(f"cover.esi_r2d7_system_1_channel_{ch:02d}")
    return st.state, st.attributes.get("last_command")


async def _call(hass, service, ch):
    await hass.services.async_call("cover", service, {"entity_id": f"cover.esi_r2d7_system_1_channel_{ch:02d}"}, blocking=True)


async def test_state_shows_last_command(hass, r2d7):
    e = await _setup(hass, r2d7.url, first=4, last=6)
    assert _lc(hass, 4) == ("unknown", None)          # nothing sent yet
    await _call(hass, "open_cover", 4)
    assert _lc(hass, 4) == ("open", "open")
    await _call(hass, "close_cover", 4)
    assert _lc(hass, 4) == ("closed", "closed")
    await _call(hass, "stop_cover", 4)
    assert _lc(hass, 4) == ("open", "stopped")        # card shows Open, attribute says stopped
    assert hass.states.get("cover.esi_r2d7_system_1_channel_04").attributes["assumed_state"] is True
    await hass.config_entries.async_unload(e.entry_id)


async def test_stop_marks_recently_moved_blinds_only(hass, r2d7, freezer):
    e = await _setup(hass, r2d7.url, first=4, last=7)
    await _call(hass, "close_cover", 4)                # long ago: finished travelling
    freezer.tick(600)
    # the connection times moves on the event-loop clock, so age that entry directly
    e.runtime_data._moved_at[4] -= 600
    await _call(hass, "close_cover", 5)                # just now: may still be moving
    await _call(hass, "stop_cover", 6)                 # Stop pressed on a third blind
    await hass.async_block_till_done()
    assert _lc(hass, 4) == ("closed", "closed")        # idle blind keeps its state
    assert _lc(hass, 5) == ("open", "stopped")         # caught by the universal Stop
    assert _lc(hass, 6) == ("open", "stopped")         # the one pressed
    assert _lc(hass, 7) == ("unknown", None)           # never commanded
    await hass.config_entries.async_unload(e.entry_id)


async def test_stop_all_button_marks_recently_moved(hass, r2d7):
    e = await _setup(hass, r2d7.url, first=4, last=5)
    await _call(hass, "close_cover", 4)
    await hass.services.async_call("button", "press", {"entity_id": "button.esi_r2d7_system_1_stop_all_blinds"}, blocking=True)
    await hass.async_block_till_done()
    assert _lc(hass, 4) == ("open", "stopped")
    assert _lc(hass, 5) == ("unknown", None)
    await hass.config_entries.async_unload(e.entry_id)


async def test_failed_command_does_not_change_state(hass, r2d7):
    e = await _setup(hass, r2d7.url, first=4, last=4)
    await _call(hass, "close_cover", 4)

    async def dead(_):
        raise OSError(5, "link gone")
    e.runtime_data._serial.write = dead
    with pytest.raises(HomeAssistantError):
        await _call(hass, "open_cover", 4)
    assert e.runtime_data.take_recently_moved(120) == {4}   # only the Close that was really sent
    await hass.config_entries.async_unload(e.entry_id)


async def test_last_command_survives_restart_and_link_loss(hass, r2d7):
    mock_restore_cache_with_extra_data(hass, [
        (State("cover.esi_r2d7_system_1_channel_04", "unavailable"), {"last_command": "closed"}),
        (State("cover.esi_r2d7_system_1_channel_05", "open"), {"last_command": "stopped"}),
        (State("cover.esi_r2d7_system_1_channel_06", "open"), {"last_command": "bogus"}),
    ])
    e = await _setup(hass, r2d7.url, first=4, last=6)
    assert _lc(hass, 4) == ("closed", "closed")        # restored even though it was unavailable
    assert _lc(hass, 5) == ("open", "stopped")
    assert _lc(hass, 6) == ("unknown", None)           # junk is ignored
    # link drop -> unavailable -> reload -> state comes back
    await r2d7.drop_clients(); await asyncio.sleep(0.1); await hass.async_block_till_done()
    assert hass.states.get("cover.esi_r2d7_system_1_channel_04").state == "unavailable"
    await _tick(hass, 6)
    assert e.state is ConfigEntryState.LOADED
    assert _lc(hass, 4) == ("closed", "closed")
    await hass.config_entries.async_unload(e.entry_id)


async def test_attribute_translations_load(hass):
    tr = await async_get_translations(hass, "en", "entity", [DOMAIN])
    k = "component.esi_r2d7.entity.cover.blind.state_attributes.last_command.state."
    assert (tr[k + "open"], tr[k + "closed"], tr[k + "stopped"]) == ("Open", "Closed", "Stopped")


async def test_brand_icon_files_are_valid(hass):
    """HA 2026.3+ serves custom_components/<domain>/brand/icon.png through its brands API."""
    from homeassistant.components.brands.const import ALLOWED_IMAGES
    brand = ROOT / "brand"
    files = {f.name for f in brand.iterdir()}
    assert "icon.png" in files and files <= ALLOWED_IMAGES, files
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        data = (brand / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert int.from_bytes(data[16:20], "big") == size == int.from_bytes(data[20:24], "big")


async def test_cover_declares_assumed_state(hass, r2d7):
    e = await _setup(hass, r2d7.url, last=1)
    st = hass.states.get(hass.states.async_entity_ids("cover")[0])
    await hass.config_entries.async_unload(e.entry_id)
    assert st.attributes.get("assumed_state") is True, dict(st.attributes)


async def test_stop_on_all_covers_sends_one_frame(hass, r2d7):
    """'Stop all blinds' via area/all target issues one universal stop per entity."""
    e = await _setup(hass, r2d7.url)
    await hass.services.async_call("cover", "stop_cover", {"entity_id": "all"}, blocking=True)
    await asyncio.sleep(0.05)
    n = bytes(r2d7.rx).count(b"*1s;")
    await hass.config_entries.async_unload(e.entry_id)
    assert n == 1, f"{n} universal STOP frames sent for one user action"


async def test_setup_retries_when_esphome_entry_not_loaded(hass):
    """Real ESPHome handler registered, but the AUX-2 entry is not loaded yet (boot race / AUX-2 offline)."""
    from homeassistant.components.esphome import serial_proxy
    unregister = serial_proxy.register_serialx_transport(hass.loop)
    try:
        e = await _setup(hass, ESPHOME_URL)
        assert e.state is ConfigEntryState.SETUP_RETRY, f"{e.state} reason={e.reason!r}"
    finally:
        unregister(None)


async def test_setup_retries_with_usb_stub(hass):
    assert await async_setup_component(hass, "usb", {})
    e = await _setup(hass, ESPHOME_URL)
    assert e.state is ConfigEntryState.SETUP_RETRY, f"{e.state} reason={e.reason!r}"


async def _tick(hass, seconds):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()


async def test_link_loss_goes_unavailable_then_recovers(hass, r2d7):
    e = await _setup(hass, r2d7.url, last=2)
    cover = sorted(hass.states.async_entity_ids("cover"))[0]
    await r2d7.drop_clients()
    await asyncio.sleep(0.1); await hass.async_block_till_done()
    assert hass.states.get(cover).state == "unavailable"   # honest, before any command is sent
    await _tick(hass, 6)                                     # delayed reload
    assert e.state is ConfigEntryState.LOADED
    assert hass.states.get(cover).state == "unknown"
    await hass.services.async_call("cover", "stop_cover", {"entity_id": cover}, blocking=True)
    await asyncio.sleep(0.05)
    assert bytes(r2d7.rx) == b"*1s;"
    await hass.config_entries.async_unload(e.entry_id)


async def test_link_loss_with_controller_gone_retries_setup(hass, r2d7):
    e = await _setup(hass, r2d7.url, last=2)
    cover = sorted(hass.states.async_entity_ids("cover"))[0]
    await r2d7.stop()
    await asyncio.sleep(0.1); await hass.async_block_till_done()
    assert hass.states.get(cover).state == "unavailable"
    await _tick(hass, 6)
    assert e.state is ConfigEntryState.SETUP_RETRY, e.state
    await r2d7.start()
    await _tick(hass, 120)                                   # HA's own retry backoff
    assert e.state is ConfigEntryState.LOADED, e.state
    await hass.config_entries.async_unload(e.entry_id)


async def test_send_failure_is_a_homeassistant_error(hass, r2d7):
    e = await _setup(hass, r2d7.url, last=2)
    cover = sorted(hass.states.async_entity_ids("cover"))[0]

    async def dead(_):
        raise OSError(5, "link gone")
    e.runtime_data._serial.write = dead
    with pytest.raises(HomeAssistantError, match="may not have reached"):
        await hass.services.async_call("cover", "open_cover", {"entity_id": cover}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(cover).state == "unavailable"
    await hass.config_entries.async_unload(e.entry_id)


async def test_homeassistant_stop_closes_the_port(hass, r2d7):
    await _setup(hass, r2d7.url, last=1)
    await hass.async_stop()
    await asyncio.sleep(0.1)
    assert r2d7.live == 0


@pytest.mark.xfail(strict=True, reason="REVIEW M5: HA does not report a failed platform to the integration")
async def test_failed_platform_setup_does_not_leak_port(hass, r2d7):
    from unittest.mock import patch
    with patch("custom_components.esi_r2d7.cover.async_setup_entry", side_effect=RuntimeError("boom")):
        e = await _setup(hass, r2d7.url)
    await asyncio.sleep(0.1)
    print("STATE", e.state, "open clients", r2d7.live)
    assert r2d7.live == 0, "serial port left open after failed setup"


@pytest.mark.xfail(strict=True, reason="REVIEW M1: not implemented")
async def test_entity_ids_survive_delete_and_readd(hass, r2d7):
    e = await _setup(hass, r2d7.url, last=2)
    reg = er.async_get(hass)
    before = sorted(x.unique_id for x in er.async_entries_for_config_entry(reg, e.entry_id))
    await hass.config_entries.async_remove(e.entry_id); await hass.async_block_till_done()
    e2 = await _setup(hass, r2d7.url, last=2)
    after = sorted(x.unique_id for x in er.async_entries_for_config_entry(reg, e2.entry_id))
    await hass.config_entries.async_unload(e2.entry_id)
    assert before == after, "unique_id embeds entry_id; room names/mapping are lost on re-add"


async def test_port_url_not_logged(hass, caplog):
    assert await async_setup_component(hass, "usb", {})
    await _setup(hass, ESPHOME_URL + "&key=SECRETKEY123")
    assert "SECRETKEY123" not in caplog.text
