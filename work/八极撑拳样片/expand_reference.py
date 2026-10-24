from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
import subprocess
work=Path('/Users/chenjianhu/Documents/Codex/2026-10-23/new-chat/work/八极撑拳样片')
video=work/'source/官方八极拳演示.mp4'
font=ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc',22)
for page,start in [(2,15),(3,30)]:
    sheet=Image.new('RGB',(2560,1600),'#ece7df')
    draw=ImageDraw.Draw(sheet)
    for j in range(16):
        i=start+j
        p=work/f'source/reference_frames/full-{i+1:03d}.png'
        subprocess.run(['ffmpeg','-y','-v','error','-ss',f'{6.6+i/10:.3f}','-i',str(video),'-frames:v','1',str(p)],check=True,capture_output=True)
        im=Image.open(p).convert('RGB')
        x=j%4*640;y=j//4*400
        sheet.paste(im,(x,y))
        draw.text((x+12,y+372),f'{i+1:02d}   {6.6+i/10:.2f}s',fill='#241e18',font=font)
    sheet.save(work/f'source/reference-sheet-{page:02d}-full.png')
print('后两页改用完整视频画幅，保留伸拳最远处。')
