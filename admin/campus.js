/* Current MyChuannong modules. Uses the existing authenticated admin session. */
(() => {
  const base = '/api/v1/admin/campus/';
  const E = escapeHtml;
  const campuses = ['成都校区','雅安校区','都江堰校区'];
  const categories = {driving_school:'驾校',ielts_toefl:'雅思托福',civil_postgrad:'考公考研',gym:'健身房'};
  const labels = {PUBLISHED:'已发布',COMPLETED:'已完成',HIDDEN:'已下架',DELETED:'已删除',DRAFT:'草稿',UNLISTED:'已下架',PENDING:'待处理',CONFIRMED:'已确认',CANCELLED:'已取消',REJECTED:'未通过'};
  const optionsHtml = (values, selected, blank='') => `${blank ? `<option value="">${E(blank)}</option>` : ''}${values.map(v=>`<option value="${E(v)}" ${v===selected?'selected':''}>${E(labels[v]||categories[v]||v)}</option>`).join('')}`;
  const tag = value => `<span class="badge ${['PUBLISHED','COMPLETED','CONFIRMED'].includes(value)?'ok':['PENDING','DRAFT'].includes(value)?'warn':'bad'}">${E(labels[value]||value)}</span>`;
  const button = (action,text,id='',extra='') => `<button type="button" class="ghost" data-campus="${action}" data-id="${E(id)}" ${extra}>${text}</button>`;
  const field = (name,label,value='',type='text') => `<label>${label}<input name="${name}" type="${type}" value="${E(value)}" ${['name','title'].includes(name)?'required':''}></label>`;
  const area = (name,label,value='') => `<label>${label}<textarea name="${name}">${E(value)}</textarea></label>`;
  const picker = (name,label,values,value) => `<label>${label}<select name="${name}">${optionsHtml(values,value)}</select></label>`;
  const safeImage = url => /^\/uploads\/[0-9a-f-]+\.(png|jpg|webp)$/.test(url||'') ? url : '';
  const imageMarkup = url => safeImage(url)?`<img class="provider-image" src="${E(url)}" alt="内容图片">`:'';
  const imageFields = value => `${imageMarkup(value)}<label>图片（最多 3MB）<input name="imageFile" type="file" accept="image/png,image/jpeg,image/webp"></label><label class="checkbox-row"><input name="removeImage" type="checkbox">移除图片</label>`;
  async function imageInput(data, previous='') {
    if(data.has('removeImage'))return '';
    const file=data.get('imageFile');
    if(!file?.size)return previous;
    if(file.size>3*1024*1024)throw Error('图片不能超过 3MB');
    return await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(Error('图片读取失败'));r.readAsDataURL(file);});
  }
  Object.assign(titles,{overview:'校园运营工作台',marketplace:'二手好物',communityLost:'失物招领',providers:'机构与课程',bookings:'预约管理',guides:'校园服务指南',catalogue:'APP 功能对照'});
  const originalOverview=renderers.overview;
  renderers.overview=async()=>{
    if(state.admin.adminRole==='LOST_FOUND_OPERATOR')return navigate('communityLost');
    if(!['SUPER_ADMIN','CONTENT_OPERATOR'].includes(state.admin.adminRole))return originalOverview();
    const data=await api(base+'summary');
    content.innerHTML=`<div class="hero-card"><p class="eyebrow">一起连接校园日常</p><h3>你好，川农运营者</h3><p>成都 · 雅安 · 都江堰<br>从一条校园动态，到一次失物归还，在这里照顾每一份校园日常。</p></div>
      <div class="grid metric-grid">${[['二手好物',data.counts.secondhand,'marketplace'],['失物招领',data.counts['lost-found'],'communityLost'],['已发布机构',data.counts.providers,'providers'],['待处理预约',data.counts.bookings,'bookings']].filter(x=>state.admin.adminRole==='SUPER_ADMIN'||x[2]!=='communityLost').map(([label,n,page])=>`<button class="card metric metric-link" data-campus="go" data-id="${page}"><span>${label}</span><strong>${n}</strong><span>进入管理 →</span></button>`).join('')}</div>
      <h3 class="section-title">常用工作</h3><div class="provider-grid">${[['announcements','发布服务动态','发布、排序和维护 APP 服务页的公告。'],['providers','维护校外园地','管理驾校、雅思托福、考公考研和健身房。'],['guides','更新校园指南','管理跑腿代取、报修、电费的说明与图片。']].map(([page,t,d])=>`<article class="card"><h3>${t}</h3><p class="muted">${d}</p>${button('go','进入管理 →',page)}</article>`).join('')}</div>
      <div class="module-note">课表、成绩、学校账号与校园网络认证由手机直接处理。这里管理 APP 自建内容和服务；校园活动发布已移除。</div><p class="source-note">我的川农 · 校园运营后台</p>`;
  };
  function pager(data){return `<div class="campus-pager"><span class="muted">共 ${data.total} 条</span>${data.offset?button('page','上一页',String(Math.max(0,data.offset-data.limit))):''}${data.offset+data.limit<data.total?button('page','下一页',String(data.offset+data.limit)):''}</div>`;}
  async function postList(module,filters={}){
    const q=new URLSearchParams({...filters,limit:'20'});
    const data=await api(base+module+'?'+q);
    content.innerHTML=`<div class="toolbar"><form id="campus-filter" class="toolbar"><label>搜索<input name="q" value="${E(filters.q||'')}" placeholder="标题或内容关键词"></label><label>校区<select name="campus">${optionsHtml(campuses,filters.campus,'全部校区')}</select></label><label>状态<select name="status">${optionsHtml(['PUBLISHED','COMPLETED','HIDDEN','DELETED'],filters.status,'全部状态')}</select></label><button class="primary">查询</button></form></div>
    <div class="table-wrap"><table><thead><tr><th>内容</th><th>类型</th><th>校区</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${data.items.map(r=>`<tr><td><strong>${E(r.title)}</strong><br><span class="subtle">${E(r.description.slice(0,55))}</span></td><td>${module==='secondhand'?(r.type==='sell'?'出售':'求购'):(r.kind==='lost'?'寻物':'招领')}</td><td>${E(r.campus)}</td><td>${tag(r.status)}</td><td>${formatTime(r.updatedAt)}</td><td><div class="actions">${button('post-detail','详情',r.id)}${r.status!=='DELETED'?button('post-moderate','处理',r.id):''}</div></td></tr>`).join('')||'<tr><td colspan="6"><div class="empty">暂无已提交内容。APP 联机发布后会出现在这里。</div></td></tr>'}</tbody></table></div>${pager(data)}<p class="source-note">只管理已提交到后台的信息。本机历史草稿与示例不会自动上传。</p>`;
  }
  renderers.marketplace=(f)=>postList('secondhand',f);
  renderers.communityLost=(f)=>postList('lost-found',f);
  let providerRows=[];
  renderers.providers=async(f={})=>{
    providerRows=(await api(base+'providers'+(f.category?'?category='+encodeURIComponent(f.category):''))).items;
    content.innerHTML=`<div class="toolbar"><div class="campus-tabs">${button('category','全部','')}${Object.entries(categories).map(([id,name])=>button('category',name,id)).join('')}</div><button class="primary" data-campus="provider-new">新增机构</button></div><div class="provider-grid">${providerRows.map(r=>`<article class="card">${imageMarkup(r.image)||`<div class="provider-placeholder">${E(categories[r.category])}</div>`}<div class="card-header"><div><h3>${E(r.name)}</h3><span class="subtle">${E(r.campus)} · ${E(categories[r.category])}</span></div>${tag(r.status)}</div><p>${E(r.summary||'尚未填写简介')}</p><p class="subtle">${r.isDemo?'演示机构':r.bookingEnabled?'接受预约意向':'暂未开放预约'} · 排序 ${r.sortOrder}</p><div class="actions">${button('provider-edit','编辑资料与课程',r.id)}</div></article>`).join('')||'<div class="empty">尚未配置机构。添加并发布后，APP 可读取机构内容。</div>'}</div>`;
  };
  function courseFields(course={}) {
    return `<div class="sub-card course-editor"><input type="hidden" name="courseId" value="${E(course.id||crypto.randomUUID())}"><div class="stack">${field('courseTitle','课程 / 班型名称',course.title||course.name||'')}${field('coursePrice','参考价格（元，可留空）',course.price??'','number')}${area('courseDescription','课程说明',course.description||'')}${button('course-remove','移除此课程')}</div></div>`;
  }
  function editProvider(row={}){
    openDialog(row.id?'编辑机构':'新增机构',`<div class="stack">${picker('category','板块',Object.keys(categories),row.category||'driving_school')}${field('name','机构名称',row.name)}${picker('campus','校区',campuses,row.campus||campuses[0])}${area('summary','简介',row.summary)}${field('address','地址',row.address)}${field('phone','公开咨询电话',row.phone)}${imageFields(row.image)}${field('sortOrder','显示顺序',row.sortOrder||0,'number')}${picker('status','发布状态',['DRAFT','PUBLISHED','UNLISTED'],row.status||'DRAFT')}<label class="checkbox-row"><input type="checkbox" name="isDemo" ${row.isDemo?'checked':''}>演示内容</label><label class="checkbox-row"><input type="checkbox" name="bookingEnabled" ${row.bookingEnabled?'checked':''}>开放预约意向登记</label>${area('description','详细介绍',row.details?.description)}${field('hours','营业 / 接待时间',row.details?.hours)}${field('pickup','接送说明',row.details?.pickup)}<h4>课程与班型</h4><div id="campus-course-editor" class="grid">${(row.details?.courses||[]).map(courseFields).join('')}</div>${button('course-add','＋ 添加课程 / 班型')}${area('facilities','设施与服务（每行一项）',(row.details?.facilities||[]).join('\n'))}${area('bookingSlots','可预约时段说明（每行一项）',(row.details?.bookingSlots||[]).join('\n'))}</div>`,async data=>{
      const b=Object.fromEntries(data);b.sortOrder=Number(b.sortOrder);b.isDemo=data.has('isDemo');b.bookingEnabled=data.has('bookingEnabled');b.image=await imageInput(data,row.image);
      const ids=data.getAll('courseId'), prices=data.getAll('coursePrice'), descriptions=data.getAll('courseDescription');
      const courses=data.getAll('courseTitle').map((title,i)=>{
        if(!String(title).trim())throw Error('请填写每个课程名称，或移除空课程');
        const course={id:String(ids[i]),title:String(title).trim(),description:String(descriptions[i]||'')};
        if(prices[i]!==''){const price=Number(prices[i]);if(!Number.isFinite(price)||price<0)throw Error('价格必须为非负数字');course.price=price;}return course;
      });
      b.details={description:b.description,hours:b.hours,pickup:b.pickup,courses,facilities:b.facilities.split('\n').map(v=>v.trim()).filter(Boolean),bookingSlots:b.bookingSlots.split('\n').map(v=>v.trim()).filter(Boolean)};
      delete b.imageFile;
      await api(base+'providers'+(row.id?'/'+row.id:''),{method:row.id?'PUT':'POST',body:b});toast('机构资料已保存');await renderers.providers(state.filters);
    });
  }
  let bookingRows=[];
  renderers.bookings=async(f={})=>{
    const data=await api(base+'bookings?'+new URLSearchParams({...f,limit:'20'}));bookingRows=data.items;
    content.innerHTML=`<form id="campus-filter" class="toolbar"><label>预约状态<select name="status">${optionsHtml(['PENDING','CONFIRMED','COMPLETED','CANCELLED','REJECTED'],f.status,'全部状态')}</select></label><button class="primary">查询</button></form><div class="module-note">预约为意向登记，需要运营人员联系确认。未确认前，APP 不会显示预约成功。</div><div class="table-wrap"><table><thead><tr><th>机构 / 课程</th><th>预约日期</th><th>联系人</th><th>状态</th><th>提交时间</th><th>处理</th></tr></thead><tbody>${data.items.map(r=>`<tr><td>${E(r.provider_name)}<br><span class="subtle">${E(r.course)}</span></td><td>${E(r.preferred_date)}</td><td>${E(r.name)}<br>${E(r.phone)}</td><td>${tag(r.status)}</td><td>${formatTime(r.created_at)}</td><td>${button('booking-edit','查看 / 处理',r.id)}</td></tr>`).join('')||'<tr><td colspan="6"><div class="empty">暂无预约记录</div></td></tr>'}</tbody></table></div>${pager(data)}`;
  };
  let guideRows=[];
  renderers.guides=async()=>{
    guideRows=(await api(base+'guides')).items;
    content.innerHTML=`<div class="provider-grid">${guideRows.map(r=>`<article class="card"><div class="card-header"><h3>${E(r.title)}</h3>${tag(r.status)}</div>${imageMarkup(r.image)}<p class="campus-detail">${E(r.body||'尚未填写服务说明')}</p>${button('guide-edit','编辑指南',r.id)}</article>`).join('')}</div><p class="source-note">指南用于说明流程和维护图片，不接收学校账号或密码。</p>`;
  };
  renderers.catalogue=async()=>{
    const data=await api(base+'catalogue');
    const school=new Set(['schedule','grade','exam','electric','ncre','comprehensive_score','weekly_attendance','holiday_destination','cet','mandarin']);
    content.innerHTML=`<div class="hero-card"><h3>与 APP 一一对应</h3><p>${data.navigation.length} 个当前入口 · 三个校区 · 川农绿色主题</p></div><div class="toolbar">${button('catalogue-draft','按当前 APP 生成配置草稿')}</div><div class="table-wrap"><table><thead><tr><th>APP 功能</th><th>所属板块</th><th>管理方式</th><th>标识</th></tr></thead><tbody>${data.navigation.map(r=>`<tr><td>${E(r.title)}</td><td>${E({learning:'学习工具',life:'校园生活',off_campus:'校外园地'}[r.section]||r.section)}</td><td>${school.has(r.actionId)?'手机访问学校系统；后台管理入口':categories[r.actionId]?'机构、课程与预约':r.actionId==='secondhand'||r.actionId==='lost_found'?'内容管理、下架与恢复':r.actionId==='document'?'资料发布与投诉处理':'校园服务指南'}</td><td><code>${E(r.actionId)}</code></td></tr>`).join('')}<tr><td>校园地图</td><td>学校服务</td><td>高德地图与 APP 内置已核验地点</td><td><code>campus_map</code></td></tr><tr><td>校园活动发布</td><td>已移除</td><td>无发布入口；接口返回已移除</td><td>—</td></tr></tbody></table></div><div class="module-note">配置草稿可以在「版本与功能开关」中预览、激活和回滚；学校认证与个人教务数据保持在设备端。</div>`;
  };
  document.addEventListener('submit',async event=>{
    if(event.target.id!=='campus-filter')return;
    event.preventDefault();const f=Object.fromEntries(new FormData(event.target));
    await navigate(state.page,Object.fromEntries(Object.entries(f).filter(([,v])=>v)));
  });
  document.addEventListener('click',async event=>{
    const b=event.target.closest('[data-campus]');if(!b)return;
    const action=b.dataset.campus,id=b.dataset.id;
    try{
      if(action==='course-add'){document.querySelector('#campus-course-editor').insertAdjacentHTML('beforeend',courseFields());return;}
      if(action==='course-remove'){b.closest('.course-editor').remove();return;}
      if(action==='go')return navigate(id);
      if(action==='page')return navigate(state.page,{...state.filters,offset:id});
      if(action==='category')return navigate('providers',id?{category:id}:{});
      if(action==='provider-new')return editProvider();
      if(action==='provider-edit')return editProvider(providerRows.find(r=>r.id===id));
      if(action==='post-detail'){
        const module=state.page==='marketplace'?'secondhand':'lost-found';const r=await api(base+module+'/'+id);
        return openDialog(r.title,`<div class="campus-detail">${tag(r.status)}<p>${E(r.campus)} · ${E(r.location||r.category||'')}</p><p>${E(r.description)}</p><p>${E(r.contact||[r.wechat,r.phone].filter(Boolean).join(' / ')||'暂无联系方式')}</p>${[r.image,...(r.images||[])].filter(Boolean).map(imageMarkup).join('')}<p class="muted">${E(r.moderationNote||'')}</p></div>`,async()=>{},'关闭');
      }
      if(action==='post-moderate'){
        const module=state.page==='marketplace'?'secondhand':'lost-found';
        return openDialog('处理内容',`<div class="stack">${picker('status','处理结果',['HIDDEN','PUBLISHED','COMPLETED'],'HIDDEN')}<label>处理原因<textarea name="reason" maxlength="500" required></textarea></label></div>`,async data=>{await api(base+module+'/'+id,{method:'PATCH',body:Object.fromEntries(data)});toast('处理结果已保存');await navigate(state.page,state.filters);});
      }
      if(action==='booking-edit'){
        const r=bookingRows.find(r=>r.id===id);const next={PENDING:['CONFIRMED','REJECTED','CANCELLED'],CONFIRMED:['COMPLETED','CANCELLED']}[r.status];
        return openDialog('预约详情',`<div class="stack"><p>${E(r.provider_name)} · ${E(r.course)}<br>${E(r.name)} / ${E(r.phone)}<br>${E(r.preferred_date)}</p><p class="campus-detail">${E(r.note)}</p>${next?picker('status','下一状态',next,next[0])+area('reply','给用户的回复',r.reply):`<p>${tag(r.status)} ${E(r.reply)}</p>`}</div>`,async data=>{if(next){await api(base+'bookings/'+id,{method:'PATCH',body:Object.fromEntries(data)});toast('预约已更新');await navigate('bookings',state.filters);}},next?'保存处理':'关闭');
      }
      if(action==='guide-edit'){
        const r=guideRows.find(r=>r.id===id);
        return openDialog('编辑服务指南',`<div class="stack">${field('title','标题',r.title)}${area('body','操作说明',r.body)}${imageFields(r.image)}${field('url','HTTPS 参考链接（可留空）',r.url,'url')}${picker('status','状态',['DRAFT','PUBLISHED','UNLISTED'],r.status)}</div>`,async data=>{const body=Object.fromEntries(data);body.image=await imageInput(data,r.image);delete body.imageFile;await api(base+'guides/'+id,{method:'PUT',body});toast('指南已保存');await renderers.guides();});
      }
      if(action==='catalogue-draft'){
        const data=await api(base+'catalogue');
        await api('/api/v1/admin/app-config/revisions',{method:'POST',body:{config:{navigation:data.navigation,cards:[],remotePages:[],resources:[]},minAppVersionCode:9,maxAppVersionCode:null,reason:'与当前我的川农 APP 功能对齐'}});
        toast('配置草稿已生成，可在版本与功能开关中激活');return navigate('config');
      }
    }catch(error){toast(error.message,true);}
  });
  const icons={overview:'<path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/>',marketplace:'<path d="M4 7h16l-1 14H5L4 7ZM8 7V5a4 4 0 0 1 8 0v2"/>',communityLost:'<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',providers:'<path d="M3 21h18M5 21V7l7-4 7 4v14M9 10h6M9 14h6M10 21v-4h4v4"/>',bookings:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18m-14 5 3 3 6-5"/>'};
  document.querySelectorAll('#nav button').forEach(b=>b.insertAdjacentHTML('afterbegin',`<span class="nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${icons[b.dataset.page]||'<rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 8h8M8 12h8M8 16h5"/>'}</svg></span>`));
})();
