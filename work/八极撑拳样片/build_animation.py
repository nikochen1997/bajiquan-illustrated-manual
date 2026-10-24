#!/usr/bin/env python3
"""把三张连续姿态精灵图裁成关键帧，再以运动补偿插帧输出 PNG 和 MP4。"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent
GENERATED = WORK / "generated"
OUTPUT = ROOT / "outputs" / "八极拳-撑拳样片"
SIZE = (640, 800)
KEY_RATE = "9/2"
FRAME_RATE = 24
FRAME_COUNT = 240
PAPER = (241, 230, 208)
INK = (74, 61, 46)
ALPHA_THRESHOLD = 24
SEAM_ALPHA_THRESHOLD = 8
SEAM_RADIUS = 8
SAFE_BOX = (62, 180, 578, 698)
DYNAMIC_BOX = (46, 164, 594, 714)
FONT_PATH = Path("/System/Library/Fonts/Supplemental/Songti.ttc")


def run(command, log_path=None):
    """命令使用参数数组，避免路径、中文及空格经 shell 再解释。"""
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    if log_path:
        Path(log_path).write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("命令失败: {}\n{}".format(command[0], result.stdout[-5000:]))
    return result.stdout


def grid_box(width, height, index):
    """按整页比例四等分；非 4 整除的边长也覆盖全部像素。"""
    row, column = divmod(index, 4)
    return (round(column * width / 4), round(row * height / 4),
            round((column + 1) * width / 4), round((row + 1) * height / 4))


def safe_grid_boundaries(sheet):
    """比例线 ±8px 内找无有效笔画的整页缝；低 alpha 噪点不当作人体。"""
    alpha = np.asarray(sheet.getchannel("A"))
    effective = alpha >= SEAM_ALPHA_THRESHOLD
    boundaries = {}
    seams = []
    for axis, length, counts, maxima in (
            ("x", sheet.width, effective.sum(axis=0), alpha.max(axis=0)),
            ("y", sheet.height, effective.sum(axis=1), alpha.max(axis=1))):
        positions = [0]
        for division in (1, 2, 3):
            nominal = round(length * division / 4)
            candidates = [position for position in range(max(1, nominal - SEAM_RADIUS),
                          min(length - 1, nominal + SEAM_RADIUS) + 1) if counts[position] == 0]
            if not candidates:
                return None, seams
            chosen = min(candidates, key=lambda position: (abs(position - nominal), int(maxima[position]), position))
            positions.append(chosen)
            seams.append({"axis": axis, "nominal": nominal, "actual": chosen,
                          "max_alpha_on_seam": int(maxima[chosen])})
        positions.append(length)
        boundaries[axis] = positions
    return boundaries, seams


def component_crops(sheet):
    """无整页缝时分离完整连通人物；恢复到各自名义格坐标而不切断手脚。"""
    import cv2

    alpha = np.asarray(sheet.getchannel("A"))
    count, labels, stats, centers = cv2.connectedComponentsWithStats(
        (alpha >= SEAM_ALPHA_THRESHOLD).astype(np.uint8), connectivity=8)
    candidates = sorted(range(1, count), key=lambda label: int(stats[label, cv2.CC_STAT_AREA]), reverse=True)[:16]
    minimum_area = sheet.width * sheet.height / (16 * 500)
    if len(candidates) != 16 or min(stats[label, cv2.CC_STAT_AREA] for label in candidates) < minimum_area:
        raise ValueError("没有透明横缝，且未找到 16 个可可靠分离的完整人物；停止以免切断人物")
    people = {}
    for label in candidates:
        cx, cy = centers[label]
        index = min(3, int(cy * 4 / sheet.height)) * 4 + min(3, int(cx * 4 / sheet.width))
        if index in people:
            raise ValueError("完整人物连通域无法一一对应 4×4 名义格；需要复核源图")
        x, y, width, height, _ = stats[label]
        people[index] = {"label": label, "bbox": (x, y, x + width, y + height), "center": (cx, cy)}
    assigned = {index: [] for index in range(16)}
    main_to_index = {person["label"]: index for index, person in people.items()}
    for label in range(1, count):
        if label in main_to_index:
            index = main_to_index[label]
        else:
            cx, cy = centers[label]

            def distance(index):
                person = people[index]
                x0, y0, x1, y1 = person["bbox"]
                dx, dy = max(x0 - cx, 0, cx - x1), max(y0 - cy, 0, cy - y1)
                pcx, pcy = person["center"]
                return (dx * dx + dy * dy, (cx - pcx) ** 2 + (cy - pcy) ** 2)

            index = min(range(16), key=distance)
        assigned[index].append(label)
    rgba = np.asarray(sheet)
    crops = []
    for index in range(16):
        mask = np.isin(labels, assigned[index]).astype(np.uint8)
        # 仅给该完整人物保留 1px 抗锯齿边，不把附近另一人物带入。
        mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1).astype(bool)
        ys, xs = np.where(mask)
        box = (int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1))
        x0, y0, x1, y1 = box
        sprite = rgba[y0:y1, x0:x1].copy()
        sprite[:, :, 3][~mask[y0:y1, x0:x1]] = 0
        crops.append((Image.fromarray(sprite), box))
    return crops


def split_sheet(sheet):
    """完整拆成 16 姿态；每页共同留白并保留每格名义坐标。"""
    boundaries, seams = safe_grid_boundaries(sheet)
    crops = []
    if boundaries:
        method = "proportional_grid_with_transparent_seams"
        for index in range(16):
            row, column = divmod(index, 4)
            box = (boundaries["x"][column], boundaries["y"][row],
                   boundaries["x"][column + 1], boundaries["y"][row + 1])
            crops.append((sheet.crop(box), box))
    else:
        method = "complete_person_connected_components"
        crops = component_crops(sheet)
    padding = 16
    for index, (_, box) in enumerate(crops):
        nominal = grid_box(*sheet.size, index)
        padding = max(padding, nominal[0] - box[0] + 2, nominal[1] - box[1] + 2,
                      box[2] - nominal[2] + 2, box[3] - nominal[3] + 2)
    common_size = ((sheet.width + 3) // 4 + padding * 2, (sheet.height + 3) // 4 + padding * 2)
    cells, records = [], []
    for index, (sprite, box) in enumerate(crops):
        nominal = grid_box(*sheet.size, index)
        offset = (box[0] - nominal[0] + padding, box[1] - nominal[1] + padding)
        cell = Image.new("RGBA", common_size, (0, 0, 0, 0))
        cell.paste(sprite, offset)
        mask = np.asarray(cell.getchannel("A")) >= ALPHA_THRESHOLD
        source_mask = np.asarray(sprite.getchannel("A")) >= ALPHA_THRESHOLD
        records.append({"cell": index + 1, "nominal_grid_box": list(nominal), "crop_box": list(box),
                        "common_plane_offset": list(offset), "alpha_bbox": list(alpha_bbox(cell)),
                        "alpha_at_source_page_edge": {
                            "top": int(source_mask[0].sum()) if box[1] == 0 else 0,
                            "bottom": int(source_mask[-1].sum()) if box[3] == sheet.height else 0,
                            "left": int(source_mask[:, 0].sum()) if box[0] == 0 else 0,
                            "right": int(source_mask[:, -1].sum()) if box[2] == sheet.width else 0},
                        "alpha_at_cell_edge": {"top": int(mask[0].sum()), "bottom": int(mask[-1].sum()),
                                               "left": int(mask[:, 0].sum()), "right": int(mask[:, -1].sum())}})
        cells.append(cell)
    return cells, {"split_method": method, "actual_boundaries": boundaries,
                   "seam_alpha_threshold": SEAM_ALPHA_THRESHOLD, "seam_search_radius": SEAM_RADIUS,
                   "seam_records": seams, "common_padding": padding,
                   "common_plane_size": list(common_size), "cells": records}


def alpha_bbox(image):
    alpha = np.asarray(image.getchannel("A"))
    ys, xs = np.where(alpha >= ALPHA_THRESHOLD)
    if not len(xs):
        raise ValueError("姿势格没有可见人物 alpha")
    return tuple(float(v) for v in (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))


def apply_transform(box, transform):
    scale, tx, ty = transform
    return (box[0] * scale + tx, box[1] * scale + ty,
            box[2] * scale + tx, box[3] * scale + ty)


def overlap_transform(reference_box, current_box):
    """同源重叠姿势确定整页的单一等比缩放和位移。"""
    rw, rh = reference_box[2] - reference_box[0], reference_box[3] - reference_box[1]
    cw, ch = current_box[2] - current_box[0], current_box[3] - current_box[1]
    scale = (rw * cw + rh * ch) / (cw * cw + ch * ch)
    rcx, rcy = (reference_box[0] + reference_box[2]) / 2, (reference_box[1] + reference_box[3]) / 2
    ccx, ccy = (current_box[0] + current_box[2]) / 2, (current_box[1] + current_box[3]) / 2
    return (scale, rcx - scale * ccx, rcy - scale * ccy)


def union_box(boxes):
    return (min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes))


def fit_transform(box, safe_box=SAFE_BOX):
    scale = min((safe_box[2] - safe_box[0]) / (box[2] - box[0]),
                (safe_box[3] - safe_box[1]) / (box[3] - box[1]))
    tx = (safe_box[0] + safe_box[2]) / 2 - scale * (box[0] + box[2]) / 2
    ty = safe_box[3] - scale * box[3]
    return scale, tx, ty


def combine_transforms(first, second):
    """先执行 first，再执行 second。"""
    a, x, y = first
    b, u, v = second
    return a * b, x * b + u, y * b + v


def transform_rgba(image, transform, size=SIZE):
    scale, tx, ty = transform
    inverse = (1 / scale, 0, -tx / scale, 0, 1 / scale, -ty / scale)
    return image.transform(size, Image.Transform.AFFINE, inverse,
                           resample=Image.Resampling.BICUBIC,
                           fillcolor=(0, 0, 0, 0))


def read_pages():
    paths = [GENERATED / ("poses-page-%02d.png" % number) for number in (1, 2, 3)]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("输入尚未就绪，未开始渲染: " + ", ".join(missing))
    pages, details = [], []
    for page_number, path in enumerate(paths, 1):
        sheet = Image.open(path).convert("RGBA")
        alpha = np.asarray(sheet.getchannel("A"))
        if float((alpha == 0).mean()) < 0.10:
            raise ValueError("输入没有足够的透明背景: " + str(path))
        cells, extraction = split_sheet(sheet)
        source_pixels = int((alpha >= ALPHA_THRESHOLD).sum())
        split_pixels = sum(int((np.asarray(cell.getchannel("A")) >= ALPHA_THRESHOLD).sum()) for cell in cells)
        if source_pixels != split_pixels:
            raise ValueError("拆帧有效人体像素数量不守恒: {} → {}".format(source_pixels, split_pixels))
        pages.append(cells)
        details.append({"page": page_number, "source": str(path),
                        "size": list(sheet.size), "source_effective_alpha_pixels": source_pixels,
                        "split_effective_alpha_pixels": split_pixels, **extraction})
    return pages, details


def prepare_keys(pages, details):
    transforms = [(1.0, 0.0, 0.0)]
    overlaps = []
    for page_index in (1, 2):
        reference = apply_transform(alpha_bbox(pages[page_index - 1][-1]), transforms[-1])
        current = alpha_bbox(pages[page_index][0])
        transform = overlap_transform(reference, current)
        transforms.append(transform)
        aligned = apply_transform(current, transform)
        overlaps.append({"between_pages": [page_index, page_index + 1],
                         "reference_bbox": list(reference), "aligned_bbox": list(aligned),
                         "bbox_residual_pixels": [round(a - b, 4) for a, b in zip(aligned, reference)]})
    all_keys = []
    for page_index, cells in enumerate(pages):
        for cell_index, cell in enumerate(cells):
            if page_index and cell_index == 0:
                continue
            all_keys.append((cell, transforms[page_index], page_index + 1, cell_index + 1))
    if len(all_keys) != 46:
        raise ValueError("关键姿态数量不是 46")
    bounds = union_box([apply_transform(alpha_bbox(cell), transform)
                        for cell, transform, _, _ in all_keys])
    global_transform = fit_transform(bounds)
    for detail, transform in zip(details, transforms):
        detail["page_transform"] = list(transform)
    return all_keys, global_transform, {"pages": details, "overlap_alignment": overlaps,
                                       "all_key_union_before_fit": list(bounds),
                                       "global_transform": list(global_transform)}


def static_page():
    if not FONT_PATH.is_file():
        raise FileNotFoundError("本地宋体不存在: " + str(FONT_PATH))
    page = Image.new("RGB", SIZE, PAPER)
    draw = ImageDraw.Draw(page)
    draw.rectangle((23, 23, 616, 776), outline=(132, 110, 78), width=2)
    draw.rectangle((29, 29, 610, 770), outline=(181, 155, 115), width=1)
    draw.text((320, 55), "八极拳", font=ImageFont.truetype(str(FONT_PATH), 49),
              fill=INK, anchor="mt")
    draw.text((320, 116), "撑拳 · 动作拆解样片", font=ImageFont.truetype(str(FONT_PATH), 23),
              fill=INK, anchor="mt")
    draw.line((194, 154, 446, 154), fill=(165, 138, 99), width=1)
    draw.line((89, 718, 551, 718), fill=(160, 136, 100), width=1)
    draw.text((320, 738), "古法手绘  ·  慢速观摩", font=ImageFont.truetype(str(FONT_PATH), 18),
              fill=(125, 104, 75), anchor="mt")
    return page


def write_contact_sheets():
    """保留动作全程和两次接页的视觉复核索引。"""
    qa = WORK / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(FONT_PATH), 15)
    frames_dir = OUTPUT / "逐帧PNG"
    timeline = [1, 14, 28, 42, 56, 70, 78, 81, 84, 87, 100, 118,
                136, 150, 158, 161, 164, 167, 180, 194, 202, 216, 230, 240]
    labels = {1: "开始", 42: "迈步", 78: "页一末", 81: "页一末", 84: "接页一→二",
              87: "页二初", 150: "迈步展开", 158: "页二末", 161: "页二末",
              164: "接页二→三", 167: "页三初", 180: "收拳", 194: "并步",
              202: "收势", 240: "结束"}
    sheet = Image.new("RGB", (6 * 180, 4 * 250), PAPER)
    draw = ImageDraw.Draw(sheet)
    for position, frame_number in enumerate(timeline):
        frame = Image.open(frames_dir / ("八极撑拳_%04d.png" % frame_number)).convert("RGB")
        frame = frame.resize((180, 225), Image.Resampling.LANCZOS)
        x, y = (position % 6) * 180, (position // 6) * 250
        sheet.paste(frame, (x, y))
        draw.text((x + 5, y + 228), "{:03d}  {}".format(frame_number, labels.get(frame_number, "动作过渡")),
                  font=font, fill=INK)
    sheet.save(qa / "全程24帧视觉索引.png")
    transitions = [78, 80, 82, 84, 86, 88, 156, 158, 160, 162, 164, 166]
    detail = Image.new("RGB", (6 * 250, 2 * 280), PAPER)
    draw = ImageDraw.Draw(detail)
    for position, frame_number in enumerate(transitions):
        frame = Image.open(frames_dir / ("八极撑拳_%04d.png" % frame_number)).convert("RGB")
        frame = frame.crop(DYNAMIC_BOX).resize((250, 250), Image.Resampling.LANCZOS)
        x, y = (position % 6) * 250, (position // 6) * 280
        detail.paste(frame, (x, y))
        draw.text((x + 7, y + 255), "{:03d}  页{}→{}".format(frame_number, 1 if position < 6 else 2,
                  2 if position < 6 else 3), font=font, fill=INK)
    detail.save(qa / "两次接页附近12帧细节索引.png")
    (qa / "视觉索引帧号.json").write_text(json.dumps({"timeline": timeline, "transitions": transitions},
        ensure_ascii=False, indent=2), encoding="utf-8")


def render(overwrite=False):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("需要本地 ffmpeg 和 ffprobe")
    pages, details = read_pages()
    all_keys, global_transform, manifest = prepare_keys(pages, details)
    key_work = WORK / "aligned_keys"
    raw_frames = WORK / "interpolated_frames"
    qa = WORK / "qa"
    output_frames = OUTPUT / "逐帧PNG"
    output_keys = OUTPUT / "关键姿态PNG"
    video_path = OUTPUT / "八极拳-撑拳-240帧慢速样片.mp4"
    output_paths = list(output_frames.glob("八极撑拳_*.png")) + list(output_keys.glob("关键姿态_*.png"))
    if (output_paths or video_path.exists()) and not overwrite:
        raise FileExistsError("交付文件已存在；仅确认重建时使用 --overwrite")
    for folder in (key_work, raw_frames, qa, output_frames, output_keys):
        folder.mkdir(parents=True, exist_ok=True)
    for folder, pattern in ((key_work, "key_*.png"), (raw_frames, "raw_*.png"),
                            (output_frames, "八极撑拳_*.png"), (output_keys, "关键姿态_*.png")):
        for path in folder.glob(pattern):
            path.unlink()
    page = static_page()
    page.save(qa / "固定书页背景.png")
    manifest["keyframes"] = []
    for index, (cell, transform, page_number, cell_number) in enumerate(all_keys, 1):
        final_transform = combine_transforms(transform, global_transform)
        foreground = transform_rgba(cell, final_transform)
        flat = Image.new("RGBA", SIZE, PAPER + (255,))
        flat.alpha_composite(foreground)
        flat_rgb = flat.convert("RGB")
        flat_rgb.save(key_work / ("key_%03d.png" % index))
        decorated = page.copy()
        decorated.paste(flat_rgb.crop(DYNAMIC_BOX), DYNAMIC_BOX)
        decorated.save(output_keys / ("关键姿态_%03d.png" % index))
        final_bbox = alpha_bbox(foreground)
        manifest["keyframes"].append({"index": index, "page": page_number, "cell": cell_number,
                                      "transform": list(final_transform), "output_alpha_bbox": list(final_bbox)})
    filter_graph = (
        "tpad=stop_mode=clone:stop_duration=0.666667,"
        "minterpolate=fps=24:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1,"
        "trim=end_frame=240,setpts=N/(24*TB)"
    )
    print("已保存 46 张统一比例关键姿态；开始运动补偿插帧。", flush=True)
    run(["ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-framerate", KEY_RATE,
         "-start_number", "1", "-i", str(key_work / "key_%03d.png"), "-vf", filter_graph,
         "-frames:v", str(FRAME_COUNT), "-fps_mode", "passthrough",
         str(raw_frames / "raw_%04d.png")], qa / "ffmpeg-插帧.log")
    interpolated = sorted(raw_frames.glob("raw_*.png"))
    if len(interpolated) != FRAME_COUNT:
        raise RuntimeError("插帧输出数量为 {}，预期 240".format(len(interpolated)))
    # 文字、边框和地面在插帧之后统一添加，保证每帧背景坐标和像素完全相同。
    for index, path in enumerate(interpolated, 1):
        body = np.array(Image.open(path).convert("RGB"), dtype=np.int16)
        # RGB↔YUV 的整数舍入会使纯色纸底偏移；统一抵消该固定色差。
        offset = np.array(PAPER, dtype=np.int16) - body[0, 0]
        body = Image.fromarray(np.clip(body + offset, 0, 255).astype(np.uint8))
        frame = page.copy()
        frame.paste(body.crop(DYNAMIC_BOX), DYNAMIC_BOX)
        frame.save(output_frames / ("八极撑拳_%04d.png" % index))
    print("已保存 240 张逐帧 PNG；从同一 PNG 序列编码 MP4。", flush=True)
    run(["ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-framerate", str(FRAME_RATE),
         "-start_number", "1", "-i", str(output_frames / "八极撑拳_%04d.png"),
         "-frames:v", str(FRAME_COUNT), "-an", "-c:v", "libx264", "-preset", "medium",
         "-crf", "15", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
         "-movflags", "+faststart", str(video_path)], qa / "ffmpeg-编码.log")
    manifest.update({"canvas_size": list(SIZE), "key_rate": KEY_RATE, "frame_rate": FRAME_RATE,
                     "frame_count": FRAME_COUNT, "duration_seconds": FRAME_COUNT / FRAME_RATE,
                     "interpolation_filter": filter_graph, "dynamic_box": list(DYNAMIC_BOX),
                     "safe_box": list(SAFE_BOX), "font": str(FONT_PATH),
                     "video_path": str(video_path), "output_frames": str(output_frames),
                     "output_keys": str(output_keys), "static_background": str(qa / "固定书页背景.png")})
    (qa / "render-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_contact_sheets()
    print(json.dumps({"video": str(video_path), "frames": len(interpolated),
                      "keyframes": len(all_keys), "duration_seconds": 10}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite", action="store_true", help="重建此样片中同名的已生成文件")
    args = parser.parse_args()
    render(overwrite=args.overwrite)
