const el=id=>document.getElementById(id);
let token=sessionStorage.getItem('companionToken')||'', timer=null, epoch=0;
const pairCode=new URLSearchParams(location.hash.slice(1)).get('pair');
history.replaceState(null,'',location.pathname);
async function poll(version){
  if(version!==epoch||!token)return;
  try{
    const response=await fetch('/view',{headers:{Authorization:'Bearer '+token},cache:'no-store'});
    if(version!==epoch)return;
    if(response.status===401){token='';sessionStorage.removeItem('companionToken');el('question').textContent='配对已失效';el('answer').textContent='';el('answerFor').textContent='';throw Error('配对已失效，请在电脑重新生成二维码。');}
    if(!response.ok)throw Error('电脑暂时无法同步，正在重试。');
    const data=await response.json();
    if(version!==epoch)return;
    el('status').textContent='已连接电脑';
    el('message').textContent=data.desktop_connected?(data.listening?'电脑正在听取提问':'电脑尚未开始实时采集，可在电脑输入问题体验'):'电脑问答页面未连接，请打开电脑页面以触发自动回答。';
    el('question').textContent=data.question||'等待面试官提问…';
    el('answerFor').textContent=data.answer_question?'回答对应：'+data.answer_question:'';
    el('answer').textContent=data.answer||'等待生成建议回答。';
    el('progress').textContent=({generating:'生成中',done:'已完成',cancelled:'已取消',error:'生成失败',idle:'等待'}[data.answer_status]||'等待');
  }catch(error){if(version!==epoch)return;el('status').textContent='连接中断';el('message').textContent=token?'连接中断，正在重试；检查电脑服务及同一 Wi-Fi。':error.message;}
  if(version===epoch&&token)timer=setTimeout(()=>poll(version),700);
}
function reconnect(){clearTimeout(timer);const version=++epoch;if(token)poll(version);else{el('status').textContent='需要配对';el('message').textContent='请在电脑端开启手机显示并扫描新的二维码。';}}
el('retry').onclick=reconnect;
el('disconnect').onclick=()=>{token='';sessionStorage.removeItem('companionToken');reconnect();el('question').textContent='已退出';el('answer').textContent='';el('answerFor').textContent='';};
document.addEventListener('visibilitychange',()=>{if(!document.hidden)reconnect();});
(async()=>{
  if(pairCode){
    try{const response=await fetch('/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:pairCode})});
      const data=await response.json();if(!response.ok)throw Error(data.detail||'配对失败');token=data.token;sessionStorage.setItem('companionToken',token);
    }catch(error){el('status').textContent='配对失败';el('message').textContent=error.message;return;}
  }
  reconnect();
})();
