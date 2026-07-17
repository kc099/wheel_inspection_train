import argparse, os, shutil
from pathlib import Path
import torch
from torchvision import transforms
from anomalib.data import Folder
from anomalib.data.utils.split import ValSplitMode
from anomalib.models import Patchcore
from anomalib.engine import Engine

os.environ["RANK"] = "0"

# Derived from this file's own location, so moving the project folder (or running
# from a different working directory) doesn't strand the dataset and checkpoints.
PROJECT_DIR  = Path(__file__).resolve().parent
DATASET_ROOT = PROJECT_DIR / "dataset"
RESULTS_DIR  = PROJECT_DIR / "results"
# Where the WPF app's inference server loads checkpoints from: models/<name>/model.ckpt.
MODELS_DIR   = PROJECT_DIR / "models"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MODEL_NAME = "model1"   # fallback for a plain `python train.py` run

# Custom transforms to handle shadows
train_transform = transforms.Compose([
    transforms.Resize((256, 256)),
    # Normalize lighting to reduce shadow effect
    transforms.ColorJitter(
        brightness=0.3,
        contrast=0.3,
        saturation=0.1,
    ),
    transforms.ToTensor(),
    transforms.Normalize( #zero mean, unit variance
        mean=[0.485, 0.456, 0.406],
        std=[0.229, 0.224, 0.225] #xi_model = xi_input-mean/std
    ),
])


def train_model(model_name, epochs=1, train_batch_size=32, eval_batch_size=32):
    """Train a Patchcore anomaly model for a single wheel-model class and
    return the path to its checkpoint. Results for each class land under
    their own `results/Patchcore/<model_name>/` folder so multiple trained
    models never collide."""
    datamodule = Folder(
        name=model_name,
        normal_dir=f"train/{model_name}",
        root=str(DATASET_ROOT),
        abnormal_dir=None,
        normal_test_dir=f"val/{model_name}",
        train_batch_size=train_batch_size,
        eval_batch_size=eval_batch_size,
        num_workers=0,
        seed=42,
        # prepare_dataset.py already carved out val/<model_name>. Without this, anomalib
        # defaults to FROM_TEST and re-splits that folder in half again -- which yields an
        # empty validation set (and a ZeroDivisionError) for a small folder of samples.
        val_split_mode=ValSplitMode.SAME_AS_TEST,
    )
    datamodule.setup()

    model = Patchcore(
        backbone="resnet50",
        layers=["layer2", "layer4"],
        pre_trained=True,
        coreset_sampling_ratio=0.5,
        num_neighbors=9,
    )

    engine = Engine(
        accelerator="auto",
        max_epochs=epochs,
        default_root_dir=str(RESULTS_DIR),
    )

    print(f"Starting Training for '{model_name}'...")
    engine.fit(datamodule=datamodule, model=model)

    print("Starting Testing...")
    engine.test(datamodule=datamodule, model=model)

    ckpt_path = (engine.trainer.checkpoint_callback.best_model_path
                 or engine.trainer.checkpoint_callback.last_model_path)
    if not ckpt_path:
        raise RuntimeError("Training finished but no checkpoint was saved.")

    # Copy to a stable, predictable location for downstream consumers (the WPF app),
    # since anomalib nests the real file under a version folder that changes each run.
    stable_ckpt = MODELS_DIR / model_name / "model.ckpt"
    stable_ckpt.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ckpt_path, stable_ckpt)

    print(f"\nDone! Checkpoint: {stable_ckpt}")
    return stable_ckpt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train a Patchcore anomaly model for one wheel-model class.")
    parser.add_argument("model_name", nargs="?", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--epochs", type=int, default=1)
    args = parser.parse_args()
    train_model(args.model_name, epochs=args.epochs)
