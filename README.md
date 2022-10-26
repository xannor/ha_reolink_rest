[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]][license]

[![hacs][hacsbadge]][hacs]
[![Project Maintenance][maintenance-shield]][user_profile]


[![Community Forum][forum-shield]][forum]

## Installation

1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`).
2. If you do not have a `custom_components` directory (folder) there, you need to create it.
3. In the `custom_components` directory (folder) create a new folder called `reolink_rest`.
4. Download _all_ the files from the `custom_components/reolink_rest/` directory (folder) in this repository.
5. Place the files you downloaded in the new directory (folder) you created.
6. Restart Home Assistant
7. In the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Reolink Discovery"

## Configuration is done in the UI

Reolink IP Devices for Home Assistant

This integration provides support for REOLink Cameras and devices that support the REST style API.

See also my other (integration)[/xannor/ha_reolink_discovery] for automatic detection of cameras and their ip addresses

Current status:
Feature incomplete, beta stage.

Currently supports:
Multichannel devices, and multi output cameras. Only Web enabled cameras are supported with this integration, cameras that do not have a web interface will not work, even if detected. NVR's should work (and thus, all cameras they provide, regardless of the camera its self) but I do not have access to one so I cannot test that it will.

Tested Devices
DUO, 511W, 820A, 810A, 520A

Untested, should work:
NVR, most other IP ReoLink Cameras

Will not work:
Any camera that does not support a web interface, such as the Lumus, or the NVR only cameras Bxxx versions.

Entities provided:
Camera - one per channel, per stream type, per output type. Only primary output type is enabled and visible by default. Other output types (SUB/EXT) are hidden and other stream types (RTSP, MJPEG) are disabled, but present if needed.

Binary Sensor - one per channel per supported detection type. Cameras with AI detection capabilities should include all supported types as sensors. Sensor will use ONVIF for push notifications if supported by camera, otherwise will poll every few seconds.

Planned:
Media playback support for recorded videos

Ideas:
Sensor that is updated on motion with state and possibly holding snapshot from the SUB or EXT stream, if history can capture the snapshot this could be used with the media playback as thumbnails.

Investigate:
how to possibly interface with the push system the client use for potentially more reliable motion notificiations.
possibly create email addon for email push notifications

<!---->

***

[reolink_rest]: https://github.com/xannor/ha_reolink_rest
[commits-shield]: https://img.shields.io/github/commit-activity/y/xannor/ha_reolink_rest.svg?style=for-the-badge
[commits]: https://github.com/xannor/ha_reolink_rest/commits/master
[hacs]: https://hacs.xyz
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[forum-shield]: https://img.shields.io/badge/community-forum-brightgreen.svg?style=for-the-badge
[forum]: https://community.home-assistant.io/
[license]: https://github.com/xannor/ha_reolink_rest/blob/main/LICENSE
[license-shield]: https://img.shields.io/github/license/xannor/ha_reolink_rest.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-Xannor%20%40xannor-blue.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/xannor/ha_reolink_rest.svg?style=for-the-badge
[releases]: https://github.com/xannor/ha_reolink_rest/releases
[user_profile]: https://github.com/xannor

