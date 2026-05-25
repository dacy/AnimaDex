"""Artist quality scoring via the Muinez/artwork-scorer model.

Optional. Requires `pip install -r requirements-scoring.txt`. The
heavy imports (torch, transformers) are deferred to the first call so
users who don't need scoring don't pay for them at startup.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from .. import db
from ..config import Config
from ..images import full_path

REPO_ID = 'Muinez/artwork-scorer'
MODEL_FILES = ['config.json', 'preprocessor_config.json',
               'model.safetensors']
COMMIT_EVERY = 20

_model_cache = None  # (processor, model, device, score_idx)


def _model_dir(cfg: Config) -> str:
    return os.path.join(cfg.paths.data_dir, 'models', 'artwork-scorer')


def _ensure_model(model_dir: str) -> None:
    if os.path.exists(os.path.join(model_dir, 'model.safetensors')):
        return
    print(f'Downloading {REPO_ID} -> {model_dir}', flush=True)
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=REPO_ID, local_dir=model_dir,
                      allow_patterns=MODEL_FILES)
    print('Model downloaded.', flush=True)


def _load_model(cfg: Config):
    global _model_cache
    if _model_cache is not None:
        return _model_cache
    try:
        import torch
        from transformers import (AutoImageProcessor,
                                  AutoModelForImageClassification)
    except ImportError as e:
        raise RuntimeError(
            f'Scoring dependency missing: {e}. Install with: '
            f'pip install -r requirements-scoring.txt'
        ) from e

    model_dir = _model_dir(cfg)
    _ensure_model(model_dir)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f'Loading artwork-scorer on {device} ...', flush=True)
    processor = AutoImageProcessor.from_pretrained(model_dir)
    model = AutoModelForImageClassification.from_pretrained(model_dir)
    model.eval().to(device)
    torch.set_grad_enabled(False)
    score_idx = model.config.label2id.get('score', 0)
    _model_cache = (processor, model, device, score_idx, torch)
    return _model_cache


def score_one(conn, cfg: Config, artist_slug: str, imgname: str) -> float | None:
    """Score one artist's image and write the result. Returns the
    score (or None if the image wasn't on disk)."""
    from PIL import Image
    path = full_path(cfg, 'artists', imgname)
    if not os.path.isfile(path):
        return None
    processor, model, device, score_idx, torch = _load_model(cfg)
    with Image.open(path) as im:
        inputs = processor(images=im.convert('RGB'),
                           return_tensors='pt').to(device)
    logits = model(**inputs).logits
    value = float(torch.sigmoid(logits)[0, score_idx])
    db.set_artist_score(conn, artist_slug, value)
    return value


def score_all_pending(cfg: Config, limit: int = 0) -> dict:
    """Bulk: score every artist with no score yet. Returns a summary."""
    conn = db.connect_rw(cfg.paths.database)
    try:
        todo = conn.execute(
            'SELECT artist, imgname FROM artists '
            'WHERE score IS NULL').fetchall()
        if limit:
            todo = todo[:limit]
        total = len(todo)
        print(f'{total} artist(s) need scoring.', flush=True)
        if not total:
            return {'scored': 0, 'skipped': 0, 'failed': 0}

        scored = skipped = failed = 0
        start = time.time()
        try:
            for index, row in enumerate(todo, 1):
                try:
                    value = score_one(conn, cfg, row['artist'],
                                       row['imgname'])
                except (OSError, RuntimeError) as e:
                    failed += 1
                    print(f'  FAIL {row["artist"]}: {e}', flush=True)
                    continue
                if value is None:
                    skipped += 1
                    continue
                scored += 1
                if scored % COMMIT_EVERY == 0:
                    conn.commit()
                    rate = scored / max(time.time() - start, 0.001)
                    print(f'  {index}/{total}  scored={scored}  '
                          f'({rate:.1f} img/s)', flush=True)
        except KeyboardInterrupt:
            print('\nInterrupted -- committing progress, re-run to '
                  'resume.')
        conn.commit()
        elapsed = time.time() - start
        print(f'\nDone. scored={scored} skipped={skipped} failed={failed} '
              f'elapsed={elapsed:.0f}s')
        return {'scored': scored, 'skipped': skipped, 'failed': failed}
    finally:
        conn.close()
