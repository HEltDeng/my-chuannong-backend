/* Account-only statistics, using the existing 川农 workspace and metric navigation. */
(() => {
  const E = escapeHtml;
  const endpoint = '/api/v1/admin/account-analytics';
  Object.assign(titles, {analytics:'用户与活跃统计', analyticsAccounts:'活跃账号明细'});
  const stamp = value => value ? new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(value) : '尚未收到';
  const nav = (text,page,filter,extra='') => `<button type="button" class="ghost" data-action="metric-nav" data-page="${page}" data-filter="${E(JSON.stringify(filter))}" ${extra}>${E(text)}</button>`;
  function metrics(data, compact=false) {
    const m=data.metrics;
    const values=[['今日活跃账号 · DAU',m.dau,'today'],['近 7 日活跃 · WAU',m.wau,'7d'],['近 30 日活跃 · MAU',m.mau,'30d'],['本自然月活跃',m.calendarMonth,'month']];
    return `<div class="grid metric-grid analytics-metrics">${values.map(([label,value,window])=>`<button class="card metric metric-link" data-action="metric-nav" data-page="analyticsAccounts" data-filter="${E(JSON.stringify({window,title:label}))}"><span>${label}</span><strong>${value}</strong><span>查看账号 →</span></button>`).join('')}</div>${compact?'':`<div class="grid metric-grid">${[['现有注册账号',m.registeredAccounts],['可用账号',m.activeAccounts],['今日新注册',m.todayRegistrations],['今日前台上报账号',m.todayForegroundAccounts]].map(([label,value])=>`<div class="card metric"><span>${label}</span><strong>${value}</strong></div>`).join('')}</div>`}`;
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
  renderers.analytics=async(filters={})=>{
    const days=['7','30','90'].includes(String(filters.days))?String(filters.days):'30';
    const data=await api(endpoint+'?days='+days), m=data.metrics;
    content.innerHTML=`<div class="hero-card analytics-hero"><p class="eyebrow">MY CHUANNONG · 用户洞察</p><h3>看见每天回到川农的同学</h3><p>按已登录的平台账号去重，同一账号跨设备只计一次。<br>北京时间 · 更新于 ${stamp(data.asOf)}</p></div>
      ${metrics(data)}
      <article class="card analytics-trend"><div class="card-header"><div><h3>活跃与注册趋势</h3><p class="muted">今日数据持续更新；没有观测记录的历史日期显示为“未观测”。</p></div><div class="actions">${['7','30','90'].map(n=>nav('近 '+n+' 天','analytics',{days:n},n===days?'aria-current="true"':'')).join('')}</div></div>
      <p class="analytics-legend"><span>● 活跃账号</span><span>● 新注册账号</span></p>${chart(data.trend)}
      <details><summary>查看逐日数据与账号</summary><div class="table-wrap"><table><thead><tr><th>日期（北京时间）</th><th>活跃账号</th><th>前台上报账号</th><th>新注册账号</th><th>明细</th></tr></thead><tbody>${[...data.trend].reverse().map(r=>`<tr><td>${r.day}</td><td>${r.dau??'未观测'}</td><td>${r.foreground??'未观测'}</td><td>${r.registrations}</td><td>${r.dau==null?'—':nav('查看账号','analyticsAccounts',{window:'day',day:r.day,title:r.day+' 活跃账号'})}</td></tr>`).join('')}</tbody></table></div></details></article>
      <div class="grid two-columns"><article class="card"><h3>我的川农账号</h3><p class="muted">手机号来自用户注册信息，列表默认脱敏。当前注册尚未接入短信核验，账号数不代表已经核验的自然人人数。</p>${nav('管理注册账号','users',{accountType:'PHONE'})}</article>
      <article class="card"><h3>设备上报覆盖</h3><p><strong>${m.reportedInstallations}</strong> 个安装实例已上报型号</p><p class="muted">前台数据首次观测：${stamp(data.coverage.firstForegroundAt)}。正式新版 APK 发布后，已同意政策并登录的用户在前台使用时上报。型号缺失时显示“未上报”。</p></article></div>
      <details class="card section-title"><summary>统计口径与数据范围</summary><p class="muted">${E(data.definition)}</p><p class="muted">活跃及设备明细保留 ${data.retentionDays} 天；注销时删除相应记录。设备型号由客户端上报，用于兼容性和设备关联，不能证明唯一真实用户。手机通讯录、短信、IMEI、IMSI、硬件序列号不在本次采集范围内。</p></details>`;
  };
  renderers.analyticsAccounts=async(filters={})=>{
    const query=new URLSearchParams({window:filters.window||'today',...(filters.day?{day:filters.day}:{}),offset:String(filters.offset||0)});
    const data=await api(endpoint+'/accounts?'+query);
    content.innerHTML=`<div class="toolbar">${nav('← 返回统计','analytics',{})}<span class="muted">${data.from} 至 ${data.to} · 共 ${data.total} 个账号 · 手机号脱敏</span></div><div class="table-wrap"><table><thead><tr><th>账号</th><th>注册手机号</th><th>最近上报设备</th><th>系统 / APP</th><th>最近活跃</th></tr></thead><tbody>${data.items.map(r=>`<tr><td><strong>${E(r.nickname)}</strong><br><span class="subtle">${E(r.userCode||r.id)}</span></td><td>${E(r.phoneMasked||'未填写')}</td><td>${r.deviceProfile?E(r.deviceProfile.manufacturer+' '+r.deviceProfile.model):'未上报'}</td><td>${r.deviceProfile?E('Android '+r.deviceProfile.android_version+' / '+r.deviceProfile.app_version):'—'}</td><td>${stamp(r.lastActiveAt)}</td></tr>`).join('')||'<tr><td colspan="5"><div class="empty">这个时间范围内还没有符合统计口径的账号记录。</div></td></tr>'}</tbody></table></div><div class="campus-pager">${data.offset?nav('上一页','analyticsAccounts',{...filters,offset:Math.max(0,data.offset-50)}):''}${data.offset+data.limit<data.total?nav('下一页','analyticsAccounts',{...filters,offset:data.offset+50}):''}</div>`;
  };
  const overview=renderers.overview;
  renderers.overview=async()=>{
    await overview();
    if(state.admin.adminRole!=='SUPER_ADMIN'||state.page!=='overview')return;
    // Load independently: a failed statistics read must not hide the module workspace.
    const section=document.createElement('section');section.className='analytics-home';
    content.querySelector('.hero-card')?.after(section);
    try{const data=await api(endpoint);if(state.page!=='overview')return;
      section.innerHTML=`<div class="card-header"><h3 class="section-title">用户活跃</h3>${nav('统计与趋势 →','analytics',{})}</div>${metrics(data,true)}<p class="source-note">按已登录账号统计 · 北京时间 · ${stamp(data.asOf)} 更新</p>`;
    }catch(error){section.innerHTML=`<p class="muted">活跃数据暂未加载：${E(error.message)} ${nav('重试','overview',{})}</p>`;}
  };
})();
