"""HydroBulletin — автоматизація оперативних гідрологічних даних."""

from .archive import (
    ImportResult,
    ProductResult,
    archive_raw_text,
    archive_summary,
    import_observations,
    initialize_archive,
    query_observations,
    read_product_provenance,
    register_product,
)
from .batch import BatchImportResult, discover_batch_files, run_batch_import
from .bulletins import BulletinResult, generate_bulletin, generate_bulletins
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
from .quality import (
    INCONSISTENT_CHANGE,
    MISSING,
    OUT_OF_RANGE,
    SUSPICIOUS,
    VALID,
    run_initial_quality_control,
)
from .sources import (
    ArchiveDataSource,
    DataSourceError,
    FallbackDataSource,
    LocalFileSource,
    OnlineDataSource,
    OnlineMeteoDataSource,
    OnlineSourceSettings,
    TextDataSource,
)
from .workflow import WorkflowRequest, WorkflowResult, execute_workflow

__version__ = "0.3.3.1"

__all__ = [
    "HydroObservation",
    "HydroMeasurement",
    "Station",
    "TextDataSource",
    "LocalFileSource",
    "OnlineSourceSettings",
    "OnlineDataSource",
    "OnlineMeteoDataSource",
    "ArchiveDataSource",
    "FallbackDataSource",
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
    "ProductResult",
    "query_observations",
    "register_product",
    "read_product_provenance",
    "run_import_pipeline",
    "PipelineResult",
    "run_initial_quality_control",
    "VALID",
    "MISSING",
    "SUSPICIOUS",
    "OUT_OF_RANGE",
    "INCONSISTENT_CHANGE",
    "discover_batch_files",
    "run_batch_import",
    "BatchImportResult",
    "generate_bulletin",
    "generate_bulletins",
    "BulletinResult",
    "WorkflowRequest",
    "WorkflowResult",
    "execute_workflow",
]
