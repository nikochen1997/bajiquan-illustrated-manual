#!/usr/bin/env python3
"""只在 work/qa 准备 RIFE 候选：页单位一致、头部接页、支撑脚落地。"""

import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

WORK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORK))
import build_animation as build

DESTINATION = WORK / "qa" / "rife-normalized"
REUSE_FROM_KEY = 39


def head_anchor(cell):
    """无举臂的重叠姿态，取人体上方 15% 的有效 alpha 加权重心。"""
    alpha = np.asarray(cell.getchannel("A"), dtype=np.float64)
    _, y0, _, y1 = build.alpha_bbox(cell)
    top, bottom = int(y0), max(int(y0) + 1, int(np.ceil(y0 + (y1 - y0) * 0.15)))
    weights = np.where(alpha[top:bottom] >= build.ALPHA_THRESHOLD, alpha[top:bottom], 0)
    ys, xs = np.indices(weights.shape)
    total = float(weights.sum())
    if total == 0:
        raise ValueError("重叠姿态头部没有有效 alpha")
    return {"x": float((weights * xs).sum() / total),
            "y": float((weights * (ys + top)).sum() / total),
            "top_band": [top, bottom], "valid_pixels": int((weights > 0).sum())}


def normalized_static_page():
    page = build.static_page()
    draw = ImageDraw.Draw(page)
    draw.rectangle((50, 727, 590, 763), fill=build.PAPER)
    draw.text((320, 738), "古风手绘  ·  慢速观摩",
              font=ImageFont.truetype(str(build.FONT_PATH), 18),
              fill=(125, 104, 75), anchor="mt")
    return page


def page_registration(pages, page_details):
    reference_unit = page_details[0]["size"][1] / 4
    scales = [reference_unit / (page["size"][1] / 4) for page in page_details]
    shifts = [0.0]
    joins = []
    for page_index in (1, 2):
        previous = head_anchor(pages[page_index - 1][-1])
        current = head_anchor(pages[page_index][0])
        target_x = previous["x"] * scales[page_index - 1] + shifts[-1]
        shift = target_x - current["x"] * scales[page_index]
        shifts.append(shift)
        joins.append({"pages": [page_index, page_index + 1], "previous_head": previous,
                      "current_head": current, "registered_head_x": target_x,
                      "residual_x": current["x"] * scales[page_index] + shift - target_x})
    return scales, shifts, joins


def make_plan(pages, page_details):
    scales, shifts, joins = page_registration(pages, page_details)
    baseline = build.alpha_bbox(pages[0][0])[3]
    keys = []
    for page_index, cells in enumerate(pages):
        for cell_index, cell in enumerate(cells):
            if page_index and cell_index == 0:
                continue
            key_index = len(keys) + 1
            scale = scales[page_index]
            bbox = build.alpha_bbox(cell)
            transform = (scale, shifts[page_index], baseline - bbox[3] * scale)
            record = {"index": key_index, "original_page": page_index + 1, "original_cell": cell_index + 1,
                      "page_scale": scale, "page_x_shift": shifts[page_index],
                      "ground_y_shift": transform[2], "source_alpha_bbox": list(bbox),
                      "reused_from_key": None}
            if key_index >= REUSE_FROM_KEY:
                cell = keys[0]["cell"]
                transform = keys[0]["transform"]
                record.update({"reused_from_key": 1, "page_scale": transform[0],
                               "page_x_shift": transform[1], "ground_y_shift": transform[2],
                               "source_alpha_bbox": list(build.alpha_bbox(cell))})
            keys.append({"cell": cell, "transform": transform, "record": record})
    if len(keys) != 46:
        raise ValueError("候选关键帧必须为 46 张")
    union = build.union_box([build.apply_transform(build.alpha_bbox(key["cell"]), key["transform"]) for key in keys])
    fit = build.fit_transform(union)
    return keys, fit, {"reference_nominal_grid_height": page_details[0]["size"][1] / 4,
                      "page_scales": scales, "page_x_shifts": shifts,
                      "head_joins": joins, "raw_ground_baseline": baseline,
                      "registered_union": list(union), "global_fit": list(fit),
                      "target_output_baseline": round(baseline * fit[0] + fit[2])}


def render():
    pages, page_details = build.read_pages()
    keys, global_fit, metadata = make_plan(pages, page_details)
    inputs, aligned = DESTINATION / "input", DESTINATION / "aligned"
    inputs.mkdir(parents=True, exist_ok=True)
    aligned.mkdir(parents=True, exist_ok=True)
    static = normalized_static_page()
    static.save(DESTINATION / "static-background.png")
    metadata["pages"] = page_details
    metadata["canvas"] = list(build.SIZE)
    metadata["safe_box"] = list(build.SAFE_BOX)
    metadata["dynamic_box"] = list(build.DYNAMIC_BOX)
    metadata["footer"] = "古风手绘  ·  慢速观摩"
    metadata["keys"] = []
    for key in keys:
        transform = build.combine_transforms(key["transform"], global_fit)
        foreground = build.transform_rgba(key["cell"], transform)
        # 等比变换后的整数像素舍入最多偏移1px；只作竖向整数补偿，确保支撑脚同基线。
        pixel_y_shift = int(metadata["target_output_baseline"] - build.alpha_bbox(foreground)[3])
        if pixel_y_shift:
            shifted = Image.new("RGBA", build.SIZE, (0, 0, 0, 0))
            shifted.paste(foreground, (0, pixel_y_shift))
            foreground = shifted
        clean = Image.new("RGBA", build.SIZE, (0, 0, 0, 0))
        clean.paste(foreground.crop(build.DYNAMIC_BOX), build.DYNAMIC_BOX)
        foreground = clean
        name = "%08d.png" % key["record"]["index"]
        foreground.save(aligned / name)
        composed = static.convert("RGBA")
        composed.alpha_composite(foreground)
        rgb = composed.convert("RGB")
        rgb.save(inputs / name)
        record = dict(key["record"])
        record.update({"combined_transform": list(transform), "pixel_ground_y_shift": pixel_y_shift,
                       "output_alpha_bbox": list(build.alpha_bbox(foreground)),
                       "rgb_sha256_pixels": hashlib.sha256(rgb.tobytes()).hexdigest(),
                       "aligned_sha256_pixels": hashlib.sha256(foreground.tobytes()).hexdigest()})
        metadata["keys"].append(record)
    metadata["input_pattern"] = str(inputs / "%08d.png")
    metadata["aligned_pattern"] = str(aligned / "%08d.png")
    (DESTINATION / "registration.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    font = ImageFont.truetype(str(build.FONT_PATH), 15)
    sheet = Image.new("RGB", (8 * 140, 6 * 198), build.PAPER)
    draw = ImageDraw.Draw(sheet)
    for index in range(46):
        frame = Image.open(inputs / ("%08d.png" % (index + 1))).convert("RGB")
        frame = frame.resize((140, 175), Image.Resampling.LANCZOS)
        x, y = (index % 8) * 140, (index // 8) * 198
        sheet.paste(frame, (x, y))
        label = "%02d%s" % (index + 1, " 收势复用" if index + 1 >= REUSE_FROM_KEY else "")
        draw.text((x + 6, y + 178), label, font=font, fill=build.INK)
    sheet.save(DESTINATION / "46关键帧索引.png")
    heights = [key["output_alpha_bbox"][3] - key["output_alpha_bbox"][1] for key in metadata["keys"]]
    feet = [key["output_alpha_bbox"][3] for key in metadata["keys"]]
    summary = {"rgb_frames": len(keys), "aligned_frames": len(keys), "size": list(build.SIZE),
               "page_scales": metadata["page_scales"], "page_x_shifts": metadata["page_x_shifts"],
               "first_height_pixels": heights[0], "last_height_pixels": heights[-1],
               "foot_baseline_min": min(feet), "foot_baseline_max": max(feet),
               "height_range_preserving_crouch": [min(heights), max(heights)],
               "first_last_rgb_identical": metadata["keys"][0]["rgb_sha256_pixels"] == metadata["keys"][-1]["rgb_sha256_pixels"],
               "reused_key_indices": list(range(REUSE_FROM_KEY, 47))}
    (DESTINATION / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    render()
