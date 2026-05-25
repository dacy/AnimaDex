"""Image generation hook.

The current generator is the standalone `scripts/generate_dataset.py`
ComfyUI driver. We don't import it -- ComfyUI workflows are
installation-specific. Instead, the orchestrator shells out to that
script when `cfg.generation.workflow_file` is set, otherwise generation
is treated as disabled and the orchestrator just notes any missing
images so the user knows to drop their own renders into place.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..config import Config
from ..images import full_path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / 'scripts' / 'generate_dataset.py'


def is_enabled(cfg: Config) -> bool:
    return bool(cfg.generation.workflow_file
                and os.path.isfile(cfg.generation.workflow_file))


def render(cfg: Config, mode: str, imgname: str, tags: str = '',
           timeout: int = 600) -> bool:
    """Invoke the generator for one image. Returns True on success.
    Raises RuntimeError with the generator's stderr/stdout on failure.

    `tags` overrides the prompt; pass an empty string to let the
    generator pick its own (from the CSV row it looks up by name).
    """
    if not is_enabled(cfg):
        return False
    if not SCRIPT_PATH.is_file():
        raise RuntimeError(f'generator script missing: {SCRIPT_PATH}')

    out_path = full_path(cfg, mode, imgname)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    cmd = [sys.executable, str(SCRIPT_PATH), '--regen',
           '--out', out_path,
           '--workflow', cfg.generation.workflow_file,
           '--comfyui-url', cfg.generation.comfyui_url]
    if tags:
        cmd += ['--tags', tags]

    try:
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT),
                              capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f'generation timed out after {timeout}s') from e
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or 'generation failed').strip()
        raise RuntimeError(msg[-500:])
    return True
