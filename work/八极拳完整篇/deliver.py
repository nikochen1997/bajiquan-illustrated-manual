"""只整理已经生成/核对的图片、视频与说明，不生成动作。"""
import argparse
import csv
import html
import json
from pathlib import Path
import shutil
import sys
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE/'pipeline'))
import batch


def keyframes(config):
    state, output = batch.locations(config)
    folder = output/'关键姿态单张PNG'
    folder.mkdir(parents=True, exist_ok=True)
    seen, rows = set(), []
    for segment in config['segments']:
        for local, key in enumerate(segment['keyframes'], 1):
            identity = key['source_sample_id']
            if identity in seen or 'reuse_key' in key:
                continue
            seen.add(identity)
            n = len(rows)+1
            name = f'{n:03d}_{segment["title"]}.png'
            source = state/segment['id']/'input'/f'{local:08d}.png'
            shutil.copyfile(source, folder/name)
            origin = config['sources'][segment['source_id']]
            rows.append({'序号':n, '文件名':name, '分招':segment['id'], '动作':segment['title'], '类别':segment['kind'],
                         '来源采样秒':key['capture_actual_seconds'], '来源URL':origin['url'], '来源采样ID':identity})
    if len(rows) != 342:
        raise AssertionError(f'实采样关键图数量异常：{len(rows)}')
    batch.write_csv(output/'关键姿态来源目录.csv', rows)
    batch.json_write(output/'关键姿态清单.json', rows)
    return rows


def viewer(config):
    _, output = batch.locations(config)
    chapters = [{'title':'完整小八极 · 35段', 'path':'八极拳-完整套路慢放.mp4'}]
    chapters += [{'title':f'{i+1:02d} · {s["title"]}', 'path':f'分招/{s["id"]}/{s["id"]}.mp4'} for i,s in enumerate(config['segments'])]
    options = ''.join(f'<option value="{html.escape(c["path"])}">{html.escape(c["title"])}</option>' for c in chapters)
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>八极拳 · 手绘图谱</title><style>body{margin:0;background:#28251f;color:#eadfc9;font:16px system-ui}main{max-width:1000px;margin:auto;padding:24px;display:grid;grid-template-columns:minmax(280px,520px) 1fr;gap:28px}video{width:100%;max-height:82vh;background:#f1e6d0}h1{font-family:serif;font-size:28px;margin-top:10px}p{line-height:1.7;color:#c8bda9}select,button{font:inherit;padding:10px;margin:5px 0;background:#f1e6d0;border:0;border-radius:3px;color:#332b20}select{max-width:100%;width:100%}a{color:#e6c994}.controls{display:flex;flex-wrap:wrap;gap:8px}small{display:block;margin-top:18px;line-height:1.7;color:#b6ab97}@media(max-width:700px){main{display:flex;flex-direction:column;padding:12px;gap:12px}video{max-height:66vh}}</style>
<main><video id="movie" controls playsinline preload="metadata" src="八极拳-完整套路慢放.mp4" poster="播放封面.png"></video><section><h1>八极拳 · 手绘图谱</h1>
<p>一套完整《小八极》，另列肘法、靠法补充。每张图一个姿势，连续播放。</p><label>选择全套或单招<select id="chapter">OPTIONS</select></label>
<div class="controls"><button id="back">上一帧</button><button id="toggle">播放 / 暂停</button><button id="next">下一帧</button></div>
<label>播放速度<select id="speed"><option value="1">正常播放（已慢放）</option><option value="0.5">再慢一半</option><option value="1.5">1.5倍</option></select></label>
<p id="time">第 1 帧</p><p><a href="关键姿态来源目录.csv">342张关键图目录</a> · <a href="动作目录与来源.txt">动作与来源说明</a> · <a href="测试报告.md">核对记录</a></p>
<small>AI重绘与补间用于视觉观摩；细微拳掌、肩胯和角度未经拳师校订。两仪顶补充是短入桩定式；铁山靠保留练功靶遮挡。解压总包后打开本页即可离线播放。</small></section></main>
<script>const v=document.querySelector('#movie'), c=document.querySelector('#chapter'), speed=document.querySelector('#speed');function step(d){v.pause();v.currentTime=Math.max(0,Math.min(Math.max(0,(v.duration||0)-1/24),v.currentTime+d/24))}c.onchange=()=>{v.src=c.value;v.load();v.playbackRate=Number(speed.value)};document.querySelector('#back').onclick=()=>step(-1);document.querySelector('#next').onclick=()=>step(1);document.querySelector('#toggle').onclick=()=>{if(v.paused)v.play();else v.pause()};speed.onchange=()=>v.playbackRate=Number(speed.value);v.ontimeupdate=()=>{document.querySelector('#time').textContent='第 '+Math.min(Math.max(1,Math.round((v.duration||0)*24)),Math.floor(v.currentTime*24)+1)+' 帧 · '+v.currentTime.toFixed(2)+' 秒'};document.addEventListener('keydown',e=>{if(e.target.tagName==='SELECT')return;if(e.key==='ArrowLeft'){e.preventDefault();step(-1)}if(e.key==='ArrowRight'){e.preventDefault();step(1)}});</script></html>'''.replace('OPTIONS',options)
    (output/'打开播放.html').write_text(page)
    return len(chapters)


def packages(config):
    _, output = batch.locations(config)
    # 全套CSV同步采用已经逐招修正过的密集控制时码。
    full_path=output/'完整来源时间映射.csv'
    with full_path.open(encoding='utf-8-sig') as handle:
        full_rows=list(csv.DictReader(handle))
    local_rows={}
    for segment in config['segments']:
        if segment['kind']!='routine':
            continue
        with (output/'分招'/segment['id']/'来源逐帧映射.csv').open(encoding='utf-8-sig') as handle:
            local_rows[segment['id']]=list(csv.DictReader(handle))
    fields=['source_seconds','left_key','right_key','weight','control_group','control_left','control_right','control_weight']
    for row in full_rows:
        local=local_rows[row['segment']][int(row['local_frame'])-1]
        for field in fields:
            row[field]=local.get(field,'')
    if len(full_rows)!=4469 or any(float(b['source_seconds'])<float(a['source_seconds']) for a,b in zip(full_rows,full_rows[1:])):
        raise AssertionError('整套来源映射数量或时间连续性错误')
    batch.write_csv(full_path,full_rows)
    limit = 350*1024*1024
    key_files = [(p,str(Path('关键姿态单张PNG')/p.name)) for p in sorted((output/'关键姿态单张PNG').glob('*.png'))]
    extra_files=[(p,str(p.relative_to(output))) for p in sorted((output/'额外过渡控制图').rglob('*.png'))]
    repair_receipts=sorted(output.glob('过渡修复来源-*.json'))
    expected_extras=sum(json.loads(p.read_text())['extra_images'] for p in repair_receipts)
    if len(key_files)!=342 or len(extra_files)!=expected_extras:
        raise AssertionError('精选或额外控制图数量与来源记录不符')
    pose_count=len(key_files)+len(extra_files)
    videos = [(output/'八极拳-完整套路慢放.mp4','八极拳-完整套路慢放.mp4')]
    videos += [(output/'分招'/s['id']/(s['id']+'.mp4'),str(Path('分招')/s['id']/(s['id']+'.mp4'))) for s in config['segments']]
    names = ['打开播放.html','播放封面.png','动作目录与来源.txt','关键姿态来源目录.csv','关键姿态清单.json','完整来源时间映射.csv','测试报告.md',
             '本地测试汇总.json','技术核对记录.json','完整篇核对记录.json','修复逐帧核对.json']
    misc = [(output/n,n) for n in names]
    misc += [(p,p.name) for p in repair_receipts]
    main = batch.zip_parts(key_files+extra_files+videos+misc, output/'下载', f'八极拳-{pose_count}张单姿势图与全部视频', limit)
    frames = [(p,str(Path('完整编号逐帧PNG')/p.name)) for p in sorted((output/'完整编号逐帧PNG').glob('*.png'))]
    frames += [(p,str(Path('补充逐帧PNG')/s['id']/p.name)) for s in config['segments'] if s['kind']!='routine' for p in sorted((output/'分招'/s['id']/'逐帧PNG').glob('*.png'))]
    if len(frames)!=4857:
        raise AssertionError(f'全逐帧图片数异常：{len(frames)}')
    full = batch.zip_parts(frames, output/'下载', '八极拳-全部4857张逐帧图', limit)
    for item in main+full:
        with zipfile.ZipFile(output/'下载'/item['file']) as archive:
            if archive.testzip() is not None:
                raise AssertionError('ZIP校验失败：'+item['file'])
    result={'关键姿态总包':main,'全部逐帧图':full,'精选关键图数量':342,'额外过渡控制图数量':len(extra_files),'单姿势图总数':pose_count,'逐帧数量':4857,'主套路帧':4469,'补充帧':388,'fps':24,'main_duration':4469/24,'zip_crc':'passed'}
    batch.json_write(output/'交付清单.json',result)
    return {k:[{'file':v['file'],'bytes':v['bytes'],'entry_count':len(v['entries'])} for v in result[k]] for k in ['关键姿态总包','全部逐帧图']}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['keys','viewer','pack'])
    args=parser.parse_args()
    config=batch.load(HERE/'production_manifest.json',ready=args.stage!='viewer')
    result={'keys':keyframes,'viewer':viewer,'pack':packages}[args.stage](config)
    print(json.dumps(result if args.stage=='pack' else {'count':len(result) if isinstance(result,list) else result},ensure_ascii=False))
