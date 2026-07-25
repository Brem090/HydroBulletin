"""HydroBulletin — автоматизація оперативних гідрологічних даних."""

from .archive import (
    ImportResult,
    archive_raw_text,
    archive_summary,
    import_observations,
    initialize_archive,
)
from .decoder import (
    decode_codes,
    parse_change,
    parse_discharge,
    parse_evening_level,
    parse_level,
    parse_precipitation,
    parse_temperature,
)
from .models import HydroMeasurement, HydroObservation, Station
from .pipeline import PipelineResult, run_import_pipeline
from .sources import (
    DataSourceError,
    LocalFileSource,
    OnlineDataSource,
    OnlineSourceSettings,
    TextDataSource,
)

__version__ = "0.2.2"

__all__ = [
    "HydroObservation",
    "HydroMeasurement",
    "Station",
    "TextDataSource",
    "LocalFileSource",
    "OnlineSourceSettings",
    "OnlineDataSource",
    "DataSourceError",
    "decode_codes",
    "parse_level",
    "parse_change",
    "parse_evening_level",
    "parse_temperature",
    "parse_precipitation",
    "parse_discharge",
    "initialize_archive",
    "archive_summary",
    "archive_raw_text",
    "import_observations",
    "ImportResult",
    "run_import_pipeline",
    "PipelineResult",
]
