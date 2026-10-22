/* Dedicated module workspaces and direct administrative publication. */
(() => {
  const E = escapeHtml;
  function noticeButton(module) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'ghost'; button.textContent = '本板块动态与通知';
    button.addEventListener('click',()=>navigate('announcements',{module}));
    return button;
  }
  for (const [key, workspace] of Object.entries(moduleWorkspaces)) {
    titles[key] = workspace.title + '管理';
    renderers[key] = async filters => {
      if (workspace.category) {
        state.filters = {...filters, category: workspace.category};
        await renderers.providers(state.filters);
        content.querySelector('.campus-tabs')?.remove();
        const booking = document.createElement('button');
        booking.className = 'ghost';
        booking.textContent = '查看本板块预约';
        booking.addEventListener('click', () => navigate('bookings', {category: workspace.category}));
        content.querySelector('.toolbar').append(booking);
      } else {
        await renderers.guides();
      }
      const toolbar = document.createElement('div'); toolbar.className = 'toolbar';
      toolbar.append(noticeButton(key)); content.prepend(toolbar);
    };
  }

  function field(name, title, type = 'text', extra = '') {
    return `<label>${title}<input name="${name}" type="${type}" ${extra}></label>`;
  }
  function select(name, title, values) {
    return `<label>${title}<select name="${name}">${values.map(([v,t])=>`<option value="${E(v)}">${E(t)}</option>`).join('')}</select></label>`;
  }
  async function readImage(file) {
    if (!['image/png','image/jpeg','image/webp'].includes(file.type)) throw Error('请选择 PNG、JPEG 或 WebP 图片');
    if (file.size > 3 * 1024 * 1024) throw Error('每张图片不能超过 3MB');
    return new Promise((resolve,reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(Error('图片读取失败'));
      reader.readAsDataURL(file);
    });
  }
  function publish(module) {
    const market = module === 'secondhand';
    const clientId = crypto.randomUUID();
    openDialog(market ? '发布二手信息' : '发布失物招领', `<div class="stack">
      ${select(market?'type':'kind','类型',market?[['sell','出售'],['want','求购']]:[['lost','寻物'],['found','招领']])}
      ${field('title','标题','text',`required maxlength="${market?50:30}"`)}
      ${select('campus','校区',['成都校区','雅安校区','都江堰校区'].map(v=>[v,v]))}
      ${market?select('category','分类',['自行车','书籍','电动车','高速材料','四六级耳机','宿舍用品','数码产品','其他'].map(v=>[v,v])):field('location','遗失 / 拾获地点','text','required maxlength="40"')+field('date','遗失 / 拾获日期','date','required')}
      <label>详细说明<textarea name="description" ${market?'required':''} maxlength="${market?1000:300}"></textarea></label>
      ${market?field('wechat','微信号（与电话至少填一项）','text','maxlength="64"')+field('phone','联系电话','tel','maxlength="32"'):field('contact','联系方式','text','required maxlength="60"')}
      <label>图片（${market?'最多4张':'1张'}，每张最多3MB）<input type="file" name="images" accept="image/png,image/jpeg,image/webp" ${market?'multiple':''}></label>
      <p class="muted">管理员发布后会直接展示在 APP 对应板块，操作会记录到审计日志。</p>
    </div>`, async form => {
      const files = form.getAll('images').filter(f=>f.size);
      if (files.length > (market?4:1)) throw Error(market?'最多上传4张图片':'只可上传1张图片');
      const images = await Promise.all(files.map(readImage));
      const body = {...Object.fromEntries(form), clientId};
      delete body.images;
      if (market) body.images = images;
      else {body.image = images[0] || ''; body.time = '';}
      await api('/api/v1/admin/campus/' + module, {method:'POST', body});
      toast('已发布，APP 刷新对应板块后可见');
      await navigate(market?'marketplace':'communityLost', state.filters);
    }, '确认发布');
  }
  for (const [page, module, title] of [['marketplace','secondhand','发布二手信息'],['communityLost','lost-found','发布失物招领']]) {
    const original = renderers[page];
    renderers[page] = async filters => {
      await original(filters);
      const toolbar = document.createElement('div');
      toolbar.className = 'toolbar';
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'primary'; button.textContent = title;
      button.addEventListener('click',()=>publish(module));
      toolbar.append(button, noticeButton(module)); content.prepend(toolbar);
    };
  }
  addEventListener('hashchange', () => {
    if (!state.admin) return;
    const page = location.hash.slice(1);
    const button = [...document.querySelectorAll('#nav button[data-page]')]
      .find(b=>b.dataset.page===page && !b.classList.contains('hidden'));
    if (button && page !== state.page) navigate(page);
  });
})();
