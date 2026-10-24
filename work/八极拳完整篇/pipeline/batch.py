#!/usr/bin/env python3
"""八极拳批量书页动画：显式清单、准备、RIFE命令计划、成片、校验、拼接、分包。"""

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import sprites

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SIZE = (640, 800)
FPS = 24
SAFE = (62, 180, 578, 698)
ROI = (46, 164, 594, 714)
PAPER = (241, 230, 208)
FONT = Path("/System/Library/Fonts/Supplemental/Songti.ttc")
KINDS = {"routine", "elbow", "kao"}


def json_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(args, log=None):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True)
    if log:
        Path(log).write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("命令失败: {}\n{}".format(args[0], result.stderr[-4000:]))
    return result.stdout


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + " 必须是有限数字")
    return float(value)


def integer(value, label, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(label + " 必须是≥{}的整数".format(minimum))
    return value


def close(a, b):
    return abs(a - b) <= 1e-5


def source_interval(value, label):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(label + " 必须是[start,end]")
    start, end = [finite(v, label) for v in value]
    if start < 0 or end <= start:
        raise ValueError(label + " 必须满足0≤start<end")
    return start, end


def resolved(config, path):
    path = Path(path)
    return path if path.is_absolute() else Path(config["_base"]) / path


def validate(config, ready=True):
    """结构通过不等于来源真实；ready模式还要求明确锁定来源及全部素材存在。"""
    if config.get("schema_version") != 1:
        raise ValueError("仅支持schema_version=1")
    if config.get("canvas", {"width": 640, "height": 800, "fps": 24}) != {"width": 640, "height": 800, "fps": 24}:
        raise ValueError("首版画幅固定640×800、24fps")
    warnings = []
    sources = config.get("sources", {})
    main_source = config.get("main_source")
    if main_source not in sources:
        raise ValueError("main_source必须指向一个来源")
    for source_id, source in sources.items():
        if not source.get("teacher") or not source.get("version") or not source.get("url"):
            raise ValueError("来源必须包含teacher/version/url: " + source_id)
        if ready and source.get("locked") is not True:
            raise ValueError("来源尚未锁定，拒绝制作: " + source_id)
    segments = config.get("segments", [])
    if not segments:
        raise ValueError("没有segments")
    by_id = {}
    for segment in segments:
        sid = segment.get("id", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", sid) or sid in by_id:
            raise ValueError("segment id须唯一且只含小写字母、数字、短横线")
        by_id[sid] = segment
        if segment.get("kind") not in KINDS or not segment.get("title"):
            raise ValueError(sid + " 缺少title或kind不受支持")
        if segment.get("source_id") not in sources:
            raise ValueError(sid + " 引用了未知来源")
        if segment["kind"] == "routine" and segment["source_id"] != main_source:
            raise ValueError("主篇不允许混用来源、老师或版本")
        start, end = source_interval(segment.get("source_range"), sid + ".source_range")
        pages = segment.get("pages", [])
        if not pages:
            raise ValueError(sid + " 没有sprite页")
        for page_index, page in enumerate(pages, 1):
            if not page.get("path"):
                raise ValueError(sid + " 缺少sprite路径")
            if ready and not resolved(config, page["path"]).is_file():
                raise FileNotFoundError(str(resolved(config, page["path"])))
            for field in ("x_shift", "y_shift"):
                finite(page.get(field, 0), sid + "." + field)
            if finite(page.get("art_scale", 1), sid + ".art_scale") <= 0:
                raise ValueError(sid + ".art_scale必须为正数")
            if "head_match" in page:
                match = page["head_match"]
                if "x_shift" in page or match.get("head_clear") is not True:
                    raise ValueError("head_match必须明确头顶无遮挡，且不能同时设置x_shift")
                if not 1 <= integer(match.get("page"), "head_match.page") < page_index:
                    raise ValueError("head_match只能引用本段较早sprite页")
                for field in ("cell", "current_cell"):
                    if not 1 <= integer(match.get(field), field) <= 16:
                        raise ValueError("head_match格号范围1..16")
        keys = segment.get("keyframes", [])
        if len(keys) < 2:
            raise ValueError(sid + " 至少需要2个有时间来源的关键态")
        integer(segment.get("frames"), sid + ".frames", len(keys))
        previous = None
        for key_index, key in enumerate(keys, 1):
            page = integer(key.get("page"), sid + ".page")
            cell = integer(key.get("cell"), sid + ".cell")
            if page > len(pages) or cell > 16:
                raise ValueError(sid + " 关键态页/格越界")
            timestamp = finite(key.get("source_seconds"), sid + ".source_seconds")
            if timestamp < start - 1e-5 or timestamp > end + 1e-5 or (previous is not None and timestamp <= previous):
                raise ValueError(sid + " 关键态来源时间须严格递增且在本段范围内")
            previous = timestamp
            contact = key.get("contact", {"state": "unknown"})
            state = contact.get("state")
            if state not in {"support", "airborne", "unknown"}:
                raise ValueError(sid + " contact.state不受支持")
            if contact.get("foot", "unknown") not in {"left", "right", "both", "unknown"}:
                raise ValueError("foot须是left/right/both/unknown")
            if "lift" in contact and finite(contact["lift"], "contact.lift") < 0:
                raise ValueError("离地高度lift不能为负")
            if "support_y" in contact:
                finite(contact["support_y"], "contact.support_y")
            if state == "unknown" or (state == "airborne" and "lift" not in contact):
                warnings.append("{}关键态{}：{}，保留源竖坐标，不推断落地".format(sid, key_index, state))
            if state == "unknown" and ("lift" in contact or "support_y" in contact):
                raise ValueError("unknown接触状态不能同时指定强制地面参数")
            if "reuse_key" in key and not 1 <= integer(key["reuse_key"], "reuse_key") < key_index:
                raise ValueError("reuse_key只能指向本段较早关键态，且仍须有真实来源时间")
        if not close(keys[0]["source_seconds"], start) or not close(keys[-1]["source_seconds"], end):
            raise ValueError(sid + " 首尾关键态未覆盖本段完整来源范围")
    main = config.get("routine_order", [])
    supplements = config.get("supplements_order", [])
    if not main or len(set(main + supplements)) != len(main + supplements) or set(main + supplements) != set(by_id):
        raise ValueError("两张顺序表必须恰好包含全部segments各一次")
    if any(by_id[sid]["kind"] != "routine" for sid in main) or any(by_id[sid]["kind"] == "routine" for sid in supplements):
        raise ValueError("肘法/靠法必须与原主套路分列")
    if [sid for sid in main if by_id[sid].get("starts_routine")] != [main[0]]:
        raise ValueError("主套路须且仅首段标记完整起势")
    if [sid for sid in main if by_id[sid].get("ends_routine")] != [main[-1]]:
        raise ValueError("主套路须且仅末段标记完整收势")
    entire = source_interval(sources[main_source].get("complete_range"), "main_source.complete_range")
    if not close(by_id[main[0]]["source_range"][0], entire[0]) or not close(by_id[main[-1]]["source_range"][1], entire[1]):
        raise ValueError("主篇未覆盖锁定来源的完整起收时间")
    for left_id, right_id in zip(main[:-1], main[1:]):
        left, right = by_id[left_id], by_id[right_id]
        if not close(left["source_range"][1], right["source_range"][0]):
            raise ValueError("主套路来源断段/重叠: " + left_id + " → " + right_id)
        if right.get("join") != "shared_boundary":
            raise ValueError("主套路接段必须共享同源同时间姿态，拒绝跨段硬混姿势")
    limit = integer(config.get("zip_part_bytes", 350 * 1024 * 1024), "zip_part_bytes", 1024)
    if limit > 900 * 1024 * 1024:
        raise ValueError("分包上限不得超过900MiB")
    return warnings


def load(path, ready=True):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    config["_base"], config["_manifest"] = str(path.parent), str(path)
    config["_warnings"] = validate(config, ready=ready)
    return config


def locations(config):
    state = resolved(config, config.get("state_dir", str(HERE / "build" / Path(config["_manifest"]).stem)))
    output = resolved(config, config.get("output_dir", str(ROOT / "outputs" / "八极拳完整篇")))
    return state, output


def fingerprint(config):
    public = {key: value for key, value in config.items() if not key.startswith("_")}
    return hashlib.sha256(json.dumps(public, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_prepared(config):
    state, _ = locations(config)
    saved = json.loads((state / "prepared.json").read_text())
    if saved["manifest_sha256"] != fingerprint(config):
        raise ValueError("清单已变更；使用新的state_dir重新prepare，不能复用旧RIFE结果")
    for path, expected in saved["source_sha256"].items():
        if file_hash(path) != expected:
            raise ValueError("源sprite已变更，拒绝复用旧结果: " + path)
    return saved


def head_x(image):
    alpha = np.asarray(image.getchannel("A"), dtype=np.float64)
    _, top, _, bottom = sprites.alpha_bbox(image)
    band = alpha[int(top):int(math.ceil(top + (bottom - top) * 0.15))]
    weights = np.where(band >= sprites.ALPHA_THRESHOLD, band, 0)
    return float((weights * np.arange(image.width)[None, :]).sum() / weights.sum())


def ground_shift(bbox, scale, contact, baseline, fallback=0):
    """未知/无离地量的腾空保留源坐标，绝不默认拉地。"""
    state = contact.get("state", "unknown")
    if state == "support":
        return baseline - contact.get("support_y", bbox[3]) * scale, "support-foot"
    if state == "airborne" and "lift" in contact:
        return baseline - bbox[3] * scale - contact["lift"] * scale, "explicit-airborne-lift"
    return fallback, "preserved-source-y-unknown"


def title_font(title, size):
    font = ImageFont.truetype(str(FONT), size)
    if any(not char.isspace() and font.getmask(char).getbbox() is None for char in title):
        font = ImageFont.truetype(str(FONT), size, index=1)
    if any(not char.isspace() and font.getmask(char).getbbox() is None for char in title):
        raise ValueError('标题字体含空白字形：' + title)
    return font


def book_page(title):
    if not FONT.is_file():
        raise FileNotFoundError(str(FONT))
    page = Image.new("RGB", SIZE, PAPER)
    draw = ImageDraw.Draw(page)
    draw.rectangle((23, 23, 616, 776), outline=(132, 110, 78), width=2)
    draw.rectangle((29, 29, 610, 770), outline=(181, 155, 115))
    draw.text((320, 55), "八极拳", font=ImageFont.truetype(str(FONT), 49), fill=(74, 61, 46), anchor="mt")
    font_size = 23
    while font_size > 14 and title_font(title, font_size).getlength(title) > 520:
        font_size -= 1
    draw.text((320, 116), title, font=title_font(title, font_size), fill=(74, 61, 46), anchor="mt")
    draw.line((194, 154, 446, 154), fill=(165, 138, 99))
    draw.line((89, 718, 551, 718), fill=(160, 136, 100))
    draw.text((320, 738), "古风手绘  ·  慢速观摩", font=ImageFont.truetype(str(FONT), 18), fill=(125, 104, 75), anchor="mt")
    return page


def prepare(config):
    warnings = list(validate(config, ready=True))
    state, _ = locations(config)
    if (state / "prepared.json").exists():
        raise FileExistsError("此state_dir已有准备结果；换新的state_dir以避免混用旧RIFE输出")
    prepared = []
    all_boxes = []
    canonical_unit = None
    source_hashes = {}
    for segment in config["segments"]:
        page_cells, page_meta, page_transforms = [], [], []
        for page_number, page in enumerate(segment["pages"], 1):
            image = Image.open(resolved(config, page["path"])).convert("RGBA")
            source_hashes[str(resolved(config, page["path"]))] = file_hash(resolved(config, page["path"]))
            cells, extraction = sprites.split_sheet(image)
            used_cells = {key["cell"] for key in segment["keyframes"] if key["page"] == page_number}
            unused_cut_cells = []
            for cell in extraction["cells"]:
                if any(cell["alpha_at_source_page_edge"].values()):
                    if cell["cell"] in used_cells:
                        raise ValueError("源sprite外边缘真实切到人体，须先修复: {} 格{}".format(page["path"], cell["cell"]))
                    unused_cut_cells.append(cell["cell"])
            if unused_cut_cells:
                warnings.append("{}页{}未用格{}触源图外缘，未进入成片；保留原图并记录".format(
                    segment["id"], page_number, unused_cut_cells))
            before = int((np.asarray(image.getchannel("A")) >= sprites.ALPHA_THRESHOLD).sum())
            after = sum(int((np.asarray(cell.getchannel("A")) >= sprites.ALPHA_THRESHOLD).sum()) for cell in cells)
            if before != after:
                raise ValueError("拆帧人体像素不守恒: " + page["path"])
            if canonical_unit is None:
                canonical_unit = image.height / 4
            scale = canonical_unit / (image.height / 4) * page.get("art_scale", 1)
            dx = page.get("x_shift", 0) + 16 - extraction["common_padding"] * scale
            if "head_match" in page:
                match = page["head_match"]
                ref_index = match["page"] - 1
                ref_scale, ref_dx, _ = page_transforms[ref_index]
                dx = head_x(page_cells[ref_index][match["cell"] - 1]) * ref_scale + ref_dx - head_x(cells[match["current_cell"] - 1]) * scale
            page_cells.append(cells)
            page_transforms.append((scale, dx, page.get("y_shift", 0)))
            page_meta.append({"page": page_number, "path": str(resolved(config, page["path"])),
                              "sha256": source_hashes[str(resolved(config, page["path"]))],
                              "source_size": list(image.size), "scale": scale, "x_shift": dx,
                              "source_effective_pixels": before, "split_effective_pixels": after, **extraction})
        keys = []
        baseline = canonical_unit + 16
        for index, key in enumerate(segment["keyframes"], 1):
            cell = page_cells[key["page"] - 1][key["cell"] - 1]
            scale, dx, fallback_y = page_transforms[key["page"] - 1]
            bbox = sprites.alpha_bbox(cell)
            dy, registration = ground_shift(bbox, scale, key.get("contact", {"state": "unknown"}), baseline, fallback_y)
            transform = (scale, dx, dy)
            if "reuse_key" in key:
                reused = keys[key["reuse_key"] - 1]
                cell, transform = reused["cell"], reused["transform"]
                registration = "explicit-source-hold-reuse"
            record = {"index": index, "source": key, "transform": list(transform), "registration": registration,
                      "bbox": list(sprites.apply_transform(sprites.alpha_bbox(cell), transform))}
            keys.append({"cell": cell, "transform": transform, "record": record})
            all_boxes.append(record["bbox"])
        prepared.append({"segment": segment, "keys": keys, "pages": page_meta})
    common_fit = sprites.fit_transform(sprites.union_box(all_boxes), SAFE)
    by_id = {item["segment"]["id"]: item for item in prepared}
    previous = None
    ordered = config["routine_order"] + config.get("supplements_order", [])
    for sid in ordered:
        item = by_id[sid]
        segment = item["segment"]
        folder = state / sid
        for child in ("input", "aligned", "roi_input", "roi_output"):
            (folder / child).mkdir(parents=True, exist_ok=True)
            if child == "roi_output" and list((folder / child).glob("*.png")):
                raise FileExistsError("已有RIFE输出；须使用新的state_dir")
            if child != "roi_output":
                for old in (folder / child).glob("*.png"):
                    old.unlink()
        static = book_page(segment["title"])
        static.save(folder / "static-background.png")
        records = []
        for index, key in enumerate(item["keys"], 1):
            transform = sprites.combine_transforms(key["transform"], common_fit)
            foreground = sprites.transform_rgba(key["cell"], transform, SIZE)
            record = dict(key["record"])
            if record["registration"] in {"support-foot", "explicit-airborne-lift"}:
                desired_bottom = round(key["record"]["bbox"][3] * common_fit[0] + common_fit[2])
                correction = int(desired_bottom - sprites.alpha_bbox(foreground)[3])
                if correction:
                    shifted = Image.new("RGBA", SIZE, (0, 0, 0, 0))
                    shifted.paste(foreground, (0, correction))
                    foreground = shifted
                record["pixel_y_rounding_correction"] = correction
            if index == 1 and sid in config["routine_order"][1:]:
                # 验证器已确认这是同来源同一时刻，不在不同姿势之间发明过渡。
                foreground = Image.open(state / previous / "aligned" / ("%08d.png" % len(by_id[previous]["keys"]))).convert("RGBA")
                record["shared_boundary_from"] = previous
            bbox = sprites.alpha_bbox(foreground)
            if bbox[0] < ROI[0] or bbox[1] < ROI[1] or bbox[2] > ROI[2] or bbox[3] > ROI[3]:
                raise ValueError("人物超出ROI")
            clean = Image.new("RGBA", SIZE, (0, 0, 0, 0))
            clean.paste(foreground.crop(ROI), ROI)
            foreground = clean
            foreground.save(folder / "aligned" / ("%08d.png" % index))
            rgba = static.convert("RGBA")
            rgba.alpha_composite(foreground)
            rgb = rgba.convert("RGB")
            rgb.save(folder / "input" / ("%08d.png" % index))
            rgb.crop(ROI).save(folder / "roi_input" / ("%08d.png" % index))
            record.update({"output_bbox": list(bbox), "common_fit": list(common_fit),
                           "combined_transform": list(transform)})
            records.append(record)
        json_write(folder / "registration.json", {"segment": segment, "pages": item["pages"], "keys": records,
                   "common_fit": list(common_fit), "nominal_unit": canonical_unit, "roi": list(ROI)})
        if sid in config["routine_order"]:
            previous = sid
    json_write(state / "prepared.json", {"manifest": config["_manifest"], "warnings": warnings,
               "manifest_sha256": fingerprint(config), "source_sha256": source_hashes,
               "common_fit": list(common_fit), "segments": ordered, "canvas": list(SIZE), "fps": FPS})
    return state


def rife_plan(config, binary, model):
    validate(config, ready=True)
    state, _ = locations(config)
    assert_prepared(config)
    commands = []
    by_id = {item["id"]: item for item in config["segments"]}
    for sid in config["routine_order"] + config.get("supplements_order", []):
        segment = by_id[sid]
        folder = state / sid
        commands.append({"segment": sid, "expected_frames": segment["frames"],
                         "argv": [str(binary), "-i", str(folder / "roi_input"), "-o", str(folder / "roi_output"),
                                  "-m", str(model), "-n", str(segment["frames"]), "-f", "%08d.png"]})
    result = {"commands": commands, "note": "只生成计划，不执行GPU程序。主线程按本机审批/沙箱要求运行argv；不调用网络。"}
    json_write(state / "rife-plan.json", result)
    return result


def encode_pngs(pattern, destination, frames):
    run(["ffmpeg", "-y", "-v", "error", "-framerate", FPS, "-start_number", 1, "-i", pattern,
         "-frames:v", frames, "-an", "-c:v", "libx264", "-preset", "medium", "-crf", 15,
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", destination])


def mapping_rows(segment, source=None):
    keys = segment["keyframes"]
    source = source or {}
    for index in range(segment["frames"]):
        position = index * (len(keys) - 1) / (segment["frames"] - 1)
        left = int(math.floor(position))
        right = min(left + 1, len(keys) - 1)
        weight = position - left
        timestamp = keys[left]["source_seconds"] * (1 - weight) + keys[right]["source_seconds"] * weight
        yield {"local_frame": index + 1, "source_id": segment["source_id"], "source_url": source.get("url", ""),
               "teacher": source.get("teacher", ""), "version": source.get("version", ""), "source_seconds": round(timestamp, 7),
               "left_key": left + 1, "right_key": right + 1, "weight": round(weight, 7),
               "left_contact": keys[left].get("contact", {"state": "unknown"}).get("state", "unknown"),
               "right_contact": keys[right].get("contact", {"state": "unknown"}).get("state", "unknown")}


def write_csv(path, rows):
    rows = list(rows)
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def finalize(config, only_segments=None):
    validate(config, ready=True)
    state, output = locations(config)
    assert_prepared(config)
    if only_segments is not None and (not only_segments or not set(only_segments) <= {s['id'] for s in config['segments']}):
        raise ValueError('指定导出的分招不存在')
    for segment in config["segments"]:
        sid = segment["id"]
        if only_segments is not None and sid not in only_segments:
            continue
        source = state / sid
        paths = sorted((source / "roi_output").glob("*.png"))
        if [path.name for path in paths] != ["%08d.png" % i for i in range(1, segment["frames"] + 1)]:
            raise ValueError(sid + " RIFE帧数/编号未完整，拒绝输出")
        destination = output / "分招" / sid
        for child in ("逐帧PNG", "关键姿态PNG"):
            (destination / child).mkdir(parents=True, exist_ok=True)
            for old in (destination / child).glob("*.png"):
                old.unlink()
        static = Image.open(source / "static-background.png").convert("RGB")
        for index, path in enumerate(paths, 1):
            image = Image.open(path).convert("RGB")
            if image.size != (ROI[2] - ROI[0], ROI[3] - ROI[1]):
                raise ValueError("RIFE ROI尺寸错误: " + str(path))
            raw = np.asarray(image, dtype=np.int16)
            paper = np.array(PAPER, dtype=np.int16)
            raw = np.clip(raw + paper - raw[0, 0], 0, 255)
            raw[np.max(np.abs(raw - paper), axis=2) <= 2] = paper
            frame = static.copy()
            frame.paste(Image.fromarray(raw.astype(np.uint8)), ROI)
            frame.save(destination / "逐帧PNG" / ("%08d.png" % index))
        for index in range(1, len(segment["keyframes"]) + 1):
            shutil.copyfile(source / "input" / ("%08d.png" % index), destination / "关键姿态PNG" / ("%08d.png" % index))
        video = destination / (sid + ".mp4")
        encode_pngs(destination / "逐帧PNG" / "%08d.png", video, segment["frames"])
        write_csv(destination / "来源逐帧映射.csv", mapping_rows(segment, config["sources"][segment["source_id"]]))
        json_write(destination / "动作与来源.json", {"segment": segment, "source": config["sources"][segment["source_id"]],
                   "warnings": config["_warnings"], "registration": json.loads((source / "registration.json").read_text()),
                   "note": "AI重绘及神经补间；接地未知处保留源坐标；不等于拳师校订的完整教学。"})
    return output


def video_check(video, pngs, expected_frames):
    metadata = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height,avg_frame_rate,nb_frames,duration", "-of", "json", video]))["streams"][0]
    if (metadata["width"], metadata["height"]) != SIZE or metadata["avg_frame_rate"] != "24/1":
        raise AssertionError("视频画幅/帧率不符")
    if int(metadata["nb_frames"]) != expected_frames or abs(float(metadata["duration"]) - expected_frames / FPS) > 0.001:
        raise AssertionError("视频时长/帧数不符")
    indexes = {0, expected_frames // 2, expected_frames - 1}
    command = ["ffmpeg", "-v", "error", "-xerror", "-i", str(video), "-map", "0:v:0", "-an", "-c:v", "rawvideo",
               "-pix_fmt", "rgb24", "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    decoded, samples = 0, []
    try:
        while True:
            data = process.stdout.read(SIZE[0] * SIZE[1] * 3)
            if not data:
                break
            if len(data) != SIZE[0] * SIZE[1] * 3:
                raise AssertionError("视频解码得到不完整帧")
            if decoded in indexes:
                actual = np.frombuffer(data, np.uint8).reshape((SIZE[1], SIZE[0], 3))
                expected = np.asarray(Image.open(pngs[decoded]).convert("RGB"))
                delta = actual.astype(np.float32) - expected.astype(np.float32)
                mae, mse = float(np.abs(delta).mean()), float(np.square(delta).mean())
                psnr = float(10 * np.log10(255 ** 2 / mse)) if mse else 99.0
                if mae > 2.5 or psnr < 32:
                    raise AssertionError("视频与对应PNG不符")
                samples.append({"frame": decoded + 1, "mae": mae, "psnr": psnr})
            decoded += 1
        error = process.stderr.read().decode("utf-8", errors="replace")
        if process.wait() or decoded != expected_frames:
            raise AssertionError("视频全解码失败: " + error)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()
    return {"metadata": metadata, "decoded": decoded, "samples": samples}


def verify(config):
    validate(config, ready=True)
    state, output = locations(config)
    assert_prepared(config)
    results = []
    for segment in config["segments"]:
        folder = output / "分招" / segment["id"]
        paths = sorted((folder / "逐帧PNG").glob("*.png"))
        if [path.name for path in paths] != ["%08d.png" % i for i in range(1, segment["frames"] + 1)]:
            raise AssertionError("交付PNG断号/缺图")
        static = np.asarray(Image.open(state / segment["id"] / "static-background.png").convert("RGB"))
        fixed = np.ones((SIZE[1], SIZE[0]), bool)
        fixed[ROI[1]:ROI[3], ROI[0]:ROI[2]] = False
        hashes = []
        for path in paths:
            with Image.open(path) as image:
                image.load()
                if image.size != SIZE or image.mode != "RGB":
                    raise AssertionError("PNG画幅/模式错误")
                pixels = np.asarray(image)
                if not np.array_equal(pixels[fixed], static[fixed]):
                    raise AssertionError("静态背景漂移")
                if pixels.mean() < 100 or (pixels[ROI[1]:ROI[3], ROI[0]:ROI[2]].mean(axis=2) < 130).sum() < 50:
                    raise AssertionError("黑屏或缺人物")
                hashes.append(hashlib.sha256(pixels.tobytes()).hexdigest())
        rows = list(csv.DictReader((folder / "来源逐帧映射.csv").open(encoding="utf-8-sig")))
        if len(rows) != segment["frames"] or not close(float(rows[0]["source_seconds"]), segment["source_range"][0]) or not close(float(rows[-1]["source_seconds"]), segment["source_range"][1]):
            raise AssertionError("来源映射首尾或数量错误")
        results.append({"segment": segment["id"], "png_count": len(paths), "pixel_unique": len(set(hashes)),
                        "adjacent_same": sum(a == b for a, b in zip(hashes[:-1], hashes[1:])),
                        "video": video_check(folder / (segment["id"] + ".mp4"), paths, segment["frames"])})
    report = {"technical": "passed", "visual": "待逐段视觉复核，不作为拳法教学校订通过", "segments": results,
              "warnings": config["_warnings"]}
    json_write(output / "技术核对记录.json", report)
    return report


def assemble(config):
    validate(config, ready=True)
    _, output = locations(config)
    assert_prepared(config)
    by_id = {item["id"]: item for item in config["segments"]}
    global_dir = output / "完整编号逐帧PNG"
    global_dir.mkdir(parents=True, exist_ok=True)
    for old in global_dir.glob("八极拳_*.png"):
        old.unlink()
    rows, videos, expected_count = [], [], 0
    for sid in config["routine_order"]:
        segment = by_id[sid]
        folder = output / "分招" / sid
        video = folder / (sid + ".mp4")
        if not video.is_file():
            raise FileNotFoundError(str(video))
        videos.append(video.resolve())
        for row in mapping_rows(segment, config["sources"][segment["source_id"]]):
            expected_count += 1
            source = folder / "逐帧PNG" / ("%08d.png" % row["local_frame"])
            target = global_dir / ("八极拳_%06d.png" % expected_count)
            if target.exists():
                target.unlink()
            try:
                os.link(source, target)
            except OSError:
                shutil.copyfile(source, target)
            rows.append({"global_frame": expected_count, "video_seconds": round((expected_count - 1) / FPS, 7),
                         "segment": sid, **row})
    # 只拼主套路；肘/靠补充仍独立分招，不改动原套路次序。
    concat = output / "concat-main.txt"
    concat.write_text("\n".join("file '" + str(path).replace("'", "'\\''") + "'" for path in videos) + "\n")
    full_video = output / "八极拳-完整套路慢放.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", 0, "-i", concat,
         "-c", "copy", "-movflags", "+faststart", full_video])
    write_csv(output / "完整来源时间映射.csv", rows)
    global_pngs = [global_dir / ("八极拳_%06d.png" % i) for i in range(1, expected_count + 1)]
    result = video_check(full_video, global_pngs, expected_count)
    json_write(output / "完整篇核对记录.json", {"routine_order": config["routine_order"], "supplements_separate": config.get("supplements_order", []),
               "main_source": config["sources"][config["main_source"]],
               "frames": expected_count, "duration_seconds": expected_count / FPS, "video": result,
               "joins": "相邻段同源同时间边界复用透明姿态后直接拼接，无跨段神经混姿势；共享边界有1个播放帧停顿。"})
    return result


def zip_parts(files, destination, prefix, limit):
    """PNG已经压缩，ZIP_STORED便于可靠控制包体；绝不生成超过上限的大包。"""
    destination.mkdir(parents=True, exist_ok=True)
    groups, current, estimate = [], [], 22
    for path, name in files:
        needed = path.stat().st_size + 2 * len(name.encode("utf-8")) + 128
        if needed + 22 > limit:
            raise ValueError("单文件超过分包上限，先分段该视频/文件: " + str(path))
        if current and estimate + needed > limit:
            groups.append(current)
            current, estimate = [], 22
        current.append((path, name))
        estimate += needed
    if current:
        groups.append(current)
    results = []
    for index, group in enumerate(groups, 1):
        target = destination / ("{}-part{:02d}.zip".format(prefix, index))
        temporary = target.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
            for path, name in group:
                archive.write(path, name)
        if temporary.stat().st_size > limit:
            temporary.unlink()
            raise ValueError("实际ZIP超过上限；未发布该包")
        temporary.replace(target)
        results.append({"file": target.name, "bytes": target.stat().st_size, "entries": [name for _, name in group]})
    return results


def pack(config):
    validate(config, ready=True)
    assert_prepared(config)
    _, output = locations(config)
    limit = config.get("zip_part_bytes", 350 * 1024 * 1024)
    groups = []
    for segment in config["segments"]:
        folder = output / "分招" / segment["id"]
        files = [(path, str(Path(segment["id"]) / path.relative_to(folder)))
                 for path in sorted(folder.rglob("*")) if path.is_file()]
        if not files:
            raise ValueError("分段尚未成片，拒绝空包")
        groups.append({"segment": segment["id"], "kind": segment["kind"],
                       "parts": zip_parts(files, output / "分包下载", segment["id"], limit)})
    json_write(output / "分包索引.json", {"max_part_bytes": limit, "segments": groups,
               "full_video": "八极拳-完整套路慢放.mp4", "note": "完整视频独立文件；逐帧PNG按分招和体积分包，不生成1GB总包。"})
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["validate", "prepare", "rife-plan", "finalize", "verify", "assemble", "pack"])
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--structure-only", action="store_true")
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--segment", action="append")
    args = parser.parse_args()
    if args.structure_only and args.stage != "validate":
        parser.error("--structure-only只能用于validate，不能渲染未锁定来源")
    if args.segment and args.stage != 'finalize':
        parser.error('--segment只能用于finalize')
    config = load(args.manifest, ready=not args.structure_only)
    if args.stage == "validate":
        result = {"valid": True, "ready_checked": not args.structure_only, "warnings": config["_warnings"]}
    elif args.stage == "rife-plan":
        if not args.binary or not args.model:
            parser.error("rife-plan需明确本机--binary和--model路径")
        result = rife_plan(config, args.binary, args.model)
    elif args.stage == 'finalize':
        result = finalize(config, args.segment)
    else:
        result = globals()[args.stage](config)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
