# RadioPlugin for Covas:NEXT

## 📦 Overview

**RadioPlugin** is an extension for **Covas:NEXT** that lets you listen to and control internet radio stations directly from the assistant interface. It supports actions and voice commands to play, stop, switch stations, adjust volume, and optionally announce track changes with a DJ-style tone.  
Now featuring **Pydantic validation**, **lazy/active track monitoring**, and a **toggle to enable/disable track-change announcements**.

---

## 📡 Supported Stations

Includes a curated list of stations such as:
- **Elite Dangerous community stations**: Radio Sidewinder, Hutton Orbital Radio
- **SomaFM channels**: Deep Space One, Groove Salad, Space Station, Secret Agent, Defcon, Lush, Synphaera
- **Italian stations**: Radio Capital, Radio DeeJay, DeeJay Linetti
- **Gaming/Demoscene stations**: Kohina Radio, CVGM, Nectarine, Ericade
- **Others**: BigFM, GalNET Radio

*(Full list with URLs is available in the plugin settings UI.)*

---

## 🗣 Voice Commands

Examples:
- `Play radio`
- `Play Radio Sidewinder`
- `Stop radio`
- `Change station to BigFM`
- `Set volume to 50`
- `Enable announcements` / `Disable announcements`
- `What's playing right now?` [with announcements enabled]

---


## 🔧 Features
- **Play/Stop/Change Station** via actions and voice commands.
- **Volume Control**: Set playback volume (0–100).
- **Lazy/Active Track Monitoring**:
  - Starts in lazy mode (long intervals), switches to active mode when titles repeat.
- **Track Announcements**:
  - Announces current track with duplicate suppression and Unicode normalization.
  - Toggle to enable/disable DJ-style track-change announcements.
- **Special Station Retrievers**:
  - Dedicated retrievers for SomaFM, Hutton Orbital, Radio DeeJay, and MP3 streams that VLC cannot parse.
  - Fallback to VLC metadata for standard stations.
- **Robust Event Handling**:
  - Debounced announcements, delayed after commands to avoid overlap.
- **Safe Threading & Logging**:
  - Improved concurrency and error handling.

---

## ⚙️ Settings
In **Settings → Radio Plugin**:
- **Default Volume**: Initial playback volume.
- **Track Change Announcements**: Toggle ON/OFF for DJ-style track-change notifications.
- **Available Stations**: Informational list with descriptions.

---

## 📥 Installation

1. Copy the plugin folder into `%APPDATA%/com.covas-next.ui/plugins/`.
2. Ensure `python_vlc` and `vlc.py` are present in `deps/` or installed globally.
3. Install dependencies:
   ```
   pip install -r requirements.txt --target=./deps --upgrade
   ```
4. Install **VLC media player**.
5. Restart **Covas:NEXT** and enable the plugin.
6. [If you're upgrading from previous version]: do a "Clear History" in the "Characters" tab on **Covas:NEXT**

---

## ⚙️ Requirements

- `python_vlc >= 3.0.12118`
- `requests>=2.25.0`
- `beautifulsoup4>=4.9.3`
- `urllib3>=1.26.0`
- **VLC media player** installed on the system.

---

## ⚠️ Migration Notes

- Include `deejay_track_retriever.py` in the plugin folder for DeeJay stations.
- Include `mp3_stream_track_retriever.py` in the plugin folder for generic mp3 stream that use Icy Metadata.
- No breaking changes for existing settings.

---

## 📚 Release Notes

See [CNRadio v4.0.0 Release Notes](https://github.com/TheDeviceNull/CNRadio/releases/tag/v.4.0.0).

See [CNRadio v3.3.1 Release Notes](https://github.com/TheDeviceNull/CNRadio/releases/tag/v.3.3.1).
