#!/usr/bin/env python3
"""仅生成 work 内视觉候选，不覆盖用户交付。DIS 使用单源轮廓，避免两张图叠影。"""

from pathlib import Path
import json
import subprocess
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

WORK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORK))
import build_animation as build

QA = WORK / "qa"


def candidate_index(folder, pattern, destination, decorated=False):
    numbers = [14, 28, 42, 56, 82, 84, 136, 164, 180, 194, 202, 240]
    sheet = Image.new("RGB", (6 * 220, 2 * 250), build.PAPER)
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(str(build.FONT_PATH), 16)
    for index, number in enumerate(numbers):
        path = folder / (pattern % number)
        if not path.exists():
            continue
        frame = Image.open(path).convert("RGB")
        frame = frame.crop(build.DYNAMIC_BOX).resize((220, 220), Image.Resampling.LANCZOS)
        x, y = (index % 6) * 220, (index // 6) * 250
        sheet.paste(frame, (x, y))
        draw.text((x + 7, y + 226), "第%03d帧" % number, font=font, fill=build.INK)
    sheet.save(destination)


def inverse_warp(image, flow, amount, grid_x, grid_y):
    map_x = grid_x - amount * flow[:, :, 0]
    map_y = grid_y - amount * flow[:, :, 1]
    for _ in range(2):
        current = cv2.remap(flow, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        map_x = grid_x - amount * current[:, :, 0]
        map_y = grid_y - amount * current[:, :, 1]
    return cv2.remap(image, map_x, map_y, cv2.INTER_CUBIC,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=build.PAPER)


def render_dis():
    folder = QA / "DIS-single-outline-end-reuse"
    folder.mkdir(parents=True, exist_ok=True)
    keys = [np.asarray(Image.open(WORK / "aligned_keys" / ("key_%03d.png" % number)).convert("RGB"))
            for number in range(1, 47)]
    # 原参考第38关键态已完成并脚收拳，末段合法复用相同站姿以比较比例漂移。
    keys[37:] = [keys[0].copy() for _ in range(len(keys) - 37)]
    x0, y0, x1, y1 = build.DYNAMIC_BOX
    bodies = [np.ascontiguousarray(key[y0:y1, x0:x1]) for key in keys]
    gray = [cv2.cvtColor(body, cv2.COLOR_RGB2GRAY) for body in bodies]
    height, width = gray[0].shape
    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    dis.setUseSpatialPropagation(True)
    dis.setFinestScale(0)
    dis.setGradientDescentIterations(35)
    dis.setVariationalRefinementIterations(7)
    page = build.static_page()
    cached_pair, forward, backward = None, None, None
    for frame_index in range(240):
        # n / 24 * 4.5 = n * 3 / 16，整数运算避免时间舍入漂移。
        numerator = frame_index * 3
        left, remainder = divmod(numerator, 16)
        right = min(left + 1, 45)
        progress = remainder / 16
        if remainder == 0 or np.array_equal(bodies[left], bodies[right]):
            body = bodies[left]
        else:
            if cached_pair != left:
                forward = dis.calc(gray[left], gray[right], None)
                backward = dis.calc(gray[right], gray[left], None)
                cached_pair = left
            # 每个中间帧只取一个完整源图的变形结果，不叠加不同位置的两只手。
            if progress < 0.5:
                body = inverse_warp(bodies[left], forward, progress, grid_x, grid_y)
            else:
                body = inverse_warp(bodies[right], backward, 1 - progress, grid_x, grid_y)
        frame = page.copy()
        frame.paste(Image.fromarray(body), (x0, y0))
        frame.save(folder / ("candidate_%04d.png" % (frame_index + 1)))
    video = folder / "DIS单轮廓-末段站姿复用-候选.mp4"
    result = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-framerate", "24",
        "-i", str(folder / "candidate_%04d.png"), "-frames:v", "240", "-an", "-c:v", "libx264",
        "-crf", "15", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video)], capture_output=True, text=True)
    (folder / "encoding.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(result.stderr)
    (folder / "方法说明.json").write_text(json.dumps({"interpolation": "OpenCV DIS medium bidirectional; single source inverse warp per frame",
        "keyframes_reused": list(range(38, 47)), "reused_from": 1,
        "known_tradeoff": "避免透明双影，但遮挡处可能变形，两个单源变形结果的中点切换可能出现顿跳；未获视觉验收"},
        ensure_ascii=False, indent=2))
    candidate_index(folder, "candidate_%04d.png", folder / "12帧问题部位索引.png", decorated=True)
    print(video, flush=True)


if __name__ == "__main__":
    render_dis()
