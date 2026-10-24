#!/usr/bin/env python3
"""最终 RIFE 交付检查；不写最终交付、不覆盖旧 MCI 报告。"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest

import numpy as np
from PIL import Image, ImageDraw, ImageFont

WORK = Path(__file__).resolve().parents[1]
ROOT = WORK.parents[1]
RUN = WORK / "qa" / "rife-normalized"
REPORT = WORK / "qa" / "rife-final-validation"
OUTPUT = ROOT / "outputs" / "八极拳-撑拳样片"
PAPER = np.array((241, 230, 208), dtype=np.int16)
BOX = (46, 164, 594, 714)
DATA = {}
SPECS = {
    "test_png_sequence": ("SPEC-1", "最终 PNG 与关键姿态目录", "检查连续编号并全部解码", "240+46张，均640×800 RGB，首尾文件存在"),
    "test_rife_roi_correspondence": ("SPEC-2", "240幅原RIFE ROI及交付PNG", "独立重算固定纸色偏移和≤2近纸色清理", "每帧ROI均与明确变换后的神经插帧结果精确相符"),
    "test_static_background": ("SPEC-3", "240张交付PNG和固定书页", "逐像素比较ROI外区域", "标题、边框、地面、古风手绘页脚及纸底完全静止"),
    "test_video_parameters": ("SPEC-4", "最终MP4", "ffprobe读取容器参数", "640×800，24fps，10秒，声明240帧"),
    "test_decode_and_png_correspondence": ("SPEC-5", "MP4及对应PNG", "一次完整FFmpeg解码并比较12个分布样本", "真实解码240帧；样本MAE≤2.5/255且PSNR≥32dB"),
    "test_unique_and_holds": ("EXTRA-1", "240张PNG", "统计像素哈希、相邻变化与连续静止段", "如实报告唯一帧和重复停顿，不强求每帧不同"),
    "test_keyframe_and_reuse_provenance": ("EXTRA-2", "46张交付关键图、注册输入及记录", "逐图比较，核对39–46收势复用和38保留", "交付关键图与RIFE输入一致；复用来源明确"),
    "test_endpoints_and_visibility": ("EXTRA-3", "首末帧和对应源站姿", "检查亮度、人物墨线及与源图误差", "无黑屏，人物可见，首尾分别对应首站姿与合法收势复用"),
}


def read_rgb(path):
    with Image.open(path) as image:
        image.load()
        return np.asarray(image.convert("RGB")).copy()


def measures(actual, expected):
    difference = actual.astype(np.float32) - expected.astype(np.float32)
    mae = float(np.abs(difference).mean())
    mse = float(np.square(difference).mean())
    psnr = float(10 * np.log10(255.0 ** 2 / mse)) if mse else 99.0
    return {"mae_255": round(mae, 6), "psnr_db": round(psnr, 4),
            "max_channel_difference": int(np.abs(difference).max())}


def problem_index():
    paths = sorted((RUN / "roi_output").glob("*.png"))
    if [path.name for path in paths] != ["%08d.png" % i for i in range(1, 241)]:
        raise RuntimeError("RIFE ROI输出尚未完整到240帧，未制作索引")
    numbers = [14, 42, 82, 83, 84, 136, 194, 195, 196, 197, 198, 199]
    REPORT.mkdir(parents=True, exist_ok=True)
    sheet = Image.new("RGB", (1500, 560), tuple(int(v) for v in PAPER))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Songti.ttc", 17)
    for position, number in enumerate(numbers):
        image = Image.open(RUN / "roi_output" / ("%08d.png" % number)).convert("RGB")
        if image.size != (548, 550):
            raise ValueError("ROI尺寸异常: " + str(number))
        image = image.resize((250, 250), Image.Resampling.LANCZOS)
        x, y = (position % 6) * 250, (position // 6) * 280
        sheet.paste(image, (x, y))
        draw.text((x + 8, y + 256), "RIFE 第%03d帧" % number, font=font, fill=(74, 61, 46))
    destination = REPORT / "RIFE问题区12帧索引.png"
    sheet.save(destination)
    print(destination)


class FinalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((RUN / "final-render-manifest.json").read_text())
        cls.registration = json.loads((RUN / "registration.json").read_text())
        cls.frames = sorted((OUTPUT / "逐帧PNG").glob("八极撑拳_*.png"))
        cls.keys = sorted((OUTPUT / "关键姿态PNG").glob("关键姿态_*.png"))
        cls.video = OUTPUT / "八极拳-撑拳-240帧慢速样片.mp4"
        if cls.manifest.get("version") != "rife-v4.6-ground-registered":
            raise RuntimeError("最终版本标识不是已注册的 RIFE v4.6；不检查旧MCI为新交付")
        DATA["render_manifest"] = cls.manifest

    def test_png_sequence(self):
        """输入交付文件 → 全部解码、检查边界 → 编号、尺寸、颜色模式正确。"""
        self.assertEqual([p.name for p in self.frames], ["八极撑拳_%04d.png" % i for i in range(1, 241)])
        self.assertEqual([p.name for p in self.keys], ["关键姿态_%03d.png" % i for i in range(1, 47)])
        for path in self.frames + self.keys:
            with Image.open(path) as image:
                image.load()
                self.assertEqual(image.size, (640, 800), str(path))
                self.assertEqual(image.mode, "RGB", str(path))
        DATA["png_count"] = len(self.frames)
        DATA["keyframe_count"] = len(self.keys)

    def test_rife_roi_correspondence(self):
        """输入原始ROI和最终PNG → 重算finalize_rife明确纸色变换 → 每帧相符。"""
        self.assertEqual(self.manifest["body_roi"], list(BOX))
        offsets = []
        cleaned_pixels = 0
        for number in range(1, 241):
            raw = read_rgb(RUN / "roi_output" / ("%08d.png" % number)).astype(np.int16)
            self.assertEqual(raw.shape, (550, 548, 3))
            # 独立按已完整读取的finalize_rife.py契约核对，不调用其实现。
            offset = PAPER - raw[0, 0]
            expected = np.clip(raw + offset, 0, 255)
            near_paper = np.max(np.abs(expected - PAPER), axis=2) <= 2
            expected[near_paper] = PAPER
            final = read_rgb(OUTPUT / "逐帧PNG" / ("八极撑拳_%04d.png" % number))
            x0, y0, x1, y1 = BOX
            self.assertTrue(np.array_equal(final[y0:y1, x0:x1], expected.astype(np.uint8)), "第%d帧ROI不符" % number)
            offsets.append(tuple(int(v) for v in offset))
            cleaned_pixels += int(near_paper.sum())
        DATA["unique_rgb_paper_offsets"] = sorted(set(offsets))
        DATA["paper_cleanup_pixel_occurrences"] = cleaned_pixels
        DATA["roi_correspondence_checked_frames"] = 240

    def test_static_background(self):
        """输入PNG与固定书页 → 比较ROI外全部像素 → 零漂移。"""
        expected = read_rgb(RUN / "static-background.png")
        mask = np.ones((800, 640), dtype=bool)
        x0, y0, x1, y1 = BOX
        mask[y0:y1, x0:x1] = False
        patch_colors = set()
        for path in self.frames:
            actual = read_rgb(path)
            self.assertTrue(np.array_equal(actual[mask], expected[mask]), str(path))
            # ROI左上角远离人物的8×8纸面也记录颜色，区别静态外框和神经输出纸面。
            patch_colors.update(tuple(int(v) for v in color) for color in np.unique(actual[y0:y0+8, x0:x0+8].reshape(-1, 3), axis=0))
        DATA["outside_roi_background_pixel_difference"] = 0
        DATA["inside_roi_corner_patch_colors"] = sorted(patch_colors)

    def test_video_parameters(self):
        """输入视频 → 读取容器参数 → 640×800、24fps、10秒、240帧。"""
        result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,duration,codec_name,pix_fmt",
            "-of", "json", str(self.video)], capture_output=True, text=True, check=True)
        stream = json.loads(result.stdout)["streams"][0]
        self.assertEqual((stream["width"], stream["height"]), (640, 800))
        self.assertEqual(stream["avg_frame_rate"], "24/1")
        self.assertEqual(int(stream["nb_frames"]), 240)
        self.assertAlmostEqual(float(stream["duration"]), 10, places=4)
        DATA["video_parameters"] = stream

    def test_decode_and_png_correspondence(self):
        """输入视频 → 一次全解码并采样 → 解码240帧且与对应PNG相符。"""
        command = ["ffmpeg", "-hide_banner", "-v", "error", "-xerror", "-i", str(self.video),
                   "-map", "0:v:0", "-an", "-c:v", "rawvideo", "-pix_fmt", "rgb24",
                   "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        frame_size = 640 * 800 * 3
        samples = {0, 1, 13, 41, 81, 82, 83, 135, 193, 197, 198, 239}
        comparisons = []
        count = 0
        try:
            while True:
                buffer = process.stdout.read(frame_size)
                if not buffer:
                    break
                self.assertEqual(len(buffer), frame_size, "视频解码到不完整帧")
                if count in samples:
                    decoded = np.frombuffer(buffer, np.uint8).reshape((800, 640, 3))
                    expected = read_rgb(OUTPUT / "逐帧PNG" / ("八极撑拳_%04d.png" % (count + 1)))
                    measurement = {"frame": count + 1, **measures(decoded, expected)}
                    comparisons.append(measurement)
                    self.assertLessEqual(measurement["mae_255"], 2.5)
                    self.assertGreaterEqual(measurement["psnr_db"], 32)
                count += 1
            stderr = process.stderr.read().decode("utf-8", errors="replace")
            return_code = process.wait()
            (REPORT / "ffmpeg-full-decode.log").write_text(stderr)
            self.assertEqual(return_code, 0, stderr)
            self.assertEqual(count, 240)
            self.assertEqual(len(comparisons), len(samples))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            process.stderr.close()
        DATA["actual_decoded_frames"] = count
        DATA["video_to_png_samples"] = comparisons

    def test_unique_and_holds(self):
        """输入240PNG → 像素哈希与相邻比较 → 如实记录静止收势停顿。"""
        hashes, differences, previous = [], [], None
        for path in self.frames:
            pixels = read_rgb(path)
            hashes.append(hashlib.sha256(pixels.tobytes()).hexdigest())
            if previous is not None:
                differences.append(float(np.abs(pixels.astype(np.int16) - previous).mean()))
            previous = pixels.astype(np.int16)
        runs = []
        start = 0
        for index in range(1, len(hashes) + 1):
            if index == len(hashes) or hashes[index] != hashes[start]:
                if index - start > 1:
                    runs.append({"start_frame": start + 1, "end_frame": index, "frames": index - start})
                start = index
        DATA["pixel_unique_frames"] = len(set(hashes))
        DATA["adjacent_same_pixel_pairs"] = sum(a == b for a, b in zip(hashes[:-1], hashes[1:]))
        DATA["identical_pixel_holds"] = runs
        DATA["adjacent_mae_255"] = {"min": round(min(differences), 6), "max": round(max(differences), 6),
                                      "mean": round(float(np.mean(differences)), 6)}
        self.assertEqual(len(hashes), 240)

    def test_keyframe_and_reuse_provenance(self):
        """输入交付关键图和RIFE来源 → 全46图比对 → 复用来源正确且38未替换。"""
        first = read_rgb(RUN / "input" / "00000001.png")
        source_hashes = []
        for number in range(1, 47):
            source = read_rgb(RUN / "input" / ("%08d.png" % number))
            delivered = read_rgb(OUTPUT / "关键姿态PNG" / ("关键姿态_%03d.png" % number))
            self.assertTrue(np.array_equal(source, delivered), "第%d关键图与RIFE输入不符" % number)
            source_hashes.append(hashlib.sha256(source.tobytes()).hexdigest())
            if number >= 39:
                self.assertTrue(np.array_equal(first, source))
        self.assertFalse(np.array_equal(first, read_rgb(RUN / "input" / "00000038.png")))
        reused = [key["index"] for key in self.registration["keys"] if key["reused_from_key"] == 1]
        self.assertEqual(reused, list(range(39, 47)))
        DATA["source_keyframe_pixel_unique_count"] = len(set(source_hashes))
        DATA["reuse_provenance"] = {"keys": reused, "from_key": 1, "reason": "已并脚收拳的收势停顿",
                                     "key_38_preserved": True}
        DATA["registered_source_foot_baselines"] = sorted(set(key["output_alpha_bbox"][3] for key in self.registration["keys"]))

    def test_endpoints_and_visibility(self):
        """输入首末帧和源关键图 → 亮度、人物和来源比对 → 无黑屏且来源可信。"""
        observations = []
        x0, y0, x1, y1 = BOX
        for final_number, source_number in ((1, 1), (240, 46)):
            actual = read_rgb(OUTPUT / "逐帧PNG" / ("八极撑拳_%04d.png" % final_number))
            expected = read_rgb(RUN / "input" / ("%08d.png" % source_number))
            dark = int((actual[y0:y1, x0:x1].mean(axis=2) < 130).sum())
            self.assertGreater(float(actual.mean()), 120)
            self.assertGreater(dark, 1000)
            measurement = measures(actual, expected)
            self.assertLessEqual(measurement["mae_255"], 1.0)
            observations.append({"frame": final_number, "source_key": source_number,
                                 "mean_brightness": round(float(actual.mean()), 3),
                                 "figure_dark_pixels": dark, **measurement})
        DATA["endpoint_provenance"] = observations


def all_tests(suite):
    result = []
    for item in suite:
        result.extend(all_tests(item)) if isinstance(item, unittest.TestSuite) else result.append(item)
    return result


def write_report(result, tests, log):
    failures = {test.id(): message for test, message in result.failures + result.errors}
    skipped = {test.id(): reason for test, reason in result.skipped}
    visual_path = REPORT / "visual-review.json"
    visual = json.loads(visual_path.read_text()) if visual_path.exists() else {
        "status": "待人工复核", "conclusion": "技术测试不判断手指、遮挡、动作准确性及自然程度；RIFE问题区索引交主线程视觉复核。"}
    rows = []
    for test in tests:
        case, source, operation, expected = SPECS[test.id().split(".")[-1]]
        state = "失败" if test.id() in failures else "跳过" if test.id() in skipped else "通过"
        rows.append("| {} | {} | {} | {} | {} |".format(case, source, operation, expected, state))
    technical = "通过" if result.wasSuccessful() else "未通过"
    report = ["# RIFE最终交付测试报告", "", "技术验收：{}。视觉验收：{}。".format(technical, visual["status"]), "",
              "CLAUDE.md、WORKFLOW.md 均不存在；按用户的输入 → 操作 → 预期结果格式报告。本报告独立于旧 MCI 报告。", "",
              "| 编号 | 输入 | 操作 | 预期结果 | 实测 |", "| --- | --- | --- | --- | --- |"]
    report.extend(rows)
    report.extend(["", "## 实测数据", "", "```json", json.dumps(DATA, ensure_ascii=False, indent=2), "```", "",
                   "## 视觉复核", "", visual["conclusion"], "", "问题区索引：RIFE问题区12帧索引.png；包含14、42、82–84、136、194–199帧。", "",
                   "## Bug 清单", "", "未发现技术失败。" if not failures else "存在以下技术失败，未修改断言或跳过测试凑通过。"])
    for name, message in failures.items():
        report.extend(["", "### " + name, "", "```text", message, "```"])
    report.extend(["", "## 范围与局限", "", "本次为公开《八极拳健身操》6.60–11.10秒撑拳片段的AI重绘与神经补间。46个关键态中39–46收势复用首站姿，240个播放帧不等同于240个独立手绘姿势。真实像素唯一数和停顿见实测。未经过拳师逐帧校订，技术测试通过不能证明可以直接作为完整拳法教学。", "",
                   "视频由交付PNG顺序编码，H.264有损压缩因此允许有限像素误差。原RIFE ROI先抵消角点固定纸色差，再把最大通道纸色差≤2的像素设为纸色，测试按这个完整公开变换核对。", "",
                   "## 运行日志", "", "```text", log, "```"])
    (REPORT / "实测报告.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    (REPORT / "实测报告.json").write_text(json.dumps({"technical_status": technical, "visual": visual,
        "tests_run": result.testsRun, "failures": failures, "skipped": skipped,
        "observations": DATA}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roi-index-only", action="store_true", help="仅从完成的240幅ROI制作问题区索引，不检查尚未替换的最终交付")
    args = parser.parse_args()
    REPORT.mkdir(parents=True, exist_ok=True)
    problem_index()
    if args.roi_index_only:
        sys.exit(0)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FinalTests)
    tests = all_tests(suite)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = stream.getvalue()
    print(log, end="")
    write_report(result, tests, log)
    print("报告: " + str(REPORT / "实测报告.md"))
    sys.exit(0 if result.wasSuccessful() else 1)
