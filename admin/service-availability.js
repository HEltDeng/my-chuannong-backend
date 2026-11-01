/* Only super administrators can change shared service availability. */
(() => {
  titles.serviceAvailability = '服务开放状态';
  renderers.serviceAvailability = async () => {
    const data = await api('/api/v1/admin/service-availability');
    content.innerHTML = `<div class="grid two-columns">${data.items.map(item => {
      const status = item.status;
      if (!data.canManage) return `<article class="card"><h3>${escapeHtml(item.title)}</h3><p>${status === 'OPEN' ? '开放' : '关闭'}</p></article>`;
      return `<form class="card stack service-state-form" data-key="${escapeHtml(item.key)}">
        <div class="card-header"><h3>${escapeHtml(item.title)}</h3>${badge(status)}</div>
        <label>服务状态<select name="status">${options(['OPEN','MAINTENANCE','COMING_SOON','CLOSED'], status)}</select></label>
        <div class="stack state-details ${status === 'OPEN' ? 'hidden' : ''}">
          <label>显示给用户的提示<textarea name="maintenanceNote" maxlength="300" placeholder="例如：正在升级服务，预计今晚恢复">${escapeHtml(item.maintenanceNote)}</textarea></label>
          <label><span class="expected-label">${status === 'MAINTENANCE' ? '预计恢复时间' : '预计上线时间'}（可选）</span><input type="datetime-local" name="expectedRestoreAt" value="${datetimeLocal(item.expectedRestoreAt)}"></label>
        </div>
        <label>修改备注（可选）<input name="changeNote" maxlength="300"></label>
        <div class="subtle">最后更新：${formatTime(item.updatedAt)}</div>
        <button class="primary" type="submit">保存状态</button>
      </form>`;
    }).join('')}</div>`;
  };
  document.addEventListener('change', event => {
    const form = event.target.closest('.service-state-form');
    if (!form || event.target.name !== 'status') return;
    const status = event.target.value;
    form.querySelector('.state-details').classList.toggle('hidden', status === 'OPEN');
    form.querySelector('.expected-label').textContent = (status === 'MAINTENANCE' ? '预计恢复时间' : '预计上线时间') + '（可选）';
  });
  document.addEventListener('submit', async event => {
    const form = event.target.closest('.service-state-form');
    if (!form) return;
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    if (button.disabled) return;
    button.disabled = true;
    const values = Object.fromEntries(new FormData(form));
    try {
      await api('/api/v1/admin/flags/' + encodeURIComponent(form.dataset.key), {method:'PUT', body:{
        changeNote:values.changeNote,
        payload:{status:values.status, maintenanceNote:values.maintenanceNote,
          expectedRestoreAt:values.expectedRestoreAt ? new Date(values.expectedRestoreAt).getTime() : null}
      }});
      toast('已保存，两端应用刷新配置后生效');
      await renderers.serviceAvailability();
    } catch(error) { toast(error.message); button.disabled = false; }
  });
})();

// Synchronize a module closure even while its operator keeps a workspace open.
(() => {
  let checking = false, blocked = false;
  async function synchronize() {
    if (checking || document.hidden || !state.admin || !operatorAnnouncementModule(state.admin.adminRole)) return;
    checking = true;
    try {
      const data = await api('/api/v1/admin/service-availability');
      const closed = data.items.some(item => item.status !== 'OPEN');
      if (closed) {
        document.querySelector('#dialog')?.close?.();
        content.innerHTML = '<div class="empty"><strong>关闭</strong></div>';
        blocked = true;
      } else if (blocked) {
        blocked = false;
        await navigate(state.page, state.filters);
      }
    } catch (_) { /* Server-side guards remain authoritative if connectivity fails. */ }
    finally { checking = false; }
  }
  setInterval(synchronize, 30000);
  document.addEventListener('visibilitychange', synchronize);
})();
