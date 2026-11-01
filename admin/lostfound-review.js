/* Lost-and-found moderation, using the actual mobile fields and completion progress. */
(() => {
  const base='/api/v1/admin/campus/lost-found', E=escapeHtml;
  const states={PENDING:'待审核',PUBLISHED:'已发布',VIOLATION:'违规'}, kinds={lost:'寻物',found:'招领'};
  const badge=r=>`<span class="market-status ${E(r.status.toLowerCase())}">${states[r.status]||E(r.status)}</span>`;
  const progress=r=>r.done?(r.kind==='lost'?'已找回':'已归还'):'进行中';
  const btn=(action,label,id='',extra='')=>`<button type="button" class="ghost" data-lf-admin="${action}" data-id="${E(id)}" ${extra}>${label}</button>`;
  const actions=r=>`${btn('detail','查看详情',r.id)}${r.status==='PENDING'?btn('approve','通过',r.id)+btn('reject','驳回',r.id):r.status==='PUBLISHED'?btn('take_down','下架',r.id):''}${btn('delete','删除',r.id)}`;
  const options=(values,selected)=>values.map(([v,t])=>`<option value="${v}" ${v===selected?'selected':''}>${t}</option>`).join('');
  const safeImage=r=>/^\/uploads\/[0-9a-f-]+\.(png|jpg|webp)$/.test(r.image||'')?r.image:'';
  const detailDialog=document.createElement('dialog');detailDialog.id='lf-admin-detail';detailDialog.className='market-dialog';document.body.append(detailDialog);
  const imageDialog=document.createElement('dialog');imageDialog.id='lf-admin-image';imageDialog.className='market-image-dialog';document.body.append(imageDialog);
  let detail=null,requestVersion=0;
  renderers.communityLost=async(f={})=>{
    const version=++requestVersion,data=await api(base+'?'+new URLSearchParams({...f,limit:'20'}));
    if(version!==requestVersion)return;
    content.innerHTML=`<form id="lf-admin-filter" class="market-filters" role="search"><label>搜索<span class="market-search"><input name="q" aria-label="搜索关键词" value="${E(f.q||'')}" maxlength="100" placeholder="搜索标题、地点、内容关键词"><button type="submit" aria-label="搜索"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></svg></button></span></label><label>校区<select name="campus" aria-label="校区">${options([['','全部校区'],...['成都校区','雅安校区','都江堰校区'].map(c=>[c,c])],f.campus||'')}</select></label><label>状态<select name="status" aria-label="状态">${options([['','全部状态'],...Object.entries(states)],f.status||'')}</select></label><label>分类<select name="kind" aria-label="分类">${options([['','全部分类'],...Object.entries(kinds)],f.kind||'')}</select></label></form>

    <div class="table-wrap"><table><thead><tr><th>物品信息</th><th>分类 / 进度</th><th>校区 / 地点</th><th>审核状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${data.items.map(r=>`<tr><td><strong>${E(r.title)}</strong><br><span class="subtle">${E(r.description.slice(0,65))}</span>${r.moderationNote?`<p class="market-reason-summary">原因：${E(r.moderationNote)}</p>`:''}</td><td>${kinds[r.kind]}<br><span class="subtle">${progress(r)}</span></td><td>${E(r.campus)}<br><span class="subtle">${E(r.location)}</span></td><td>${badge(r)}</td><td>${formatTime(r.updatedAt)}</td><td><div class="actions">${actions(r)}</div></td></tr>`).join('')||'<tr><td colspan="6"><div class="empty">没有符合条件的信息</div></td></tr>'}</tbody></table></div><div class="campus-pager"><span>共 ${data.total} 条</span>${data.offset?btn('page','上一页',Math.max(0,data.offset-data.limit)):''}${data.offset+data.limit<data.total?btn('page','下一页',data.offset+data.limit):''}</div>`;
  };
  function showDetail(r){
    detail=r;const image=safeImage(r),event=r.kind==='lost'?'遗失':'拾获';
    detailDialog.innerHTML=`<header class="market-dialog-header"><div><h3>手机端展示预览</h3><p>失物招领 · ${kinds[r.kind]}</p></div>${btn('close-detail','关闭',r.id,'aria-label="关闭详情"')}</header><div class="market-detail-scroll"><div class="market-review-note">${badge(r)}<span>${r.status==='PUBLISHED'&&!r.done?'应用公开列表展示中':'应用公开列表不展示'}</span>${r.moderationNote?`<p><strong>处理原因：</strong>${E(r.moderationNote)}</p>`:''}</div><article class="market-phone-preview">${image?`<div class="market-photo-list"><button type="button" data-lf-admin="image" aria-label="预览完整物品照片"><img src="${E(image)}" alt="${E(r.title)}"><span>点击查看完整大图</span></button></div>`:''}<section><span class="market-type">${kinds[r.kind]} · ${progress(r)}</span><h2>${E(r.title)}</h2><div class="market-meta"><span>${E(r.campus)}</span><span>${E(r.location)}</span></div><p>${E(r.date)} · ${event}</p><h4>物品描述</h4><p class="market-body">${E(r.description||'发布人暂未补充描述，可联系了解物品特征。')}</p><p class="subtle">${r.kind==='found'?'领取时请核对物品特征，确认后当面交还。':'如有相关线索，可以通过联系方式联系发布人。'}</p></section><section><h4>联系方式</h4><p class="market-contact"><small>微信号 / 电话</small><span>${E(r.contact)}</span></p><p class="subtle">发布者：${E(r.ownerName||'川农同学')}<br>提交时间：${formatTime(r.createdAt)}${r.time?`<br>时间备注：${E(r.time)}`:''}</p></section></article><p class="source-note">更新时间：${formatTime(r.updatedAt)} · 信息编号：${E(r.id)}</p></div><footer class="market-dialog-footer">${actions(r)}</footer>`;
    detailDialog.querySelector('footer [data-lf-admin=detail]')?.remove();if(!detailDialog.open)detailDialog.showModal();
  }
  async function manage(action,id){
    const row=detailDialog.open&&detail?.id===id?detail:await api(base+'/'+id);
    const label={approve:'通过审核',reject:'驳回信息',take_down:'下架信息',delete:'删除信息'}[action],reason=['reject','take_down'].includes(action);
    openDialog(label,`<div class="stack"><p><strong>${E(row.title)}</strong> · ${kinds[row.kind]}</p>${badge(row)}${reason?'<label>处理原因<textarea name="reason" required maxlength="500" placeholder="请填写具体原因，发布者将在手机端收到此内容"></textarea></label>':`<p>${action==='delete'?'永久删除后，后台、公开列表和用户发布记录都将移除，不能撤销。':row.done?'该信息已完成，通过审核后仍不进入公开列表。':'审核通过后，这条信息将出现在应用公开列表。'}</p>`}</div>`,async data=>{
      const body={action,expectedUpdatedAt:row.updatedAt,reason:String(data.get('reason')||'').trim()};if(reason&&!body.reason)throw Error('请填写具体处理原因');
      await api(base+'/'+id,{method:action==='delete'?'DELETE':'PATCH',...(action==='delete'?{}:{body})});
      if(detailDialog.open){if(action==='delete'){detailDialog.close();imageDialog.close();}else showDetail(await api(base+'/'+id));}
      toast(action==='delete'?'发布记录已删除':'处理已保存，手机端将同步结果');await navigate('communityLost',state.filters);
    },label);
  }
  document.addEventListener('submit',async e=>{if(e.target.id!=='lf-admin-filter')return;e.preventDefault();try{await navigate('communityLost',Object.fromEntries([...new FormData(e.target)].filter(([,v])=>String(v).trim())));}catch(error){toast(error.message,true);}});
  document.addEventListener('change',e=>{if(e.target.closest('#lf-admin-filter')&&e.target.tagName==='SELECT')e.target.form.requestSubmit();});
  document.addEventListener('click',async e=>{
    const b=e.target.closest('[data-lf-admin]');if(!b||b.disabled)return;const action=b.dataset.lfAdmin,id=b.dataset.id;
    try{
      if(action==='detail')showDetail(await api(base+'/'+id));
      else if(action==='image'){imageDialog.innerHTML=`<header class="market-dialog-header"><strong>完整物品照片</strong>${btn('close-image','关闭大图')}</header><div class="market-image-scroll"><img src="${E(safeImage(detail))}" alt="${E(detail.title)}完整照片"></div>`;imageDialog.showModal();}
      else if(action==='close-image')imageDialog.close();
      else if(action==='close-detail')detailDialog.close();
      else if(action==='page')await navigate('communityLost',{...state.filters,offset:id});
      else await manage(action,id);
    }catch(error){toast(error.message,true);if(error.status===404){detailDialog.close();imageDialog.close();}}
  });
})();
