from pathlib import Path
import subprocess, json, zipfile

ROOT=Path('/Users/chenjianhu/Documents/Codex/2026-10-23/new-chat')
OUT=ROOT/'outputs'
FRAMES=sorted((OUT/'松风六式-单帧').glob('*.png'))
MP4=OUT/'松风六式-6关键姿势连播预览.mp4'
GIF=OUT/'松风六式-6关键姿势循环预览.gif'
results=[]

def probe(path):
    return json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-show_entries','stream=width,height,nb_read_frames,r_frame_rate:format=duration','-of','json',str(path)]))

def raw(path, at=None):
    args=['ffmpeg','-v','error']
    if at is not None: args+=['-ss',str(at)]
    args+=['-i',str(path),'-frames:v','1','-vf','scale=64:72','-pix_fmt','rgb24','-f','rawvideo','-']
    return subprocess.check_output(args)

def check(identifier, description, condition):
    assert condition, identifier+': '+description
    results.append(identifier+' 通过：'+description)

check('TEST-1','输入六格样页 → 拆分 → 得到6张互不相同的独立PNG姿势图',len(FRAMES)==6 and len({p.read_bytes() for p in FRAMES})==6)
check('TEST-2','输入六张姿势图 → 检查尺寸 → 全部为512×576，无画幅切换',all((p['streams'][0]['width'],p['streams'][0]['height'])==(512,576) for p in map(probe,FRAMES)))
source=[raw(p) for p in FRAMES]
errors=[]
for loop in range(3):
    for i in range(6):
        pixels=raw(MP4,loop*3+i*0.5+0.25)
        scores=[sum(abs(a-b) for a,b in zip(pixels,reference))/len(pixels) for reference in source]
        assert scores.index(min(scores))==i, f'姿势顺序异常: {loop}, {i}, {scores}'
        errors.append(scores[i])
check('TEST-3','输入MP4 → 检查每个姿势的中间帧 → 3轮均按01至06播放，无漏帧、倒序或错图',max(errors)<8)
metadata=probe(MP4)
check('TEST-4','输入MP4 → 读取时长和帧数 → 9秒、24fps、216帧',metadata['format']['duration']=='9.000000' and metadata['streams'][0]['nb_read_frames']=='216' and metadata['streams'][0]['r_frame_rate']=='24/1')
subprocess.run(['ffmpeg','-v','error','-i',str(MP4),'-f','null','-'],check=True,capture_output=True)
check('TEST-5','输入MP4 → 从头完整解码 → 无解码错误',True)
gifmeta=probe(GIF)
check('EXTRA-1','输入GIF → 检查帧数与时长 → 6个关键帧、3秒一轮',gifmeta['streams'][0]['nb_read_frames']=='6' and gifmeta['format']['duration']=='3.000000')
with zipfile.ZipFile(OUT/'松风六式-6张独立姿势图.zip') as archive:
    check('EXTRA-2','输入ZIP → 校验清单与CRC → 恰好6张PNG，中文文件名正确，文件无损坏',archive.testzip() is None and archive.namelist()==[p.name for p in FRAMES])
last=subprocess.check_output(['ffmpeg','-v','error','-i',str(MP4),'-vf','select=eq(n\\,215),scale=64:72','-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'])
last_error=sum(abs(a-b) for a,b in zip(last,source[-1]))/len(last)
check('EXTRA-3','输入视频末帧 → 对比第06姿势 → 结尾为收势，无黑屏',last_error<8)
report='''松风六式：单帧与播放预览校验报告

本次交付：6张512×576独立PNG、9秒MP4（每姿势0.5秒，重复3轮）、3秒循环GIF、PNG压缩包。
已人工查看六帧检查图：每张包含一个完整人物，头、手、脚未被裁断，未混入相邻格人物。
当前是6个关键姿势的放映演示，未生成过渡姿势；MP4的24fps是编码帧率，不代表每秒24张不同动作。
原绘制人物存在少量位置与比例变化，因此不将本版描述为流畅的逐帧动作动画。

'''+ '\n'.join(results)+f'\n\n抽样视频与姿势图的最大平均像素误差：{max(errors):.3f}（允许有损编码差异）。\n\nBug清单：未发现文件、顺序、尺寸、时长或解码问题。\n未能使用WORKFLOW.md报告模板：当前目录未提供该文件。\nHyperFrames CLI未安装且npm缓存不存在；本版使用本机FFmpeg拆图与编码，未完成HyperFrames专用检查。\n'
(OUT/'松风六式-预览校验说明.txt').write_text(report)
print('\n'.join(results))
