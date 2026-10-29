(() => {
  let revision=0;
  function show(background){
    preparedContextId=background?background.id:'';
    $('backgroundPreview').textContent=background?background.summary:'';
    $('backgroundStatus').textContent=background?'上下文已就绪'+(background.filename?' · '+background.filename:'')+'；后续回答使用精简背景。':'尚未建立面试上下文。';
  }
  function changed(){revision++;backgroundDirty=true;clearTimeout(autoTimer);$('backgroundStatus').textContent='材料已变更，请重新建立面试上下文。';}
  $('resumeFile').addEventListener('change',changed);
  $('profile').addEventListener('input',changed);
  action('prepareBackground',async()=>{
    if(preparingBackground)return;
    const file=$('resumeFile').files[0],profile=$('profile').value;
    if(!file&&!profile.trim())throw Error('请选择简历附件或填写个人材料。');
    if(file&&file.size>5*1024*1024)throw Error('附件不能超过 5 MB。');
    const current=revision;
    preparingBackground=true;clearTimeout(autoTimer);$('prepareBackground').disabled=true;
    $('backgroundStatus').textContent='模型正在预读材料，请稍候…';
    try{
      const data=file?await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('附件读取失败。'));reader.readAsDataURL(file);}):'';
      const result=await api('background',{filename:file?file.name:'',file_data:data,profile});
      if(current!==revision){backgroundDirty=true;$('backgroundStatus').textContent='预读期间材料已变更，请重新建立上下文。';return;}
      backgroundDirty=false;show(result.background);
    }catch(e){$('backgroundStatus').textContent='预读失败：'+e.message;throw e;}
    finally{preparingBackground=false;$('prepareBackground').disabled=false;}
  });
  action('clearBackground',async()=>{
    await api('background/clear',{});revision++;backgroundDirty=false;
    $('resumeFile').value='';$('profile').value='';show(null);
  });
  const initial=revision;
  api('background').then(data=>{if(initial===revision)show(data.background);}).catch(()=>{$('backgroundStatus').textContent='请重启后台以加载材料预读功能。';});
})();
