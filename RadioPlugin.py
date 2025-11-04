# RadioPlugin v1.4.1 — Added Radio stations:
# SomaFM Groove Salad
# GalNET Radio

import vlc
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from lib.PluginBase import PluginBase
from lib.PluginHelper import PluginHelper, PluginManifest
from lib.Event import Event, ProjectedEvent
from lib.EventManager import Projection
from lib.Logger import log

# Pre-installed radio stations
RADIO_STATIONS = {
    "Radio Sidewinder": "https://radiosidewinder.out.airtime.pro:8000/radiosidewinder_b",
    "Hutton Orbital Radio": "https://quincy.torontocast.com/hutton",
    "SomaFM Deep Space One": "https://ice.somafm.com/deepspaceone",
    "SomaFM Groove Salad": "https://ice.somafm.com/groovesalad",
    "GalNET Radio": "http://listen.radionomy.com/galnet"
}

# Logging configuration
PLUGIN_LOG_LEVEL = "INFO"
_LEVELS = {"DEBUG": 10, "INFO": 20, "ERROR": 40}

def p_log(level: str, *args):
    """Filtered logging based on PLUGIN_LOG_LEVEL."""
    try:
        lvl = _LEVELS.get(level.upper(), 999)
        threshold = _LEVELS.get(PLUGIN_LOG_LEVEL.upper(), 999)
        if lvl >= threshold:
            log(level, *args)
    except Exception:
        pass

# Event emitted when the radio track changes
@dataclass
class RadioChangedEvent(Event):
    station: str
    title: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    kind: Literal['tool'] = 'tool'
    text: list[str] = field(default_factory=list)
    processed_at: float = 0.0
    memorized_at: str = None
    responded_at: str = None

    def __post_init__(self):
        self.text = [f"Radio changed to {self.station}: {self.title}"]

    def __str__(self) -> str:
        return self.text[0]

# Projection to track current radio state
class CurrentRadioState(Projection[dict[str, Any]]):
    def get_default_state(self) -> dict[str, Any]:
        return {"station": "", "title": "", "playing": False}

    def process(self, event: Event) -> list[ProjectedEvent]:
        projected = []
        if isinstance(event, RadioChangedEvent):
            self.state["station"] = event.station
            self.state["title"] = event.title
            self.state["playing"] = True
            projected.append(ProjectedEvent({
                "event": "RadioChanged",
                "station": event.station,
                "title": event.title
            }))
        return projected

# Main plugin class
class RadioPlugin(PluginBase):
    def __init__(self, plugin_manifest: PluginManifest):
        super().__init__(plugin_manifest, event_classes=[RadioChangedEvent])
        self.current_station = None
        self.player = None
        self.playing = False
        self.track_monitor_thread = None
        self.stop_monitor = False

    # Register actions for play, stop, change radio, and set volume
    def register_actions(self, helper: PluginHelper):
        helper.register_action(
            "play_radio",
            "Play a webradio station",
            {
                "type": "object",
                "properties": {
                    "station": {"type": "string", "enum": list(RADIO_STATIONS.keys())}
                },
                "required": ["station"]
            },
            lambda args, states: self._start_radio(RADIO_STATIONS.get(args["station"]), args["station"], helper),
            "global"
        )
        helper.register_action(
            "stop_radio",
            "Stop the radio",
            {},
            lambda args, states: self._stop_radio(),
            "global"
        )
        helper.register_action(
            "change_radio",
            "Change to another radio station",
            {
                "type": "object",
                "properties": {
                    "station": {"type": "string", "enum": list(RADIO_STATIONS.keys())}
                },
                "required": ["station"]
            },
            lambda args, states: self._start_radio(RADIO_STATIONS.get(args["station"]), args["station"], helper),
            "global"
        )
        helper.register_action(
            "set_volume",
            "Set the volume of the radio",
            {
                "type": "object",
                "properties": {
                    "volume": {"type": "integer", "minimum": 0, "maximum": 100}
                },
                "required": ["volume"]
            },
            lambda args, states: self._set_volume(args["volume"]),
            "global"
        )

    # Register projection for radio state
    def register_projections(self, helper: PluginHelper):
        helper.register_projection(CurrentRadioState())

    # Register status generator to provide context to the LLM
    def register_status_generators(self, helper: PluginHelper):
        helper.register_status_generator(
            lambda states: [(
                "Radio Status",
                {
                    "available_stations": list(RADIO_STATIONS.keys()),
                    "current_station": states.get("CurrentRadioState", {}).get("station", ""),
                    "current_track": states.get("CurrentRadioState", {}).get("title", ""),
                    "is_playing": states.get("CurrentRadioState", {}).get("playing", False),
                    "available_actions": {
                        "play_radio": "Play a station",
                        "change_radio": "Change to another station (provide station name)",
                        "stop_radio": "Stop the radio",
                        "set_volume": "Set the volume (0–100)"
                    },
                    "hint": "To change station, use change_radio with parameter station."
                }
            )]
        )

    # Stop radio playback when Covas:NEXT shuts down
    def on_chat_stop(self, helper: PluginHelper):
        if self.playing:
            p_log("INFO", "Covas:NEXT stopped. Stopping radio playback.")
            self._stop_radio()

    # Start radio playback
    def _start_radio(self, url, station_name, helper: PluginHelper):
        self._stop_radio()
        if not url:
            return f"URL for station {station_name} not found."
        self.player = vlc.MediaPlayer(url)
        self.player.play()
        self.current_station = station_name
        self.playing = True
        self.stop_monitor = False
        self.track_monitor_thread = threading.Thread(target=self._monitor_track_changes, args=(helper,))
        self.track_monitor_thread.start()
        p_log("INFO", f"Started playing {station_name}")
        return f"Playing {station_name}"

    # Stop radio playback
    def _stop_radio(self):
        if self.player:
            self.player.stop()
            self.player = None
        self.playing = False
        self.current_station = None
        self.stop_monitor = True
        if self.track_monitor_thread:
            self.track_monitor_thread.join(timeout=1)
            self.track_monitor_thread = None
        p_log("INFO", "Stopped radio")
        return "Radio stopped."

    # Monitor track changes and emit events
    def _monitor_track_changes(self, helper: PluginHelper):
        last_title = ""
        while not self.stop_monitor:
            try:
                media = self.player.get_media()
                media.parse()
                title = media.get_meta(vlc.Meta.Title)
                if title and title != last_title:
                    last_title = title
                    event = RadioChangedEvent(station=self.current_station, title=title)
                    helper.put_incoming_event(event)
                    p_log("INFO", f"Track changed: {title}")
            except Exception as e:
                p_log("ERROR", f"Track monitor error: {e}")
            time.sleep(10)

    # Set volume of the radio player
    def _set_volume(self, volume: int):
        if self.player:
            self.player.audio_set_volume(volume)
            p_log("INFO", f"Volume set to {volume}")
            return f"Volume set to {volume}"
        else:
            return "No active player to set volume."
