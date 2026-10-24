from pathlib import Path
import subprocess, json, zipfile

ROOT = Path('/Users/chenjianhu/Documents/Codex/2026-10-23/new-chat')
SOURCE = Path('/Users/chenjianhu/.codex/generated_images/01a169df-245b-70b0-9b49-59af3bc3c705/exec-783216a6-7d1c-4c3f-ab80-dda815554547.png')
OUT = ROOT / 'outputs'
FRAMES = OUT / '松风六式-单帧'
WORK = ROOT / 'work/松风六式预览'
SPECS = [
    ('01-抱元起势.png',28,120,474,430,90),
    ('02-开步分掌.png',520,120,474,430,90),
    ('03-转身云手.png',28,566,474,430,92),
    ('04-弓步推掌.png',520,566,474,430,92),
    ('05-虚步回收.png',28,1012,474,478,75),
    ('06-并步收势.png',520,1012,474,478,75),
]

def run(args):
    subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

for name,x,y,w,h,pad_y in SPECS:
    run(['ffmpeg','-y','-v','error','-i',str(SOURCE),'-vf',f'crop={w}:{h}:{x}:{y},pad=512:576:19:{pad_y}:color=0xe9d5b4','-frames:v','1',str(FRAMES/name)])

entries = []
for _ in range(3):
    for name,*_ in SPECS:
        entries.extend([f"file '{FRAMES/name}'", 'duration 0.5'])
entries.append(f"file '{FRAMES/SPECS[-1][0]}'")
manifest = WORK / 'frames.ffconcat'
manifest.write_text('\n'.join(entries)+'\n')
mp4 = OUT / '松风六式-6关键姿势连播预览.mp4'
run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(manifest),'-t','9','-vf','fps=24','-c:v','libx264','-preset','medium','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(mp4)])
gif = OUT / '松风六式-6关键姿势循环预览.gif'
run(['ffmpeg','-y','-v','error','-i',str(mp4),'-t','3','-filter_complex','fps=2,split[a][b];[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=sierra2_4a','-loop','0',str(gif)])
with zipfile.ZipFile(OUT/'松风六式-6张独立姿势图.zip','w',zipfile.ZIP_DEFLATED) as archive:
    for name,*_ in SPECS:
        archive.write(FRAMES/name,name)

info=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-show_entries','stream=width,height,nb_read_frames,r_frame_rate:format=duration','-of','json',str(mp4)]))
(WORK/'media-info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2))
print(json.dumps({'mp4':str(mp4),'gif':str(gif),'frames':len(SPECS),'probe':info},ensure_ascii=False))
