"""PTZ"""


class ReolinkPTZNumberEntityFeature(IntFlag):
    """REOLink Sensor Features"""

    ZOOM = auto()
    FOCUS = auto()


_PTZTYPE_FEATURE_MAP: Final = MappingProxyType(
    {
        capabilities.PTZType.AF: ReolinkPTZNumberEntityFeature.FOCUS
        | ReolinkPTZNumberEntityFeature.ZOOM,
        capabilities.PTZType.PTZ: ReolinkPTZNumberEntityFeature.ZOOM,
        capabilities.PTZType.PTZ_NO_SPEED: ReolinkPTZNumberEntityFeature.ZOOM,
    }
)


@dataclass
class ReolinkPTZNumberEntityDescription(NumberEntityDescription):
    """Describe Reolink PTZ Sensor Entity"""

    has_entity_name: bool = True
    entity_category: EntityCategory | None = EntityCategory.CONFIG
    feature: ReolinkPTZNumberEntityFeature | None = None


PTZ_NUMBERS: Final = [
    ReolinkPTZNumberEntityDescription(
        key="ptz_focus_position",
        name="Focus",
        icon="mdi:camera-iris",
        feature=ReolinkPTZNumberEntityFeature.FOCUS,
        native_min_value=1,
        native_max_value=64,
        native_step=1,
    ),
    ReolinkPTZNumberEntityDescription(
        key="ptz_zoom_position",
        name="Zoom",
        icon="mdi:magnify",
        feature=ReolinkPTZNumberEntityFeature.ZOOM,
        native_min_value=1,
        native_max_value=64,
        native_step=1,
    ),
]
