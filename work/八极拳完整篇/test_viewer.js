// 测试生成HTML实际脚本的按钮逻辑；DOM替身不代替浏览器运行验收。
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const path=require('node:path');
const file=path.resolve(__dirname,'../../outputs/八极拳完整篇/打开播放.html');
const html=fs.readFileSync(file,'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const nodes={};
for(const id of ['chapter','speed','back','next','toggle','time'])nodes[id]={value:'1',textContent:''};
const video={paused:false,currentTime:.5,duration:2,playbackRate:1,pause(){this.paused=true},play(){this.paused=false},load(){this.loaded=true}};
nodes.movie=video;
const events={};
vm.runInNewContext(script,{document:{querySelector:s=>nodes[s.slice(1)],addEventListener:(event,fn)=>events[event]=fn}});
let tests=0;
function spec(id,input,action,expected,fn){fn();tests++;process.stdout.write(`${id} ${input} → ${action} → ${expected} PASS\n`)}
spec('VIEW-1','0.5秒播放中','点击下一帧','暂停且恰好前进1/24秒',()=>{nodes.next.onclick();assert.equal(video.paused,true);assert.ok(Math.abs(video.currentTime-(.5+1/24))<1e-10)});
spec('EXTRA-V1','视频起点','上一帧','保持0秒',()=>{video.currentTime=0;nodes.back.onclick();assert.equal(video.currentTime,0)});
spec('EXTRA-V2','末帧','下一帧','停在末帧，显示不超过48帧',()=>{video.currentTime=2-1/24;nodes.next.onclick();assert.ok(Math.abs(video.currentTime-(2-1/24))<1e-10);video.currentTime=2;video.ontimeupdate();assert.match(nodes.time.textContent,/第 48 帧/)});
spec('VIEW-2','选择0.5倍','切换速度','video playbackRate等于0.5',()=>{nodes.speed.value='.5';nodes.speed.onchange();assert.equal(video.playbackRate,.5)});
spec('VIEW-3','选择分招','改变章节','加载对应视频且保留速度',()=>{nodes.chapter.value='分招/routine-001/routine-001.mp4';nodes.chapter.onchange();assert.equal(video.src,nodes.chapter.value);assert.equal(video.loaded,true);assert.equal(video.playbackRate,.5)});
spec('VIEW-4','已暂停','两次点击播放/暂停','先播放再暂停',()=>{video.paused=true;nodes.toggle.onclick();assert.equal(video.paused,false);nodes.toggle.onclick();assert.equal(video.paused,true)});
spec('EXTRA-V3','章节下拉框聚焦','按右箭头','不触发帧跳转',()=>{video.currentTime=.3;events.keydown({target:{tagName:'SELECT'},key:'ArrowRight',preventDefault(){throw Error('不应拦截')}});assert.equal(video.currentTime,.3)});
console.log(JSON.stringify({tests_run:tests,passed:true,browser_runtime_tested:false}));
