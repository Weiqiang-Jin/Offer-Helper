const pairButton=document.getElementById('pairEnable'), revokeButton=document.getElementById('pairDisable');
let qrUrls=[], pairExpiry=null;
function clearPairing(){qrUrls.forEach(URL.revokeObjectURL);qrUrls=[];document.getElementById('pairLinks').replaceChildren();clearTimeout(pairExpiry);}
pairButton.onclick=async()=>{
  pairButton.disabled=true;
  try{
    const data=await api('mobile/enable',{});clearPairing();
    document.getElementById('pairStatus').textContent='配对链接 5 分钟内有效、仅可使用一次。手机与电脑连接同一局域网。';
    document.getElementById('pairFingerprint').textContent='HTTPS 证书 SHA-256：'+data.fingerprint;
    data.links.forEach((link,index)=>{
      const card=document.createElement('div'),img=document.createElement('img'),input=document.createElement('input'),label=document.createElement('p');
      const url=URL.createObjectURL(new Blob([data.qrs[index]],{type:'image/svg+xml'}));qrUrls.push(url);img.src=url;img.alt='手机配对二维码';img.width=200;img.height=200;
      label.textContent='电脑地址：'+data.addresses[index];input.value=link;input.readOnly=true;input.setAttribute('aria-label','手机配对链接');input.onclick=()=>input.select();
      card.append(label,img,input);document.getElementById('pairLinks').append(card);
    });
    pairExpiry=setTimeout(()=>{clearPairing();document.getElementById('pairStatus').textContent='二维码已过期；已配对手机仍可使用，需要新配对请重新生成。';},300000);
  }catch(error){document.getElementById('pairStatus').textContent=error.message;}
  finally{pairButton.disabled=false;}
};
revokeButton.onclick=async()=>{
  revokeButton.disabled=true;
  try{await api('mobile/disable',{});clearPairing();document.getElementById('pairStatus').textContent='手机连接已全部撤销，局域网服务已关闭。';document.getElementById('pairFingerprint').textContent='';}
  catch(error){document.getElementById('pairStatus').textContent=error.message;}
  finally{revokeButton.disabled=false;}
};
