let previewStream = null;
function showOverlayState(running) {
  document.body.classList.toggle('overlay-active', running);
  $('overlayStatus').textContent = running ? '悬浮回答已开启，请检查共享预览。' : '悬浮窗未开启，回答显示在主页面。';
  $('overlayStart').disabled = running;
  $('overlayStop').disabled = !running;
}
action('overlayStart', async () => {
  await api('overlay/start', {});
  const result = await api('overlay/status');
  if (!result.running) throw Error('悬浮窗启动失败，请检查 Windows 图形桌面环境。');
  showOverlayState(true);
});
action('overlayStop', async () => { await api('overlay/stop', {}); showOverlayState(false); });
action('previewStart', async () => {
  if (!navigator.mediaDevices?.getDisplayMedia) throw Error('当前浏览器不支持屏幕共享预览。');
  const stream = await navigator.mediaDevices.getDisplayMedia({video: {displaySurface: 'monitor'}, audio: false});
  const track = stream.getVideoTracks()[0];
  if (track.getSettings().displaySurface !== 'monitor') {
    stream.getTracks().forEach(t => t.stop());
    throw Error('请在浏览器选择“整个屏幕”，再检查悬浮窗是否出现在预览中。');
  }
  if (previewStream) previewStream.getTracks().forEach(t => t.stop());
  previewStream = stream;
  $('sharePreview').srcObject = stream;
  $('sharePreview').hidden = false;
  $('overlayStatus').textContent = '正在预览整个屏幕。请检查画面中是否出现回答文字。';
  track.onended = () => { if (previewStream === stream) { previewStream = null; $('sharePreview').srcObject = null; $('sharePreview').hidden = true; } };
});
action('previewStop', () => { if (previewStream) previewStream.getTracks().forEach(t => t.stop()); previewStream = null; $('sharePreview').srcObject = null; $('sharePreview').hidden = true; });
api('overlay/status').then(data => showOverlayState(data.running)).catch(() => {});
setInterval(() => api('overlay/status').then(data => {
  if (document.body.classList.contains('overlay-active') !== data.running) showOverlayState(data.running);
}).catch(() => {}), 2000);
