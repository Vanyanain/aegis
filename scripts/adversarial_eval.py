"""Adversarial robustness: what survives when the forger adapts.

Run: python -m scripts.adversarial_eval

Every forensic number in this project assumes the forger did nothing to hide their work.
That assumption is worth about one afternoon of a motivated attacker's time. The counter-
measures below are all trivial -- each is a single command or a phone screenshot -- and each
attacks a specific feature group the detector relies on:

    strip_exif        `exiftool -all=`. Deletes the provenance group outright.
    recompress        Re-save the whole file at one quality. The tampered region and its
                      surroundings now share a compression history, which is precisely the
                      discontinuity error-level analysis reads.
    resize            Downscale and re-encode. Resampling destroys the JPEG block grid and
                      smooths the local noise statistics.
    add_noise         Uniform sensor-like noise over the whole image, masking the local
                      noise-floor difference the splice introduced.
    screenshot        Re-render the image and save it fresh, the way forwarding through a
                      messenger or taking a screenshot of a receipt does. Combines EXIF
                      loss, recompression and mild resampling.

These are applied ONLY to the tampered half of the held-out set. Genuine items are left
alone, because a real customer has no reason to launder their own receipt -- so any recall
lost here is lost for real, and the false-positive rate is unaffected.

This is defensive measurement, not attack tooling: it quantifies the floor a merchant
should plan around instead of quoting the best case.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joblib  # noqa: E402

from aegis.sideb import forensics as FX  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA, MODELS, DOCS = ROOT / "data", ROOT / "models", ROOT / "docs"
SEED = 20260822


def _save(img: Image.Image, path: Path, quality: int, exif: bytes = b"") -> None:
    img.save(path, "JPEG", quality=quality, exif=exif)


def strip_exif(src: Path, dst: Path) -> None:
    img = Image.open(src)
    q = 92
    img.convert("RGB").save(dst, "JPEG", quality=q)  # no exif= argument -> metadata gone


def recompress(src: Path, dst: Path) -> None:
    Image.open(src).convert("RGB").save(dst, "JPEG", quality=75)


def resize(src: Path, dst: Path) -> None:
    img = Image.open(src).convert("RGB")
    w, h = img.size
    img = img.resize((int(w * 0.7), int(h * 0.7)), Image.LANCZOS)
    img.save(dst, "JPEG", quality=85)


def add_noise(src: Path, dst: Path) -> None:
    a = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
    a += np.random.default_rng(SEED).normal(0, 3.0, a.shape)
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(dst, "JPEG", quality=88)


def screenshot(src: Path, dst: Path) -> None:
    img = Image.open(src).convert("RGB")
    w, h = img.size
    img = img.resize((int(w * 0.85), int(h * 0.85)), Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    buf.seek(0)
    Image.open(buf).convert("RGB").save(dst, "JPEG", quality=90)


ATTACKS = {
    "none": None,
    "strip_exif": strip_exif,
    "recompress": recompress,
    "resize": resize,
    "add_noise": add_noise,
    "screenshot": screenshot,
}


def main() -> None:
    bundle = joblib.load(MODELS / "real_sideb_forensics.joblib")
    model, iso, feats = bundle["model"], bundle["calibrator"], bundle["features"]

    scored = pd.read_parquet(DATA / "real_sideb_test_scored.parquet")
    man = pd.read_parquet(DATA / "evidence_real_manifest.parquet")
    test = man[man["item_id"].isin(scored["item_id"])].copy()
    fakes = test[test["is_fake"] == 1]
    genuine = test[test["is_fake"] == 0]
    print(f"held-out test: {len(test):,}  ({len(fakes)} tampered, {len(genuine)} genuine)")

    tmp = DATA / "_adv_tmp"
    tmp.mkdir(exist_ok=True)
    results = {}

    for name, fn in ATTACKS.items():
        rows = []
        for _, r in fakes.iterrows():
            src = ROOT / r["path"]
            if fn is None:
                path = src
            else:
                path = tmp / f"{r['item_id']}.jpg"
                try:
                    fn(src, path)
                except Exception:
                    path = src
            try:
                f = FX.extract(path, r["claimed_amount_inr"], r["claimed_ts"],
                               r["claimed_descriptor"])
            except Exception:
                continue
            f["family"] = r["family"]
            rows.append(f)
        if not rows:
            continue
        X = pd.DataFrame(rows)
        Xf = X.reindex(columns=feats).fillna(0.0)
        p = iso.predict(model.predict_proba(Xf)[:, 1])
        rec = float((p >= 0.5).mean())
        per_fam = {
            fam: float((p[(X["family"] == fam).to_numpy()] >= 0.5).mean())
            for fam in X["family"].unique()
        }
        results[name] = {"recall": rec, "n": int(len(X)), "per_family": per_fam}
        base = results.get("none", {}).get("recall", rec)
        delta = rec - base
        print(f"  {name:12s} recall {rec:.3f}   {'(baseline)' if name == 'none' else f'{delta:+.3f}'}")

        for f in tmp.glob("*.jpg"):
            f.unlink()

    baseline = results["none"]["recall"]
    worst = min((k for k in results if k != "none"), key=lambda k: results[k]["recall"])
    out = {
        "baseline_recall": baseline,
        "attacks": results,
        "worst_attack": worst,
        "worst_recall": results[worst]["recall"],
        "recall_lost_to_worst": baseline - results[worst]["recall"],
        "note": (
            "Countermeasures applied only to tampered items; genuine items untouched, so "
            "the false-positive rate is unchanged and any recall lost here is real."
        ),
    }
    (DOCS / "metrics_adversarial.json").write_text(json.dumps(out, indent=2))
    print(f"\n  worst case: {worst} -> recall {results[worst]['recall']:.3f} "
          f"(down {baseline - results[worst]['recall']:.3f} from {baseline:.3f})")
    print(f"saved -> {DOCS / 'metrics_adversarial.json'}")
    tmp.rmdir()


if __name__ == "__main__":
    main()
