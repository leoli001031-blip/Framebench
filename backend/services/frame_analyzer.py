"""Extract technical features and optical flow from keyframe images."""
import json
import os
from typing import Optional

import numpy as np
from PIL import Image


_FRAME_ANALYSIS_MAX_SIDE = 1024


def analyze_frame(image_path: str) -> Optional[dict]:
    """Extract visual features from a single keyframe."""
    try:
        with Image.open(image_path) as source:
            original_width, original_height = source.size
            img = source.convert("RGB")
        if max(img.size) > _FRAME_ANALYSIS_MAX_SIDE:
            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            img.thumbnail((_FRAME_ANALYSIS_MAX_SIDE, _FRAME_ANALYSIS_MAX_SIDE), resampling)
    except Exception:
        return None

    arr = np.array(img, dtype=np.float32) / 255.0
    h, w = arr.shape[:2]

    lum = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
    mean_lum = float(np.mean(lum))
    std_lum = float(np.std(lum))

    shadows_pct = float(np.mean(lum < 0.15))
    midtones_pct = float(np.mean((lum >= 0.15) & (lum <= 0.7)))
    highlights_pct = float(np.mean(lum > 0.7))

    warm_mask = (arr[:, :, 0] > arr[:, :, 2] * 1.1)
    cool_mask = (arr[:, :, 2] > arr[:, :, 0] * 1.1)
    warm_pct = float(np.mean(warm_mask))
    cool_pct = float(np.mean(cool_mask))
    neutral_pct = 1.0 - warm_pct - cool_pct

    hsv = np.array(Image.fromarray((arr * 255).astype(np.uint8)).convert("HSV"), dtype=np.float32)
    mean_sat = float(np.mean(hsv[:, :, 1] / 255.0))
    std_sat = float(np.std(hsv[:, :, 1] / 255.0))

    dominant_colors = _extract_dominant_colors(arr, n_colors=4)

    gy, gx = np.gradient(lum)
    edge_mag = np.sqrt(gx ** 2 + gy ** 2)
    mean_edge = float(np.mean(edge_mag))
    std_edge = float(np.std(edge_mag))

    hist, _ = np.histogram(lum, bins=32, range=(0, 1))
    hist = hist / hist.sum()
    hist = hist[hist > 0]
    entropy = float(-np.sum(hist * np.log2(hist)))

    left_half = np.mean(lum[:, : w // 2])
    right_half = np.mean(lum[:, w // 2:])
    top_half = np.mean(lum[: h // 2, :])
    bottom_half = np.mean(lum[h // 2:, :])
    lr_balance = float(left_half - right_half)
    tb_balance = float(top_half - bottom_half)

    return {
        "resolution": f"{original_width}x{original_height}",
        "mean_brightness": round(mean_lum, 3),
        "contrast_std": round(std_lum, 3),
        "shadows_pct": round(shadows_pct, 2),
        "midtones_pct": round(midtones_pct, 2),
        "highlights_pct": round(highlights_pct, 2),
        "color_temperature": (
            "warm-dominant" if warm_pct > 0.35
            else "cool-dominant" if cool_pct > 0.35
            else "neutral"
        ),
        "warm_ratio": round(warm_pct, 2),
        "cool_ratio": round(cool_pct, 2),
        "neutral_ratio": round(neutral_pct, 2),
        "saturation_mean": round(mean_sat, 3),
        "saturation_std": round(std_sat, 3),
        "dominant_colors": dominant_colors,
        "edge_density_mean": round(mean_edge, 5),
        "edge_density_std": round(std_edge, 5),
        "entropy": round(entropy, 2),
        "lr_balance": round(lr_balance, 3),
        "tb_balance": round(tb_balance, 3),
    }


def compute_optical_flow(frame1_path: str, frame2_path: str) -> Optional[dict]:
    """Compute dense optical flow between two frames to detect camera movement.

    Returns a dict with motion description, or None if frames can't be loaded.
    """
    try:
        import cv2
    except Exception:
        return None

    try:
        with Image.open(frame1_path) as source1:
            img1 = source1.convert("L")
        with Image.open(frame2_path) as source2:
            img2 = source2.convert("L")
    except Exception:
        return None

    try:
        max_side = 320
        if max(img1.size) > max_side:
            img1.thumbnail((max_side, max_side))
        if max(img2.size) > max_side:
            img2.thumbnail((max_side, max_side))
        if img1.size != img2.size:
            img2 = img2.resize(img1.size)

        prev = np.array(img1, dtype=np.uint8)
        nxt = np.array(img2, dtype=np.uint8)
        flow = cv2.calcOpticalFlowFarneback(prev, nxt, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
    except Exception:
        return None

    mean_mag = float(np.mean(mag))
    if mean_mag < 0.25:
        movement = "static"
    else:
        mean_x = float(np.mean(flow[..., 0]))
        mean_y = float(np.mean(flow[..., 1]))
        if abs(mean_x) > abs(mean_y):
            movement = "pan-right" if mean_x > 0 else "pan-left"
        else:
            movement = "tilt-down" if mean_y > 0 else "tilt-up"

    return {
        "motion_magnitude": round(mean_mag, 3),
        "motion_std": round(float(np.std(mag)), 3),
        "dominant_angle_deg": round(float(np.degrees(np.mean(ang))), 1),
        "camera_movement": movement,
    }


def _extract_dominant_colors(arr: np.ndarray, n_colors: int = 4) -> list[dict]:
    # Keep arithmetic outside uint8; NumPy 2 rejects multiplying uint8 by 256.
    q = np.clip(arr * 15, 0, 15).astype(np.int32)
    indices = q[:, :, 0] * 256 + q[:, :, 1] * 16 + q[:, :, 2]
    counts = np.bincount(indices.flatten(), minlength=4096)

    top_n = np.argpartition(counts, -n_colors)[-n_colors:]
    top_n = top_n[np.argsort(counts[top_n])[::-1]]

    colors = []
    total = counts.sum()
    for idx in top_n:
        if counts[idx] == 0:
            continue
        b = idx % 16
        g = (idx // 16) % 16
        r = idx // 256
        colors.append({
            "hex": f"#{int(r/15*255):02x}{int(g/15*255):02x}{int(b/15*255):02x}",
            "pct": round(counts[idx] / total, 2),
        })

    return colors[:n_colors]
