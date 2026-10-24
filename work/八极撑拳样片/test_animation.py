#!/usr/bin/env python3
"""输入 → 操作 → 预期结果；验证真实 PNG 交付及 MP4 全解码和帧对应关系。"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np
from PIL import Image, ImageDraw

import build_animation as animation


WORK = Path(__file__).resolve().parent
QA = WORK / "qa"
OBSERVATIONS = {}
CASE_SPECS = {
    "test_grid_non_divisible_dimensions": ("EXTRA-1", "1254×1254 和 1255×1253 的整页", "按 4×4 比例裁格", "16 格无缝覆盖原图，没有漏像素或重叠"),
    "test_same_pose_page_alignment": ("UNIT-2", "同一姿势的两个等比例 bbox", "按重叠姿势算整页变换", "四边对齐，且只产生整页单一等比变换"),
    "test_global_fit_preserves_relative_height": ("EXTRA-2", "高低不同的两个姿势", "使用同一个全局变换", "身体高度比不变，不逐帧放大"),
    "test_transparent_seam_preserves_whole_bodies": ("EXTRA-6", "鞋底超过名义格线 1px 的合成图", "比例格线 ±8px 寻找全页透明缝", "完整人物像素均保留，且不混入下一格"),
    "test_component_fallback_preserves_whole_bodies": ("EXTRA-7", "找不到整页横缝但含 16 个完整人物的合成图", "按完整人物连通域分离并恢复名义格坐标", "16 个人物有效像素无损保留，保持相对位置"),
    "test_png_count_and_dimensions": ("SPEC-1", "PNG 交付目录", "遍历编号、解码并检查大小", "240 张逐帧、46 张关键姿态，编号连续且均为 640×800"),
    "test_video_metadata": ("SPEC-2", "MP4", "ffprobe 检查视频参数", "240 帧、24fps、10 秒、640×800"),
    "test_full_decode_and_png_correspondence": ("SPEC-3", "MP4 及原始 PNG", "完整解码并比较 10 个分布样本", "全部 240 帧可解码；样本与对应 PNG 的平均误差≤2.5/255，PSNR≥32dB"),
    "test_fixed_paper_and_text": ("SPEC-4", "240 张逐帧 PNG 和固定书页", "逐帧逐像素比较人物区域以外内容", "边框、标题、地面、页脚和纸底完全一致"),
    "test_start_and_end_are_visible": ("EXTRA-3", "第 1、240 帧", "检查画面亮度及人物区域墨线", "首尾无黑屏，并包含可见人物"),
    "test_frame_uniqueness_statistics": ("EXTRA-4", "240 张逐帧 PNG", "统计像素哈希与相邻帧变化", "如实报告唯一帧及重复帧，不要求真实停顿的每帧都不同"),
    "test_source_crop_boundary_audit": ("EXTRA-5", "精灵原图裁格记录", "统计 alpha 触格边情况", "报告可能被源图格线截断的位置，供视觉复核"),
}


class UnitTests(unittest.TestCase):
    def test_grid_non_divisible_dimensions(self):
        """输入非整除尺寸 → 划分网格 → 每个像素被且仅被一格覆盖。"""
        for width, height in ((1254, 1254), (1255, 1253)):
            coverage = np.zeros((height, width), np.uint8)
            for index in range(16):
                x0, y0, x1, y1 = animation.grid_box(width, height, index)
                coverage[y0:y1, x0:x1] += 1
            self.assertTrue(np.all(coverage == 1))

    def test_same_pose_page_alignment(self):
        """输入同源姿势不同页大小 → 算整页变换 → bbox 四边重合。"""
        reference = (35, 15, 195, 295)
        current = (12, 20, 92, 160)
        transform = animation.overlap_transform(reference, current)
        np.testing.assert_allclose(animation.apply_transform(current, transform), reference)
        self.assertAlmostEqual(transform[0], 2)

    def test_global_fit_preserves_relative_height(self):
        """输入站直/屈膝两姿势 → 共用全局变换 → 高度比例保持。"""
        standing = (20, 5, 150, 305)
        crouching = (10, 85, 200, 305)
        transform = animation.fit_transform(animation.union_box([standing, crouching]))
        a = animation.apply_transform(standing, transform)
        b = animation.apply_transform(crouching, transform)
        self.assertAlmostEqual((a[3] - a[1]) / (b[3] - b[1]), 300 / 220)
        self.assertLessEqual(max(a[2], b[2]), animation.SAFE_BOX[2] + 1e-6)

    def test_transparent_seam_preserves_whole_bodies(self):
        """输入跨过名义线一像素的人物 → 移至透明缝 → 保全鞋底。"""
        sheet = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sheet)
        for row in range(4):
            for column in range(4):
                draw.rectangle((column * 100 + 30, row * 100 + 15,
                                column * 100 + 60, min(row * 100 + 100, 390)), fill=(60, 50, 40, 255))
        cells, details = animation.split_sheet(sheet)
        self.assertEqual(details["split_method"], "proportional_grid_with_transparent_seams")
        self.assertEqual(details["actual_boundaries"]["y"], [0, 101, 201, 301, 400])
        expected = int((np.asarray(sheet)[:, :, 3] >= 24).sum())
        actual = sum(int((np.asarray(cell)[:, :, 3] >= 24).sum()) for cell in cells)
        self.assertEqual(actual, expected)
        self.assertTrue(all(not any(record["alpha_at_cell_edge"].values()) for record in details["cells"]))

    def test_component_fallback_preserves_whole_bodies(self):
        """输入越格且无整页横缝 → 完整人物分离 → 像素不丢失或复制。"""
        sheet = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sheet)
        for row in range(4):
            for column in range(4):
                top = row * 100 + (80 if column == 0 and row < 3 else 35)
                draw.rectangle((column * 100 + 35, top, column * 100 + 65, top + 30), fill=(60, 50, 40, 255))
        cells, details = animation.split_sheet(sheet)
        self.assertEqual(details["split_method"], "complete_person_connected_components")
        expected = int((np.asarray(sheet)[:, :, 3] >= 24).sum())
        actual = sum(int((np.asarray(cell)[:, :, 3] >= 24).sum()) for cell in cells)
        self.assertEqual(actual, expected)
        self.assertEqual(len(cells), 16)
        bbox = animation.alpha_bbox(cells[0])
        self.assertEqual(bbox[1] - details["common_padding"], 80)


class ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((QA / "render-manifest.json").read_text(encoding="utf-8"))
        cls.frames = sorted(Path(cls.manifest["output_frames"]).glob("八极撑拳_*.png"))
        cls.keys = sorted(Path(cls.manifest["output_keys"]).glob("关键姿态_*.png"))
        cls.video = Path(cls.manifest["video_path"])

    def test_png_count_and_dimensions(self):
        """输入交付目录 → 全部 PNG 解码 → 编号连续且尺寸统一。"""
        self.assertEqual([p.name for p in self.frames], ["八极撑拳_%04d.png" % i for i in range(1, 241)])
        self.assertEqual([p.name for p in self.keys], ["关键姿态_%03d.png" % i for i in range(1, 47)])
        for path in self.frames + self.keys:
            with Image.open(path) as image:
                image.load()
                self.assertEqual(image.size, (640, 800), str(path))
                self.assertEqual(image.mode, "RGB", str(path))
        OBSERVATIONS["png_count"] = len(self.frames)
        OBSERVATIONS["keyframe_count"] = len(self.keys)

    def test_video_metadata(self):
        """输入 MP4 → ffprobe → 240 帧、24fps、10秒、640×800。"""
        raw = animation.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                             "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,nb_read_frames,duration",
                             "-of", "json", str(self.video)])
        stream = json.loads(raw)["streams"][0]
        self.assertEqual((stream["width"], stream["height"]), (640, 800))
        self.assertEqual(stream["avg_frame_rate"], "24/1")
        self.assertEqual(int(stream["nb_read_frames"]), 240)
        self.assertAlmostEqual(float(stream["duration"]), 10.0, places=4)
        OBSERVATIONS["video_metadata"] = stream

    def test_full_decode_and_png_correspondence(self):
        """输入视频及 PNG → 完整解码并抽样 → 可解码且对应帧相符。"""
        animation.run(["ffmpeg", "-hide_banner", "-v", "error", "-xerror", "-i", str(self.video),
                       "-f", "null", "-"], QA / "ffmpeg-全解码.log")
        capture = cv2.VideoCapture(str(self.video))
        self.assertTrue(capture.isOpened())
        samples = {0, 1, 26, 53, 79, 106, 132, 159, 213, 239}
        comparisons = []
        count = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            self.assertEqual(frame.shape, (800, 640, 3))
            if count in samples:
                decoded = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                original = np.asarray(Image.open(self.frames[count]).convert("RGB"))
                difference = decoded.astype(np.float32) - original.astype(np.float32)
                mae = float(np.abs(difference).mean())
                mse = float(np.square(difference).mean())
                psnr = float(10 * np.log10(255.0 ** 2 / mse)) if mse else 99.0
                comparisons.append({"frame": count + 1, "mae_255": round(mae, 5), "psnr_db": round(psnr, 3)})
                self.assertLessEqual(mae, 2.5, "第 %s 帧" % (count + 1))
                self.assertGreaterEqual(psnr, 32.0, "第 %s 帧" % (count + 1))
            count += 1
        capture.release()
        self.assertEqual(count, 240)
        self.assertEqual(len(comparisons), len(samples))
        OBSERVATIONS["decoded_frame_count"] = count
        OBSERVATIONS["mp4_png_sample_comparisons"] = comparisons

    def test_fixed_paper_and_text(self):
        """输入 240 帧及静态书页 → 比较动态区域外全部像素 → 零漂移。"""
        expected = np.asarray(Image.open(self.manifest["static_background"]).convert("RGB"))
        fixed = np.ones((800, 640), dtype=bool)
        x0, y0, x1, y1 = self.manifest["dynamic_box"]
        fixed[y0:y1, x0:x1] = False
        for path in self.frames:
            actual = np.asarray(Image.open(path).convert("RGB"))
            self.assertTrue(np.array_equal(actual[fixed], expected[fixed]), str(path))
        OBSERVATIONS["static_region_pixel_difference"] = 0

    def test_start_and_end_are_visible(self):
        """输入首尾帧 → 查亮度和墨线 → 没有黑屏且人物存在。"""
        records = []
        for index in (0, 239):
            frame = np.asarray(Image.open(self.frames[index]).convert("RGB"))
            x0, y0, x1, y1 = self.manifest["dynamic_box"]
            region = frame[y0:y1, x0:x1]
            dark_pixels = int((region.mean(axis=2) < 130).sum())
            self.assertGreater(float(frame.mean()), 120)
            self.assertGreater(dark_pixels, 1000)
            records.append({"frame": index + 1, "mean_brightness": round(float(frame.mean()), 2),
                            "figure_region_dark_pixels": dark_pixels})
        OBSERVATIONS["endpoint_visibility"] = records

    def test_frame_uniqueness_statistics(self):
        """输入所有帧 → 像素哈希和相邻差值 → 只如实统计停顿。"""
        hashes, adjacent, previous = [], [], None
        for path in self.frames:
            frame = np.asarray(Image.open(path).convert("RGB"))
            hashes.append(hashlib.sha256(frame.tobytes()).hexdigest())
            if previous is not None:
                adjacent.append(float(np.abs(frame.astype(np.int16) - previous).mean()))
            previous = frame.astype(np.int16)
        unique = len(set(hashes))
        OBSERVATIONS["pixel_unique_frames"] = unique
        OBSERVATIONS["same_pixel_adjacent_pairs"] = sum(a == b for a, b in zip(hashes[:-1], hashes[1:]))
        OBSERVATIONS["adjacent_mae_255"] = {"min": round(min(adjacent), 5), "max": round(max(adjacent), 5),
                                             "mean": round(float(np.mean(adjacent)), 5)}
        self.assertGreaterEqual(unique, 1)
        self.assertLessEqual(unique, 240)

    def test_source_crop_boundary_audit(self):
        """输入裁格记录 → 检查有效 alpha 触边 → 保留全部源图缺陷记录。"""
        warnings = []
        for page in self.manifest["pages"]:
            self.assertEqual(page["source_effective_alpha_pixels"], page["split_effective_alpha_pixels"])
            for cell in page["cells"]:
                for location in ("alpha_at_cell_edge", "alpha_at_source_page_edge"):
                    edges = {name: count for name, count in cell[location].items() if count}
                    if edges:
                        warnings.append({"page": page["page"], "cell": cell["cell"],
                                         "location": location, "edge_pixels": edges})
        OBSERVATIONS["source_crop_boundary_warnings"] = warnings
        OBSERVATIONS["page_alignment"] = self.manifest["overlap_alignment"]
        OBSERVATIONS["actual_split_boundaries"] = [
            {"page": page["page"], "method": page["split_method"], "boundaries": page["actual_boundaries"],
             "seams": page["seam_records"]} for page in self.manifest["pages"]]
        self.assertEqual(len(self.manifest["keyframes"]), 46)


def collect_tests(suite):
    collected = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            collected.extend(collect_tests(item))
        else:
            collected.append(item)
    return collected


def write_report(result, executed, output):
    failures = {test.id(): message for test, message in result.failures + result.errors}
    skipped = {test.id(): reason for test, reason in result.skipped}
    rows = []
    for test in executed:
        method = test.id().split(".")[-1]
        case, source, action, expected = CASE_SPECS[method]
        status = "失败" if test.id() in failures else "跳过" if test.id() in skipped else "通过"
        rows.append("| {} | {} | {} | {} | {} |".format(case, source, action, expected, status))
    boundary_warnings = OBSERVATIONS.get("source_crop_boundary_warnings", [])
    report = ["# 八极拳撑拳样片测试报告", "", "项目 CLAUDE.md、WORKFLOW.md 均不存在；以下按用户给出的输入 → 操作 → 预期结果格式记录。", "",
              "测试结果：运行 {} 项，通过 {} 项，失败/错误 {} 项，跳过 {} 项。".format(
                  result.testsRun, result.testsRun - len(failures) - len(skipped), len(failures), len(skipped)), "",
              "| 编号 | 输入 | 操作 | 预期结果 | 实测 |", "| --- | --- | --- | --- | --- |"]
    report.extend(rows)
    report.extend(["", "## 实测数据", "", "```json", json.dumps(OBSERVATIONS, ensure_ascii=False, indent=2), "```", "",
                   "## Bug 清单与素材问题", ""])
    if boundary_warnings:
        report.append("发现 {} 条源图外边界或拆帧画布的有效 alpha 触边记录，可能截断鞋尖、举手或发梢；具体页/格/边见上方实测数据。位置：generated/poses-page-*.png。影响：插帧无法恢复缺失的身体细节。建议：用四周至少 8% 透明留白重新生成对应源页。脚本没有重画人物。".format(len(boundary_warnings)))
    else:
        report.append("拆帧先在比例格线 ±8px 内寻找无有效笔画的全页透明缝（alpha<8 的透明噪点不当作人体），无缝时才分离完整人物连通域；实际边界和缝上最大 alpha 见实测数据。拆帧后未发现有效人体 alpha 触到最终画布边界。未发现需要修改任务范围外业务代码的 Bug。")
    report.extend(["", "## 局限", "", "本次仅为公开示范中 6.60–11.10 秒撑拳动作的 AI 重绘慢速样片。运动补偿插帧不是新动作教学依据；手指、遮挡、衣摆及源关键姿态的画法差异仍需视觉复核。MP4 使用 H.264 有损编码，抽样比较允许有限像素误差。人物区域外背景与文字按 PNG 原始像素检查，视频编码后的像素不保证无损。"])
    visual_path = QA / "视觉复核.json"
    if visual_path.is_file():
        visual = json.loads(visual_path.read_text(encoding="utf-8"))
        report.extend(["", "## 视觉复核", "", visual["conclusion"], ""])
        for issue in visual["issues"]:
            report.append("- {}：{}".format(issue["location"], issue["observation"]))
        report.extend(["", "视觉索引：qa/全程24帧视觉索引.png；qa/两次接页附近12帧细节索引.png。"])
    if failures:
        report.extend(["", "## 失败详情", ""])
        for name, message in failures.items():
            report.extend(["### " + name, "", "```text", message, "```", ""])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(report) + "\n", encoding="utf-8")
    output.with_suffix(".json").write_text(json.dumps({"tests_run": result.testsRun,
        "failures": list(failures), "skipped": skipped, "observations": OBSERVATIONS},
        ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit-only", action="store_true", help="仅运行不依赖图片交付的计算边界测试")
    parser.add_argument("--report", type=Path, default=QA / "实测报告.md")
    args = parser.parse_args()
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(UnitTests))
    if not args.unit_only:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ArtifactTests))
    executed = collect_tests(suite)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    print(stream.getvalue(), end="")
    write_report(result, executed, args.report)
    print("测试报告: " + str(args.report))
    sys.exit(0 if result.wasSuccessful() else 1)
