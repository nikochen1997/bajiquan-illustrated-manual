# 八极拳完整篇批量流水线

当前仅完成可复用工具和本地契约测试。没有制作未知来源的完整篇，没有执行RIFE/GPU，没有修改原图或旧撑拳样片。

首版固定640×800、24fps、宋体、同一纸色与「古风手绘」页脚。`sprites.py`独立保留了样片验证过的透明缝/完整人物连通域拆帧函数，不依赖旧样片目录。

## 最简单的JSON接口

`manifest.example.json`只是结构示例，链接、老师、版本、时间都不是实际拳谱；来源`locked:false`，不能制作。来源逐式对齐完成后复制为新的真实清单，填写实际素材。

```json
{
  "schema_version": 1,
  "canvas": {"width": 640, "height": 800, "fps": 24},
  "main_source": "teacher-version-a",
  "sources": {
    "teacher-version-a": {
      "url": "实际原视频链接",
      "teacher": "已核实老师",
      "version": "已核实完整套路版本",
      "locked": true,
      "complete_range": [5.0, 75.0]
    }
  },
  "routine_order": ["routine-001", "routine-002"],
  "supplements_order": ["elbow-001", "kao-001"],
  "zip_part_bytes": 367001600,
  "segments": [
    {
      "id": "routine-001",
      "kind": "routine",
      "title": "实际动作名",
      "source_id": "teacher-version-a",
      "source_range": [5.0, 7.0],
      "frames": 120,
      "starts_routine": true,
      "ends_routine": false,
      "join": "cut",
      "pages": [{"path": "../generated/page-01.png", "x_shift": 0}],
      "keyframes": [
        {"page": 1, "cell": 1, "source_seconds": 5.0, "contact": {"state": "support", "foot": "both"}},
        {"page": 1, "cell": 16, "source_seconds": 7.0, "contact": {"state": "support", "foot": "left"}}
      ]
    }
  ]
}
```

上面节选只展示字段；清单必须包含顺序表引用的全部段。每页固定16格，但关键态列表可只选实际需要的格子。不要把生成图中的重复填充格算成新动作。

- `sources`：主篇只能引用同一个`main_source`；肘/靠补充可有各自来源，保持独立。`locked:true`表示操作者已经核实来源，工具不能证明该声明真实。
- `source_range`：本段原视频起止秒；关键态时间必须严格递增，首末精确覆盖范围。整套主篇从`complete_range`的起势连续覆盖到收势，不允许缺段、重叠或混用来源。
- `frames`：本段最终播放帧数，时长为`frames/24`。RIFE以关键态索引均匀插值；来源映射按邻近两关键态时间分段线性计算，所以允许非等间隔参考，但会相应改变局部播放速度。
- `starts_routine`/`ends_routine`：仅主篇首段/末段分别为真。不是把任意剪辑自称完整套路；真实起收仍须对照原视频确认。
- 第二个及以后主段`join:"shared_boundary"`：上段终点与下段起点必须为同来源同一时刻；准备阶段直接复用上一段末个透明姿态，避免在两个不同绘法之间硬混动作。成片只作直接拼接，不跨段神经混姿势；共有边界带1个播放帧停顿。
- `pages[].x_shift`是整页固定横移，`y_shift`是未知接触状态下保留的整页固定竖移。不会逐帧水平居中或逐帧缩放。
- 多页同源重叠且**两幅头顶均无举臂遮挡**时，可用`head_match:{"page":1,"cell":16,"current_cell":1,"head_clear":true}`替代该页的`x_shift`，按上方15%有效alpha重心对齐。`page`引用本段较早页；不能同时给`x_shift`。有举臂时用经核对的固定`x_shift`，不猜头部。

## 支撑、腾空与未知

所有页按名义格高统一图像单位，同尺寸页scale=1；所有段最终共用一个全局fit，保留屈膝、俯身、抬腿高低变化。

| contact | 处理 |
| --- | --- |
| `{"state":"support","foot":"left"}` | 只竖移，让已知支撑脚落同基线；抬起的另一腿保持原姿态。默认取最低有效alpha；需要时用`support_y`明确支撑点在拆格公共透明平面的y坐标。 |
| `{"state":"airborne","lift":30}` | 让最低脚底比基线高30个该页原始图像单位；不会强制落地。lift不是输出像素，先按名义格高换算统一单位，再随共同全局fit缩放。 |
| `{"state":"airborne"}` | 离地量未知，保留源竖坐标和页面y_shift，报告警告。 |
| `{"state":"unknown"}` | 不推断接地、不自动拉地，保留源竖坐标，报告警告。 |

`foot`可为`left/right/both/unknown`，左右均指人物自身左右。最低alpha不足以判断足尖、翻滚或手撑地，实际支撑点需从原视频核对。未知记录不得被报告成已确认教学动作。

真实收势停顿可给后续关键态`reuse_key:1`复用同段先前图，但仍须填写该停顿在原视频中的真实`source_seconds`。工具会保留复用来源，不把重复图称作独立新姿势。

## 使用顺序

从本聊天工作目录运行；以下`真实清单.json`是你填写的新清单，不是当前示例。

```bash
python3 -B work/八极拳完整篇/pipeline/batch.py validate work/八极拳完整篇/pipeline/manifest.example.json --structure-only
python3 -B work/八极拳完整篇/pipeline/batch.py validate 真实清单.json
python3 -B work/八极拳完整篇/pipeline/batch.py prepare 真实清单.json
python3 -B work/八极拳完整篇/pipeline/batch.py rife-plan 真实清单.json --binary /实际路径/rife-ncnn-vulkan --model /实际路径/rife-v4.6
```

`rife-plan`只写`rife-plan.json`，不执行GPU，不下载、不调用外部收费接口。主线程读其中每段`argv`，按本机沙箱/审批要求执行已验证的RIFE；全部`roi_output`完整之后：

```bash
python3 -B work/八极拳完整篇/pipeline/batch.py finalize 真实清单.json
python3 -B work/八极拳完整篇/pipeline/batch.py verify 真实清单.json
python3 -B work/八极拳完整篇/pipeline/batch.py assemble 真实清单.json
python3 -B work/八极拳完整篇/pipeline/batch.py pack 真实清单.json
```

默认中间文件在`pipeline/build/<清单名>/`，默认交付在`outputs/八极拳完整篇/`。可在清单用`state_dir`/`output_dir`覆盖；路径相对清单文件，测试只写pipeline内临时目录。已prepare的state不能覆盖；源图或清单变更后用新的state目录重建，SHA256核对防止旧RIFE结果冒充新来源。

## 交付结构与核对

- `分招/<id>/逐帧PNG/00000001.png…`、关键姿态PNG、该段MP4、来源CSV、动作与来源JSON。
- `完整编号逐帧PNG/八极拳_000001.png…`只含主套路，按顺序连续编号。优先用硬链接，文件系统不支持时复制，不重复占用同机PNG数据。
- `八极拳-完整套路慢放.mp4`只拼主篇；补充肘/靠视频在各自分招目录，不插入原套路。
- `完整来源时间映射.csv`包含全局帧、视频时间、分段、来源URL/老师/版本、来源时间及邻近关键态/插值权重；共享边界和真实停顿可追踪。源sprite的SHA256也保存在每页注册记录。
- `技术核对记录.json`、`完整篇核对记录.json`记录PNG数量、画幅、全解码、视频对应PNG抽样、唯一像素帧及静止停顿。技术通过不等于动作教学或视觉通过。
- `分包下载/<id>-part01.zip…`默认每包≤350MiB，按段和体积拆分；不生成1GB总包。单个文件超过上限会明确拒绝，须先把该视频分段。`分包索引.json`列每个包包含的条目；整套视频为独立文件。

最终素材仍须逐段查看手指、遮挡、重影、支撑脚/腾空、首尾及边界过渡，尤其所有unknown记录。未锁定的老师、版本、动作目录和「贴心肘」名称不会由此工具自动补猜。

## 已完成的小测试

```bash
python3 -B work/八极拳完整篇/pipeline/test_pipeline.py
```

测试使用自建透明图和明确的RIFE结果夹具，不运行RIFE、不声称检验神经补间视觉质量。报告见`本地契约测试报告.md`。
