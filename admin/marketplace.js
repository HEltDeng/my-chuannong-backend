/* Marketplace moderation: shared workflow for sell and wanted posts. */
(() => {
  const base='/api/v1/admin/campus/secondhand', E=escapeHtml;
  const labels={PENDING:'待审核',PUBLISHED:'已发布',VIOLATION:'违规'};
  const types={sell:'出售',want:'求购'};
  const badge=status=>`<span class="market-status ${E(status.toLowerCase())}">${labels[status]||E(status)}</span>`;
  const btn=(action,label,id,extra='')=>`<button type="button" class="ghost" data-market="${action}" data-id="${E(id)}" ${extra}>${label}</button>`;
  const options=(values,selected)=>values.map(([v,t])=>`<option value="${v}" ${v===selected?'selected':''}>${t}</option>`).join('');
  const actions=r=>`${btn('detail','查看详情',r.id)}${r.status==='PENDING'?btn('approve','通过',r.id)+btn('reject','驳回',r.id):r.status==='PUBLISHED'?btn('take_down','下架',r.id):''}${btn('delete','删除',r.id)}`;
  const detailDialog=document.createElement('dialog');detailDialog.id='market-detail-dialog';detailDialog.className='market-dialog';document.body.append(detailDialog);
  const imageDialog=document.createElement('dialog');imageDialog.id='market-image-dialog';imageDialog.className='market-image-dialog';document.body.append(imageDialog);
  let detail=null,images=[],imageIndex=0;
  const safeImages=r=>(r.images||[]).filter(v=>/^\/uploads\/[0-9a-f-]+\.(png|jpg|webp)$/.test(v));
  renderers.marketplace=async(f={})=>{
    const data=await api(base+'?'+new URLSearchParams({...f,limit:'20'}));
    content.innerHTML=`<form id="market-filter" class="market-filters" role="search"><label>搜索<span class="market-search"><input name="q" value="${E(f.q||'')}" maxlength="100" placeholder="搜索标题、内容关键词"><button type="submit" aria-label="搜索"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></svg></button></span></label><label>校区<select name="campus">${options([['','全部校区'],...['成都校区','雅安校区','都江堰校区'].map(c=>[c,c])],f.campus||'')}</select></label><label>状态<select name="status">${options([['','全部状态'],...Object.entries(labels)],f.status||'')}</select></label><label>分类<select name="type">${options([['','全部分类'],...Object.entries(types)],f.type||'')}</select></label></form>
      <div class="module-note">出售与求购统一审核：待审核 → 已发布 / 违规。只有已发布信息对其他同学展示。</div>
      <div class="table-wrap"><table><thead><tr><th>内容</th><th>分类 / 品类</th><th>校区</th><th>审核状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${data.items.map(r=>`<tr><td><strong>${E(r.title)}</strong><br><span class="subtle">${E(r.description.slice(0,65))}</span>${r.moderationNote?`<p class="market-reason-summary">原因：${E(r.moderationNote)}</p>`:''}</td><td>${types[r.type]}<br><span class="subtle">${E(r.category)}</span></td><td>${E(r.campus)}</td><td>${badge(r.status)}</td><td>${formatTime(r.updatedAt)}</td><td><div class="actions">${actions(r)}</div></td></tr>`).join('')||'<tr><td colspan="6"><div class="empty">没有符合条件的信息</div></td></tr>'}</tbody></table></div><div class="campus-pager"><span>共 ${data.total} 条</span>${data.offset?btn('page','上一页',Math.max(0,data.offset-data.limit)):''}${data.offset+data.limit<data.total?btn('page','下一页',data.offset+data.limit):''}</div>`;
  };
  function showDetail(r){
    detail=r;images=safeImages(r);
    detailDialog.innerHTML=`<header class="market-dialog-header"><div><h3>手机端展示预览</h3><p>真实发布内容 · ${types[r.type]}</p></div>${btn('close-detail','关闭',r.id,'aria-label="关闭详情"')}</header><div class="market-detail-scroll"><div class="market-review-note">${badge(r.status)}<span>${r.status==='PUBLISHED'?'APP 公开区域展示中':'APP 公开区域不展示'}</span>${r.moderationNote?`<p><strong>处理原因：</strong>${E(r.moderationNote)}</p>`:''}</div><article class="market-phone-preview">${images.length?`<div class="market-photo-list">${images.map((src,i)=>`<button type="button" data-market="image" data-index="${i}" aria-label="预览第 ${i+1} 张图片"><img src="${E(src)}" alt="${E(r.title)} · 第 ${i+1} 张"><span>${i+1} / ${images.length} · 点击查看大图</span></button>`).join('')}</div>`:''}<section><span class="market-type">${types[r.type]}</span><h2>${E(r.title)}</h2><div class="market-meta"><span>${E(r.campus)}</span><span>${E(r.category)}</span><time>${formatTime(r.createdAt)}</time></div><p class="market-body">${E(r.description)}</p></section><section><div class="market-owner"><span>${E((r.ownerName||'同学').slice(0,1))}</span><div><strong>${E(r.ownerName||'同学')}</strong><p>${E(r.campus)}</p></div></div><h4>联系方式</h4>${[['微信号',r.wechat],['联系电话',r.phone]].filter(([,v])=>v).map(([t,v])=>`<p class="market-contact"><small>${t}</small><span>${E(v)}</span></p>`).join('')||'<p>未填写联系方式</p>'}</section></article><p class="source-note">更新时间：${formatTime(r.updatedAt)} · 信息编号：${E(r.id)}</p></div><footer class="market-dialog-footer">${actions(r)}</footer>`;
    // The already-open detail action does not need to display itself again.
    detailDialog.querySelector('footer [data-market=detail]')?.remove();
    if(!detailDialog.open)detailDialog.showModal();
  }
  function preview(index){
    imageIndex=Math.max(0,Math.min(images.length-1,index));
    imageDialog.innerHTML=`<header class="market-dialog-header"><strong>完整图片 · ${imageIndex+1} / ${images.length}</strong><div>${btn('previous','上一张','',imageIndex===0?'disabled':'')}${btn('next','下一张','',imageIndex===images.length-1?'disabled':'')}${btn('close-image','关闭大图','')}</div></header><div class="market-image-scroll"><img src="${E(images[imageIndex])}" alt="完整图片 ${imageIndex+1}"></div>`;
    if(!imageDialog.open)imageDialog.showModal();
  }
  async function manage(action,id){
    const row=detailDialog.open&&detail?.id===id?detail:await api(base+'/'+id);
    const label={approve:'通过审核',reject:'驳回信息',take_down:'下架信息',delete:'删除信息'}[action];
    const reason=action==='reject'||action==='take_down';
    openDialog(label,`<div class="stack"><p><strong>${E(row.title)}</strong> · ${types[row.type]}</p>${badge(row.status)}${reason?'<label>处理原因<textarea name="reason" required maxlength="500" placeholder="请说明具体原因，发布者将在手机端收到此内容"></textarea></label>':`<p>${action==='delete'?'删除后将从后台、APP 公开区域和发布者记录中永久移除。':'审核通过后，这条信息将出现在 APP 公开区域。'}</p>`}<p id="market-action-error" role="alert"></p></div>`,async data=>{
      const body={action,expectedUpdatedAt:row.updatedAt,reason:String(data.get('reason')||'').trim()};
      if(reason&&!body.reason){document.getElementById('market-action-error').textContent='请填写具体处理原因';throw Error('请填写具体处理原因');}
      await api(base+'/'+id,{method:action==='delete'?'DELETE':'PATCH',...(action==='delete'?{}:{body})});
      if(detailDialog.open){if(action==='delete')detailDialog.close();else showDetail(await api(base+'/'+id));}
      toast(action==='delete'?'发布记录已删除':'处理已保存，手机端将同步结果');await navigate('marketplace',state.filters);
    },label);
  }
  document.addEventListener('submit',async e=>{if(e.target.id!=='market-filter')return;e.preventDefault();try{await navigate('marketplace',Object.fromEntries([...new FormData(e.target)].filter(([,v])=>String(v).trim())));}catch(error){toast(error.message,true);}});
  document.addEventListener('change',e=>{if(e.target.closest('#market-filter')&&e.target.tagName==='SELECT')e.target.form.requestSubmit();});
  document.addEventListener('click',async e=>{
    const b=e.target.closest('[data-market]');if(!b||b.disabled)return;const action=b.dataset.market,id=b.dataset.id;
    try{
      if(action==='detail')showDetail(await api(base+'/'+id));
      else if(action==='image')preview(Number(b.dataset.index));
      else if(action==='previous')preview(imageIndex-1);
      else if(action==='next')preview(imageIndex+1);
      else if(action==='close-image')imageDialog.close();
      else if(action==='close-detail')detailDialog.close();
      else if(action==='page')await navigate('marketplace',{...state.filters,offset:id});
      else await manage(action,id);
    }catch(error){toast(error.message,true);if(error.status===404&&detailDialog.open)detailDialog.close();}
  });
})();
