# Tado Direct

Custom Home Assistant integration that communicates directly with the Tado API using the mobile app's OAuth2 credentials, bypassing the 3rd-party rate limits that affect the official integration.

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** > **3 dots menu** (top right) > **Custom repositories**
3. Add this repository URL and select **Integration** as category
4. Click **Download**
5. Restart Home Assistant

### Manual

1. Download the `custom_components/tado_direct/` folder
2. Copy it to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

## Setup

1. Go to **Settings** > **Integrations** > **Add Integration**
2. Search for **Tado Direct**
3. A device authorization URL will be shown — open it and log in with your Tado account
4. Once authenticated, the integration will discover your home and devices

## Features

- Climate control (heating, AC, fan, swing modes)
- Temperature & humidity sensors
- Binary sensors (power, connectivity, overlay, open window, early start)
- Water heater control with timer
- Child lock switch
- Geofencing mode & presence detection
- Energy IQ meter readings
