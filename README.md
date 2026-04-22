<div align="center">

# 🦎 Gecko Integration for Home Assistant

**Control your spa, hot tub, or pool equipment directly from Home Assistant**

[![GitHub Release](https://img.shields.io/github/release/geckoal/ha-gecko-integration.svg?style=for-the-badge)](https://github.com/geckoal/ha-gecko-integration/releases)
[![HACS](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg?style=for-the-badge)](LICENSE)

</div>

---

## ✨ Features

<table>
<tr>
<td width="33%" valign="top">

### 🌡️ Climate Control
- Set target temperature
- Monitor current temperature
- Track heating status

</td>
<td width="33%" valign="top">

### 💡 Lighting
- Multi-zone LED control
- On/off control

</td>
<td width="33%" valign="top">

### 💨 Pumps & Fans
- Blower control
- Multiple pump zones

</td>
</tr>
<tr>
<td width="33%" valign="top">

### 🔄 Watercare Modes
- Away mode
- Standard mode
- Energy savings mode
- Weekender mode

</td>
<td width="33%" valign="top">

### 📊 Monitoring
- Gateway status
- Connection health

</td>
<td width="33%" valign="top">

### ☁️ Cloud Integration
- Secure OAuth2 login
- AWS IoT backend
- Automatic discovery
- Push notifications

</td>
</tr>
</table>

---

## 📦 Installation

### Method 1: HACS (Recommended)

1. Open **HACS** in Home Assistant
2. Navigate to **Integrations**
3. Click the **⋮** menu (top right) → **Custom repositories**
4. Add repository URL: `https://github.com/geckoal/ha-gecko-integration`
5. Select category: **Integration**
6. Click **Download** on the Gecko integration
7. **Restart Home Assistant**

### Method 2: Manual Installation

1. Download the [latest release](https://github.com/geckoal/ha-gecko-integration/releases)
2. Extract and copy the `custom_components/gecko` folder to your Home Assistant `custom_components` directory
3. Your directory structure should look like:
   ```
   config/
   └── custom_components/
       └── gecko/
           ├── __init__.py
           ├── manifest.json
           └── ...
   ```
4. **Restart Home Assistant**

---

## ⚙️ Setup & Configuration

### Initial Setup

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for **"Gecko"**
4. Click on the Gecko integration

### Authentication

The integration will guide you through OAuth2 authentication:

1. Click **Continue** to begin the OAuth flow
2. You'll be redirected to the Gecko login page
3. Enter your **Gecko spa account credentials**
4. Grant permission to Home Assistant
5. You'll be redirected back automatically

### Automatic Discovery

Once authenticated:
- Your spa(s) will be **automatically discovered**
- All available entities will be created
- Devices appear under **Settings** → **Devices & Services** → **Gecko**

---

## 🎛️ Available Entities

The integration creates multiple entity types for comprehensive spa control:

| Entity Type | Description | Example |
|------------|-------------|---------|
| **Climate** | Temperature control and monitoring | Set spa to 104°F (40°C) |
| **Light** | LED lighting control with brightness | Adjust ambient lighting |
| **Fan** | Pump and blower speed control | Set pump to High speed |
| **Binary Sensor** | On/off status indicators | Gateway connected status |
| **Select** | Mode selection (watercare, presets) | Switch to Energy Savings mode |
| **Sensor** | Numeric values from the cloud device shadow outside modeled zones (e.g. Waterlab / chemistry paths) | pH, ORP, or other metrics when the spa publishes them |

### Entity Examples

**Climate Entity:**
- `climate.spa_name` - Main temperature control

**Light Entities:**
- `light.spa_name_zone_1` - Primary lighting zone
- `light.spa_name_zone_2` - Secondary lighting zone

**Fan Entities:**
- `fan.spa_name_pump_1` - Main circulation pump
- `fan.spa_name_pump_2` - Jet pump

**Sensors (shadow extensions):**
- Entities are created dynamically from the Gecko IoT **device shadow** for numeric fields under unknown `zones.*` branches (such as Waterlab) and for `features.*` other than watercare operation mode.
- Likely water-chemistry paths (names containing `ph`, `orp`, `chlorine`, etc.) are **enabled by default**; other numeric leaves are added as **disabled** diagnostic sensors you can enable in the entity registry.
- Download diagnostics (**Settings → Devices & Services → Gecko → Download diagnostics**) to inspect redacted shadow topology (`shadow_topology`) without raw values.

---

## 🆘 Troubleshooting

### Common Issues

**Integration not appearing:**
- Ensure you've restarted Home Assistant after installation
- Check that the `custom_components/gecko` folder exists
- Review Home Assistant logs for errors

**OAuth authentication fails:**
- Verify your Gecko account credentials
- Check internet connectivity
- Try clearing browser cache and retry

**No devices discovered:**
- Confirm your spa is online in the Gecko mobile app
- Wait 1-2 minutes for discovery to complete
- Check that your spa uses Gecko in.touch 3 / in.touch 3+ or compatible system

**Entities not updating:**
- Check RF signal strength sensor (low signal affects updates)
- Verify gateway connectivity in the Gecko app
- Restart the integration: **Settings** → **Devices & Services** → **Gecko** → **⋮** → **Reload**

---

## 💬 Support & Community

- 🐛 **Report Issues:** [GitHub Issues](https://github.com/geckoal/ha-gecko-integration/issues)
- 💡 **Feature Requests:** [GitHub Discussions](https://github.com/geckoal/ha-gecko-integration/discussions)
- 📖 **Documentation:** [Full Docs](https://github.com/geckoal/ha-gecko-integration)

---

## 🙏 Credits

This integration is built with the [gecko-iot-client](https://github.com/geckoal/gecko-iot-client) library.

---

## 📄 License

Copyright © 2025-2026 Gecko Alliance

Licensed under the Apache License 2.0 - see [LICENSE](LICENSE) for details.

> **⚠️ Important:** This software is intended for personal use with Gecko Alliance equipment through Home Assistant. Commercial use or use outside the intended scope may require authorization from Gecko Alliance. See [NOTICE](NOTICE) for trademark information and usage restrictions.
