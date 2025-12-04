"""Platform for the KEF Wireless Speakers."""

from __future__ import annotations

from datetime import timedelta
from functools import partial
import ipaddress
import logging

from .aiokef import AsyncKefSpeaker
from .aiokef import DSP_OPTION_MAPPING
from getmac import get_mac_address
import voluptuous as vol

from tenacity import RetryError

from homeassistant.components.media_player import (
    PLATFORM_SCHEMA as MEDIA_PLAYER_PLATFORM_SCHEMA,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "KEF"
DEFAULT_PORT = 50001
DEFAULT_MAX_VOLUME = 1
DEFAULT_VOLUME_STEP = 0.05
DEFAULT_USE_CUSTOM_VOLUME_LADDER = True
DEFAULT_INVERSE_SPEAKER_MODE = False
DEFAULT_SUPPORTS_ON = True

DOMAIN = "kef"

SCAN_INTERVAL = timedelta(seconds=15)

SOURCES = {"LSX": ["Wifi", "Bluetooth", "Aux", "Opt"]}
SOURCES["LS50"] = SOURCES["LSX"] + ["Usb"]

CONF_MAX_VOLUME = "maximum_volume"
CONF_VOLUME_STEP = "volume_step"
CONF_USE_CUSTOM_VOLUME_LADDER = "use_custom_volume_ladder"
CONF_INVERSE_SPEAKER_MODE = "inverse_speaker_mode"
CONF_SUPPORTS_ON = "supports_on"
CONF_STANDBY_TIME = "standby_time"

SERVICE_MODE = "set_mode"
SERVICE_DESK_DB = "set_desk_db"
SERVICE_WALL_DB = "set_wall_db"
SERVICE_TREBLE_DB = "set_treble_db"
SERVICE_HIGH_HZ = "set_high_hz"
SERVICE_LOW_HZ = "set_low_hz"
SERVICE_SUB_DB = "set_sub_db"
SERVICE_UPDATE_DSP = "update_dsp"

DSP_SCAN_INTERVAL = timedelta(seconds=3600)

PLATFORM_SCHEMA = MEDIA_PLAYER_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_TYPE): vol.In(["LS50", "LSX"]),
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_MAX_VOLUME, default=DEFAULT_MAX_VOLUME): cv.small_float,
        vol.Optional(CONF_VOLUME_STEP, default=DEFAULT_VOLUME_STEP): cv.small_float,
        vol.Optional(
            CONF_USE_CUSTOM_VOLUME_LADDER, default=DEFAULT_USE_CUSTOM_VOLUME_LADDER
        ): cv.boolean,
        vol.Optional(
            CONF_INVERSE_SPEAKER_MODE, default=DEFAULT_INVERSE_SPEAKER_MODE
        ): cv.boolean,
        vol.Optional(CONF_SUPPORTS_ON, default=DEFAULT_SUPPORTS_ON): cv.boolean,
        vol.Optional(CONF_STANDBY_TIME): vol.In([20, 60]),
    }
)


def get_ip_mode(host):
    """Get the 'mode' used to retrieve the MAC address."""
    try:
        ip_address = ipaddress.ip_address(host)
    except ValueError:
        return "hostname"

    if ip_address.version == 6:
        return "ip6"
    return "ip"


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the KEF platform."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    host = config[CONF_HOST]
    speaker_type = config[CONF_TYPE]
    port = config[CONF_PORT]
    name = config[CONF_NAME]
    maximum_volume = config[CONF_MAX_VOLUME]
    volume_step = config[CONF_VOLUME_STEP]
    use_custom_volume_ladder = config[CONF_USE_CUSTOM_VOLUME_LADDER]
    inverse_speaker_mode = config[CONF_INVERSE_SPEAKER_MODE]
    supports_on = config[CONF_SUPPORTS_ON]
    standby_time = config.get(CONF_STANDBY_TIME)

    sources = SOURCES[speaker_type]

    _LOGGER.debug(
        "Setting up %s with host: %s, port: %s, name: %s, sources: %s",
        DOMAIN,
        host,
        port,
        name,
        sources,
    )

    mode = get_ip_mode(host)
    mac = await hass.async_add_executor_job(partial(get_mac_address, **{mode: host}))
    if mac is None or mac == "00:00:00:00:00:00":
        raise PlatformNotReady("Cannot get the ip address of kef speaker.")

    unique_id = f"kef-{mac}"

    media_player = KefMediaPlayer(
        name,
        host,
        port,
        maximum_volume,
        volume_step,
        use_custom_volume_ladder,
        standby_time,
        inverse_speaker_mode,
        supports_on,
        sources,
        speaker_type,
        loop=hass.loop,
        unique_id=unique_id,
    )

    if host in hass.data[DOMAIN]:
        _LOGGER.debug("%s is already configured", host)
    else:
        hass.data[DOMAIN][host] = media_player
        async_add_entities([media_player])

    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        SERVICE_MODE,
        {
            vol.Optional("desk_mode"): cv.boolean,
            vol.Optional("wall_mode"): cv.boolean,
            vol.Optional("phase_correction"): cv.boolean,
            vol.Optional("high_pass"): cv.boolean,
            vol.Optional("sub_polarity"): vol.In(["-", "+"]),
            vol.Optional("bass_extension"): vol.In(["Less", "Standard", "Extra"]),
        },
        "set_mode",
    )
    platform.async_register_entity_service(SERVICE_UPDATE_DSP, None, "update_dsp")

    def add_service(name, which, option):
        options = DSP_OPTION_MAPPING[which]
        dtype = type(options[0])  # int or float
        platform.async_register_entity_service(
            name,
            {
                vol.Required(option): vol.All(
                    vol.Coerce(float), vol.Coerce(dtype), vol.In(options)
                )
            },
            f"set_{which}",
        )

    add_service(SERVICE_DESK_DB, "desk_db", "db_value")
    add_service(SERVICE_WALL_DB, "wall_db", "db_value")
    add_service(SERVICE_TREBLE_DB, "treble_db", "db_value")
    add_service(SERVICE_HIGH_HZ, "high_hz", "hz_value")
    add_service(SERVICE_LOW_HZ, "low_hz", "hz_value")
    add_service(SERVICE_SUB_DB, "sub_db", "db_value")


class KefMediaPlayer(MediaPlayerEntity):
    """Kef Player Object."""

    _attr_icon = "mdi:speaker"

    def __init__(
        self,
        name,
        host,
        port,
        maximum_volume,
        volume_step,
        use_custom_volume_ladder,
        standby_time,
        inverse_speaker_mode,
        supports_on,
        sources,
        speaker_type,
        loop,
        unique_id,
    ):
        """Initialize the media player."""
        self._attr_name = name
        self._attr_source_list = sources
        self._speaker = AsyncKefSpeaker(
            host,
            port,
            volume_step=volume_step,
            maximum_volume=maximum_volume,
            standby_time=standby_time,
            inverse_speaker_mode=inverse_speaker_mode,
            use_custom_volume_ladder=use_custom_volume_ladder,
            loop=loop,
        )
        self._attr_unique_id = unique_id
        self._supports_on = supports_on
        self._speaker_type = speaker_type

        self._attr_available = False
        self._dsp = None
        self._update_dsp_task_remover = None

        self._play_state: str | None = None

        self._attr_supported_features = (
            MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.NEXT_TRACK  # only in Bluetooth and Wifi
            | MediaPlayerEntityFeature.PAUSE  # only in Bluetooth and Wifi
            | MediaPlayerEntityFeature.PLAY  # only in Bluetooth and Wifi
            | MediaPlayerEntityFeature.PREVIOUS_TRACK  # only in Bluetooth and Wifi
        )
        if supports_on:
            self._attr_supported_features |= MediaPlayerEntityFeature.TURN_ON

    async def async_update(self) -> None:
        """Update latest state."""
        _LOGGER.debug("Running async_update")
        try:
            # Try to see if we can talk to the speaker at all
            self._attr_available = await self._speaker.is_online()

            if self.available:
                status = await self._speaker.get_full_status()

                # Volume + source from the speaker object
                self._attr_is_volume_muted = self._speaker.is_muted
                self._attr_volume_level = self._speaker.volume
                self._attr_source = status["source"]

                # Cache play state (Wifi / Bluetooth only, per get_full_status)
                self._play_state = status.get("play_state")

                if not status["is_on"]:
                    # Fully off / standby
                    self._attr_state = MediaPlayerState.OFF
                else:
                    # Speaker is powered; refine based on play state
                    if self._play_state == "Playing":
                        self._attr_state = MediaPlayerState.PLAYING
                    elif self._play_state == "Paused":
                        self._attr_state = MediaPlayerState.PAUSED
                    elif self._play_state == "Stopped":
                        # Transport stopped but powered
                        self._attr_state = MediaPlayerState.IDLE
                    else:
                        # Fallback if we couldn't read play state
                        self._attr_state = MediaPlayerState.ON

                # No DSP calls here – keeps things snappy

            else:
                # We couldn't reach the speaker at all
                self._attr_is_volume_muted = None
                self._attr_source = None
                self._attr_volume_level = None
                self._attr_state = MediaPlayerState.OFF
                self._play_state = None

        except (ConnectionError, TimeoutError, RetryError, OSError) as err:
            # Anything ugly from aiokef / tenacity → just mark it unavailable
            _LOGGER.debug("Error in `update`: %s", err)
            self._attr_available = False
            self._attr_is_volume_muted = None
            self._attr_source = None
            self._attr_volume_level = None
            self._attr_state = MediaPlayerState.OFF
            self._play_state = None

    async def async_turn_off(self) -> None:
        """Turn the media player off."""
        await self._speaker.turn_off()

    async def async_turn_on(self) -> None:
        """Turn the media player on."""
        if not self._supports_on:
            raise NotImplementedError
        await self._speaker.turn_on()

    async def async_volume_up(self) -> None:
        """Volume up the media player."""
        await self._speaker.increase_volume()

    async def async_volume_down(self) -> None:
        """Volume down the media player."""
        await self._speaker.decrease_volume()

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1."""
        await self._speaker.set_volume(volume)

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute (True) or unmute (False) media player."""
        if mute:
            await self._speaker.mute()
        else:
            await self._speaker.unmute()

    async def async_select_source(self, source: str) -> None:
        """Select input source."""
        if self.source_list is not None and source in self.source_list:
            await self._speaker.set_source(source)
        else:
            raise ValueError(f"Unknown input source: {source}.")

    async def async_media_play(self) -> None:
        """Send play command."""
        await self._speaker.set_play_pause()

    async def async_media_pause(self) -> None:
        """Send pause command."""
        await self._speaker.set_play_pause()

    async def async_media_previous_track(self) -> None:
        """Send previous track command."""
        await self._speaker.prev_track()

    async def async_media_next_track(self) -> None:
        """Send next track command."""
        await self._speaker.next_track()

    async def update_dsp(self, _=None) -> None:
        """Update the DSP settings."""
        if self._speaker_type == "LS50" and self.state == MediaPlayerState.OFF:
            # The LSX is able to respond when off the LS50 has to be on.
            return

        mode = await self._speaker.get_mode()
        self._dsp = {
            "desk_db": await self._speaker.get_desk_db(),
            "wall_db": await self._speaker.get_wall_db(),
            "treble_db": await self._speaker.get_treble_db(),
            "high_hz": await self._speaker.get_high_hz(),
            "low_hz": await self._speaker.get_low_hz(),
            "sub_db": await self._speaker.get_sub_db(),
            **mode._asdict(),
        }

    @property
    def extra_state_attributes(self):
        """Return extra info about the KEF device."""
        attrs = {}
        if self._play_state is not None:
            attrs["play_state"] = self._play_state
        return attrs

    async def set_mode(
        self,
        desk_mode=None,
        wall_mode=None,
        phase_correction=None,
        high_pass=None,
        sub_polarity=None,
        bass_extension=None,
    ):
        """Set the speaker mode."""
        await self._speaker.set_mode(
            desk_mode=desk_mode,
            wall_mode=wall_mode,
            phase_correction=phase_correction,
            high_pass=high_pass,
            sub_polarity=sub_polarity,
            bass_extension=bass_extension,
        )
        self._dsp = None

    async def set_desk_db(self, db_value):
        """Set desk_db of the KEF speakers."""
        await self._speaker.set_desk_db(db_value)
        self._dsp = None

    async def set_wall_db(self, db_value):
        """Set wall_db of the KEF speakers."""
        await self._speaker.set_wall_db(db_value)
        self._dsp = None

    async def set_treble_db(self, db_value):
        """Set treble_db of the KEF speakers."""
        await self._speaker.set_treble_db(db_value)
        self._dsp = None

    async def set_high_hz(self, hz_value):
        """Set high_hz of the KEF speakers."""
        await self._speaker.set_high_hz(hz_value)
        self._dsp = None

    async def set_low_hz(self, hz_value):
        """Set low_hz of the KEF speakers."""
        await self._speaker.set_low_hz(hz_value)
        self._dsp = None

    async def set_sub_db(self, db_value):
        """Set sub_db of the KEF speakers."""
        await self._speaker.set_sub_db(db_value)
        self._dsp = None
