"""Generate translations/en.json from strings.json"""
import json
import os
from pathlib import Path
import sys
from typing import cast

import homeassistant

sys.path.insert(0, f"{sys.path[0]}/../.scripts/ha_helpers")
from script.translations import develop
from script.translations.upload import FILENAME_FORMAT

COMPONENTS_DIR = Path(__file__).parent.parent / "custom_components"

HASS_STRINGS = Path(homeassistant.__file__).parent / "strings.json"

translations: dict[str, any] = json.loads(HASS_STRINGS.read_text())
translations["component"] = {}

for path in COMPONENTS_DIR.glob(f"*{os.sep}strings*.json"):
    component = path.parent.name
    match = FILENAME_FORMAT.search(path.name)
    platform = match.group("suffix") if match else None

    parent: dict[str, any] = translations["component"].setdefault(component, {})

    if platform:
        platforms: dict[str, any] = parent.setdefault("platform", {})
        parent = platforms.setdefault(platform, {})

    parent.update(json.loads(path.read_text()))

flattened_translations = develop.flatten_translations(translations)

for integration in cast(dict[str, any], translations["component"]).keys():
    integration_strings = translations["component"][integration]

    translations["component"][integration] = develop.substitute_translation_references(
        integration_strings, flattened_translations
    )

    transdir = (COMPONENTS_DIR / integration) / "translations"

    if not transdir.is_dir():
        transdir.mkdir(parents=True)
    (transdir / "en.json").write_text(
        json.dumps(translations["component"][integration])
    )
