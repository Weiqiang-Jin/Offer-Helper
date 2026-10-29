(() => {
  let timer=null;
  function show(data){
    $('chatgptEnabled').checked=data.enabled;
    $('chatgptEnabled').disabled=!data.sharing||!data.models.length;
    $('chatgptStatus').textContent=data.connected
      ?`${data.email || '已连接账号'} · ${data.enabled?'正在使用 ChatGPT 计划':data.sharing?'已授权，备用未启用':'尚未授权计划使用'}`
      :'未连接，当前使用原模型配置。';
    if(data.login_error)$('chatgptStatus').textContent=data.login_error;
    const select=$('chatgptModel'), previous=select.value;
    select.replaceChildren();
    for(const model of data.models){const option=document.createElement('option');option.value=model.slug;option.textContent=model.name;select.append(option);}
    if(data.models.some(x=>x.slug===data.model))select.value=data.model;
    else if(data.models.some(x=>x.slug===previous))select.value=previous;
  }
  async function status(){const data=await api('chatgpt');show(data);return data;}
  action('chatgptRefresh',async()=>{show(await api('chatgpt/models',{}));});
  $('chatgptLogin').onclick=async()=>{
    const popup=window.open('about:blank','_blank');
    if(popup)popup.opener=null;
    try{
      const data=await api('chatgpt/login',{});
      if(popup)popup.location.href=data.url;else location.href=data.url;
      $('chatgptStatus').textContent='请在授权页面登录；完成后返回这里。';
      clearInterval(timer);
      const deadline=Date.now()+300000;
      timer=setInterval(async()=>{
        try{const current=await status();if(current.login_error){clearInterval(timer);}else if(current.connected&&!current.pending){clearInterval(timer);if(current.sharing)show(await api('chatgpt/models',{}));}}
        catch(e){clearInterval(timer);notice(e.message);}
        if(Date.now()>deadline)clearInterval(timer);
      },2000);
    }catch(e){if(popup)popup.close();notice(e.message);}
  };
  async function select(){
    try{show(await api('chatgpt/select',{enabled:$('chatgptEnabled').checked,model:$('chatgptModel').value}));}
    catch(e){notice(e.message);await status();}
  }
  $('chatgptEnabled').onchange=select;
  $('chatgptModel').onchange=()=>{if($('chatgptEnabled').checked)select();};
  action('chatgptLogout',async()=>{clearInterval(timer);show(await api('chatgpt/logout',{}));});
  status().catch(()=>{$('chatgptStatus').textContent='请重启后台服务并刷新页面，以加载 ChatGPT 登录功能。';});
})();
