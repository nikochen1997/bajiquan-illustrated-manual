"""已在240帧样片验证的完整人物拆帧及等比几何函数。"""
import numpy as np
from PIL import Image
ALPHA_THRESHOLD = 24
SEAM_ALPHA_THRESHOLD = 8
SEAM_RADIUS = 8
SAFE_BOX = (62, 180, 578, 698)
SIZE = (640, 800)

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
