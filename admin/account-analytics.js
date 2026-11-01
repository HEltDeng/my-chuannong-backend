/* Account-only statistics, using the existing 川农 workspace and metric navigation. */
(() => {
  const E = escapeHtml;
  const endpoint = '/api/v1/admin/account-analytics';
  Object.assign(titles, {analytics:'用户与活跃统计', analyticsAccounts:'活跃账号明细'});
  const stamp = value => value ? new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(value) : '尚未收到';
  const nav = (text,page,filter,extra='') => `<button type="button" class="ghost" data-action="metric-nav" data-page="${page}" data-filter="${E(JSON.stringify(filter))}" ${extra}>${E(text)}</button>`;
  function metrics(data) {
    const m=data.metrics, d=data.directory || {};
    const values=[['今日平台活跃',m.dau,{window:'today',scope:'registered'}],['近 7 日平台活跃',m.wau,{window:'7d',scope:'registered'}],['近 30 日平台活跃',m.mau,{window:'30d',scope:'registered'}],['本月平台活跃',m.calendarMonth,{window:'month',scope:'registered'}],
      ['注册用户',d.registered ?? m.registeredAccounts,{scope:'registered'}],['已登录平台账号',d.platform ?? 0,{scope:'platform'}],['仅登录学校账号',d.schoolOnly ?? 0,{scope:'schoolOnly'}],['学校登录学生',d.school ?? 0,{scope:'school'}],
      ['手机号已验证',d.phoneVerified ?? m.phoneVerifiedAccounts,{scope:'phoneVerified'}],['已核验学生',d.verified ?? m.verifiedStudents,{scope:'verified'}],['今日新注册',d.todayRegistrations ?? m.todayRegistrations,{scope:'todayRegistrations'}]];
    return `<div class="grid metric-grid analytics-metrics">${values.map(([label,value,filter])=>`<button type="button" class="card metric metric-link" data-action="metric-nav" data-page="accountDirectory" data-filter="${E(JSON.stringify({...filter,title:label}))}"><span>${label}</span><strong>${value}</strong><span>查看账号 →</span></button>`).join('')}</div>`;
  }
  function chart(rows) {
    const width=900,height=235,left=45,right=20,top=20,bottom=38;
    const max=Math.ceil(Math.max(1,...rows.flatMap(r=>[r.dau||0,r.registrations]))/4)*4;
    const x=i=>left+i*(width-left-right)/Math.max(1,rows.length-1);
    const y=v=>height-bottom-v/max*(height-top-bottom);
    const grid=[0,.25,.5,.75,1].map(t=>`<line x1="${left}" y1="${y(t*max)}" x2="${width-right}" y2="${y(t*max)}" stroke="var(--line)"/><text x="${left-10}" y="${y(t*max)+4}" text-anchor="end">${Number((t*max).toFixed(1))}</text>`).join('');
    const series=[['dau','var(--blue)'],['registrations','#b17b2f']].map(([key,color])=>{
      let pen=false;
      const path=rows.map((r,i)=>{if(r[key]==null){pen=false;return '';}const v=`${pen?'L':'M'} ${x(i)} ${y(r[key])}`;pen=true;return v;}).join(' ');
      return `<path d="${path}" fill="none" stroke="${color}" stroke-width="2.5"/>${rows.map((r,i)=>r[key]==null?'':`<circle cx="${x(i)}" cy="${y(r[key])}" r="3" fill="${color}"><title>${r.day} · ${key==='dau'?'活跃账号':'新注册账号'} ${r[key]}</title></circle>`).join('')}`;
    }).join('');
    const labels=[0,Math.floor((rows.length-1)/2),rows.length-1].map(i=>`<text x="${x(i)}" y="${height-10}" text-anchor="middle">${rows[i].day.slice(5)}</text>`).join('');
    return `<svg class="analytics-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="最近${rows.length}天活跃账号与新注册趋势，逐日数值在下方数据表中">${grid}${series}${labels}</svg>`;
  }
  let analyticsRequest = 0;
  renderers.analytics=async(filters={})=>{
    const request = ++analyticsRequest;
    const days=['7','30','90'].includes(String(filters.days))?String(filters.days):'30';
    const [data,directory]=await Promise.all([api(endpoint+'?days='+days),api('/api/v1/admin/account-directory/metrics')]); data.directory=directory;
    if (state.page !== 'analytics' || request !== analyticsRequest) return;
    content.innerHTML=`<div class="toolbar">${nav('刷新数据','analytics',{days})}<span class="muted">更新于 ${stamp(data.asOf)}</span></div>${metrics(data)}
      <article class="card analytics-trend"><div class="card-header"><div><h3>活跃与注册趋势</h3></div><div class="actions">${['7','30','90'].map(n=>nav('近 '+n+' 天','analytics',{days:n},n===days?'aria-current="true"':'')).join('')}</div></div>
      <p class="analytics-legend"><span>● 活跃账号</span><span>● 新注册账号</span></p>${chart(data.trend)}
      <details><summary>查看逐日数据与账号</summary><div class="table-wrap"><table><thead><tr><th>日期（北京时间）</th><th>活跃账号</th><th>前台上报账号</th><th>新注册账号</th><th>明细</th></tr></thead><tbody>${[...data.trend].reverse().map(r=>`<tr><td>${r.day}</td><td>${r.dau??'未观测'}</td><td>${r.foreground??'未观测'}</td><td>${r.registrations}</td><td>${r.dau==null?'—':nav('查看账号','analyticsAccounts',{window:'day',day:r.day,title:r.day+' 活跃账号'})}</td></tr>`).join('')}</tbody></table></div></details></article>
      <div class="toolbar">${nav('查看账号','accountDirectory',{})}${nav('设备与协议','devicePrivacy',{})}</div>
      <details class="card section-title"><summary>统计口径与数据范围</summary><p class="muted">${E(data.definition)}</p><p class="muted">活跃及设备明细保留 ${data.retentionDays} 天；注销时删除相应记录。设备型号由客户端上报，用于兼容性和设备关联，不能证明唯一真实用户。手机通讯录、短信、IMEI、IMSI、硬件序列号不在本次采集范围内。</p></details>`;
  };
  renderers.analyticsAccounts=filters=>renderers.accountDirectory({...filters,scope:'registered'});
  const overview=renderers.overview;
  renderers.overview=async()=>{
    await overview();
    if(state.admin.adminRole!=='SUPER_ADMIN'||state.page!=='overview')return;
    // Load independently: a failed statistics read must not hide the module workspace.
    const section=document.createElement('section');section.className='analytics-home';
    content.prepend(section);
    try{const [data,directory]=await Promise.all([api(endpoint),api('/api/v1/admin/account-directory/metrics')]);data.directory=directory;if(state.page!=='overview')return;
      section.innerHTML=`<div class="card-header"><h3 class="section-title">用户活跃</h3><div class="actions">${nav('刷新数据','overview',{})}${nav('统计与趋势 →','analytics',{})}</div></div>${metrics(data)}`;
    }catch(error){section.innerHTML=`<p class="muted">活跃数据暂未加载：${E(error.message)} ${nav('重试','overview',{})}</p>`;}
  };
})();

/* Device metadata and evidence of the user's explicit first-use consent. */
(() => {
  titles.devicePrivacy = '设备与协议记录';
  renderers.devicePrivacy = async (filters = {}) => {
    const query = new URLSearchParams({offset: String(filters.offset || 0), platform: filters.platform || ''});
    const data = await api('/api/v1/admin/account-analytics/devices?' + query);
    const link = (label, next) => `<button class="ghost" data-action="metric-nav" data-page="devicePrivacy" data-filter="${escapeHtml(JSON.stringify(next))}">${label}</button>`;
    content.innerHTML = `<div class="toolbar">${['','ios','android'].map(platform => link(platform ? platform === 'ios' ? '苹果' : '安卓' : '全部', {platform})).join('')}</div>
      <article class="card"><h3>设备与协议记录</h3><p>共 ${data.total} 条账号设备记录 · 保留 ${data.retentionDays} 天</p>
      <div class="table-wrap"><table><thead><tr><th>平台账号</th><th>设备</th><th>系统 / 应用</th><th>协议版本</th><th>用户同意时间</th><th>后台首次接收同意记录</th><th>最近活跃</th></tr></thead>
      <tbody>${data.items.map(row => `<tr><td>${escapeHtml(row.realName || row.nickname || "姓名未获取")}<br>${escapeHtml(row.phone || "")}</td><td>${escapeHtml(row.manufacturer)} ${escapeHtml(row.model)}</td><td>${row.platform === "ios" ? "苹果" : "安卓"} ${escapeHtml(row.os_version)}<br>${escapeHtml(row.app_version)} (${row.app_build ?? '—'})</td><td>${escapeHtml(row.policy_version || '旧版 · ' + row.consent_version)}</td><td>${row.consent_accepted_at ? formatTime(row.consent_accepted_at) : '旧版未记录'}</td><td>${row.policy_version ? formatTime(row.consent_received_at) : '旧版未记录'}</td><td>${formatTime(row.last_at)}</td></tr>`).join('') || '<tr><td colspan="7">暂无设备上报</td></tr>'}</tbody></table></div>
      <div class="toolbar">${data.offset ? link('上一页', {...filters, offset: Math.max(0,data.offset-data.limit)}) : ''}${data.offset+data.limit < data.total ? link('下一页', {...filters, offset: data.offset+data.limit}) : ''}</div></article>`;
  };
})();

(() => {
  titles.phoneAuth = '短信验证';
  renderers.phoneAuth = async () => {
    const data = await api('/api/v1/admin/phone-auth');
    const labels = {register:'注册',reset:'找回密码',verify_phone:'验证原手机号'};
    content.innerHTML = `<article class="card"><div class="card-header"><h3>腾讯云短信</h3><strong>${data.configured ? '已配置' : '待配置'}</strong></div>
      <div class="grid metric-grid"><div class="metric"><span>每日发送上限</span><strong>${data.dailyLimit}</strong></div><div class="metric"><span>每账号每日找回次数</span><strong>${data.resetLimit}</strong></div></div>
      <table><thead><tr><th>今日用途</th><th>申请</th><th>提交成功</th><th>计费条数</th><th>验证完成</th></tr></thead><tbody>${data.today.map(r => `<tr><td>${labels[r.purpose] || escapeHtml(r.purpose)}</td><td>${r.requests}</td><td>${r.submitted}</td><td>${r.billedSegments}</td><td>${r.completed}</td></tr>`).join('') || '<tr><td colspan="5">暂无短信记录</td></tr>'}</tbody></table>
      <details><summary>接入与计费说明</summary><p>短信签名、模板及发送密钥由服务端配置。提交成功不等同于最终送达，费用以腾讯云账单为准。余额与续订在腾讯云管理；重试不会自动补发结果未知的请求。</p><p>找回密码每天两次，超限次日恢复；不会删除账号。</p></details></article>`;
  };
})();

/* One directory for school sessions, platform registrations and activity links. */
(() => {
  titles.accountDirectory = '查看账号';
  const E = escapeHtml;
  const nav = (label, filters) => `<button type="button" class="ghost" data-action="metric-nav" data-page="accountDirectory" data-filter="${E(JSON.stringify(filters))}">${E(label)}</button>`;
  renderers.accountDirectory = async (filters = {}) => {
    const query = new URLSearchParams(Object.entries(filters).filter(([key]) => key !== 'title'));
    const data = await api('/api/v1/admin/account-directory?' + query);
    if (!['accountDirectory','analyticsAccounts'].includes(state.page)) return;
    const scopes = [['all','全部'],['registered','注册用户'],['school','学校登录'],['platform','平台已登录'],['schoolOnly','仅学校登录']];
    content.innerHTML = `<div class="toolbar">${scopes.map(([scope,label])=>nav(label,{scope})).join('')}</div>
      <form id="account-directory-filter" class="toolbar"><label>搜索<input name="q" value="${E(filters.q || '')}" placeholder="姓名、完整学号或手机号"></label><button class="primary">搜索</button><span>共 ${data.total} 人</span></form>
      <div class="table-wrap"><table><thead><tr><th>姓名</th><th>年级</th><th>学号</th><th>手机号</th><th>设备</th><th>最近活跃</th><th>平台账号</th><th>操作</th></tr></thead><tbody>
      ${data.items.map(r=>`<tr><td><strong>${E(r.realName || r.nickname || '姓名未获取')}</strong></td><td>${E(r.grade)}</td><td>${E(r.studentId)}</td><td>${E(r.phone)}</td><td>${r.deviceProfile ? `${E(r.deviceProfile.manufacturer)} ${E(r.deviceProfile.model)}<br><span class="subtle">${E(r.deviceProfile.platform === 'ios' ? '苹果' : '安卓')} ${E(r.deviceProfile.os_version)} · ${E(r.deviceProfile.app_version)}</span>` : '未上报'}</td><td>${r.lastActiveAt ? formatTime(r.lastActiveAt) : ''}</td><td>${r.platformSignedIn ? '已登录' : r.accountId ? '已注册' : '未登录'}</td><td>${r.accountId ? `<button type="button" class="ghost" data-action="user-details" data-id="${E(r.accountId)}">查看账号</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="8"><div class="empty">暂无符合条件的学生或账号</div></td></tr>'}
      </tbody></table></div><div class="toolbar">${data.offset ? nav('上一页',{...filters,offset:Math.max(0,data.offset-data.limit)}) : ''}${data.offset+data.limit<data.total ? nav('下一页',{...filters,offset:data.offset+data.limit}) : ''}</div>`;
  };
  document.addEventListener('submit', event => {
    if (event.target.id !== 'account-directory-filter') return;
    event.preventDefault();
    navigate('accountDirectory', {...state.filters,offset:0,q:new FormData(event.target).get('q')});
  });
})();
