"""Platform for the KEF Wireless Speakers."""

from __future__ import annotations

from datetime import timedelta
import logging

from .aiokef import AsyncKefSpeaker
import voluptuous as vol

from tenacity import RetryError

from homeassistant.components.media_player import (
    PLATFORM_SCHEMA as MEDIA_PLAYER_PLATFORM_SCHEMA,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_TYPE, CONF_UNIQUE_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
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
        vol.Optional(CONF_UNIQUE_ID): cv.string,
    }
)

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

    configured_unique_id = config.get(CONF_UNIQUE_ID)
    if configured_unique_id:
        unique_id = configured_unique_id
    else:
        # Build a stable fallback ID based on type + host + port
        safe_host = host.replace(":", "_").replace(".", "_")
        safe_type = speaker_type.lower()
        unique_id = f"kef-{safe_type}-{safe_host}-{port}"

    _LOGGER.debug(
        "Setting up %s with host: %s, port: %s, name: %s, sources: %s",
        DOMAIN,
        host,
        port,
        name,
        sources,
    )

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
        async_add_entities([media_player], update_before_add=True)


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

            # After switching to Wifi/Bluetooth, do a one-shot play state read
            if source in ("Wifi", "Bluetooth"):
                status = await self._speaker.get_full_status()
                self._play_state = status.get("play_state")
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

    @property
    def extra_state_attributes(self):
        """Return extra info about the KEF device."""
        attrs = {}
        if self._play_state is not None:
            attrs["play_state"] = self._play_state
        return attrs
