"""按真实重合姿势固定整页配准，选回B01中比例正确的侧面稿。"""
import json
from pathlib import Path
import sys
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'pipeline'))
import sprites


def foot_center(cell):
    alpha=np.asarray(cell.getchannel('A'),dtype=float)
    ys,_=np.where(alpha>=24)
    band=alpha[max(0,int(ys.max())-19):]
    weights=np.where(band>=24,band,0)
    return float((weights*np.arange(alpha.shape[1])[None,:]).sum()/weights.sum())


manifest=ROOT/'production_manifest.json'
config=json.loads(manifest.read_text())
changed=0
for segment in config['segments']:
    for key in segment['keyframes']:
        if key.get('source_batch')=='b' and key.get('source_sheet_number')==1 and key['cell']>=6:
            page=next((i for i,p in enumerate(segment['pages'],1) if p['path']=='generated/main-b-01.png'),None)
            if page is None:
                segment['pages'].append({'path':'generated/main-b-01.png','x_shift':0,'y_shift':0,'art_scale':1,
                                        'reference_json':'source/reference_pages/reference-b-01.json','source_batch':'b','source_sheet_number':1})
                page=len(segment['pages'])
            key['page']=page
            key['art_selection_note']='前5格正面v2；6～16格用同源原侧面稿，修正v2后排人体缩小与下一页膨胀'
            changed+=1
sequence=[]
for batch in ['a','b']:
    for index in range(1,12):
        version='-v2' if (batch=='a' and index<=4) or (batch=='b' and index==1) else ''
        name=f'main-{batch}-{index:02d}{version}.png'
        last=6 if index==11 or (batch=='b' and index==1) else 16
        art=.9 if batch=='b' and index==1 else 1
        sequence.append((name,1,last,art))
        if batch=='b' and index==1:
            sequence.append(('main-b-01.png',6,16,1))
previous=None
rows=[]
by_path={}
unit=1254/4
for name,first,last,art in sequence:
    image=Image.open(ROOT/'generated'/name).convert('RGBA')
    cells,metadata=sprites.split_sheet(image)
    scale=unit/(image.height/4)*art
    base=16-metadata['common_padding']*scale
    dx=base if previous is None else previous['last_world_x']-foot_center(cells[first-1])*scale
    row={'page':name,'art_scale':art,'scale':scale,'split_padding':metadata['common_padding'],
         'first_cell':first,'last_cell':last,'absolute_dx':dx,'x_shift':dx-base,
         'last_world_x':foot_center(cells[last-1])*scale+dx}
    rows.append(row)
    by_path['generated/'+name]=row
    previous=row
for segment in config['segments']:
    if segment['kind']!='routine':
        continue
    for page in segment['pages']:
        row=by_path[page['path']]
        page['x_shift']=row['x_shift']
        page['art_scale']=row['art_scale']
        page['alignment_note']='整页固定横移：同源重复姿态脚底20px有效alpha中心；B01新旧稿用同源第6格配准，不逐帧居中'
config['state_dir']='pipeline/build/production-v4'
config['status']='v4_page15_size_fixed_and_supplement_elbow_under_repair'
manifest.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
(ROOT/'qa'/'整页固定配准-v4.json').write_text(json.dumps({'method':'同源重复姿态固定页级配准；B01v2仅前5正面，原B01第6～16修正后排缩小；不新增动作','rows':rows},ensure_ascii=False,indent=2)+'\n')
print({'changed_key_records':changed})
