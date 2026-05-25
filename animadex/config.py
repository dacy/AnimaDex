"""Unified configuration loader.

Reads `config.toml` from the repo root (or `$ANIMADEX_CONFIG`), applies
`${data_dir}` substitution, and overlays env-var overrides of the form
`ANIMADEX_<SECTION>_<KEY>`. Returns a typed `Config` dataclass so the
rest of the codebase reads `cfg.cache.api_seconds` instead of dipping
into raw dicts.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent


# ---- typed sections -----------------------------------------------------

@dataclass
class Paths:
    data_dir:       str = "../animadex-data"
    database:       str = "${data_dir}/animadex.db"
    characters_dir: str = "${data_dir}/characters"
    artists_dir:    str = "${data_dir}/artists"
    copyrights_dir: str = "${data_dir}/copyrights"


@dataclass
class Server:
    host:        str  = "127.0.0.1"
    port:        int  = 5000
    debug:       bool = False
    secret_key:  str  = ""
    trust_proxy: bool = False


@dataclass
class Gallery:
    page_size:     int = 72
    thumb_height:  int = 445
    thumb_quality: int = 82


@dataclass
class Cache:
    landing_seconds:   int = 0
    character_seconds: int = 0
    api_seconds:       int = 0
    image_seconds:     int = 604800
    thumb_seconds:     int = 604800


@dataclass
class Admin:
    username: str = "admin"
    password: str = ""


@dataclass
class RateLimit:
    enabled:       bool = True
    burst:         str  = "150/10"
    sustained:     str  = "2000/300"
    block_seconds: int  = 600


@dataclass
class Features:
    scoring_enabled: bool = False
    loras_enabled:   bool = False
    contact_enabled: bool = True


@dataclass
class CivitAI:
    api_key: str = ""


@dataclass
class Generation:
    comfyui_url:   str = "http://127.0.0.1:8188/"
    workflow_file: str = ""


@dataclass
class Config:
    paths:      Paths      = field(default_factory=Paths)
    server:     Server     = field(default_factory=Server)
    gallery:    Gallery    = field(default_factory=Gallery)
    cache:      Cache      = field(default_factory=Cache)
    admin:      Admin      = field(default_factory=Admin)
    ratelimit:  RateLimit  = field(default_factory=RateLimit)
    features:   Features   = field(default_factory=Features)
    civitai:    CivitAI    = field(default_factory=CivitAI)
    generation: Generation = field(default_factory=Generation)


# ---- loading ------------------------------------------------------------

_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _substitute(value: str, env: dict[str, str]) -> str:
    """Replace ${var} placeholders using `env` until stable."""
    prev = None
    while prev != value:
        prev = value
        value = _VAR_RE.sub(lambda m: env.get(m.group(1), m.group(0)), value)
    return value


def _apply_section(section: Any, raw: dict[str, Any]) -> None:
    """Merge a raw dict into a dataclass section, coercing types."""
    for f in fields(section):
        if f.name not in raw:
            continue
        current = getattr(section, f.name)
        new = raw[f.name]
        # Coerce booleans from strings (env-var path).
        if isinstance(current, bool) and isinstance(new, str):
            new = new.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(current, int) and isinstance(new, str):
            try:
                new = int(new)
            except ValueError:
                continue
        setattr(section, f.name, new)


def _apply_env_overrides(cfg: Config) -> None:
    """Override individual keys from ANIMADEX_<SECTION>_<KEY> env vars."""
    for section_field in fields(cfg):
        section = getattr(cfg, section_field.name)
        if not is_dataclass(section):
            continue
        prefix = f"ANIMADEX_{section_field.name.upper()}_"
        for key_field in fields(section):
            env_name = prefix + key_field.name.upper()
            if env_name in os.environ:
                _apply_section(section, {key_field.name: os.environ[env_name]})


def _resolve_paths(cfg: Config) -> None:
    """Expand ${data_dir} placeholders and turn relative paths absolute."""
    data_dir = cfg.paths.data_dir
    if not os.path.isabs(data_dir):
        data_dir = str((REPO_ROOT / data_dir).resolve())
    env = {"data_dir": data_dir}
    cfg.paths.data_dir = data_dir
    for f in fields(cfg.paths):
        if f.name == "data_dir":
            continue
        raw = getattr(cfg.paths, f.name)
        resolved = _substitute(raw, env)
        if not os.path.isabs(resolved):
            resolved = str((REPO_ROOT / resolved).resolve())
        setattr(cfg.paths, f.name, resolved)


def _config_path() -> Path:
    return Path(os.environ.get("ANIMADEX_CONFIG", REPO_ROOT / "config.toml"))


def load(path: Path | str | None = None) -> Config:
    """Load and validate the config. Missing file is allowed (defaults +
    env overrides apply); missing-but-required values (secret_key,
    admin password if admin used) are reported by callers."""
    cfg = Config()
    target = Path(path) if path else _config_path()
    if target.is_file():
        with open(target, "rb") as f:
            raw = tomllib.load(f)
        for section_name, section_data in raw.items():
            section = getattr(cfg, section_name, None)
            if section is None or not isinstance(section_data, dict):
                continue
            _apply_section(section, section_data)
    _apply_env_overrides(cfg)
    _resolve_paths(cfg)
    return cfg


def ensure_dirs(cfg: Config) -> None:
    """Create the data directories implied by the config if they're missing."""
    for d in (
        cfg.paths.data_dir,
        os.path.join(cfg.paths.characters_dir, "images"),
        os.path.join(cfg.paths.characters_dir, "thumbs"),
        os.path.join(cfg.paths.artists_dir, "images"),
        os.path.join(cfg.paths.artists_dir, "thumbs"),
        os.path.join(cfg.paths.copyrights_dir, "thumbs"),
    ):
        os.makedirs(d, exist_ok=True)
