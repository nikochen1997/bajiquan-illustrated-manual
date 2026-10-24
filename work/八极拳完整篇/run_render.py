"""依已经prepare并冻结的本地计划顺序运行RIFE；失败立即停止。"""
import json
from pathlib import Path
import subprocess
import sys
import time

plan_path = Path(sys.argv[1]).resolve()
plan = json.loads(plan_path.read_text())
report_path = plan_path.parent / 'render-progress.json'
results = []
started = time.time()
for index, item in enumerate(plan['commands'], 1):
    args = item['argv'] + ['-g', '0', '-j', '1:2:2']
    folder = Path(args[args.index('-o') + 1])
    log = folder.parent / 'rife.log'
    existing = sorted(folder.glob('*.png'))
    if existing:
        raise RuntimeError('拒绝混用已有RIFE结果：'+str(folder))
    segment_start = time.time()
    print(f'{index}/{len(plan["commands"])} 开始 {item["segment"]} ({item["expected_frames"]}帧)', flush=True)
    with log.open('w') as handle:
        result = subprocess.run(args, cwd=Path(args[0]).parent, stdout=handle, stderr=subprocess.STDOUT)
    files = sorted(folder.glob('*.png'))
    expected = [f'{n:08d}.png' for n in range(1, item['expected_frames']+1)]
    if result.returncode or [p.name for p in files] != expected:
        raise RuntimeError(f'{item["segment"]} RIFE失败，见{log}')
    results.append({'id': item['segment'], 'frames': len(files), 'elapsed_seconds': round(time.time()-segment_start, 2), 'log': str(log)})
    report_path.write_text(json.dumps({'complete': index==len(plan['commands']) and len(sys.argv)<=2, 'segments_done': index, 'total_segments': len(plan['commands']), 'elapsed_seconds': round(time.time()-started, 2), 'results': results}, ensure_ascii=False, indent=2)+'\n')
    print(f'{index}/{len(plan["commands"])} 完成 {item["segment"]}，累计{sum(r["frames"] for r in results)}帧', flush=True)

if len(sys.argv)>2:
    manifest=Path(sys.argv[2]).resolve()
    config=json.loads(manifest.read_text())
    repair=manifest.parent/'repair_lift.py'
    reference_args=plan['commands'][0]['argv']
    binary=reference_args[0]
    model=reference_args[reference_args.index('-m')+1]
    repairs=[]
    for segment in config['segments']:
        if not segment.get('interpolation_overrides'):
            continue
        sid=segment['id']
        print('密集修复 '+sid,flush=True)
        subprocess.run([sys.executable,str(repair),'controls','--segment',sid],check=True)
        folder=plan_path.parent/sid/'dense-repair'
        record=json.loads((folder/'controls.json').read_text())
        if list((folder/'output').glob('*.png')):
            raise RuntimeError('拒绝混用已有密集修复结果 '+sid)
        args=[binary,'-i',str(folder/'input'),'-o',str(folder/'output'),'-n',str(record['frames']),'-m',model,'-g','0','-j','1:2:2','-f','%08d.png']
        with (folder/'rife.log').open('w') as handle:
            subprocess.run(args,cwd=Path(binary).parent,stdout=handle,stderr=subprocess.STDOUT,check=True)
        subprocess.run([sys.executable,str(repair),'apply','--segment',sid],check=True)
        repairs.append({'segment':sid,'frames':record['output_frames'],'control_count':len(record['mapping']),'extra_pose_count':len(record['mapping'])-2})
        print('密集修复完成 '+sid,flush=True)
    saved=json.loads(report_path.read_text())
    saved['dense_repairs']=repairs
    saved['complete']=True
    saved['extra_pose_count']=sum(r['extra_pose_count'] for r in repairs)
    report_path.write_text(json.dumps(saved,ensure_ascii=False,indent=2)+'\n')
