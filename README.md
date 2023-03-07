*** This integration is on hiatus, I would reccomend using the built in ReoLink integration going forward. ***

Reolink IP Devices for Home Assistant (on hiatus)

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
Any camera that doe snot support a web interface, such as the Lumus, or the NVR only cameras Bxxx versions.

Entities provided:
Camera - one per channel, per stream type, per output type. Only primary output type is enabled and visible by default. Other output types (SUB/EXT) are hidden and other stream types (RTSP, MJPEG) are disabled, but present if needed.

Binary Sensor - one per channel per supported detection type. Cameras with AI detectino capabilities should include all supported types as sensors. Sensor will use ONVIF for push notifications if supported by camera, otherwise will poll every few seconds.

Planned:
Media playback support for recorded videos

Ideas:
Sensor that is updated on motion with state and possibly holding snapshot from the SUB or EXT stream, if history can capture the snapshot this could be used with the media playback as thumbnails.

Investigate:
how to possibly interface with the push system the client use for potentially more reliable motion notificiations.
possibly create email addon for email push notifications
