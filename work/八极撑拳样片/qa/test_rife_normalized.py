#!/usr/bin/env python3
"""RIFE 输入候选的姿态注册检查：输入 → 操作 → 预期结果。"""

import io
import json
from pathlib import Path
import sys
import unittest

import numpy as np
from PIL import Image

import build_rife_normalized as candidate


DEST = candidate.DESTINATION


class CandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registration = json.loads((DEST / "registration.json").read_text())
        cls.summary = json.loads((DEST / "summary.json").read_text())

    def test_all_rgb_and_transparent_frames_decode(self):
        """输入两目录 → 解码全部文件 → 各46张连续编号、640×800、RGB/RGBA。"""
        for folder, mode in (("input", "RGB"), ("aligned", "RGBA")):
            paths = sorted((DEST / folder).glob("*.png"))
            self.assertEqual([p.name for p in paths], ["%08d.png" % i for i in range(1, 47)])
            for path in paths:
                with Image.open(path) as image:
                    image.load()
                    self.assertEqual(image.size, (640, 800))
                    self.assertEqual(image.mode, mode)

    def test_same_unit_no_bbox_or_per_frame_scaling(self):
        """输入同1254源页 → 检查46个变换 → 页单位scale均为1，最终共用全局缩放。"""
        self.assertEqual(self.registration["page_scales"], [1, 1, 1])
        self.assertEqual({key["page_scale"] for key in self.registration["keys"]}, {1})
        global_scale = self.registration["global_fit"][0]
        for key in self.registration["keys"]:
            self.assertAlmostEqual(key["combined_transform"][0], global_scale)

    def test_page_horizontal_shift_is_fixed(self):
        """输入46个注册记录 → 按源页查x位移 → 每页固定、不逐帧横向居中。"""
        for page in (1, 2, 3):
            shifts = {key["page_x_shift"] for key in self.registration["keys"]
                      if key["original_page"] == page and key["reused_from_key"] is None}
            self.assertEqual(len(shifts), 1)
        for join in self.registration["head_joins"]:
            self.assertAlmostEqual(join["residual_x"], 0)

    def test_actual_foot_pixels_share_baseline(self):
        """输入46张透明人物 → 从alpha重新测脚底 → 全部准确落在同一基线698。"""
        baselines = []
        heights = []
        for number in range(1, 47):
            image = Image.open(DEST / "aligned" / ("%08d.png" % number)).convert("RGBA")
            bbox = candidate.build.alpha_bbox(image)
            baselines.append(bbox[3])
            heights.append(bbox[3] - bbox[1])
            x0, y0, x1, y1 = self.registration["dynamic_box"]
            self.assertGreaterEqual(bbox[0], x0)
            self.assertGreaterEqual(bbox[1], y0)
            self.assertLessEqual(bbox[2], x1)
            self.assertLessEqual(bbox[3], y1)
        self.assertEqual(set(baselines), {698})
        self.assertGreater(max(heights) - min(heights), 30, "应保留屈膝与举臂高度变化")

    def test_returned_stance_reuses_first_after_39_only(self):
        """输入收势关键态 → 检查39–46和38 → 39起复用首站姿，38保留收脚。"""
        first = np.asarray(Image.open(DEST / "input" / "00000001.png"))
        for number in range(39, 47):
            self.assertTrue(np.array_equal(first, np.asarray(Image.open(DEST / "input" / ("%08d.png" % number)))))
        self.assertFalse(np.array_equal(first, np.asarray(Image.open(DEST / "input" / "00000038.png"))))
        self.assertEqual(self.summary["first_height_pixels"], self.summary["last_height_pixels"])

    def test_static_page_and_footer_fixed(self):
        """输入固定书页与46张RGB → 比较人物安全区外 → 纸底标题和古风手绘页脚零漂移。"""
        expected = np.asarray(Image.open(DEST / "static-background.png").convert("RGB"))
        x0, y0, x1, y1 = self.registration["dynamic_box"]
        mask = np.ones((800, 640), dtype=bool)
        mask[y0:y1, x0:x1] = False
        for number in range(1, 47):
            image = np.asarray(Image.open(DEST / "input" / ("%08d.png" % number)).convert("RGB"))
            self.assertTrue(np.array_equal(expected[mask], image[mask]))
        self.assertEqual(self.registration["footer"], "古风手绘  ·  慢速观摩")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CandidateTests)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = stream.getvalue()
    print(log, end="")
    summary = json.loads((DEST / "summary.json").read_text())
    report = ["# RIFE 注册关键帧候选测试报告", "", "CLAUDE.md、WORKFLOW.md 不存在；按输入 → 操作 → 预期结果记录。未覆盖原 outputs。", "",
              "同一名义格高统一图像单位；仅页间固定横移、每帧支撑脚竖移及一个共同全局等比缩放。39–46号收势复用首站姿，38号保留。", "",
              "```text", log, "```", "", "## 实测", "", "```json", json.dumps(summary, ensure_ascii=False, indent=2), "```", "",
              "## 局限", "", "注册解决脚底漂浮与页间逐步缩小；没有重画人物，源图视角、衣袖、手型差异仍然存在。每帧竖移为本片有支撑脚着地的动作注册，不适用于腾空动作。尚未对RIFE实际插帧结果作视觉验收。"]
    (DEST / "关键帧注册测试报告.md").write_text("\n".join(report), encoding="utf-8")
    sys.exit(0 if result.wasSuccessful() else 1)
