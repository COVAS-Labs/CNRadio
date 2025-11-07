"""
SomaFM Track Retriever Module
-----------------------------
Specialized module for retrieving current track information from SomaFM stations
using HTTP requests. This module is designed to be used by the RadioPlugin
when a SomaFM station is selected.
"""

import requests
import time
import re
from typing import Dict, Optional, Tuple

# Cache to store track information and reduce API calls
_track_cache: Dict[str, Tuple[str, float]] = {}
# Cache expiration time in seconds
_CACHE_EXPIRY = 20


def get_somafm_track_info(station_name: str) -> Optional[str]:
    """
    Get the current track information for a SomaFM station.
    
    Args:
        station_name: The name of the SomaFM station (e.g., "deepspaceone", "groovesalad")
        
    Returns:
        A string with the current track information or None if unavailable
    """
    # Extract station ID from full name if needed
    station_id = _extract_station_id(station_name)
    
    # Check cache first
    if station_id in _track_cache:
        cached_info, timestamp = _track_cache[station_id]
        if time.time() - timestamp < _CACHE_EXPIRY:
            return cached_info
    
    # Try multiple methods to get track information
    track_info = (
        _get_from_json_api(station_id) or 
        _get_from_recent_api(station_id) or
        _get_from_channels_api(station_id) or
        _get_from_website(station_id)
    )
    
    # Update cache if we got information
    if track_info:
        _track_cache[station_id] = (track_info, time.time())
        
    return track_info


def _extract_station_id(station_name: str) -> str:
    """Extract the station ID from the full station name."""
    # Handle common SomaFM station names
    if "SomaFM" in station_name:
        # Extract the part after "SomaFM " if present
        match = re.search(r'SomaFM\s+(.+)', station_name, re.IGNORECASE)
        if match:
            name_part = match.group(1).lower()
            # Convert spaces to underscores and remove special characters
            return re.sub(r'[^a-z0-9]', '', name_part.replace(' ', ''))
    
    # For URLs, extract the last part
    if "/" in station_name:
        return station_name.split("/")[-1].lower()
    
    # Default: just lowercase and remove spaces/special chars
    return re.sub(r'[^a-z0-9]', '', station_name.lower().replace(' ', ''))


def _get_from_json_api(station_id: str) -> Optional[str]:
    """Try to get track info from the primary SomaFM JSON API."""
    try:
        url = f"http://somafm.com/songs/{station_id}.json"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data and isinstance(data, list) and len(data) > 0:
                song = data[0]  # Get the most recent song
                artist = song.get('artist', '')
                title = song.get('title', '')
                album = song.get('album', '')
                
                if artist and title:
                    if album:
                        return f"{artist} - {title} [{album}]"
                    else:
                        return f"{artist} - {title}"
                elif title:
                    return title
    except Exception:
        pass
    
    return None


def _get_from_recent_api(station_id: str) -> Optional[str]:
    """Try to get track info from the SomaFM recent tracks API."""
    try:
        url = f"http://somafm.com/recent/{station_id}.json"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data and isinstance(data, list) and len(data) > 0:
                song = data[0]
                artist = song.get('artist', '')
                title = song.get('title', '')
                
                if artist and title:
                    return f"{artist} - {title}"
                elif title:
                    return title
    except Exception:
        pass
    
    return None


def _get_from_channels_api(station_id: str) -> Optional[str]:
    """Try to get track info from the SomaFM channels API."""
    try:
        url = "http://somafm.com/channels.json"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            channels = data.get('channels', [])
            for channel in channels:
                if channel.get('id') == station_id:
                    last_playing = channel.get('lastPlaying', '')
                    if last_playing:
                        return last_playing
    except Exception:
        pass
    
    return None


def _get_from_website(station_id: str) -> Optional[str]:
    """Try to scrape the SomaFM website for track information."""
    try:
        from bs4 import BeautifulSoup
        
        url = f"http://somafm.com/{station_id}/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Try different selectors that might contain track info
            selectors = ['#nowplaying', '.playing', '.current-track', '.song-title']
            for selector in selectors:
                element = soup.select_one(selector)
                if element:
                    text = element.get_text(strip=True)
                    if text and len(text) > 3:
                        return text
    except Exception:
        pass
    
    return None


def is_somafm_station(station_name: str) -> bool:
    """Check if a station name refers to a SomaFM station."""
    return "somafm" in station_name.lower() or "soma.fm" in station_name.lower()