const $ = id => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
if(fragment.has('token')) { localStorage.setItem('interviewToken', fragment.get('token')); sessionStorage.setItem('interviewToken', fragment.get('token')); history.replaceState(null,'',location.pathname); }
let token = localStorage.getItem('interviewToken') || sessionStorage.getItem('interviewToken') || '';
let socket=null, retryTimer=null;
let hasClip=false;
let connected=false, generating=false, historyRows=[], selected=new Set(), busy=false;
function notice(text) { $('notice').textContent=text; $('notice').hidden=!text; }
async function api(path, body) {
  const response=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok) { const err=await response.json(); throw Error(typeof err.detail==='string'?err.detail:(Array.isArray(err.detail)?err.detail.map(x=>x.msg).join('；'):'请求参数无效')); }
  return response.json();
}
function action(id, handler) { $(id).onclick=async()=>{try{await handler();}catch(e){notice(e.message);}}; }
function controls(){ $('generate').disabled=!connected||generating; $('cancel').disabled=!connected||!generating; $('saveModel').disabled=!connected||generating; for(const id of ['meter','micTest','start','refresh','remoteDevice','selfDevice']) $(id).disabled=!connected||busy; for(const id of ['reset']) $(id).disabled=!connected; $('stop').disabled=!connected||!busy;$('deviceStop').disabled=!connected||!busy; $('micReplay').disabled=!connected||busy||!hasClip; }
let autoTimer=null, questionEdited=false;
function renderHistory(){
  const latest=historyRows.filter(x=>x.role==='remote').at(-1);
  if(latest && !questionEdited){$('question').value=latest.text;$('questionStatus').textContent=latest.final?'提问已定稿':'正在听取…';}
}
function scheduleAnswer(event){
  clearTimeout(autoTimer);
  if(!event.final||!$('autoAnswer').checked)return;
  autoTimer=setTimeout(async()=>{
    if(!connected)return;
    try{if(generating)await api('answer/cancel',{});await generateAnswer();}catch(e){notice(e.message);}
  },900);
}
async function generateAnswer(){
  clearTimeout(autoTimer);
  generating=true;controls();
  try{await api('answer',{question:$('question').value,profile:$('profile').value,demo:$('demo').checked});}
  catch(e){generating=false;controls();throw e;}
}
function apply(event){
  if(event.type==='snapshot'){
    if(event.device_test)showDeviceTest(event.device_test);historyRows=event.history;renderHistory();$('answerFor').textContent=event.question?'回答对应：'+event.question:'针对当前问题给出可直接口述的建议';$('answer').textContent=event.answer||'选取或输入问题后，点击生成回答。';
    if(!$('question').value)$('question').value=event.question;
    generating=event.answer_status==='generating';busy=Object.values(event.audio_status).some(x=>x!=='stopped');
    $('services').textContent=`语音：${event.configured.asr?'已配置':'未配置'} · 模型：${event.configured.llm?'已配置':'未配置'}`;
    for(const role of ['remote','self'])setAudio(role,event.audio_status[role]||'stopped');
    $('answerStatus').textContent=generating?'生成中':'等待操作';controls();
  } else if(event.type==='transcript') {const old=historyRows.find(x=>x.id===event.id);if(old)Object.assign(old,event);else historyRows.push(event);questionEdited=false;renderHistory();$('answerFor').textContent='正在接收新问题；生成后更新下方建议';scheduleAnswer(event);}
  else if(event.type==='model_settings'){showModel(event);$('modelFeedback').textContent='模型设置已更新，下次生成生效。';}
  else if(event.type==='device_test'){showDeviceTest(event);if(event.finished){busy=false;setAudio('remote','stopped');setAudio('self','stopped');}controls();}
  else if(event.type==='level')$(event.role+'Meter').style.width=Math.min(100,Math.sqrt(event.value)*180)+'%';
  else if(event.type==='audio_status'){setAudio(event.role,event.status);}
  else if(event.type==='error'||event.type==='warning')notice(event.message);
  else if(event.type==='answer_start'){generating=true;$('answerFor').textContent='回答对应：'+event.question;$('answer').textContent='';$('answerStatus').textContent=event.demo?'固定模板演示':'生成中';controls();}
  else if(event.type==='answer_delta'){$('answer').textContent+=event.text;}
  else if(event.type==='answer_end'){generating=false;$('answerStatus').textContent=({done:'已完成',cancelled:'已取消',error:'生成失败'}[event.status]||event.status)+` · ${event.elapsed}s`;controls();}
}
function setAudio(role,status){$(role+'Status').textContent=({running:'采集中',starting:'正在启动',stopped:'未采集'}[status]||status);if(status==='stopped')$(role+'Meter').style.width='0%';}
function connect(){
  clearTimeout(retryTimer);
  if(socket){socket.onclose=null;socket.onmessage=null;socket.close();}
  connected=false;clearTimeout(autoTimer); controls();
  token=localStorage.getItem('interviewToken')||sessionStorage.getItem('interviewToken')||'';
  if(!token){notice('请双击 F:\\project2\\start.bat，使用自动打开的新页面。首次连接需要启动链接。');$('connection').textContent='需要启动链接';return;}
  $('connection').textContent='正在连接';
  const ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws`);
  socket=ws;
  ws.onopen=()=>ws.send(JSON.stringify({token}));
  ws.onmessage=e=>{const data=JSON.parse(e.data);if(data.type==='ping'){ws.send('pong');return;}const first=!connected;connected=true;$('connection').textContent='本地服务已连接';if(first){notice('');refresh().catch(e=>notice(e.message));loadModel().catch(e=>notice(e.message));}apply(data);controls();};
  ws.onclose=e=>{clearTimeout(autoTimer);connected=false;$('connection').textContent='连接断开';controls();if(e.code===1008){notice('此页面的连接凭据已失效。请双击 F:\\project2\\start.bat，并使用自动打开的新页面；不要继续操作旧标签页。');return;}notice('本地服务已断开，正在重试。请保持启动窗口打开；服务未启动时请双击 start.bat。');retryTimer=setTimeout(connect,2000);};
}
async function refresh(){const result=await api('devices');for(const role of ['remote','self']){const select=$(role+'Device'),old=select.value;select.replaceChildren();for(const d of result[role]){const option=document.createElement('option');option.value=d.id;option.textContent=d.name;select.append(option);}if([...select.options].some(x=>x.value===old))select.value=old;if(!select.options.length){const option=document.createElement('option');option.value='';option.textContent='未发现设备，请连接耳机或麦克风后刷新';select.append(option);}}}
async function start(transcribe){if(!$('remoteDevice').value)throw Error('请先选择会议使用的播放设备。');busy=true;controls();try{await api('audio/start',{remote:Number($('remoteDevice').value),transcribe});notice(transcribe?'实时转写已请求启动，请确认播放设备显示“采集中”。':'音量测试中：不会上传音频，也不会产生字幕。请播放会议声音，观察音量条。');}catch(e){busy=false;controls();throw e;}}
function showDeviceTest(event){
  hasClip=!!event.has_clip;
  $('testFeedback').textContent=event.message+(event.phase==='recording'?' 剩余 '+Math.ceil(event.remaining)+' 秒':'');
  if(event.level!==undefined)$('selfMeter').style.width=Math.min(100,Math.sqrt(event.level)*180)+'%';
}
async function testDevice(kind){
  const select=$(kind==='record'?'selfDevice':'remoteDevice');
  if(!select.value)throw Error(kind==='record'?'未发现麦克风，请连接后刷新。':'请选择播放设备。');
  busy=true;controls();notice('');
  try{await api('audio/test',{kind,device:Number(select.value)});}
  catch(e){busy=false;controls();$('testFeedback').textContent=e.message;throw e;}
}
action('micTest',()=>testDevice('record'));
action('micReplay',()=>testDevice('replay'));
action('refresh',refresh);action('meter',()=>testDevice('tone'));action('start',()=>start(true));
action('stop',async()=>{clearTimeout(autoTimer);await api('audio/stop',{});busy=false;controls();notice('采集已停止。');});
action('generate',generateAnswer);
action('cancel',()=>{clearTimeout(autoTimer);return api('answer/cancel',{});});
action('reset',async()=>{clearTimeout(autoTimer);await api('reset',{});$('question').value='';questionEdited=false;$('questionStatus').textContent='等待提问';notice('会话已清空。个人材料仍保留在当前页面。');});
$('question').addEventListener('input',()=>{questionEdited=true;clearTimeout(autoTimer);$('questionStatus').textContent='手动编辑';});
$('autoAnswer').addEventListener('change',()=>{clearTimeout(autoTimer);});
document.addEventListener('keydown',e=>{if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();if(!$('generate').disabled)$('generate').click();}});
const reconnect=document.createElement('button');reconnect.id='reconnect';reconnect.className='quiet';reconnect.textContent='重新连接';reconnect.onclick=connect;$('connection').parentElement.append(reconnect);
window.addEventListener('storage',e=>{if(e.key==='interviewToken')connect();});
controls();connect();

function showModel(data){
  $('modelCurrent').textContent=data.model+' · '+(data.has_key?'已配置密钥':'未配置密钥');
  $('modelKey').placeholder=data.has_key?'已配置；留空保留原密钥':'未配置；请在本机输入 API Key';
}
async function loadModel(){const data=await api('model');$('modelName').value=data.model;$('modelUrl').value=data.base_url;showModel(data);}
action('saveModel',async()=>{
  const data=await api('model',{model:$('modelName').value,base_url:$('modelUrl').value,api_key:$('modelKey').value,clear_key:$('modelClearKey').checked,remember:$('modelRemember').checked});
  $('modelKey').value='';$('modelClearKey').checked=false;showModel(data);
  $('modelFeedback').textContent=($('modelRemember').checked?'已保存到本机。':'仅本次运行生效，重启恢复已保存配置。')+' 下次生成使用 '+data.model+'；真实回答请取消勾选固定模板演示。';
});

$('deviceStop').onclick=()=>$('stop').click();
