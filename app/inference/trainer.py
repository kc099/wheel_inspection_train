"""Training backend — fits a new Patchcore model from a folder of good samples.

Patchcore "training" is not gradient descent: it runs the good images through a
fixed feature extractor and builds a compressed "memory bank" of what normal
looks like. That's why it's fast on CPU and why ~10 good images is enough.

Before fitting we add 90/180/270° rotated copies of every upload (up to
MAX_UPLOAD_IMAGES uploads), so 10 photos become 40 training samples and 25
become 100 — a fuller memory bank without asking for more photos. The
augmented copies live in a temp folder and are thrown away once the fit
finishes.

Runs on a QThread so the UI never freezes. The heavy imports (anomalib/torch)
happen inside run(), not at module load. On success it saves a checkpoint that
is byte-compatible with the inference loader (verified) and hands the path back;
the caller registers it via the model registry.
"""

from __future__ import annotations

import shutil
import tempfile
import traceback
from pathlib import Path

import torch
from PIL import Image
from PySide6.QtCore import QThread, Signal

# Image extensions accepted as training samples.
_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}

# Ceiling on the operator's uploaded images.
MAX_UPLOAD_IMAGES = 25

# Ceiling on the TOTAL training set (uploads + rotated copies). Rotations top
# the set up from the uploads to here: ≤15 uploads keep all three rotations,
# 25 uploads get 35 rotations spread evenly across images and angles.
MAX_TRAIN_IMAGES = 60

# Fraction of extracted patch features kept in the memory bank. Bigger = a fuller
# picture of "normal" at the cost of TRAINING TIME (greedy coreset selection is
# the slowest step), checkpoint size, and inference speed. Coverage should come
# from diverse uploads + rotations (≈100 samples), not from hoarding patches:
# 0.1 of 100 images ≈ 10k patches — 2.5× the bank that already separated
# models cleanly (scores ~8 genuine vs ~48 foreign).
CORESET_RATIO = 0.1

# Backbone layers to embed. layer2+layer4 (the Assets/train.py recipe) mixes
# local texture with global shape, which tolerates the wheel's position and
# rotation changes far better than layer2+layer3 did. The inference engine
# detects a checkpoint's layers from its memory-bank width, so models trained
# either way keep loading.
LAYERS = ["layer2", "layer4"]

# Only right-angle turns: they're pixel-exact, so an augmented sample stays a
# real photo of a good wheel instead of a resampled one with black corners.
_ROTATIONS = (90, 180, 270)


def list_images(folder: str | Path) -> list[Path]:
    """The usable images directly in `folder`, name-sorted. Returns: list[Path]."""
    p = Path(folder)
    if not p.is_dir():
        return []
    return sorted(f for f in p.iterdir() if f.suffix.lower() in _EXTS)


def count_images(folder: str | Path) -> int:
    """How many usable images are directly in `folder`. Returns: int."""
    return len(list_images(folder))


def build_training_set(src: str | Path, dst: str | Path,
                       max_uploads: int = MAX_UPLOAD_IMAGES,
                       max_total: int = MAX_TRAIN_IMAGES) -> int:
    """Fill `dst` with the training samples for one model.

    Takes the first `max_uploads` images from `src`, then adds rotated copies
    (90/180/270°) until `max_total` is reached. Rotations are written as PNG so
    an RGBA/palette source can't fail the save. Returns: total images in `dst`.
    """
    dst = Path(dst)
    dst.mkdir(parents=True, exist_ok=True)

    originals = list_images(src)[:max_uploads]
    for f in originals:
        shutil.copy2(f, dst / f.name)

    n = len(originals)
    # Offsetting the angle by the image's position means a partial pass still
    # spreads across all three turns instead of rotating everything by 90°.
    for turn in range(len(_ROTATIONS)):
        for i, f in enumerate(originals):
            if n >= max_total:
                return n
            angle = _ROTATIONS[(i + turn) % len(_ROTATIONS)]
            with Image.open(f) as img:
                img.rotate(angle, expand=True).save(dst / f"{f.stem}_rot{angle}.png")
            n += 1
    return n


class TrainThread(QThread):
    """Fits one Patchcore model in the background.

    Signals:
      progress(str) — coarse status text for the UI.
      done(str)     — path to the trained checkpoint (.pt) on success.
      failed(str)   — error message on failure.
    """

    progress = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, image_dir: str, model_name: str) -> None:
        super().__init__()
        self._image_dir = Path(image_dir)
        self._model_name = model_name

    def run(self) -> None:
        try:
            self.progress.emit("Loading training stack…")
            from anomalib.data import Folder
            from anomalib.data.utils.split import ValSplitMode
            from anomalib.engine import Engine
            from anomalib.models import Patchcore

            with tempfile.TemporaryDirectory() as workdir:
                self.progress.emit("Preparing training images…")
                samples = Path(workdir) / "good"
                n = build_training_set(self._image_dir, samples)

                # Validation/test set = the plain uploads. Keeping it separate
                # from normal_dir (Assets/train.py recipe) means NO training
                # image is held out of the memory bank — the synthetic split we
                # used before stole ~20% of the uploads, and exactly those
                # images then failed recognition.
                val = Path(workdir) / "val"
                val.mkdir()
                for f in list_images(self._image_dir)[:MAX_UPLOAD_IMAGES]:
                    shutil.copy2(f, val / f.name)

                # anomalib's Folder wants root + normal/test dirs under it.
                dm = Folder(
                    name=f"train_{self._model_name}",
                    root=str(workdir),
                    normal_dir=samples.name,
                    abnormal_dir=None,
                    normal_test_dir=val.name,
                    train_batch_size=32,
                    eval_batch_size=32,
                    num_workers=0,               # 0 = safe on Windows / inside a thread
                    seed=42,
                    val_split_mode=ValSplitMode.SAME_AS_TEST,
                )

                self.progress.emit(
                    f"Building the model’s normal-image memory from {n} images…"
                )
                # coreset_sampling_ratio: anomalib's 0.1 default keeps only 10% of
                # the patch features, which is tuned for datasets of hundreds of
                # images. With a bank this small we keep half (Assets recipe).
                model = Patchcore(
                    backbone="resnet50",
                    layers=list(LAYERS),
                    pre_trained=True,
                    coreset_sampling_ratio=CORESET_RATIO,
                    num_neighbors=9,
                )
                # max_epochs stays 1: Patchcore.trainer_arguments forces it to 1
                # anyway, and a 2nd pass would only re-append the same embeddings
                # to the memory bank. To get a richer bank, add images or raise
                # coreset_sampling_ratio above — not epochs.
                # default_root_dir inside the temp workdir: anomalib's training
                # scratch (checkpoints, run folders) is wiped with it instead of
                # piling up under results/ on the production machine.
                engine = Engine(
                    max_epochs=1, accelerator="auto", devices=1,
                    logger=False, enable_progress_bar=False,
                    default_root_dir=str(Path(workdir) / "scratch"),
                )
                engine.fit(model=model, datamodule=dm)

            self.progress.emit("Saving the trained model…")
            sd = model.state_dict()
            # The validation set is all-normal, so anomalib's image_min/image_max
            # span only the normal scores — a hair-thin band that maps every
            # real capture to ~0 confidence (verified: a perfect-coverage model
            # rejected 25/25 genuine parts). Widen image_max to 2× image_min so
            # confidence decays over a usable range: score at image_min → 1.0,
            # at 2× image_min → 0. Foreign parts score ~6× higher, still 0.
            mn = float(sd["post_processor.image_min"])
            mx = float(sd["post_processor.image_max"])
            sd["post_processor.image_max"] = torch.tensor(max(2.0 * mn, mn + 1.0, mx))
            tmp = Path(tempfile.gettempdir()) / f"{self._model_name}_trained.pt"
            torch.save(sd, tmp)

            self.done.emit(str(tmp))
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(str(e))
