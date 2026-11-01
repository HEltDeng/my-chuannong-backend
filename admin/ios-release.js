/* Native 苹果metadata uses its own route and never changes 安卓APK policy. */
(() => {
  titles.iosRelease='苹果版本管理';
  renderers.iosRelease=async()=>{
    const value=await api('/api/v1/admin/ios-release');
    content.innerHTML=`<article class="card"><h3>我的川农 · 苹果版</h3><p>${value.published?'已公布版本信息':'尚未发布正式版本'}</p>
      <form id="ios-release-form" class="form-grid">
        <label>应用标识<input name="bundleId" value="com.mychuannong.app" readonly></label>
        <label>版本名称<input name="versionName" value="${escapeHtml(value.versionName||'')}" placeholder="1.0.0" pattern="[0-9]+\\.[0-9]+\\.[0-9]+" required></label>
        <label>构建号<input name="buildNumber" type="number" min="1" max="2147483647" value="${Number(value.buildNumber||1)}" required></label>
        <label>最低支持构建号<input name="minimumBuildNumber" type="number" min="1" max="2147483647" value="${Number(value.minimumBuildNumber||1)}" required><span class="subtle">低于此构建号的正式 苹果版本将要求更新。</span></label>
        <label>苹果商店地址<input name="storeUrl" type="url" value="${escapeHtml(value.storeUrl||'')}" placeholder="https://apps.apple.com/cn/app/id…" required></label>
        <label>版本说明<textarea name="releaseNotes" maxlength="4000">${escapeHtml(value.releaseNotes||'')}</textarea></label>
        <label class="checkbox-row"><input name="published" type="checkbox" ${value.published?'checked':''}>向 苹果用户公布此版本</label>
        <div class="actions"><button class="primary" type="submit">保存 苹果版本</button><span id="ios-release-result" role="status"></span></div>
      </form></article>`;
  };
  content.addEventListener('submit',async event=>{
    const form=event.target;if(form.id!=='ios-release-form')return;
    event.preventDefault();const button=form.querySelector('button[type=submit]'),message=form.querySelector('[role=status]');button.disabled=true;
    const fields=new FormData(form);
    const payload={published:fields.has('published'),bundleId:fields.get('bundleId'),versionName:fields.get('versionName'),buildNumber:Number(fields.get('buildNumber')),minimumBuildNumber:Number(fields.get('minimumBuildNumber')),storeUrl:fields.get('storeUrl'),releaseNotes:fields.get('releaseNotes')};
    try{await api('/api/v1/admin/ios-release',{method:'PUT',body:payload});message.textContent='苹果版本信息已保存';}
    catch(error){message.textContent=error.message;}finally{button.disabled=false;}
  });
})();
