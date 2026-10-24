from pathlib import Path
from PIL import Image
import numpy as np
import subprocess,json,shutil

ROOT=Path('/Users/chenjianhu/Documents/Codex/2026-10-23/new-chat')
WORK=ROOT/'work/八极撑拳样片'
RUN=WORK/'qa/rife-normalized'
OUT=ROOT/'outputs/八极拳-撑拳样片'
BOX=(46,164,594,714)
PAPER=np.array((241,230,208),dtype=np.int16)

def main():
    source=sorted((RUN/'roi_output').glob('*.png'))
    assert len(source)==240, f'RIFE尚未完成：{len(source)}/240'
    assert [p.name for p in source]==[f'{i:08d}.png' for i in range(1,241)]
    stage=RUN/'final-staged'
    frames=stage/'逐帧PNG'
    keys=stage/'关键姿态PNG'
    frames.mkdir(parents=True,exist_ok=True);keys.mkdir(parents=True,exist_ok=True)
    background=Image.open(RUN/'static-background.png').convert('RGB')
    assert background.size==(640,800)
    for i,p in enumerate(source,1):
        im=Image.open(p).convert('RGB')
        assert im.size==(548,550)
        a=np.asarray(im,dtype=np.int16)
        offset=PAPER-a[0,0]
        a=np.clip(a+offset,0,255)
        paperlike=np.max(np.abs(a-PAPER),axis=2)<=2
        a[paperlike]=PAPER
        frame=background.copy();frame.paste(Image.fromarray(a.astype(np.uint8)),BOX)
        frame.save(frames/f'八极撑拳_{i:04d}.png')
    for i in range(1,47):
        shutil.copyfile(RUN/'input'/f'{i:08d}.png',keys/f'关键姿态_{i:03d}.png')
    video=stage/'八极拳-撑拳-240帧慢速样片.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-framerate','24','-start_number','1','-i',str(frames/'八极撑拳_%04d.png'),'-frames:v','240','-an','-c:v','libx264','-preset','medium','-crf','15','-pix_fmt','yuv420p','-movflags','+faststart',str(video)],check=True)
    subprocess.run(['ffmpeg','-v','error','-i',str(video),'-f','null','-'],check=True)
    archive=WORK/'qa/archive-mci-first-pass'
    archive.mkdir(exist_ok=True)
    for name in ['逐帧PNG','关键姿态PNG','八极拳-撑拳-240帧慢速样片.mp4']:
        p=OUT/name
        if p.exists():
            dest=archive/name
            if dest.exists(): raise FileExistsError('已有MCI归档，停止避免覆盖')
            shutil.move(str(p),str(dest))
        shutil.move(str(stage/name),str(p))
    manifest={
      'version':'rife-v4.6-ground-registered',
      'source_page':'https://www.sport.gov.cn/n20001280/n20001265/n20066978/c20287390/content.html',
      'source_video_segment_seconds':[6.6,11.1],
      'keyframe_count':46,
      'output_frames':240,'fps':24,'duration_seconds':10,'canvas_size':[640,800],
      'interpolation':'RIFE v4.6 (nihui/rife-ncnn-vulkan 20221029), Mac M1 GPU',
      'tool_source':'https://github.com/nihui/rife-ncnn-vulkan/releases/tag/20221029',
      'body_roi':list(BOX),'static_background':str(RUN/'static-background.png'),
      'registration':str(RUN/'registration.json'),
      'notes':['每页单位比例固定，仅页间横向配准；逐姿态竖向平移统一支撑脚落地线。','39–46关键态为收势停顿，复用首站姿以保持同一人物比例。','视频直接由交付PNG顺序编码；整段含神经补间，未经过拳师逐帧校订。']}
    (RUN/'final-render-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print('RIFE最终240PNG与MP4已导出，第一版MCI已移入work归档。')

if __name__=='__main__': main()
