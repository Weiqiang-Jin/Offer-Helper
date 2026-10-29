const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function setup(decision){
  const elements=new Map(), requests=[];
  const context=vm.createContext({URLSearchParams, location:{hash:''}, localStorage:{getItem:()=>''},sessionStorage:{getItem:()=>''},
    document:{getElementById(id){if(!elements.has(id))elements.set(id,{value:'',checked:true,style:{},addEventListener(){}});return elements.get(id);}},
    setTimeout(fn){context.timer=fn;return 1;},clearTimeout(){}, Date,
    fakeApi:async(path,body)=>{requests.push({path,body});return path==='question/boundary'?{decision}:{};}});
  const source=fs.readFileSync(require('node:path').join(__dirname,'../web/app.js'),'utf8');
  vm.runInContext(source.slice(0,source.indexOf('const reconnect=')),context);
  vm.runInContext('api=fakeApi;connected=true;',context);
  return {context,requests,run:code=>vm.runInContext(code,context)};
}
for(const decision of ['continue','new','uncertain'])test('late speech: '+decision,async()=>{
  const {context,requests,run}=setup(decision);
  run("apply({type:'transcript',id:'a',role:'remote',text:'请介绍缓存设计',final:true})");
  await run('generateAnswer()');
  run("lastTranscriptAt=0;apply({type:'transcript',id:'b',role:'remote',text:'还需要考虑内存限制',final:true})");
  assert.equal(run('transcriptRows.length'),2);
  await context.timer();
  const generated=requests.filter(x=>x.path==='answer').at(-1).body.question;
  assert.equal(generated,decision==='continue'?'请介绍缓存设计，还需要考虑内存限制':'还需要考虑内存限制');
});
test('new transcript invalidates pending boundary result',async()=>{
  const {context,requests,run}=setup('new');
  run("apply({type:'transcript',id:'a',role:'remote',text:'原问题',final:true})");
  await run('generateAnswer()');
  let resolve;
  context.fakeApi=async(path)=>path==='question/boundary'?await new Promise(r=>resolve=r):{};
  run("api=fakeApi;generating=false;apply({type:'transcript',id:'b',role:'remote',text:'补充',final:true})");
  const pending=context.timer();
  run("apply({type:'transcript',id:'c',role:'remote',text:'继续补充',final:false})");
  resolve({decision:'new'});await pending;
  assert.equal(run('transcriptRows.length'),3);
  assert.equal(requests.filter(x=>x.path==='answer').length,1);
});

for(const status of [404,500])test('boundary failure answers new segment with history: '+status,async()=>{
  const {context,requests,run}=setup('continue');
  run("apply({type:'transcript',id:'a',role:'remote',text:'原问题',final:true})");
  await run('generateAnswer()');
  context.fakeApi=async(path,body)=>{
    if(path==='question/boundary'){const error=new Error('Not Found');error.status=status;throw error;}
    requests.push({path,body});return {};
  };
  run("api=fakeApi;generating=false;apply({type:'transcript',id:'b',role:'remote',text:'补充条件',final:true})");
  await context.timer();
  assert.equal(requests.filter(x=>x.path==='answer').at(-1).body.question,'补充条件');
  assert.notEqual(run("$('questionStatus').textContent"),'判断是否为补充提问…');
});

test('failed answer submission does not advance submitted segments',async()=>{
  const {context,run}=setup('continue');
  context.fakeApi=async()=>{throw new Error('network failure');};
  run("api=fakeApi;apply({type:'transcript',id:'a',role:'remote',text:'原问题',final:true})");
  await assert.rejects(run('generateAnswer()'));
  assert.equal(run('submittedCount'),0);
});

test('prepared background is referenced without resending personal materials',async()=>{
  const {requests,run}=setup('continue');
  run("preparedContextId='prepared-id';$('profile').value='完整个人材料';$('question').value='介绍项目';");
  await run('generateAnswer()');
  const body=requests.find(x=>x.path==='answer').body;
  assert.equal(body.context_id,'prepared-id');
  assert.equal(body.profile,undefined);
  run('backgroundDirty=true');
  await assert.rejects(run('generateAnswer()'));
  assert.equal(requests.filter(x=>x.path==='answer').length,1);
});
