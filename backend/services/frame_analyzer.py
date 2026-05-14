"""Extract technical features and optical flow from keyframe images."""
import json
import os
from typing import Optional

import numpy as np
import cv2
from PIL import Image


def analyze_frame(image_path: str) -> Optional[dict]:
    """Extract visual features from a single keyframe."""
    try:
        img = Image.open(image_path).convert("RGB")
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
        "resolution": f"{w}x{h}",
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
        img1 = cv2.imread(frame1_path, cv2.IMREAD_GRAYSCALE)
        img2 = cv2.imread(frame2_path, cv2.IMREAD_GRAYSCALE)
        if img1 is None or img2 is None:
            return None
    except Exception:
        return None

    h, w = img1.shape

    # Resize to speed up computation
    scale = min(1.0, 480.0 / max(h, w))
    if scale < 1.0:
        img1 = cv2.resize(img1, (int(w * scale), int(h * scale)))
        img2 = cv2.resize(img2, (int(w * scale), int(h * scale)))
        h, w = img1.shape

    flow = cv2.calcOpticalFlowFarneback(img1, img2, None, 0.5, 3, 15, 3, 5, 1.2, 0)

    mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
    mean_magnitude = float(np.mean(mag))
    max_magnitude = float(np.max(mag))

    # Direction analysis
    angles = np.arctan2(flow[..., 1], flow[..., 0])
    mean_angle = float(np.mean(angles))

    # Check for zoom (vectors radiating from center)
    cy, cx = h / 2, w / 2
    ys, xs = np.mgrid[:h, :w]
    center_dirs_x = (xs - cx).astype(np.float32)
    center_dirs_y = (ys - cy).astype(np.float32)
    center_dirs_mag = np.sqrt(center_dirs_x**2 + center_dirs_y**2) + 1e-6
    center_dirs_x /= center_dirs_mag
    center_dirs_y /= center_dirs_mag

    # Dot product of flow with radial direction: + = zoom out, - = zoom in
    dot = flow[..., 0] * center_dirs_x + flow[..., 1] * center_dirs_y
    zoom_score = float(np.mean(dot))

    # Determine dominant motion
    if max_magnitude < 0.5:
        motion = "static"
        confidence = 1.0 - mean_magnitude
    elif abs(zoom_score) > 0.3 * mean_magnitude:
        motion = "zoom_in" if zoom_score < 0 else "zoom_out"
        confidence = min(1.0, abs(zoom_score) / (mean_magnitude + 0.01))
    else:
        # Pan/tilt based on dominant direction
        dx_mean = float(np.mean(flow[..., 0]))
        dy_mean = float(np.mean(flow[..., 1]))
        mag_mean = np.sqrt(dx_mean**2 + dy_mean**2)

        if mag_mean < 0.3:
            motion = "static"
        elif abs(dx_mean) > abs(dy_mean) * 1.5:
            motion = "pan_right" if dx_mean > 0 else "pan_left"
        elif abs(dy_mean) > abs(dx_mean) * 1.5:
            motion = "tilt_down" if dy_mean > 0 else "tilt_up"
        else:
            direction = "right" if dx_mean > 0 else "left"
            motion = f"pan_{direction}_with_slight_tilt"
        confidence = min(1.0, mag_mean / 3.0)

    return {
        "dominant_motion": motion,
        "mean_magnitude": round(mean_magnitude, 2),
        "max_magnitude": round(max_magnitude, 2),
        "direction": round(float(np.degrees(mean_angle)) % 360, 0),
        "zoom_score": round(zoom_score, 3),
        "confidence": round(confidence, 2),
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
