*** this integration is on hiatus, I reccommend using the built in integration going forward ***

A Home Assistant custom integration for Reolink IP Devices (what support web)

This will add support for ReoLink cameras (and some devices) and works well with my other addon [Reolink Discovery](https://github.com/xannor/ha_reolink_discovery)

Features

- Supports defining mutliple streams for a device, depending on what is supported, only activates "best" stream for each type, e.g. RTSP Main, RTMP Sub, RTMP Ext.

- Supports providing an RTMP, RTSP, and MJPEG stream, RTMP is preferred as RTSP requires a webrtc addon for home assistant. RTMP does not support H256 so high def cameras will only have an RTSP or MJPEG Main stream.

- Supports multi channel devices, with the channels provided as sub-devices under the main device.

- Single connection per device/service (each stream will be a new connection and onvif requires a separate connection) to limit authentication logins/issues

Planned:

- Media browser for viewing/downloading recordings
- PTZ services
- Alternative to ONVIF for motion detection

Tested Devices:

- DUO
- 511W
- 810A
- 820A
- 520A
- 410

Unsupported Devices:

- any E series not conencted via NVR
- any camrea that does not provide a web interface
