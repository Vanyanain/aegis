"""Noise-residual CNN for tamper detection on real receipt photographs.

WHY A VISION MODEL AT ALL, AND WHY THIS ONE.

On the rendered corpus the classical features were enough -- generated fakes carried broken
arithmetic and impossibly regular typography. On REAL photographs they are not: hand-crafted
features reach 0.494 recall against a 0.770 human baseline, because a copy-move inside a
genuine photograph leaves no arithmetic trace the OCR can reliably read and no typographic
tell at all. What it does leave is a local statistical discontinuity, and that is a texture
problem, which is what convolutions are for.

The architecture is deliberately not a fine-tuned ImageNet backbone. With 2,400 training
images, a large pretrained network fits semantics -- "restaurant receipt", "placemat" --
which are irrelevant and identical in both classes. The literature's answer for this
problem (SRM-filtered residual streams, as in RGB-N and ManTra-Net) is to strip the
semantics out before the first learned layer:

  1. A FIXED, non-trainable high-pass front end. Three SRM-style kernels suppress image
     content and leave the noise residual. Nothing about the meal, the paper or the lighting
     survives this; only the sensor and compression statistics do.
  2. A small learned stack on top of the residual.
  3. Global MAX pooling, not average. Tampering is LOCAL -- one region out of hundreds --
     and averaging over the whole page dilutes exactly the signal being looked for.

Exported to ONNX for serving, because this environment's torch and LightGBM link different
OpenMP runtimes and loading a LightGBM booster in a process that has imported torch
segfaults the interpreter. onnxruntime carries no such dependency.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

# SRM-style high-pass kernels. Fixed, never learned: their job is to remove image content
# so the network cannot cheat by recognising what the receipt is a picture of.
SRM_KERNELS = np.array([
    # first-order horizontal difference
    [[0, 0, 0, 0, 0],
     [0, 0, 0, 0, 0],
     [0, 1, -2, 1, 0],
     [0, 0, 0, 0, 0],
     [0, 0, 0, 0, 0]],
    # second-order Laplacian-like
    [[0, 0, 0, 0, 0],
     [0, -1, 2, -1, 0],
     [0, 2, -4, 2, 0],
     [0, -1, 2, -1, 0],
     [0, 0, 0, 0, 0]],
    # third-order edge kernel
    [[-1, 2, -2, 2, -1],
     [2, -6, 8, -6, 2],
     [-2, 8, -12, 8, -2],
     [2, -6, 8, -6, 2],
     [-1, 2, -2, 2, -1]],
], dtype=np.float32)
SRM_NORM = np.array([2.0, 4.0, 12.0], dtype=np.float32)

INPUT_SIZE = 384
ELA_QUALITY = 90


def ela_channel(img: Image.Image, quality: int = ELA_QUALITY) -> np.ndarray:
    """Error-level analysis map: how much the image changes when recompressed.

    A region pasted into an already-compressed photograph has a different compression
    history from its surroundings and moves differently under recompression.
    """
    rgb = img.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    re = Image.open(buf).convert("RGB")
    d = np.abs(np.asarray(rgb, dtype=np.int16) - np.asarray(re, dtype=np.int16))
    return d.max(axis=2).astype(np.float32)


def prepare(img: Image.Image, size: int = INPUT_SIZE) -> np.ndarray:
    """Build the 2-channel input: grayscale and ELA, both resized and scaled.

    Resizing happens AFTER the ELA is computed. Computing it on a resampled image would
    measure the resampler's own artefacts rather than the file's compression history.
    """
    ela = ela_channel(img)
    gray = np.asarray(img.convert("L"), dtype=np.float32)

    ela_i = Image.fromarray(np.clip(ela * 8, 0, 255).astype(np.uint8)).resize(
        (size, size), Image.BILINEAR)
    gray_i = Image.fromarray(gray.astype(np.uint8)).resize((size, size), Image.BILINEAR)

    x = np.stack([
        np.asarray(gray_i, dtype=np.float32) / 255.0,
        np.asarray(ela_i, dtype=np.float32) / 255.0,
    ])
    return x


def build_model():
    """The residual CNN. Imported lazily so torch never loads in a LightGBM process."""
    import torch
    import torch.nn as nn

    class SRMFront(nn.Module):
        """Fixed high-pass front end. Weights are buffers, not parameters."""

        def __init__(self, in_ch: int = 2):
            super().__init__()
            k = SRM_KERNELS / SRM_NORM[:, None, None]
            w = np.repeat(k[:, None, :, :], in_ch, axis=1) / in_ch
            self.register_buffer("weight", torch.tensor(w, dtype=torch.float32))

        def forward(self, x):
            import torch.nn.functional as F
            return torch.clamp(F.conv2d(x, self.weight, padding=2), -8.0, 8.0)

    def block(i, o, pool=True):
        layers = [nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True),
                  nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True)]
        if pool:
            layers.append(nn.MaxPool2d(2))
        return nn.Sequential(*layers)

    class TamperNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.front = SRMFront(2)
            self.body = nn.Sequential(
                block(3, 32), block(32, 64), block(64, 96), block(96, 128),
            )
            self.head = nn.Sequential(nn.Dropout(0.3), nn.Linear(128, 1))

        def forward(self, x):
            import torch
            h = self.body(self.front(x))
            # MAX over space: a tamper occupies one region, and averaging would dilute it.
            h = torch.amax(h, dim=(2, 3))
            return self.head(h).squeeze(-1)

    return TamperNet()


class OnnxTamperModel:
    """Serving-side wrapper. No torch import, so it is safe alongside LightGBM."""

    def __init__(self, path: str):
        import onnxruntime as ort

        self.sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self.input_name = self.sess.get_inputs()[0].name

    def score(self, img: Image.Image) -> float:
        x = prepare(img)[None, ...].astype(np.float32)
        logit = float(self.sess.run(None, {self.input_name: x})[0].ravel()[0])
        return float(1.0 / (1.0 + np.exp(-logit)))
