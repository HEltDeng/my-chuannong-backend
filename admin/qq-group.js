(() => {
  titles.qqGroup = 'QQ 聊天群';
  renderers.qqGroup = async () => {
    const value = await api('/api/v1/admin/qq-group');
    content.innerHTML = `<article class="card"><h3>主要设置 · QQ 聊天群</h3>

      <form id="qq-group-form" class="form-grid">
        <label>QQ 群号<input name="groupNumber" inputmode="numeric" maxlength="12" pattern="[1-9][0-9]{4,11}" value="${escapeHtml(value.groupNumber || '')}" placeholder="创建群后填写"></label>
        <label>加群跳转链接<input name="joinUrl" maxlength="2048" value="${escapeHtml(value.joinUrl || '')}" placeholder="QQ 官方加群链接或 mqqapi 唤起链接"></label>
        <label class="checkbox-row"><input name="enabled" type="checkbox" ${value.enabled ? 'checked' : ''}>启用加群入口</label>
        <div class="actions"><button class="primary" type="submit">保存 QQ 群配置</button><span role="status"></span></div>
      </form></article>`;
  };
  content.addEventListener('submit', async event => {
    const form = event.target;
    if (form.id !== 'qq-group-form') return;
    event.preventDefault();
    const button = form.querySelector('button[type=submit]'), message = form.querySelector('[role=status]');
    button.disabled = true;
    const fields = new FormData(form);
    try {
      await api('/api/v1/admin/qq-group', {method: 'PUT', body: {
        enabled: fields.has('enabled'), groupNumber: fields.get('groupNumber'), joinUrl: fields.get('joinUrl')
      }});
      message.textContent = 'QQ 群配置已保存';
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; }
  });
})();
