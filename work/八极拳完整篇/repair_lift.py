"""对已确认破损的小区间使用实拍密集控制，首尾复用原图。"""
import argparse
import csv
import json
from pathlib import Path
import shutil
import sys
import numpy as np
from PIL import Image

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'pipeline'))
import batch
import sprites
SEGMENT_ID='supp-02'


def foot_x(image):
    alpha=np.asarray(image.getchannel('A'),dtype=float)
    ys,_=np.where(alpha>=24)
    band=alpha[max(0,int(ys.max())-19):]
    weights=np.where(band>=24,band,0)
    return float((weights*np.arange(alpha.shape[1])[None,:]).sum()/weights.sum())


def validate_interval(segment,override):
    start,end=override['output_frames']
    first,last=override['original_boundary_keys']
    if any(isinstance(v,bool) or not isinstance(v,int) for v in [start,end,first,last]):
        raise ValueError('修复帧及关键图序号必须为整数')
    if not 1<=start<end<=segment['frames'] or not 1<=first<last<=len(segment['keyframes']):
        raise ValueError('修复帧区间/边界关键图越界')
    count=override.get('control_count',16)
    if isinstance(count,bool) or not isinstance(count,int) or not 3<=count<=16:
        raise ValueError('控制格数必须3～16')
    if override.get('registration') not in [None,'boundary_affine']:
        raise ValueError('未知边界配准方式')


def boundary_affine(first,last,target_first,target_last,t):
    """只用相同源姿势的首末锚估计画稿单位，线性补偿，不按每张中间图bbox缩放。"""
    transforms=[]
    for source,target in [(first,target_first),(last,target_last)]:
        sx=(target[2]-target[0])/(source[2]-source[0])
        sy=(target[3]-target[1])/(source[3]-source[1])
        transforms.append((sx,sy,target[0]-source[0]*sx))
    return tuple(a*(1-t)+b*t for a,b in zip(*transforms))


def external_override(config, path):
    plan=json.loads(Path(path).read_text())
    if plan['base_manifest_sha256']!=batch.fingerprint(config) or plan['segment']!=SEGMENT_ID:
        raise ValueError('补充修复计划与已冻结清单或分招不符')
    override=plan['override']
    segment=next(s for s in config['segments'] if s['id']==SEGMENT_ID)
    validate_interval(segment,override)
    for key in ['control_page','control_mapping']:
        if batch.file_hash(batch.resolved(config,override[key]))!=plan[key+'_sha256']:
            raise ValueError('补充修复素材已变更：'+key)
    return {**override,'external_plan_sha256':batch.file_hash(path)}


def controls(config, override=None):
    state,output=batch.locations(config)
    batch.assert_prepared(config)
    segment=next(s for s in config['segments'] if s['id']==SEGMENT_ID)
    override=override if override is not None else segment['interpolation_overrides'][0]
    validate_interval(segment,override)
    page=batch.resolved(config,override['control_page'])
    mapping=json.loads(batch.resolved(config,override['control_mapping']).read_text())
    count=override.get('control_count',16)
    mapping=mapping[:count]
    if [r['cell'] for r in mapping]!=list(range(1,count+1)):
        raise ValueError('实际时码控制格数/序号错误')
    times=[r['source_seconds'] for r in mapping]
    if any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError('控制源时间必须严格递增')
    first_key,last_key=override['original_boundary_keys']
    if abs(times[0]-segment['keyframes'][first_key-1]['source_seconds'])>1e-6 or abs(times[-1]-segment['keyframes'][last_key-1]['source_seconds'])>1e-6:
        raise ValueError('控制首末没有与原关键图对齐')
    image=Image.open(page).convert('RGBA')
    cells,metadata=sprites.split_sheet(image)
    for i in range(1,count-1):
        if any(metadata['cells'][i]['alpha_at_source_page_edge'].values()):
            raise ValueError('使用的控制图被切断')
    original=Image.open(state/SEGMENT_ID/'aligned'/f'{first_key:08d}.png').convert('RGBA')
    box=sprites.alpha_bbox(original)
    first=sprites.alpha_bbox(cells[0])
    # 仅由同一个边界姿势定一次比例；整页全部控制态共用。
    scale=(box[3]-box[1])/(first[3]-first[1])
    dx=foot_x(original)-foot_x(cells[0])*scale
    anchored=override.get('registration')=='boundary_affine'
    if anchored:
        last_original=Image.open(state/SEGMENT_ID/'aligned'/f'{last_key:08d}.png').convert('RGBA')
        last_box=sprites.alpha_bbox(last_original)
        last_control=sprites.alpha_bbox(cells[count-1])
    folder=state/SEGMENT_ID/'dense-repair'
    for child in ['input','output','book-controls']:
        (folder/child).mkdir(parents=True,exist_ok=True)
    static=Image.open(state/SEGMENT_ID/'static-background.png').convert('RGBA')
    for i,cell in enumerate(cells[:count],1):
        if i in [1,count]:
            source_key=first_key if i==1 else last_key
            src=state/SEGMENT_ID/'roi_input'/f'{source_key:08d}.png'
            shutil.copyfile(src,folder/'input'/f'{i:08d}.png')
            continue
        cb=sprites.alpha_bbox(cell)
        if anchored:
            t=(times[i-1]-times[0])/(times[-1]-times[0])
            sx,sy,tx=boundary_affine(first,last_control,box,last_box,t)
            baseline=box[3]*(1-t)+last_box[3]*t
            ty=baseline-cb[3]*sy
            foreground=cell.transform(batch.SIZE,Image.AFFINE,(1/sx,0,-tx/sx,0,1/sy,-ty/sy),resample=Image.BICUBIC)
        else:
            foreground=sprites.transform_rgba(cell,(scale,dx,box[3]-cb[3]*scale),batch.SIZE)
        bounds=sprites.alpha_bbox(foreground)
        if bounds[0]<batch.ROI[0] or bounds[1]<batch.ROI[1] or bounds[2]>batch.ROI[2] or bounds[3]>batch.ROI[3]:
            raise ValueError('控制图超出ROI')
        book=static.copy();book.alpha_composite(foreground)
        book.convert('RGB').save(folder/'book-controls'/f'{i:08d}.png')
        book.convert('RGB').crop(batch.ROI).save(folder/'input'/f'{i:08d}.png')
    start_frame,end_frame=override['output_frames']
    record={'id':override['id'],'segment':SEGMENT_ID,'frames':end_frame-start_frame+1,'output_frames':[start_frame,end_frame],
            'original_boundary_keys':[first_key,last_key],'page':str(page),'page_sha256':batch.file_hash(page),'mapping':mapping,
            'scale':scale,'fixed_dx':dx,'ground_y':box[3],'first_last_reused_from_original':True,
            'note':'额外控制图为实见源帧重绘，首末仍是原精选关键图；同页单一比例，不按每姿态bbox缩放。'}
    if 'external_plan_sha256' in override:
        record['external_plan_sha256']=override['external_plan_sha256']
        record['base_manifest_sha256']=batch.fingerprint(config)
    if anchored:
        record['registration']='boundary_affine'
        record['boundary_boxes']={'generated':[first,last_control],'original':[box,last_box]}
        record['note']='按同源同姿态首末锚估计两端画稿比例，随原片时码线性补偿绘图尺度漂移；中间图不按自身bbox缩放；仅已知支撑竖向落同基线。'
    batch.json_write(folder/'controls.json',record)
    return str(folder)


def apply(config):
    state,output=batch.locations(config)
    batch.assert_prepared(config)
    folder=state/SEGMENT_ID/'dense-repair'
    record=json.loads((folder/'controls.json').read_text())
    results=sorted((folder/'output').glob('*.png'))
    if [p.name for p in results]!=[f'{i:08d}.png' for i in range(1,record['frames']+1)]:
        raise ValueError('修复区间帧数错误，不允许缺图凑数')
    backup=folder/'original-interval';backup.mkdir(exist_ok=True)
    for i,path in enumerate(results,1):
        target=state/SEGMENT_ID/'roi_output'/f'{i+record["output_frames"][0]-1:08d}.png'
        if not (backup/target.name).exists():
            shutil.copyfile(target,backup/target.name)
        with Image.open(path) as rendered:
            rendered.load()
            if rendered.size!=(548,550):
                raise ValueError('控制补间图尺寸不对')
        shutil.copyfile(path,target)
    batch.json_write(folder/'applied.json',{'frames_overwritten':record['output_frames'],'remaining_frames_preserved':True,
                    'first_last_control_source':[record['mapping'][0],record['mapping'][-1]],'output_sha256':{p.name:batch.file_hash(p) for p in results}})
    return '指定小区间已用密集控制替换，原图保留work'


def provenance(config):
    state,output=batch.locations(config)
    folder=state/SEGMENT_ID/'dense-repair'
    record=json.loads((folder/'controls.json').read_text())
    path=output/'分招'/SEGMENT_ID/'来源逐帧映射.csv'
    with path.open(encoding='utf-8-sig') as handle:
        rows=list(csv.DictReader(handle))
    mapping=[]
    controls=record['mapping']
    start,end=controls[0]['source_seconds'],controls[-1]['source_seconds']
    start_frame,end_frame=record['output_frames']
    first_key,last_key=record['original_boundary_keys']
    for index,row in enumerate(rows):
        row.update(control_group='',control_left='',control_right='',control_weight='')
        if not start_frame<=index+1<=end_frame:
            continue
        position=(index+1-start_frame)*(len(controls)-1)/(end_frame-start_frame)
        left=int(np.floor(position));right=min(left+1,len(controls)-1);weight=position-left
        time=controls[left]['source_seconds']*(1-weight)+controls[right]['source_seconds']*weight
        row.update(source_seconds=round(time,7),left_key=first_key,right_key=last_key,weight=round((time-start)/(end-start),7),
                   control_group=record['id'],control_left=left+1,control_right=right+1,control_weight=round(weight,7))
        mapping.append({'output_frame':index+1,'source_seconds':round(time,7),'control_left':left+1,'control_right':right+1,'weight':weight})
    if any(float(b['source_seconds'])<float(a['source_seconds']) for a,b in zip(rows,rows[1:])):
        raise AssertionError('修复来源映射倒退')
    batch.write_csv(path,rows)
    extras=output/'额外过渡控制图'/SEGMENT_ID;extras.mkdir(parents=True,exist_ok=True)
    for i,p in enumerate(sorted((folder/'book-controls').glob('*.png')),1):
        shutil.copyfile(p,extras/f'过渡_{i:02d}.png')
    batch.json_write(output/f'过渡修复来源-{SEGMENT_ID}.json',{'controls':record,'playback_mapping':mapping,'extra_images':len(controls)-2,
                    'selected_key_image_count_unchanged':342,'total_playback_frames_unchanged':4857})
    return {'repaired_source_rows':len(mapping),'extra_images':len(list(extras.glob('*.png')))}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['controls','apply','provenance'])
    parser.add_argument('--segment',default='supp-02')
    parser.add_argument('--plan',type=Path)
    args=parser.parse_args();SEGMENT_ID=args.segment;config=batch.load(HERE/'production_manifest.json')
    if args.plan and args.stage!='controls':parser.error('--plan只能用于controls')
    result=controls(config,external_override(config,args.plan)) if args.plan else globals()[args.stage](config)
    print(json.dumps(result,ensure_ascii=False))
