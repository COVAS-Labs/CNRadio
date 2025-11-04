# RadioPlugin for Covas:NEXT

## 📧 Overview

RadioPlugin is a Covas:NEXT extension that allows users to control and listen to a selection of webradio stations directly from the assistant interface.

It supports voice commands for starting, stopping, and switching stations, and can announce track changes when metadata is available.

## 📡 Supported Stations

- **Radio Sidewinder**  
  `https://radiosidewinder.out.airtime.pro:8000/radiosidewinder_b`
- **Hutton Orbital Radio**  
  `https://quincy.torontocast.com/hutton`
- **SomaFM DeepspaceOne**  
  `https://ice.somafm.com/deepspaceone`

## 🛰️ Voice Commands

The plugin responds to natural language commands such as:

- "Start radio"
- "Play Radio Sidewinder"
- "Stop radio"
- "Change station to Hutton Orbital Radio"

## 🔧 Features

- **Play/Stop/Change Station** via registered actions and voice commands.
- **Track Monitoring**: Announces track changes if metadata is available.
- **Status Reporting**: Displays current radio status in Covas:NEXT.

## Installation

1. Copy the entire plugin folder to the `plugins/` directory of Covas:Next.
2. Ensure the `deps/` folder contains `python_vlc` and `vlc.py` if not installed globally.
3. Install VLC player
4. Restart Covas:Next and enable the plugin via the Plugins UI.

## 🛠 Requirements

python_vlc>=3.0.12118


## ⚠️ VLC Dependency

This plugin requires [VLC media player](https://www.videolan.org/vlc/) to be installed on the system.


Without this, the plugin will fail to load with an error like:

`Failed to load plugin CNRadio: Failed to load dynlib/dll '.\libvlc.dll'. Most likely this dynlib/dll was not found when the application was frozen.`
