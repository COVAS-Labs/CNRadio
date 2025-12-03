# RadioPlugin v3.3.0
# -------------------
# Release 3.3.0 - Dec 2025
# Key improvements in this release:
# - Implemented lazy/active monitoring mode: startup announces immediately, then enters
#   lazy mode (120s checks for SomaFM/Hutton, 90s for others). After 2 unchanged lazy
#   checks, switches to active mode (30s checks for SomaFM/Hutton, 15s for others) until
#   a track change is detected, then returns to lazy mode.
# - Consolidated interval initialization: initial_interval and reduced_interval computed
#   once at startup and re-evaluated only on station changes (improved efficiency).
# - Added an 8s delay after user-triggered play/change so the assistant can respond
#   before the monitor announces the current track.
# - Suppress duplicate automatic announcements: if the normalized title matches the
#   last announced title on the same station, automatic announcements are suppressed
#   (explicit user commands still force a reply).
# - Robust title normalization using Unicode NFKC + `casefold()` to avoid false
#   positives from case or Unicode variants.
# - Added `RadioPlaybackProjection` to persist current station/title in projections
#   so Covas:NEXT can remember what's playing across sessions.
# - Improved debug logging and fixed several edge-cases in the startup/check flow.
#
# Previous versions (v3.2.0 and earlier)
# - See prior changelogs for earlier changes including dynamic intervals and Hutton/SomaFM handling

import vlc
import threading
import time
import unicodedata
from . import somafm_track_retriever as somaretriever
from . import hutton_orbital_track_retriever as huttonretriever
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Callable
from lib.PluginBase import PluginBase, PluginManifest
from lib.PluginHelper import PluginHelper, PluginEvent, Projection
from lib.Event import Event
from lib.Logger import log
from lib.PluginSettingDefinitions import (
    PluginSettings, SettingsGrid, SelectOption, TextAreaSetting, TextSetting,
    SelectSetting, NumericalSetting, ToggleSetting, ParagraphSetting
)


# Module-level Projection so it can be registered lazily from multiple places
class RadioPlaybackProjection(Projection[dict]):
    def get_default_state(self) -> dict:
        return {
            "current_station": None,
            "current_title": None,
            "last_updated": 0.0,
            "command_triggered": False
        }

    def process(self, event: Event) -> None | list:
        try:
            if not isinstance(event, PluginEvent):
                return None
            if event.plugin_event_name != "radio_changed":
                return None
            content = event.plugin_event_content
            if not isinstance(content, list) or len(content) < 2:
                return None
            title = content[0]
            station = content[1]
            command = content[2] if len(content) > 2 else False
            ts = event.processed_at if getattr(event, 'processed_at', 0) else time.time()
            self.state.update({
                "current_station": station,
                "current_title": title,
                "last_updated": ts,
                "command_triggered": bool(command)
            })
        except Exception:
            return None
        return None

# ---------------------------------------------------------------------
# Pre-installed radio stations
# ---------------------------------------------------------------------
RADIO_STATIONS = {
    "Radio Sidewinder": {
        "url": "https://radiosidewinder.out.airtime.pro:8000/radiosidewinder_b",
        "description": "Fan-made station for Elite Dangerous with ambient and techno music, in-game news and ads."
    },
    "Hutton Orbital Radio": {
#        "url": "https://quincy.torontocast.com:2775/stream",
        "url": "https://quincy.torontocast.com/hutton",
        "description": "Community radio for Elite Dangerous with pop, rock, and humorous segments."
    },
    "SomaFM Deep Space One": {
        "url": "https://ice.somafm.com/deepspaceone",
        "description": "Experimental ambient and electronic soundscapes for deep space exploration."
    },
    "SomaFM Groove Salad": {
        "url": "https://ice.somafm.com/groovesalad",
        "description": "Downtempo and chillout mix, perfect for relaxing flight time."
    },
    "SomaFM Space Station": {
        "url": "https://ice.somafm.com/spacestation",
        "description": "Futuristic electronica, ambient, and experimental tunes."
    },
    "SomaFM Secret Agent": {
        "url": "https://ice.somafm.com/secretagent",
        "description": "Spy-themed lounge and downtempo music for covert operations."
    },
    "SomaFM Defcon": {
        "url": "https://ice.somafm.com/defcon",
        "description": "Dark ambient and industrial music for intense situations."
    },
    "SomaFM Lush": {
        "url": "https://ice.somafm.com/lush",
        "description": "Ambient and ethereal soundscapes for serene journeys."
    },
    "SomaFM Synphaera": {
        "url": "https://ice.somafm.com/synphaera",
        "description": "Cinematic and ambient music for epic space adventures."
    },
    "GalNET Radio": {
        "url": "http://listen.radionomy.com/galnet",
        "description": "Sci-fi themed station with ambient, rock, and classical music, plus GalNet news."
    }
}

PLUGIN_LOG_LEVEL = "ERROR"
_LEVELS = {"DEBUG": 10, "INFO": 20, "ERROR": 40}
DEFAULT_VOLUME = 55
DEFAULT_DJ_STYLE = "Speak like a DJ or make a witty comment. Keep it concise. Match your tone to the time of day."

# ---------------------------------------------------------------------
# Helper logger
# ---------------------------------------------------------------------
def p_log(level: str, *args):
    """Custom logger for RadioPlugin with prefix."""
    try:
        lvl = _LEVELS.get(level.upper(), 999)
        threshold = _LEVELS.get(PLUGIN_LOG_LEVEL.upper(), 999)
        if lvl >= threshold:
            log(level, "[RadioPlugin]", *args)
    except Exception as e:
        log("ERROR", "[RadioPlugin] Logging failure:", e)

# ---------------------------------------------------------------------
# Main plugin class
# ---------------------------------------------------------------------
class RadioPlugin(PluginBase):
    """Main Radio Plugin for Covas:NEXT."""
    def __init__(self, plugin_manifest: PluginManifest):
        super().__init__(plugin_manifest)
        self.current_station = None
        self.player = None
        self.playing = False
        self.track_monitor_thread = None
        self.stop_monitor = False
        self._last_replied_title = None
        self._last_reply_time = 0
        self.helper = None

        self.settings_config: PluginSettings | None = PluginSettings(
            key="RadioPlugin",
            label="Radio Plugin",
            icon="radio",
            grids=[
                SettingsGrid(
                    key="general",
                    label="General",
                    fields=[
                        ParagraphSetting(
                            key="radio_plugin_description",
                            label="About Radio Plugin",
                            type="paragraph",
                            readonly=True,
                            content="The Radio Plugin lets you listen to internet radio stations while chatting with Covas:NEXT. "
                                    "It plays, stops, switches stations, and adjusts volume. Covas comments on track changes like a DJ."
                        ),
                        ParagraphSetting(
                            key="available_stations",
                            label="Available Stations",
                            type="paragraph",
                            readonly=True,
                            content=self._generate_stations_html()
                        ),
                        NumericalSetting(
                            key="default_volume",
                            label="Default Volume",
                            type="number",
                            default_value=DEFAULT_VOLUME,
                            min_value=0,
                            max_value=100,
                            step=1
                        ),
                        TextAreaSetting(
                            key="dj_response_style",
                            label="DJ Response Style",
                            type="textarea",
                            default_value=DEFAULT_DJ_STYLE,
                            rows=3
                        )
                    ]
                )
            ]
        )

    # -----------------------------------------------------------------
    # Plugin setup
    # -----------------------------------------------------------------
    def _generate_stations_html(self) -> str:
        html = "<p>The following radio stations are available:</p><ul>"
        for name, info in RADIO_STATIONS.items():
            html += f"<li><strong>{name}</strong>: {info['description']}</li>"
        html += "</ul>"
        return html

    def on_chat_start(self, helper: PluginHelper):
        """Initialize plugin when chat starts."""
        # Register actions
        self.helper = helper
        self.register_actions(helper)
        # Keep helper reference for projection/state queries
        self.helper = helper
        
        # Register the radio_changed event
        helper.register_event(
            name="radio_changed",
            should_reply_check=lambda event: self._should_reply_to_radio_event(event),
            prompt_generator=lambda event: self._generate_radio_prompt(event)
        )
        # Register the projection (attempt; may already be present). Using module-level
        # RadioPlaybackProjection so we can register lazily elsewhere if needed.
        try:
            helper.register_projection(RadioPlaybackProjection())
            p_log("INFO", "Registered RadioPlaybackProjection to remember current track")
        except Exception as e:
            p_log("DEBUG", f"RadioPlaybackProjection registration attempt returned: {e}")
        
        p_log("INFO", "RadioPlugin initialized successfully")

    def ensure_projection_registered(self) -> None:
        """Ensure the RadioPlaybackProjection is registered in the EventManager.

        If the projection is missing and we have a helper, attempt to register it.
        """
        try:
            evt_mgr = getattr(self, 'helper', None) and getattr(self.helper, '_event_manager', None)
            if not evt_mgr:
                p_log('DEBUG', 'ensure_projection_registered: event manager not available')
                return
            # Try to read existing projection; if it's missing, register it
            try:
                _ = evt_mgr.get_projection_state('RadioPlaybackProjection')
                return
            except Exception:
                # Attempt to register using helper if possible
                try:
                    if self.helper:
                        self.helper.register_projection(RadioPlaybackProjection())
                        p_log('INFO', 'ensure_projection_registered: Registered RadioPlaybackProjection')
                except Exception as e:
                    p_log('DEBUG', f'enable_projection: register failed: {e}')
        except Exception as e:
            p_log('DEBUG', f'ensure_projection_registered error: {e}')
    # -----------------------------------------------------------------
    # SomaFM track retrieval
    # -----------------------------------------------------------------
    def is_somafm_station(self, station_name: str) -> bool:
        """Check if a station name refers to a SomaFM station."""
        somafm_identifiers = ["somafm", "soma.fm"]
        somafm_station_names = [
            "deepspaceone", "deep space one", 
            "groovesalad", "groove salad", 
            "spacestation", "space station", 
            "secretagent", "secret agent", 
            "defcon", "lush", "synphaera"
        ]
    
        station_name_lower = station_name.lower()
    
        # Check if it's explicitly marked as SomaFM in the name
        for identifier in somafm_identifiers:
            if identifier in station_name_lower:
                return True
    
        # Check if it's one of the known SomaFM stations
        for somafm_name in somafm_station_names:
            if somafm_name in station_name_lower:
                return True
    
        # Check if it's in our RADIO_STATIONS dictionary and has a SomaFM URL
        if station_name in RADIO_STATIONS:
            url = RADIO_STATIONS[station_name].get("url", "")
            if "somafm.com" in url or "ice.somafm.com" in url:
                return True
    
            return False
    # -----------------------------------------------------------------
    # Event handling
    # -----------------------------------------------------------------
    def _should_reply_to_radio_event(self, event: PluginEvent) -> bool:
        """Decide whether Covas should reply to a radio track change."""
        try:
            content = event.plugin_event_content
            title = content[0]
            station = content[1]
            command_triggered = content[2] if len (content) > 2 else False
            if len(event.plugin_event_content) > 2:
                command_triggered = event.plugin_event_content[2]
        except (ValueError, TypeError):
            p_log("ERROR", f"Invalid plugin_event_content format: {event.plugin_event_content}")
            return False
        # Skip empty or invalid titles
        if not title or "unknown" in title.lower() or len(title.strip()) <3 :
            p_log("DEBUG", f"Ignoring empty or invalid title")
            return False
        
        normalized_title = title.strip().lower()
        last_title_norm = (self._last_replied_title or "").strip().lower()
        last_station = getattr(self, "_last_replied_station", None)
        current_time = time.time()
        # Initialize repeat counter if doesn't exists
        if not hasattr(self, "_title_repeat_count"):
            self._title_repeat_count ={}
        
        # Create a unque key for the title+station combo
        track_key = f"{normalized_title}|{station}"

        # Check projection state (if available) to see if this track was already announced
        try:
            # Ensure projection exists (lazy-register if needed)
            try:
                self.ensure_projection_registered()
            except Exception:
                pass
            evt_mgr = getattr(self, 'helper', None) and getattr(self.helper, '_event_manager', None)
            if evt_mgr:
                try:
                    proj_state = evt_mgr.get_projection_state('RadioPlaybackProjection')
                    proj_title = proj_state.get('current_title')
                    proj_station = proj_state.get('current_station')
                    proj_title_norm = unicodedata.normalize('NFKC', (proj_title or '').strip()).casefold()
                    # If the projection already recorded the same title/station and the event
                    # was not command-triggered, we would normally suppress replying. However,
                    # the projection may have been updated by the very same event just now.
                    # To avoid suppressing the freshly-dispatched event, allow a small grace
                    # period where projection.last_updated ~= event.processed_at.
                    proj_last = proj_state.get('last_updated', 0) or 0
                    # Extract event processed timestamp if available
                    event_ts = 0
                    try:
                        event_ts = float(getattr(event, 'processed_at', 0) or 0)
                    except Exception:
                        try:
                            event_ts = float(event.plugin_event_content[3]) if len(event.plugin_event_content) > 3 else 0
                        except Exception:
                            event_ts = 0

                    # If projection matches title+station and the projection was updated earlier
                    # than the event (by more than the grace window), suppress. If projection
                    # was updated essentially at the same time as the event, allow the reply.
                    grace_seconds = 1.5
                    if proj_title_norm == normalized_title and proj_station == station and not command_triggered:
                        if event_ts and abs(proj_last - event_ts) <= grace_seconds:
                            p_log('DEBUG', f"Projection updated ~simultaneously (delta={abs(proj_last-event_ts):.2f}s); allowing reply for '{title}' on {station}.")
                            # allow fall-through to reply
                        else:
                            p_log('DEBUG', f"Projection already recorded this track '{title}' on {station}; suppressing reply. Projection state: {proj_state}")
                            return False
                except Exception as e:
                    p_log('DEBUG', f"Could not read RadioPlaybackProjection after ensure: {e}")
        except Exception as e:
            p_log('DEBUG', f"Could not read RadioPlaybackProjection: {e}")

        # If same station and same title as last replied, suppress automatic announcements
        # even after cooldown — but allow explicit user commands to force a reply.
        if normalized_title == last_title_norm and station == last_station:
            if command_triggered:
                p_log("DEBUG", f"Same title on same station but command requested; allowing reply.")
                # allow fall-through to reply
            else:
                p_log("DEBUG", f"Same title on same station and unchanged; suppressing announcement.")
                return False

        # For new titles or different stations, manage repeat counters to avoid
        # noisy announcements on rapid repeats from retrievers.
        if not command_triggered:
            # Counter +1
            self._title_repeat_count[track_key] = self._title_repeat_count.get(track_key, 0) + 1
            if self._title_repeat_count[track_key] > 1:
                p_log("DEBUG", f"Same title repeated {self._title_repeat_count[track_key]} times, ignoring")
                return False
            else:
                p_log("DEBUG", f"First repeat of '{title}' after cooldown, allowing reply.")
        else:
            # New title or new station triggered by command; reset counter
            self._title_repeat_count[track_key] = 0

        # Update memory: store normalized title for consistent future comparisons
        try:
            stored_norm = unicodedata.normalize('NFKC', (title or '').strip()).casefold()
        except Exception:
            stored_norm = (title or '').strip().lower()
        self._last_replied_title = stored_norm
        self._last_replied_station = station
        self._last_reply_time = current_time

        p_log("DEBUG", f"Will reply to '{title}' on {station}")
        return True

    def _generate_radio_prompt(self, event: PluginEvent) -> str:
        """Generate prompt for radio track change events."""
        try:
            content = event.plugin_event_content
            if isinstance(content, list) and len(content) >= 2:
                title = content[0]
                station = content[1]
            else:
                raise ValueError(f"Expected list with at least 2 elements, got: {content}")
        except (ValueError, TypeError) as e:
            p_log("ERROR", f"Invalid plugin_event_content format in prompt generator: {event.plugin_event_content}")
            return "IMPORTANT: React to this radio track change. The track information could not be retrieved."
        dj_style = self.settings.get('dj_response_style', DEFAULT_DJ_STYLE)
        
        return f"IMPORTANT: React to this radio track change. New track: '{title}' on station '{station}'. {dj_style}"

    # -----------------------------------------------------------------
    # Action registration
    # -----------------------------------------------------------------
    def register_actions(self, helper: PluginHelper):
        helper.register_action(
            "play_radio", "Play a webradio station",
            {"type": "object", "properties": {"station": {"type": "string", "enum": list(RADIO_STATIONS.keys())}}, "required": ["station"]},
            lambda args, states: self._start_radio(RADIO_STATIONS.get(args["station"], {}).get("url"), args["station"], helper),
            "global"
        )
        helper.register_action("stop_radio", "Stop the radio", {}, lambda args, states: self._stop_radio(), "global")
        helper.register_action(
            "change_radio", "Change to another station",
            {"type": "object", "properties": {"station": {"type": "string", "enum": list(RADIO_STATIONS.keys())}}, "required": ["station"]},
            lambda args, states: self._start_radio(RADIO_STATIONS.get(args["station"], {}).get("url"), args["station"], helper),
            "global"
        )
        helper.register_action(
            "set_volume", "Set the radio volume",
            {"type": "object", "properties": {"volume": {"type": "integer", "minimum": 0, "maximum": 100}}, "required": ["volume"]},
            lambda args, states: self._set_volume(args["volume"]),
            "global"
        )
        # Status action: return the current projection state for the radio
        helper.register_action(
            "radio_status", "Get current radio playback status",
            {},
            lambda args, states: self._radio_status(args, states),
            "global"
        )

    # -----------------------------------------------------------------
    # Player control
    # -----------------------------------------------------------------
    def on_chat_stop(self, helper: PluginHelper):
        if self.playing:
            p_log("INFO", "Covas:NEXT stopped. Stopping radio playback.")
            self._stop_radio()

    def _start_radio(self, url, station_name, helper: PluginHelper):
        # Ensure proper cleanup of previous radio session
        self._stop_radio()
    
        if not url:
            p_log("ERROR", f"URL for station {station_name} not found.")
            return f"URL for station {station_name} not found."
        try:
            # Wait a moment to ensure previous thread is fully terminated
            time.sleep(0.5)
        
            self.player = vlc.MediaPlayer(url)
            self.player.play()
            default_volume = self.settings.get('default_volume', DEFAULT_VOLUME)
            self.player.audio_set_volume(default_volume)

            self.current_station = station_name
            self.playing = True
            self.stop_monitor = False
            # We had started the radio
            self.command_triggered = True

            # Create and start a new monitor thread
            self.track_monitor_thread = threading.Thread(target=self._monitor_track_changes, args=(helper,))
            self.track_monitor_thread.daemon = True  # Make thread daemon so it exits when main thread exits
            self.track_monitor_thread.start()
        
            p_log("INFO", f"Started playing {station_name} at volume {default_volume}")
            return f"Playing {station_name} at volume {default_volume}"
        except Exception as e:
            p_log("ERROR", f"Failed to start radio: {e}")
            return f"Error starting radio: {e}"

    def _stop_radio(self):
        try:
            # Set flag to stop monitoring thread
            self.stop_monitor = True
        
            # Stop player if it exists
            if self.player:
                self.player.stop()
                self.player = None
            
            self.playing = False
            self.current_station = None
            self.command_triggered = False
        
            # Wait for thread to terminate with timeout
            if self.track_monitor_thread and self.track_monitor_thread.is_alive():
                self.track_monitor_thread.join(timeout=2)
                # Force reference cleanup even if thread didn't terminate properly
                self.track_monitor_thread = None
            
            p_log("INFO", "Stopped radio")
            return "Radio stopped."
        except Exception as e:
            p_log("ERROR", f"Error stopping radio: {e}")
            return f"Error stopping radio: {e}"

    def _set_volume(self, volume: int):
        """Set the playback volume safely, even during stream startup."""
        try:
            if not self.player:
                p_log("ERROR", "No active player to set volume.")
                return "No active player to set volume."

            volume = max(0, min(100, int(volume)))
            result = self.player.audio_set_volume(volume)

            if result == -1:
                time.sleep(0.5)
                result = self.player.audio_set_volume(volume)

            if result == -1:
                p_log("ERROR", "VLC refused volume change (player not ready).")
                return "Unable to set volume right now."

            actual = self.player.audio_get_volume()
            p_log("INFO", f"Volume set to {actual} (requested {volume})")
            return f"Volume set to {actual}"

        except Exception as e:
            p_log("ERROR", f"Error setting volume: {e}")
            return f"Error setting volume: {e}"

    def _radio_status(self, args=None, states=None):
        """Return the current radio playback status from the projection."""
        try:
            # Ensure projection exists and is registered
            try:
                self.ensure_projection_registered()
            except Exception:
                pass

            evt_mgr = getattr(self, 'helper', None) and getattr(self.helper, '_event_manager', None)
            if not evt_mgr:
                return "Radio status not available (event manager missing)."
            try:
                state = evt_mgr.get_projection_state('RadioPlaybackProjection')
            except Exception as e:
                return f"RadioPlaybackProjection not available: {e}"

            station = state.get('current_station')
            title = state.get('current_title')
            last_updated_ts = state.get('last_updated')
            try:
                last_updated = datetime.fromtimestamp(last_updated_ts, timezone.utc).isoformat() if last_updated_ts else 'N/A'
            except Exception:
                last_updated = str(last_updated_ts)

            return f"Station: {station or 'N/A'} | Title: {title or 'N/A'} | Last updated: {last_updated}"
        except Exception as e:
            p_log("ERROR", f"Error reading RadioPlaybackProjection for radio_status: {e}")
            return f"Error retrieving radio status: {e}"

    # -----------------------------------------------------------------
    # Track monitoring
    # -----------------------------------------------------------------
    def _monitor_track_changes(self, helper: PluginHelper):
        """Monitor VLC metadata and trigger an event when the track actually changes."""
        last_title = ""
        last_event_time = 0
        last_check_time = 0
        # Define check intervals
        default_check_interval = 5  # 5 seconds for regular stations
        somafm_check_interval = 20  # Longer interval for SomaFM stations

        command_triggered = getattr(self, "command_triggered", False)
        # If a command just triggered the play, wait ~8 seconds so the AI can respond
        # before the monitor starts announcing tracks
        if command_triggered:
            p_log("DEBUG", "Delaying initial check by 8 seconds to allow AI response to command")
            for _ in range(8):
                if self.stop_monitor:
                    return
                time.sleep(1)
        
        # Initialize station tracking so we can detect switches during monitoring
        prev_station = self.current_station
        is_somafm = self.is_somafm_station(prev_station) if prev_station else False
        is_hutton = "hutton" in (prev_station or "").lower()
        # Startup sequence state machine:
        # We'll perform two checks at reduced_interval, then a third check at initial_interval,
        # and optionally a fourth reduced check depending on results. This helps determine
        # whether we started mid-track and avoids announcing stale metadata.
        # Initialize intervals based on station type so we can start with reduced_interval.
        if is_somafm or is_hutton:
            initial_interval = 100
            reduced_interval = 30
        else:
            initial_interval = 90
            reduced_interval = 15

        startup_sequence = True
        startup_step = 1  # 1..4 as described in design
        prev_check_title = None
        check_interval = reduced_interval

        p_log("INFO", f"Track monitor started for {prev_station}. StartupSequence={startup_sequence} step={startup_step} (SomaFM: {is_somafm}, Hutton: {is_hutton})")
    
        while not self.stop_monitor:
            try:
                if not self.player or self.stop_monitor:
                    time.sleep(1)  # Check more frequently if we should stop
                    continue
                current_time = time.time()
                # Refresh local view of the command trigger flag each loop so we
                # reflect any changes made by other threads (e.g. clearing after dispatch).
                command_triggered = getattr(self, "command_triggered", False)
                if current_time - last_check_time < check_interval:
                    time.sleep(1)
                    continue
                last_check_time = current_time

                # Get track info based on station type
                display_title = ""

                # Re-evaluate station type and determine dynamic intervals (restart startup sequence on change)
                current_station = self.current_station
                if current_station != prev_station:
                    p_log("INFO", f"Station changed from {prev_station} -> {current_station}, restarting startup sequence")
                    prev_station = current_station
                    is_somafm = self.is_somafm_station(current_station) if current_station else False
                    is_hutton = "hutton" in (current_station or "").lower()
                    # Recompute intervals for the new station
                    if is_somafm or is_hutton:
                        initial_interval = 100  # SomaFM & Hutton: initial long wait (metadata can be delayed)
                        reduced_interval = 30   # SomaFM & Hutton: follow-up checks still relatively long
                    else:
                        initial_interval = 90   # Other stations: wait ~typical track length (1-1.5 min)
                        reduced_interval = 15   # Follow-up: shorter checks to catch next track
                    # Reset detection state and restart the startup sequence on station change
                    last_title = ""
                    last_event_time = 0
                    last_check_time = 0
                    startup_sequence = True
                    startup_step = 1
                    prev_check_title = None
                # Ensure check_interval is defined (startup logic sets it during startup steps)
                if 'check_interval' not in locals() or check_interval is None:
                    check_interval = reduced_interval
                p_log("DEBUG", f"Intervals initial={initial_interval}s reduced={reduced_interval}s -> current check_interval={check_interval}s (startup_step={startup_step}, startup_sequence={startup_sequence})")

                if is_somafm:
                    # Use the specialized SomaFM track retriever
                    p_log("DEBUG", f"Using SomaFM track retriever for {current_station}")
                    display_title = somaretriever.get_somafm_track_info(current_station)
                elif is_hutton:
                    p_log("DEBUG", f"Using Hutton Orbital Radio track retriever for {current_station}")
                    display_title = huttonretriever.get_hutton_track_info()
                else:
                    # Use VLC metadata for non-SomaFM stations
                    media = self.player.get_media()
                    if not media:
                        time.sleep(1)
                        continue

                    title = media.get_meta(vlc.Meta.Title)
                    now_playing = media.get_meta(vlc.Meta.NowPlaying)
                    display_title = now_playing or title or ""
    
                # Normalize title robustly: strip, unicode normalize, and casefold for case-insensitive compare
                normalized_title = unicodedata.normalize('NFKC', (display_title or '').strip()).casefold()

                if not normalized_title:
                    default_check_interval = 5  # 5 seconds for regular stations
                    command_triggered = getattr(self, "command_triggered", False)
                    # Check stop flag more frequently
                    for _ in range(5):
                        if self.stop_monitor:
                            break
                        time.sleep(1)
                    continue

                # If we're in the startup sequence, implement the desired lazy/active flow:
                # 1) immediate announce on first check (startup_step == 1)
                # 2) enter lazy mode using `initial_interval` and perform two lazy checks
                #    (counts of unchanged checks). If after two lazy checks the title hasn't
                #    changed, switch to active mode (startup_step == 3) with `reduced_interval`.
                # 3) in active mode (step 3) poll at `reduced_interval` and when a change is
                #    detected announce and return to lazy mode (step 2).
                if startup_sequence:
                    # Ensure checks counter exists
                    if 'checks_without_change' not in locals():
                        checks_without_change = 0

                    # Step 1: immediate announce on startup
                    if startup_step == 1:
                        prev_check_title = normalized_title
                        try:
                            event = PluginEvent(kind="plugin", plugin_event_name="radio_changed", plugin_event_content=[display_title, current_station, command_triggered])
                            p_log("INFO", f"Startup announce (step1) -> {display_title} (command triggered: {command_triggered})")
                            helper.dispatch_event(event)
                            p_log("DEBUG", "Event dispatched successfully (startup step 1)")
                            # clear command flags so subsequent checks behave normally
                            try:
                                self.command_triggered = False
                            except Exception:
                                pass
                            command_triggered = False
                            last_title = normalized_title
                            last_event_time = current_time
                        except Exception as e:
                            p_log("ERROR", f"Error dispatching startup step 1 event: {e}")
                        # Move to lazy checks using the long initial interval
                        startup_step = 2
                        check_interval = initial_interval
                        checks_without_change = 0
                        last_check_time = current_time
                        p_log("DEBUG", f"Startup step 1 done: recorded normalized='{prev_check_title}' -> lazy checks every {check_interval}s")
                        time.sleep(check_interval)
                        continue

                    # Step 2: lazy checks at initial_interval (wait for two unchanged checks)
                    if startup_step == 2:
                        current_check = normalized_title
                        p_log("DEBUG", f"Startup lazy check: compared normalized '{current_check}' to baseline '{prev_check_title}' (display now: '{display_title}')")
                        # If it changed during lazy checks, announce and reset lazy counter
                        if current_check and prev_check_title and current_check != prev_check_title:
                            try:
                                event = PluginEvent(kind="plugin", plugin_event_name="radio_changed", plugin_event_content=[display_title, current_station, command_triggered])
                                p_log("INFO", f"Startup lazy announce -> {display_title} (command triggered: {command_triggered})")
                                helper.dispatch_event(event)
                                p_log("DEBUG", "Event dispatched successfully (startup lazy announce)")
                                try:
                                    self.command_triggered = False
                                except Exception:
                                    pass
                                command_triggered = False
                                last_title = current_check
                                last_event_time = current_time
                                # Baseline becomes the new title and remain in lazy mode
                                prev_check_title = current_check
                                checks_without_change = 0
                                check_interval = initial_interval
                                last_check_time = current_time
                                time.sleep(check_interval)
                                continue
                            except Exception as e:
                                p_log("ERROR", f"Error dispatching startup lazy announce: {e}")

                        # No change on this lazy check
                        checks_without_change = checks_without_change + 1
                        p_log("DEBUG", f"Startup lazy unchanged count = {checks_without_change}")
                        if checks_without_change >= 2:
                            # After two unchanged lazy checks, switch to active monitoring (reduced interval)
                            startup_step = 3
                            check_interval = reduced_interval
                            p_log("DEBUG", f"Switching to active monitoring (step3) every {check_interval}s after {checks_without_change} lazy checks")
                            last_check_time = current_time
                            time.sleep(check_interval)
                            continue
                        else:
                            # Continue lazy checks
                            last_check_time = current_time
                            time.sleep(check_interval)
                            continue

                    # Step 3: active monitoring at reduced_interval until the title changes
                    if startup_step == 3:
                        active_check = normalized_title
                        p_log("DEBUG", f"Startup active check: comparing '{active_check}' to baseline '{prev_check_title}' (display now: '{display_title}')")
                        if active_check and prev_check_title and active_check != prev_check_title:
                            try:
                                event = PluginEvent(kind="plugin", plugin_event_name="radio_changed", plugin_event_content=[display_title, current_station, command_triggered])
                                p_log("INFO", f"Startup active announce (step3) -> {display_title} (command triggered: {command_triggered})")
                                helper.dispatch_event(event)
                                p_log("DEBUG", "Event dispatched successfully (startup active announce)")
                                try:
                                    self.command_triggered = False
                                except Exception:
                                    pass
                                command_triggered = False
                                last_title = active_check
                                last_event_time = current_time
                            except Exception as e:
                                p_log("ERROR", f"Error dispatching startup active announce: {e}")
                            # After announcing on active mode, return to lazy mode
                            prev_check_title = active_check
                            checks_without_change = 0
                            startup_step = 2
                            check_interval = initial_interval
                            p_log("DEBUG", f"Change detected during active monitoring; returning to lazy mode ({check_interval}s)")
                            last_check_time = current_time
                            time.sleep(check_interval)
                            continue
                        else:
                            # No change; keep active monitoring
                            last_check_time = current_time
                            time.sleep(check_interval)
                            continue
                    # Step 1: immediate announce on startup
                    if startup_step == 1:
                        prev_check_title = normalized_title
                        try:
                            event = PluginEvent(kind="plugin", plugin_event_name="radio_changed", plugin_event_content=[display_title, current_station, command_triggered])
                            p_log("INFO", f"Startup announce (step1) -> {display_title} (command triggered: {command_triggered})")
                            helper.dispatch_event(event)
                            p_log("DEBUG", "Event dispatched successfully (startup step 1)")
                            # clear both persistent and local flags so subsequent
                            # iterations do not treat this as a command-triggered event
                            try:
                                self.command_triggered = False
                            except Exception:
                                pass
                            command_triggered = False
                            last_title = normalized_title
                            last_event_time = current_time
                        except Exception as e:
                            p_log("ERROR", f"Error dispatching startup step 1 event: {e}")
                        # Move to safety reduced check
                        startup_step = 2
                        check_interval = reduced_interval
                        last_check_time = current_time
                        p_log("DEBUG", f"Startup step 1 done: recorded normalized='{prev_check_title}' display='{display_title}' -> next reduced check in {check_interval}s")
                        time.sleep(check_interval)
                        continue

                    # Step 2: reduced-interval safety check
                    if startup_step == 2:
                        second_title = normalized_title
                        p_log("DEBUG", f"Startup step 2: compared normalized '{second_title}' to first '{prev_check_title}' (display now: '{display_title}')")
                        # If second check differs from first, announce the new track
                        if second_title and prev_check_title and second_title != prev_check_title:
                            try:
                                event = PluginEvent(kind="plugin", plugin_event_name="radio_changed", plugin_event_content=[display_title, current_station, command_triggered])
                                p_log("INFO", f"Startup announce (step2) -> {display_title} (command triggered: {command_triggered})")
                                helper.dispatch_event(event)
                                p_log("DEBUG", "Event dispatched successfully (startup step 2)")
                                try:
                                    self.command_triggered = False
                                except Exception:
                                    pass
                                command_triggered = False
                                last_title = second_title
                                last_event_time = current_time
                            except Exception as e:
                                p_log("ERROR", f"Error dispatching startup step 2 event: {e}")
                        # Regardless of announce or not, move to initial-interval check (step 3)
                        startup_step = 3
                        check_interval = initial_interval
                        prev_check_title = second_title or prev_check_title
                        last_check_time = current_time
                        p_log("DEBUG", f"Startup step 2 complete: moving to initial interval ({check_interval}s)")
                        time.sleep(check_interval)
                        continue

                    # Step 3: check at initial_interval
                    if startup_step == 3:
                        third_title = normalized_title
                        p_log("DEBUG", f"Startup step 3 (initial interval): compared normalized '{third_title}' to previous '{prev_check_title}' (display now: '{display_title}')")
                        if third_title and prev_check_title and third_title != prev_check_title:
                            # Announce change and remain using initial_interval
                            try:
                                event = PluginEvent(kind="plugin", plugin_event_name="radio_changed", plugin_event_content=[display_title, current_station, command_triggered])
                                p_log("INFO", f"Startup announce (step3) -> {display_title} (command triggered: {command_triggered})")
                                helper.dispatch_event(event)
                                p_log("DEBUG", "Event dispatched successfully (startup step 3)")
                                try:
                                    self.command_triggered = False
                                except Exception:
                                    pass
                                command_triggered = False
                                last_title = third_title
                                last_event_time = current_time
                            except Exception as e:
                                p_log("ERROR", f"Error dispatching startup step 3 event: {e}")
                            # Exit startup sequence into steady reduced mode (use reduced interval)
                            startup_sequence = False
                            check_interval = reduced_interval
                            # Reset numeric startup step so logging/diagnostics show steady-state clearly
                            startup_step = 0
                            p_log("DEBUG", f"Entering steady state: reduced interval ({check_interval}s) (startup_step reset)")
                            continue
                        else:
                            # No change on initial-interval check: perform a fourth reduced check
                            startup_step = 4
                            prev_check_title = third_title or prev_check_title
                            check_interval = reduced_interval
                            last_check_time = current_time
                            p_log("DEBUG", f"Startup step 3 saw no change: scheduling reduced follow-up in {check_interval}s")
                            time.sleep(check_interval)
                            continue

                    # Step 4: final reduced check
                    if startup_step == 4:
                        fourth_title = normalized_title
                        p_log("DEBUG", f"Startup step 4 (reduced): compared normalized '{fourth_title}' to previous '{prev_check_title}' (display now: '{display_title}')")
                        if fourth_title and prev_check_title and fourth_title != prev_check_title:
                            try:
                                event = PluginEvent(kind="plugin", plugin_event_name="radio_changed", plugin_event_content=[display_title, current_station, command_triggered])
                                p_log("INFO", f"Startup announce (step4) -> {display_title} (command triggered: {command_triggered})")
                                helper.dispatch_event(event)
                                p_log("DEBUG", "Event dispatched successfully (startup step 4)")
                                try:
                                    self.command_triggered = False
                                except Exception:
                                    pass
                                command_triggered = False
                                last_title = fourth_title
                                last_event_time = current_time
                            except Exception as e:
                                p_log("ERROR", f"Error dispatching startup step 4 event: {e}")
                        # After step 4, whether changed or not, enter steady reduced mode
                        startup_sequence = False
                        check_interval = reduced_interval
                        # Reset numeric startup step so logging/diagnostics show steady-state clearly
                        startup_step = 0
                        p_log("DEBUG", f"Entering steady state: reduced interval ({check_interval}s) (startup_step reset)")
                        continue

                # Normal steady-state monitoring: announce when track changes compared to last announced title
                # Note: last_title is always normalized for consistent comparison. Use explicit parentheses
                # to avoid operator-precedence surprises.
                if (normalized_title != last_title and (current_time - last_event_time > 5)) or command_triggered:
                    p_log("DEBUG", f"New track detected: '{display_title}' (previous normalized: '{last_title}')")
                    last_title = normalized_title
                    last_event_time = current_time
                    # Only create event if we're still playing the same station
                    if not self.stop_monitor:
                        try:
                            event = PluginEvent(
                                kind="plugin",
                                plugin_event_name="radio_changed",
                                plugin_event_content=[display_title, current_station, command_triggered]
                            )
                            p_log("INFO", f"Track changed -> {display_title} (command triggered: {command_triggered})")
                            helper.dispatch_event(event)
                            p_log("DEBUG", "Event dispatched successfully")
                            # After announcing a new track in steady state, keep the current interval
                            command_triggered = False
                        except Exception as e:
                            p_log("ERROR", f"Error creating or dispatching event: {e}")

                # Regular sleep for steady-state or in-case fallback
                time.sleep(check_interval)
            except Exception as e:
                p_log("ERROR", f"Track monitor error: {e}")
                time.sleep(5)
    
        p_log("INFO", f"Track monitor stopped for {self.current_station}.")