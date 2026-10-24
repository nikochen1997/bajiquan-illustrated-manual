from pathlib import Path
import subprocess,json
from PIL import Image,ImageDraw,ImageFont
root=Path('/Users/chenjianhu/Documents/Codex/2026-10-23/new-chat')
work=root/'work/八极撑拳样片'
video=work/'source/官方八极拳演示.mp4'
refs=work/'source/reference_frames'
refs.mkdir(exist_ok=True)
meta=[]
for i in range(46):
    ts=6.6+i/10
    p=refs/f'pose-{i+1:03d}.png'
    subprocess.run(['ffmpeg','-y','-v','error','-ss',f'{ts:.3f}','-i',str(video),'-vf','crop=420:368:80:0','-frames:v','1',str(p)],check=True,capture_output=True)
    meta.append({'keyframe':i+1,'source_time_seconds':round(ts,3),'path':str(p)})
font=ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc',20)
for page,start in enumerate([0,15,30],1):
    sheet=Image.new('RGB',(1536,1536),'#ece7df')
    draw=ImageDraw.Draw(sheet)
    for j in range(16):
        i=start+j
        im=Image.open(refs/f'pose-{i+1:03d}.png').convert('RGB').resize((384,336),Image.Resampling.LANCZOS)
        x=(j%4)*384;y=(j//4)*384
        sheet.paste(im,(x,y+12))
        draw.text((x+10,y+354),f'{i+1:02d}  {6.6+i/10:.2f}s',font=font,fill='#241e18')
    sheet.save(work/f'source/reference-sheet-{page:02d}.png')
(work/'source/reference-timing.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
(work/'BRIEF.md').write_text('''---
workflow: general-video
flow: automation
storyboard: no
message: 古代手绘同人物逐帧演示八极拳撑拳片段
aspect: square
language: zh-CN
length: 10s
---

用户要求：先交付一小段流畅样片，每帧一个姿势，同一个人物、古代手绘风格；单张图和视频一起交付。最终计划扩展几百张及整套。
用户选择：先找公开示范做样片。
动作依据：国家体育总局《居家健身教学视频—八极拳》，来源吉林省体育局；视频真实片头《八极拳健身操》。
来源：https://www.sport.gov.cn/n20001280/n20001265/n20066978/c20287390/content.html
原视频：https://www.sport.gov.cn/n20001280/n20001265/n20066978/c20287390/part/20287660.mp4
本次范围：原视频6.60–11.10秒撑拳片段；46个密集参考姿态，目标240帧/10秒慢速样片。不是整套传统套路。
人物与画风：沿用前次松风六式成年男性、黑高髻、浅色交领短袍、灰裤、黑布鞋；古籍墨线淡彩。
技术：内置图像工具绘制关键姿态；本地光流补帧；静态纸张背景；不使用淡入淡出模拟动作。
准确性边界：AI重绘及光流可能改变手指或衣袖细节；这是动作可视化样片，尚非经拳师逐帧校订的完整教学资料。
''')
print('已生成46帧来源参考与3张16格姿态参考页。')
