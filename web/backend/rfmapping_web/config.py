from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path

GIB = 1024**3
DEFAULT_ALLOWED_NETWORKS = (
    "127.0.0.0/8",
    "::1/128",
    # macOS commonly prefers the server's mDNS IPv6 address for *.local.
    # Link-local traffic is non-routable and remains confined to the LAN.
    "fe80::/10",
    "165.124.111.0/24",
    "10.103.68.0/24",
    "172.28.0.0/16",
)


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _allowed_networks() -> tuple[str, ...]:
    raw = os.environ.get("RFMAPPING_ALLOWED_NETWORKS")
    values = DEFAULT_ALLOWED_NETWORKS if raw is None else tuple(
        value.strip() for value in raw.split(",") if value.strip()
    )
    if not values:
        raise ValueError("RFMAPPING_ALLOWED_NETWORKS must not be empty")
    for value in values:
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise ValueError(
                f"RFMAPPING_ALLOWED_NETWORKS contains an invalid network: {value}"
            ) from exc
    return values


@dataclass(frozen=True)
class Settings:
    rf_root: Path
    cache_root: Path
    output_root: Path
    figure_export_root: Path = Path("/mnt/senzailab")
    gate_db_path: Path = field(
        default_factory=lambda: Path(
            "~/.local/share/lab-access-gates/rfmapping.sqlite3"
        ).expanduser()
    )
    cache_max_bytes: int = 50 * GIB
    directory_page_size_max: int = 500
    allowed_networks: tuple[str, ...] = DEFAULT_ALLOWED_NETWORKS

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            rf_root=Path(os.environ.get("RFMAPPING_RF_ROOT", "/mnt/senzailab")),
            cache_root=Path(
                os.environ.get(
                    "RFMAPPING_CACHE_ROOT",
                    "/mnt/ssd4.1/Apps/rfmapping/cache",
                )
            ),
            output_root=Path(
                os.environ.get(
                    "RFMAPPING_OUTPUT_ROOT",
                    "/mnt/ssd4.1/Apps/rfmapping/exports",
                )
            ),
            figure_export_root=Path(
                os.environ.get(
                    "RFMAPPING_FIGURE_EXPORT_ROOT",
                    "/mnt/senzailab",
                )
            ),
            gate_db_path=Path(
                os.environ.get(
                    "RFMAPPING_GATE_DB",
                    "~/.local/share/lab-access-gates/rfmapping.sqlite3",
                )
            ).expanduser(),
            cache_max_bytes=_positive_int("RFMAPPING_CACHE_MAX_BYTES", 50 * GIB),
            allowed_networks=_allowed_networks(),
        )
