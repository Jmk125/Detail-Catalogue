from __future__ import annotations

import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class StorageSettings:
    crop_image_format: str = os.getenv("DETAIL_CROP_FORMAT", "webp").lower()
    crop_max_width: int = int(os.getenv("DETAIL_CROP_MAX_WIDTH", "1800"))
    image_quality: int = int(os.getenv("DETAIL_IMAGE_QUALITY", "82"))
    retain_temporary_page_images: bool = os.getenv("DETAIL_RETAIN_PAGE_IMAGES", "false").lower() in {"1", "true", "yes", "on"}
    automatic_temp_cleanup: bool = os.getenv("DETAIL_AUTO_TEMP_CLEANUP", "true").lower() in {"1", "true", "yes", "on"}
    render_zoom: float = float(os.getenv("DETAIL_RENDER_ZOOM", "2.0"))

    def normalized_format(self) -> str:
        return self.crop_image_format if self.crop_image_format in {"webp", "jpeg", "jpg", "png"} else "webp"

    def extension(self) -> str:
        fmt = self.normalized_format()
        return "jpg" if fmt == "jpeg" else fmt

    def as_dict(self) -> dict:
        return asdict(self) | {"data_root_env": "DETAIL_HARVESTER_DATA_ROOT"}


def get_settings() -> StorageSettings:
    return StorageSettings()
