from json import JSONDecodeError
from typing import Optional

import httpx
import voluptuous as vol
from homeassistant.components.camera import Camera, PLATFORM_SCHEMA, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ALIAS, DOMAIN, LOGGER, IPCAMLIVE_STREAM_STATE_URL, \
    GET_IMAGE_TIMEOUT, ATTR_ALIAS

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_ALIAS): cv.string,
        vol.Optional(CONF_NAME, default=''): cv.string,
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a IPCamLive Camera based on a config entry."""
    async_add_entities(
        [
            IPCamLiveCamera(
                name=entry.title,
                alias=entry.options[CONF_ALIAS],
                unique_id=entry.entry_id,
            )
        ],
    )


class IPCamLiveStreamState:
    def __init__(self, stream_available: bool, address: str, stream_id: str):
        self.stream_available: bool = stream_available
        self.address: str = address
        self.stream_id: str = stream_id

    @classmethod
    async def async_from_alias(cls, hass, alias: str) -> Optional['IPCamLiveStreamState']:
        try:
            async_client = get_async_client(hass, verify_ssl=True)
            response = await async_client.get(IPCAMLIVE_STREAM_STATE_URL, params={
                'alias': alias,
            })
            response.raise_for_status()
            data = response.json()
            details = data.get('details')
            return cls(
                stream_available=details.get('streamavailable') == "1",
                address=details.get('address'),
                stream_id=details.get('streamid'),
            )
        except JSONDecodeError:
            LOGGER.warning(f'No stream found with alias `{alias}`')
        return None

    def is_available(self) -> bool:
        return self.stream_available

    def get_stream_url(self) -> Optional[str]:
        if self.stream_available:
            return f'{self.address}streams/{self.stream_id}/stream.m3u8'
        return None

    def get_snaphsot_url(self) -> Optional[str]:
        if self.stream_available:
            return f'{self.address}streams/{self.stream_id}/snapshot.jpg'
        return None


class IPCamLiveCamera(Camera):
    # Entity Properties
    _attr_name: Optional[str] = None

    def __init__(
            self,
            *,
            name: str,
            alias: str,
            unique_id: str,
    ) -> None:
        """Initialize an IPCamLive camera."""
        super().__init__()
        self._attr_name = name
        self._attr_alias = alias
        self._attr_supported_features = CameraEntityFeature.STREAM
        if unique_id is not None:
            self._attr_unique_id = unique_id

    @property
    def should_poll(self) -> bool:
        """Override should_poll so that async_update() is called periodically"""
        return True

    @property
    def extra_state_attributes(self):
        return {
            ATTR_ALIAS: self._attr_alias,
        }

    @property
    def name(self) -> str:
        """Return the name of this device."""
        return self._attr_name

    @property
    def alias(self) -> str:
        """Return the alias of this device."""
        return self._attr_alias

    async def async_camera_image(
            self,
            width: Optional[int] = None,
            height: Optional[int] = None,
    ) -> Optional[bytes]:
        """Return a still image response from the camera."""
        stream_state = await IPCamLiveStreamState.async_from_alias(hass=self.hass, alias=self._attr_alias)
        if not stream_state or not stream_state.is_available():
            self._attr_is_streaming = False
            return None
        self._attr_is_streaming = True
        snapshot_url = stream_state.get_snaphsot_url()
        try:
            async_client = get_async_client(self.hass, verify_ssl=True)
            response = await async_client.get(
                snapshot_url, timeout=GET_IMAGE_TIMEOUT
            )
            response.raise_for_status()
            return response.content
        except httpx.TimeoutException:
            LOGGER.error("Timeout getting camera image from %s", self._attr_name)
        except (httpx.RequestError, httpx.HTTPStatusError) as err:
            LOGGER.error("Error getting new camera image from %s: %s", self._attr_name, err)
        return None

    async def stream_source(self) -> str:
        """Return the source of the stream."""
        stream_state = await IPCamLiveStreamState.async_from_alias(hass=self.hass, alias=self._attr_alias)
        if not stream_state or not stream_state.is_available():
            self._attr_is_streaming = False
            LOGGER.error("Error getting stream url from %s", self._attr_name)
            return None
        self._attr_is_streaming = True
        stream_url = stream_state.get_stream_url()
        return stream_url

    async def async_update(self) -> None:
        """Update the stream source when IPCamLive's stream url has changed."""
        if self.stream:
            new_url = await self.stream_source()
            if self.stream.source != new_url:
                self.stream.update_source(new_url)
                LOGGER.info(f'Updated IPCamLive stream_source for {self._attr_alias} to "{new_url}"')
