"""把正常浏览器实见截图排成有时码的16格参考，不生成动作。"""
import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FONT = '/System/Library/Fonts/Supplemental/Songti.ttc'


def read_frames(batch=None):
    result = []
    folders = ('captured_main_'+batch,) if batch else ('captured_main_a', 'captured_main_b')
    for folder in folders:
        directory = HERE / 'source' / folder
        path = directory / 'frames.json'
        if not path.exists():
            continue
        for frame in json.loads(path.read_text()):
            file = directory / frame['file']
            if not file.is_file():
                raise FileNotFoundError(file)
            result.append({**frame, 'path': str(file)})
    result.sort(key=lambda item: item['actual_time'])
    unique = []
    for item in result:
        if not unique or item['actual_time'] - unique[-1]['actual_time'] > .15:
            unique.append(item)
    return unique


def sheet(frames, output):
    if len(frames) != 16:
        raise ValueError('参考页须有16格')
    canvas = Image.new('RGB', (1280, 1280), (238, 235, 225))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(FONT, 17)
    for index, frame in enumerate(frames):
        image = Image.open(frame['path']).convert('RGB')
        # 固定裁去字幕上空及空地，保留主示范者全身和移动空间。
        image = image.crop((0, 150, image.width, image.height))
        image.thumbnail((304, 286), Image.Resampling.LANCZOS)
        x, y = (index % 4) * 320, (index // 4) * 320
        canvas.paste(image, (x + (320-image.width)//2, y+26))
        draw.text((x+9, y+5), f'{index+1:02d} · {frame["actual_time"]:.3f}s', font=font, fill=(40,40,40))
    canvas.save(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--page', type=int, required=True)
    parser.add_argument('--batch', choices=('a', 'b'))
    parser.add_argument('--allow-final-padding', action='store_true')
    args = parser.parse_args()
    frames = read_frames(args.batch)
    start = (args.page-1)*15
    selected = frames[start:start+16]
    if len(selected)<16:
        if not args.allow_final_padding or not selected:
            raise ValueError(f'第{args.page}页参考不够16幅，当前共有{len(frames)}幅')
        selected += [selected[-1]]*(16-len(selected))
    target = HERE/'source'/'reference_pages'
    target.mkdir(exist_ok=True)
    label = f'{args.batch or "all"}-{args.page:02d}'
    file = target/f'reference-{label}.png'
    sheet(selected, file)
    (target/f'reference-{label}.json').write_text(json.dumps(selected, ensure_ascii=False, indent=2)+'\n')
    print(file)


if __name__ == '__main__':
    main()
