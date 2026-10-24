"""修复工具测试只用临时色块，不代替真人动作或补间视觉验收。"""
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import repair_lift as repair


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.state=self.root/'state';self.output=self.root/'output'
        self.folder=self.state/'supp-02/dense-repair'
        (self.folder/'output').mkdir(parents=True)
        (self.folder/'book-controls').mkdir()
        (self.state/'supp-02/roi_output').mkdir()
        (self.output/'分招/supp-02').mkdir(parents=True)
        self.locations=patch.object(repair.batch,'locations',return_value=(self.state,self.output));self.locations.start()
        self.prepared=patch.object(repair.batch,'assert_prepared',return_value={});self.prepared.start()
        repair.SEGMENT_ID='supp-02'
        self.record={'id':'fixture','frames':2,'output_frames':[2,3],'original_boundary_keys':[1,2],
                     'mapping':[{'source_seconds':.2},{'source_seconds':.4},{'source_seconds':.6}]}
        (self.folder/'controls.json').write_text(json.dumps(self.record))
        self.before={}
        for i in range(1,7):
            p=self.state/'supp-02/roi_output'/f'{i:08d}.png'
            Image.new('RGB',(548,550),(i,i,i)).save(p);self.before[i]=p.read_bytes()
        for i in range(1,3):
            Image.new('RGB',(548,550),(100+i,100+i,100+i)).save(self.folder/'output'/f'{i:08d}.png')

    def tearDown(self):
        self.locations.stop();self.prepared.stop();self.temp.cleanup()

    def test_only_requested_frames_replaced_and_originals_backed_up(self):
        """SPEC-R1 输入6帧与修复2～3 → apply → 仅2～3变化，原图备份可还原。"""
        repair.apply({})
        for i in [1,4,5,6]:
            self.assertEqual((self.state/'supp-02/roi_output'/f'{i:08d}.png').read_bytes(),self.before[i])
        for i in [2,3]:
            self.assertEqual((self.folder/'original-interval'/f'{i:08d}.png').read_bytes(),self.before[i])
            self.assertNotEqual((self.state/'supp-02/roi_output'/f'{i:08d}.png').read_bytes(),self.before[i])

    def test_missing_repair_frame_fails_before_any_output_change(self):
        """EXTRA-R1 输入少末帧修复 → apply → 拒绝，原6帧逐字节不变。"""
        (self.folder/'output/00000002.png').unlink()
        with self.assertRaises(ValueError):repair.apply({})
        for i in range(1,7):
            self.assertEqual((self.state/'supp-02/roi_output'/f'{i:08d}.png').read_bytes(),self.before[i])

    def test_provenance_times_and_outside_interval_preserved(self):
        """SPEC-R2 输入实际控制时码 → 写CSV → 修复范围时码递增，其余源映射保留。"""
        self.record['frames']=3;self.record['output_frames']=[2,4]
        (self.folder/'controls.json').write_text(json.dumps(self.record))
        rows=[{'local_frame':i+1,'source_seconds':i*.2,'left_key':1,'right_key':2,'weight':i/5} for i in range(6)]
        path=self.output/'分招/supp-02/来源逐帧映射.csv';repair.batch.write_csv(path,rows)
        Image.new('RGB',(640,800),'white').save(self.folder/'book-controls/00000002.png')
        result=repair.provenance({})
        self.assertEqual(result,{'repaired_source_rows':3,'extra_images':1})
        with path.open(encoding='utf-8-sig') as handle:after=list(csv.DictReader(handle))
        self.assertEqual([float(r['source_seconds']) for r in after],[0,.2,.4,.6,.8,1])
        self.assertEqual([r['control_group'] for r in after],['','fixture','fixture','fixture','',''])
        self.assertEqual(after[1]['control_left'],'1');self.assertEqual(after[3]['control_right'],'3')

    def test_interval_bounds_and_control_count(self):
        """EXTRA-R2 输入0/负数/小数/倒序/越界 → 校验 → 制作前拒绝。"""
        segment={'frames':20,'keyframes':[{},{}]}
        good={'output_frames':[1,20],'original_boundary_keys':[1,2],'control_count':13}
        repair.validate_interval(segment,good)
        cases=[('output_frames',[0,20]),('output_frames',[-1,20]),('output_frames',[1,21]),('output_frames',[2,1]),
               ('output_frames',[1.0,20]),('original_boundary_keys',[0,2]),('original_boundary_keys',[1,3]),
               ('control_count',0),('control_count',2),('control_count',17),('control_count',True)]
        for field,value in cases:
            with self.subTest(field=field,value=value):
                case=copy.deepcopy(good);case[field]=value
                with self.assertRaises(ValueError):repair.validate_interval(segment,case)

    def test_external_plan_binds_manifest_segment_and_material_hashes(self):
        """EXTRA-R3 输入独立修复计划 → 校验 → 绑定原清单/分招/两素材哈希，不修改原清单。"""
        config={'_manifest':str(self.root/'manifest.json'),'segments':[{'id':'supp-02','frames':20,'keyframes':[{},{}]}]}
        page=self.root/'page.png';mapping=self.root/'mapping.json'
        page.write_bytes(b'page-fixture');mapping.write_text('[]')
        override={'output_frames':[1,20],'original_boundary_keys':[1,2],'control_count':7,
                  'control_page':str(page),'control_mapping':str(mapping)}
        good={'base_manifest_sha256':repair.batch.fingerprint(config),'segment':'supp-02','override':override,
              'control_page_sha256':repair.batch.file_hash(page),'control_mapping_sha256':repair.batch.file_hash(mapping)}
        plan=self.root/'plan.json';before=copy.deepcopy(config)
        plan.write_text(json.dumps(good));result=repair.external_override(config,plan)
        self.assertEqual(result['control_count'],7);self.assertEqual(config,before)
        self.assertEqual(result['external_plan_sha256'],repair.batch.file_hash(plan))
        for key in ['base_manifest_sha256','segment','control_page_sha256','control_mapping_sha256']:
            bad=copy.deepcopy(good);bad[key]='changed';plan.write_text(json.dumps(bad))
            with self.subTest(key=key),self.assertRaises(ValueError):repair.external_override(config,plan)

    def test_boundary_registration_matches_same_pose_anchors_without_middle_bbox(self):
        """EXTRA-R4 输入首末同姿态锚尺寸不同 → 配准 → 两端宽高准确，中途只线性取两锚，不按中间姿态缩放。"""
        first=(10,20,110,220);last=(20,30,220,230)
        original_first=(100,200,250,500);original_last=(130,180,330,500)
        a=repair.boundary_affine(first,last,original_first,original_last,0)
        b=repair.boundary_affine(first,last,original_first,original_last,1)
        self.assertEqual(a,(1.5,1.5,85));self.assertEqual(b,(1.0,1.6,110))
        self.assertEqual(first[0]*a[0]+a[2],original_first[0])
        self.assertEqual(last[2]*b[0]+b[2],original_last[2])
        self.assertEqual(repair.boundary_affine(first,last,original_first,original_last,.5),(1.25,1.55,97.5))


if __name__=='__main__':unittest.main(verbosity=2)
