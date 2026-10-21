
function announcementFields(row = {}) {
  return `<label>分类 / 标签<input name="category" maxlength="30" value="${escapeHtml(row.category || "公告")}"></label>
    <label>显示顺序（越小越靠前）<input name="sortOrder" type="number" min="-100000" max="100000" value="${Number(row.sort_order || 0)}"></label>
    <label>跳转链接（留空打开公告详情）<input name="targetUrl" type="url" placeholder="https://" value="${escapeHtml(row.target_url || "")}"></label>
    ${row.image_name ? `<img alt="公告图片" style="max-width:240px;max-height:160px" src="/uploads/${encodeURIComponent(row.image_name)}">` : ""}
    <label>上传 / 替换图片（JPEG、PNG、WebP，最多3MB）<input name="announcementImage" type="file" accept="image/jpeg,image/png,image/webp"></label>
    <label class="checkbox-row"><input name="removeImage" type="checkbox">移除现有图片</label>`;
}
async function announcementMetadata(data) {
  const result = {category: data.get("category"), sortOrder: Number(data.get("sortOrder") || 0), targetUrl: data.get("targetUrl"), removeImage: data.has("removeImage")};
  const file = data.get("announcementImage");
  if (file?.size) {
    if (file.size > 3 * 1024 * 1024) throw new Error("图片不能超过3MB");
    result.imageBase64 = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(",")[1]);
      reader.onerror = () => reject(new Error("图片读取失败，请重试"));
      reader.readAsDataURL(file);
    });
  }
  return result;
}
let announcementRows = [];
const state = {
  token: sessionStorage.getItem("myChuannongAdminToken") || "",
  page: "overview",
  venues: [],
  admin: null,
  phoneRevealTimer: null,
  profileRevealTimer: null,
  filters: {},
  overviewScrollY: 0,
};

const titles = {
  overview: "平台概览",
  canonicalUsers: "用户明细",
  devices: "设备明细",
  installations: "安装实例明细",
  users: "用户与权限",
  venues: "场地与库存",
  reservations: "预约记录",
  rules: "预约规则",
  lost: "失物处理",
  lostReports: "失物举报处理",
  lostClaims: "失物认领申请",
  resources: "资料管理",
  resourceComplaints: "资料投诉 / 举报处理",
  resourceActivity: "资料库用户记录",
  announcements: "APP 公告",
  websiteAnnouncements: "网站公告",
  config: "版本与功能开关",
  feedback: "用户反馈",
  riskCandidates: "异常候选",
  userEvents: "用户行为记录",
  adminAudit: "管理员操作记录",
  admins: "运营管理员",
};

const roleLabels = {
  SUPER_ADMIN: "超级管理员",
  SPORTS_OPERATOR: "体育预约运营管理员",
  LOST_FOUND_OPERATOR: "失物招领运营管理员",
  LIBRARY_OPERATOR: "资料库运营管理员",
  CONTENT_OPERATOR: "内容运营管理员",
};

const $ = (selector) => document.querySelector(selector);
const content = $("#page-content");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(Number(value)));
}

function datetimeLocal(value) {
  if (!value) return "";
  const date = new Date(Number(value));
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function userLabel(row) {
  return row.reservation_name || row.nickname || row.phone_masked || "川农同学";
}

function statusText(value) {
  return {
    OPEN: "开放",
    CLOSED: "关闭",
    MAINTENANCE: "维护",
    ACTIVE: "已预约",
    CANCELLED: "已取消",
    NOT_REQUIRED: "无需核验",
    PENDING: "待核验",
    VERIFIED: "已核验",
    REJECTED: "核验未通过",
    LOST: "寻物",
    FOUND: "招领",
    SEARCHING: "寻找中",
    FOUND_STATUS: "已找回",
    WAITING: "等待认领",
    CLAIMED: "已认领",
    RETURNED: "已归还",
    REVIEWED: "已查看",
    PROCESSING: "处理中",
    RESOLVED: "已解决",
    SUSPENDED: "已停用",
    PHONE: "手机号账号",
    DEVICE: "未登录用户",
    REGISTERED: "注册用户",
    GUEST: "游客用户",
    ANONYMOUS: "未注册",
    DRAFT: "草稿",
    PUBLISHED: "已发布",
    DISABLED: "已停用",
    UNLISTED: "已下架",
  }[value] || value || "—";
}

function feedbackStatusText(value) {
  return {
    OPEN: "待处理",
    PROCESSING: "处理中",
    RESOLVED: "已解决",
    CLOSED: "已关闭",
  }[value] || value || "—";
}

function feedbackCategoryText(value) {
  return {
    FEATURE_SUGGESTION: "功能建议",
    RESERVATION: "预约问题",
    LOST_FOUND: "失物招领",
    RESOURCE_LIBRARY: "资料库",
    ACCOUNT_SECURITY: "账号与安全",
    CONTENT_REPORT: "内容投诉",
    OTHER: "其他",
  }[value] || value || "其他";
}

function feedbackCategoryOptions(selected, allowBlank = false) {
  const values = [
    "FEATURE_SUGGESTION", "RESERVATION", "LOST_FOUND", "RESOURCE_LIBRARY",
    "ACCOUNT_SECURITY", "CONTENT_REPORT", "OTHER",
  ];
  return `${allowBlank ? '<option value="">全部</option>' : ""}${values.map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${feedbackCategoryText(value)}</option>`).join("")}`;
}

function feedbackStatusOptions(selected, allowBlank = false) {
  const values = ["OPEN", "PROCESSING", "RESOLVED", "CLOSED"];
  return `${allowBlank ? '<option value="">全部</option>' : ""}${values.map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${feedbackStatusText(value)}</option>`).join("")}`;
}

function feedbackBadge(value) {
  const kind = value === "OPEN" ? "warn" : value === "PROCESSING" ? "warn" : value === "RESOLVED" ? "ok" : "bad";
  return `<span class="badge ${kind}">${escapeHtml(feedbackStatusText(value))}</span>`;
}

function badge(value) {
  const kind = ["OPEN", "ACTIVE", "WAITING", "SEARCHING"].includes(value)
    ? "ok"
    : ["MAINTENANCE", "PROCESSING", "CLAIMED"].includes(value)
      ? "warn"
      : ["CLOSED", "CANCELLED", "RETURNED"].includes(value)
        ? "bad"
        : "";
  return `<span class="badge ${kind}">${escapeHtml(statusText(value))}</span>`;
}

function accountBadge(value) {
  const labels = { ACTIVE: "正常", LIMITED: "已限制", SUSPENDED: "已限制", DISABLED: "已禁用", PENDING_DELETION: "待注销", DELETED: "已注销" };
  const kind = value === "ACTIVE" ? "ok" : value === "LIMITED" || value === "SUSPENDED" || value === "PENDING_DELETION" ? "warn" : "bad";
  return `<span class="badge ${kind}">${labels[value] || value}</span>`;
}

function accountStatusFilterOptions(selected) {
  const values = [
    ["ACTIVE", "正常"],
    ["LIMITED", "已限制"],
    ["DISABLED", "已禁用"],
    ["PENDING_DELETION", "待注销"],
    ["DELETED", "已注销"],
  ];
  return `<option value="">全部（不含已注销）</option>${values.map(([value, label]) => `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`).join("")}`;
}

function accountStatusOptions(selected, allowBlank = false, includeDeleted = false) {
  const values = [
    ["ACTIVE", "正常"],
    ["LIMITED", "受限"],
    ["DISABLED", "已禁用"],
    ["PENDING_DELETION", "待注销"],
  ];
  if (includeDeleted) values.push(["DELETED", "已注销"]);
  return `${allowBlank ? '<option value="">全部</option>' : ""}${values.map(([value, label]) => `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`).join("")}`;
}

function selectedUserIds() {
  return Array.from(document.querySelectorAll(".user-select:checked"), (item) => item.value);
}

function refreshUserBulkActions() {
  const toolbar = document.querySelector("#user-bulk-actions");
  if (!toolbar) return;
  const ids = selectedUserIds();
  toolbar.hidden = ids.length === 0;
  const count = toolbar.querySelector("[data-selected-user-count]");
  if (count) count.textContent = `已选择 ${ids.length} 个用户`;
  const selected = Array.from(document.querySelectorAll(".user-select:checked"));
  const onlyDeleted = selected.length > 0 && selected.every((item) => item.dataset.accountStatus === "DELETED");
  const purge = toolbar.querySelector('[data-action="bulk-purge-users"]');
  if (purge) purge.hidden = !onlyDeleted;
  ["bulk-delete-users", "bulk-limit-users", "bulk-restore-users"].forEach((action) => {
    const button = toolbar.querySelector(`[data-action="${action}"]`);
    if (button) button.hidden = selected.some((item) => item.dataset.accountStatus === "DELETED");
  });
}

function openBulkUserAction(action) {
  const ids = selectedUserIds();
  if (!ids.length) throw new Error("请先选择需要处理的普通用户");
  if (action === "purge") {
    const selected = Array.from(document.querySelectorAll(".user-select:checked"));
    if (!selected.every((item) => item.dataset.accountStatus === "DELETED")) {
      throw new Error("永久清理只能处理已注销账号");
    }
    if (!confirm(`即将永久删除 ${ids.length} 个已注销账号。此操作不可恢复，确认进入第二次核验？`)) return;
    const confirmation = `PURGE ${ids.length} DELETED USERS`;
    return openDialog(
      "永久删除已注销账号",
      `<div class="stack"><p><strong>即将永久删除 ${ids.length} 个已注销账号。</strong></p><p class="muted">此操作不可恢复。与账号相关的个人信息将按照系统清理规则永久删除或匿名化。存在业务关联的数据会先按服务端外键规则处理。</p><label>再次输入确认文本 <strong>${confirmation}</strong><input name="confirmation" autocomplete="off" required></label></div>`,
      async (form) => {
        const result = await api("/api/v1/admin/users/purge", {
          method: "POST",
          body: { ids, confirmation: form.get("confirmation") },
        });
        toast(`已永久清理 ${result.purged} 个已注销账号`);
        renderers.users();
      },
      "确认永久删除",
    );
  }
  const definitions = {
    limit: {
      title: "批量限制访问",
      accountStatus: "LIMITED",
      confirmation: `UPDATE ${ids.length} USERS`,
      fields: '<label>暂停时长<select name="suspensionDuration"><option value="7_DAYS">7 天</option><option value="30_DAYS">30 天</option><option value="PERMANENT">永久</option></select></label>',
      note: "受限期间用户仍可登录和使用本地功能，但不能访问失物招领、资料库及其他需服务端授权的功能。",
      success: `已限制 ${ids.length} 个普通用户`,
    },
    restore: {
      title: "批量恢复权限",
      accountStatus: "ACTIVE",
      confirmation: `UPDATE ${ids.length} USERS`,
      fields: "",
      note: "恢复后用户可重新访问服务端业务功能。",
      success: `已恢复 ${ids.length} 个普通用户`,
    },
    delete: {
      title: "批量注销账号",
      accountStatus: "DELETED",
      confirmation: `DELETE ${ids.length} USERS`,
      fields: "",
      note: "删除后账号无法恢复，手机号可重新注册。用户个人资料、会话和用户内容会清理；管理员、公告、资料库条目和系统配置不受影响。",
      success: `已注销 ${ids.length} 个普通用户账号`,
    },
  };
  const definition = definitions[action];
  if (!definition) throw new Error("批量操作无效");
  if (action === "delete" && !confirm(`将删除 ${ids.length} 个普通用户账号。确认进入第二次核验？`)) return;
  return openDialog(definition.title, `<div class="stack"><p>${escapeHtml(definition.note)}</p>${definition.fields}<label>处理原因<textarea name="reason" maxlength="300" required></textarea></label><label>再次输入确认文本 <strong>${definition.confirmation}</strong><input name="confirmation" autocomplete="off" required></label></div>`, async (form) => {
    await api("/api/v1/admin/users/bulk-status", {
      method: "POST",
      body: {
        ids,
        accountStatus: definition.accountStatus,
        suspensionDuration: form.get("suspensionDuration") || undefined,
        reason: form.get("reason"),
        confirmation: form.get("confirmation"),
      },
    });
    toast(definition.success);
    renderers.users();
  }, definition.title);
}

function openDeleteUserAccountDialog(user) {
  const confirmation = `DELETE USER ${user.id}`;
  return openDialog("注销用户账号", `<div class="stack"><p><strong>${escapeHtml(user.nickname || "川农同学")}</strong> · ${escapeHtml(user.phone || "无手机号")}</p><p class="muted">注销后个人数据会清理且原账号无法恢复，用户可以使用原手机号重新注册。管理员、公告、资料库条目和系统配置不受影响。</p><label>注销原因<textarea name="reason" maxlength="300" required></textarea></label><label>再次输入确认文本 <strong>${escapeHtml(confirmation)}</strong><input name="confirmation" autocomplete="off" required></label></div>`, async (form) => {
    await api(`/api/v1/admin/users/${encodeURIComponent(user.id)}`, {
      method: "DELETE",
      body: { reason: form.get("reason"), confirmation: form.get("confirmation") },
    });
    toast("用户账号已注销");
    renderers.users();
  }, "确认注销账号");
}

function toast(message, isError = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show${isError ? " error-toast" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    node.className = "toast";
  }, 2800);
}

function filterSummary(filters) {
  const values = Object.values(filters || {}).filter(Boolean);
  return values.length ? `<div class="filter-summary"><span>当前筛选：${escapeHtml(values.join(" · "))}</span><button class="ghost" data-action="clear-page-filter">清除筛选</button></div>` : "";
}

function linkedFilterSummary(filters) {
  const summary = filterSummary({用户: filters.userLabel || filters.user, 类型: filters.kind || ""});
  if (!filters.user) return summary;
  return `${summary}<div class="filter-actions"><button class="ghost" data-action="return-user-profile" data-user-id="${escapeHtml(filters.user)}">返回用户档案</button></div>`;
}

function relatedCard(label, count, page, user, kind = "") {
  if (!Number(count)) return `<article class="sub-card"><strong>0</strong><br><span class="subtle">${escapeHtml(label)} · 暂无记录</span></article>`;
  return `<button type="button" class="sub-card related-card" data-action="user-related" data-page="${page}" data-user-id="${escapeHtml(user.id)}" data-user-label="${escapeHtml(`${user.nickname} · ${user.phone || "无手机号"} · ${user.id}`)}" data-kind="${kind}"><strong>${count}</strong><br><span class="subtle">${escapeHtml(label)} →</span></button>`;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (options.body && typeof options.body !== "string") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && state.token) showLogin();
    const error = new Error(payload?.error?.message || `操作失败（${response.status}）`);
    error.status = response.status;
    error.code = payload?.error?.code || "";
    throw error;
  }
  return payload.data;
}

function showLogin() {
  state.token = "";
  state.admin = null;
  sessionStorage.removeItem("myChuannongAdminToken");
  $("#app-view").classList.add("hidden");
  $("#login-view").classList.remove("hidden");
  $("#login-password").value = "";
}

async function showApp() {
  try {
    state.admin = await api("/api/v1/auth/me");
    if (state.admin.role !== "ADMIN") throw new Error("该账号没有管理员权限");
    const role = state.admin.adminRole || (state.admin.adminPermissions?.includes("*") ? "SUPER_ADMIN" : "");
    document.querySelectorAll("#nav [data-roles]").forEach((node) => {
      node.classList.toggle("hidden", !node.dataset.roles.split(" ").includes(role));
    });
    document.querySelectorAll("#nav .nav-group").forEach((group) => {
      const members = [];
      let cursor = group.nextElementSibling;
      while (cursor && !cursor.classList.contains("nav-group")) {
        if (cursor.matches("button[data-page]")) members.push(cursor);
        cursor = cursor.nextElementSibling;
      }
      group.classList.toggle("hidden", !members.some((item) => !item.classList.contains("hidden")));
    });
    // A fresh administrator session always starts from the role-scoped
    // dashboard.  Reusing the previous role's route can otherwise land an
    // operator on a detail page before the workspace context is established.
    state.page = "overview";
    const activePage = document.querySelector(`#nav button[data-page="${CSS.escape(state.page)}"]`);
    if (!activePage || activePage.classList.contains("hidden")) state.page = "overview";
    $("#admin-identity").textContent = `当前管理员：${state.admin.nickname || state.admin.id} · ${roleLabels[role] || role}`;
    $("#login-view").classList.add("hidden");
    $("#app-view").classList.remove("hidden");
    await navigate(state.page);
  } catch (_) {
    showLogin();
  }
}

async function navigate(page, filters = {}) {
  state.page = page;
  state.filters = filters;
  $("#page-title").textContent = filters.title || titles[page];
  document.querySelectorAll("#nav button").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === page);
  });
  content.innerHTML = '<div class="empty">正在读取数据…</div>';
  try {
    await renderers[page](filters);
    if (page === "overview" && state.overviewScrollY) {
      requestAnimationFrame(() => window.scrollTo(0, state.overviewScrollY));
    }
  } catch (error) {
    const title = error.status === 403 ? "无权限访问" : "读取失败";
    content.innerHTML = `<div class="empty"><strong>${title}</strong><br>${escapeHtml(error.message)}<br><button class="ghost" data-action="reload">重新加载</button></div>`;
  }
}

const renderers = {
  async overview() {
    const role = state.admin.adminRole || "SUPER_ADMIN";
    if (role !== "SUPER_ADMIN") {
      $("#page-title").textContent = {
        SPORTS_OPERATOR: "体育预约概览",
        LOST_FOUND_OPERATOR: "失物招领概览",
        LIBRARY_OPERATOR: "资料库概览",
        CONTENT_OPERATOR: "内容运营概览",
      }[role] || "工作台概览";
      const suffix = { SPORTS_OPERATOR:"sports", LOST_FOUND_OPERATOR:"lost-found", LIBRARY_OPERATOR:"library", CONTENT_OPERATOR:"content" }[role];
      const data = await api(`/api/v1/admin/overview/${suffix}`);
      const configs = {
        SPORTS_OPERATOR: [["体育预约概览", [["今日预约", data.todayReservations, "reservations", {date:new Date().toISOString().slice(0,10)}], ["当前有效预约", data.activeReservations, "reservations", {status:"ACTIVE"}], ["待核验", data.pendingVerification, "reservations", {verification:"pending"}], ["今日取消", data.cancelledToday, "reservations", {status:"CANCELLED"}], ["场地数量", data.venues, "venues", {}]]]],
        LOST_FOUND_OPERATOR: [["失物招领概览", [["待处理失物", data.openItems, "lost", {pending:"true"}], ["待处理举报", data.openReports, "lostReports", {status:"OPEN"}]]]],
        LIBRARY_OPERATOR: [["资料库概览", [["已发布资料", data.publishedResources, "resources", {status:"PUBLISHED"}], ["待处理投诉", data.openComplaints, "resourceComplaints", {status:"OPEN"}]]]],
        CONTENT_OPERATOR: [["内容运营概览", [["已发布公告", data.publishedAnnouncements, "announcements", {enabled:"true"}]]]],
      };
      const groups = configs[role] || [];
      content.innerHTML = groups.map(([title, metrics]) => `<h3 class="section-title">${title}</h3><div class="grid metric-grid">${metrics.map(([label,value,page,filter]) => page ? `<button class="card metric metric-link" data-action="metric-nav" data-page="${page}" data-filter="${escapeHtml(JSON.stringify(filter))}"><span>${label}</span><strong>${value}</strong></button>` : `<div class="card metric"><span>${label}</span><strong>${value}</strong></div>`).join("")}</div>`).join("");
      return;
    }
    const data = await api("/api/v1/admin/overview");
    const todayNewRegistrations = data.todayNewRegistrations ?? data.todayRegistrations ?? 0;
    const todayFirstUseUsers = data.todayFirstUseUsers ?? data.todayNewUsers ?? 0;
    const asOf = String(data.asOf || Date.now());
    const groups = [
      ["用户概况", [
        ["累计用户量", data.totalUsers, "canonicalUsers", {cohort:"all",title:"累计用户量"}], ["我的川农注册用户", data.registeredUsers, "users", {accountType:"PHONE",includeDeleted:"true"}],
        ["未注册 / 游客用户", data.guestUsers, "canonicalUsers", {cohort:"guest",title:"未注册 / 游客用户"}], ["今日新增用户", data.todayNewUsers, "canonicalUsers", {firstSeen:"today",title:"今日新增用户"}],
        ["今日新增注册", todayNewRegistrations, "users", {createdToday:"true",accountType:"PHONE",includeDeleted:"true"}],
      ]],
      ["活跃情况", [
        ["最近 5 分钟活跃用户", data.active5m, "canonicalUsers", {activity:"5m",asOf,title:"最近 5 分钟活跃用户"}], ["今日活跃用户（DAU）", data.dau, "canonicalUsers", {activity:"today",asOf,title:"今日活跃用户（DAU）"}],
        ["近 7 日活跃用户（WAU）", data.wau, "canonicalUsers", {activity:"7d",asOf,title:"近 7 日活跃用户（WAU）"}], ["近 30 日活跃用户（MAU）", data.mau, "canonicalUsers", {activity:"30d",asOf,title:"近 30 日活跃用户（MAU）"}],
        ["今日首次使用用户", todayFirstUseUsers, "canonicalUsers", {firstSeen:"today",title:"今日首次使用用户"}],
      ]],
      ["我的川农账号", [
        ["累计注册账号", data.registeredAccounts, "users", {accountType:"PHONE",includeDeleted:"true"}], ["正常账号", data.activeAccounts, "users", {status:"ACTIVE",accountType:"PHONE"}],
        ["受限账号", data.limitedAccounts, "users", {status:"LIMITED",accountType:"PHONE"}], ["已禁用账号", data.disabledAccounts, "users", {status:"DISABLED",accountType:"PHONE"}],
        ["已注销账号", data.deletedAccounts, "users", {status:"DELETED",accountType:"PHONE"}],
      ]],
      ["设备与安装实例", [
        ["累计独立设备", data.totalDevices, "devices", {window:"all",title:"累计独立设备"}], ["今日新增设备", data.todayNewDevices, "devices", {window:"today",title:"今日新增设备"}],
        ["最近 5 分钟活跃设备", data.activeDevices, "devices", {window:"5m",asOf,title:"最近 5 分钟活跃设备"}], ["近 7 日活跃设备", data.activeDevices7d, "devices", {window:"7d",asOf,title:"近 7 日活跃设备"}],
        ["近 30 日活跃设备", data.activeDevices30d, "devices", {window:"30d",asOf,title:"近 30 日活跃设备"}], ["累计安装实例", data.totalInstallations, "installations", {window:"all",title:"累计安装实例"}],
        ["今日新增安装", data.todayNewInstallations, "installations", {window:"today",title:"今日新增安装"}], ["累计重新安装", data.reinstallations, "installations", {reinstallation:"true",title:"累计重新安装"}],
      ]],
      ["业务与处理", [
        ["体育预约 · 当前有效预约", data.activeReservations, "reservations", {status:"ACTIVE"}], ["体育预约 · 今日有效预约", data.todayReservations, "reservations", {date:new Date().toISOString().slice(0,10),status:"ACTIVE"}],
        ["体育预约 · 今日取消预约", data.todayCancelledReservations, "reservations", {cancelledToday:"true",status:"CANCELLED"}],
        ["失物招领 · 待处理信息", data.lostFoundOpen, "lost", {pending:"true"}], ["失物招领 · 待处理举报", data.openReports, "lostReports", {status:"OPEN"}],
        ["资料库 · 待处理投诉", data.openResourceComplaints, "resourceComplaints", {status:"OPEN"}], ["平台 · 待处理用户反馈", data.openFeedback, "feedback", {status:"OPEN"}],
        ["内容运营 · 已发布公告", data.enabledAnnouncements, "announcements", {enabled:"true"}],
      ]],
      ["风险情况", [
        ["待复核异常账号", data.riskCandidates, "riskCandidates", {status:"OPEN"}], ["近 24 小时登录失败次数", data.loginFailures24h, "userEvents", {eventType:"LOGIN_FAILURE",last24h:"true"}],
        ["近 24 小时新受限 / 禁用账号", data.newLimitedOrDisabled24h, "users", {statusGroup:"restricted24h"}],
      ]],
    ];
    content.innerHTML = `
      ${groups.map(([title, metrics]) => `<h3 class="section-title">${title}</h3><div class="grid metric-grid">
        ${metrics.map(([label, value, page, filter]) => page ? `<button class="card metric metric-link" data-action="metric-nav" data-page="${page}" data-filter="${escapeHtml(JSON.stringify(filter))}"><span>${label}</span><strong>${value}</strong></button>` : `<div class="card metric"><span>${label}</span><strong>${value}</strong></div>`).join("")}
      </div>`).join("")}
      <article class="card section-title">
        <h3>生产运行说明</h3>
        <p class="muted">这里的修改会通过生产 API 写入生产数据库。App 刷新对应页面后即可看到最新状态。</p>
      </article>`;
  },

  async users(filters = {}) {
    const normalized = {...filters};
    const now = Date.now();
    if (normalized.activeWindow) {
      normalized.activeSince = normalized.activeWindow === "5m" ? now - 300000 : normalized.activeWindow === "dau" ? new Date().setHours(0,0,0,0) : normalized.activeWindow === "wau" ? now - 7*86400000 : now - 30*86400000;
      delete normalized.activeWindow;
    }
    delete normalized.title;
    if (normalized.statusGroup === "restricted24h") { normalized.statusChangedSince = Date.now()-86400000; delete normalized.statusGroup; }
    normalized.paged = "true";
    normalized.limit = "100";
    const params = new URLSearchParams(normalized);
    const result = await api(`/api/v1/admin/users?${params}`);
    const rows = result.items || [];
    const summary = Object.keys(filters).length ? `<div class="filter-summary"><span>当前筛选：${escapeHtml(Object.values(filters).join(" · "))}</span><button class="ghost" data-action="clear-page-filter">清除筛选</button></div>` : "";
    content.innerHTML = `
      ${summary}
      <form id="user-filter" class="toolbar">
        <label>搜索<input name="q" value="${escapeHtml(filters.q || "")}" placeholder="手机号、昵称、用户编号、内部 UUID、预约学号或姓名"></label>
        <label class="compact">账号类型<select name="accountType"><option value="">全部</option>${options(["PHONE","DEVICE"], filters.accountType)}</select></label>
        <label class="compact">状态<select name="status">${accountStatusFilterOptions(filters.status)}</select></label>
        <button class="primary">筛选</button>
      </form>
      <p class="muted">共 ${result.total} 个账号。默认隐藏已注销用户；选择“已注销”可单独查询。</p>
      ${rows.length ? `<div id="user-bulk-actions" class="toolbar" hidden><strong data-selected-user-count>已选择 0 个用户</strong><button type="button" class="danger" data-action="bulk-purge-users" hidden>永久删除所选已注销账号</button><button type="button" class="danger" data-action="bulk-delete-users">注销账号</button><button type="button" class="primary" data-action="bulk-limit-users">批量限制访问</button><button type="button" class="ghost" data-action="bulk-restore-users">批量恢复权限</button><span class="muted">管理员账号不属于此列表，也不会被批量操作修改。</span></div><div class="table-wrap"><table><thead><tr><th><input type="checkbox" data-action="toggle-user-all" aria-label="全选当前用户"></th><th>用户完整资料</th><th>预约资料</th><th>账号状态</th><th>业务统计</th><th>时间 / 限制</th><th>操作</th></tr></thead><tbody>
        ${rows.map((row) => `<tr>
          <td><input type="checkbox" class="user-select" value="${escapeHtml(row.id)}" data-account-status="${escapeHtml(row.account_status)}" aria-label="选择 ${escapeHtml(row.nickname || "用户")}"></td><td><strong>${escapeHtml(row.nickname || "川农同学")}</strong><br>${escapeHtml(row.phone || "无手机号")}<br><span class="subtle">用户编号：${escapeHtml(row.user_code || "待生成")}</span><br><span class="subtle">内部 UUID：${escapeHtml(row.id)}</span></td>
          <td><strong>${escapeHtml(row.reservation_student_id || "未填写")}</strong><br><span class="subtle">${escapeHtml(row.reservation_name || "—")} · ${row.reservation_profile_consented_at ? "用户已同意" : "未记录同意"}</span></td>
          <td>${badge(row.account_type)}<br>${accountBadge(row.account_status)}</td>
          <td><span class="subtle">预约 ${row.reservation_count} · 失物 ${row.lost_found_count}<br>资料浏览 ${row.resource_view_count} · 收藏 ${row.resource_favorite_count}<br>投诉 ${row.resource_complaint_count} · 反馈 ${row.feedback_count}</span></td>
          <td><span class="subtle">注册 ${formatTime(row.created_at)}<br>活跃 ${formatTime(row.last_active_at)}<br>${escapeHtml(row.restriction_reason || row.account_status_reason || "无限制")}</span></td>
          <td>${row.account_status === "DELETED" ? '<span class="subtle">已注销，可重新注册</span>' : `<div class="actions"><button class="ghost" data-action="user-details" data-id="${escapeHtml(row.id)}">详情</button><button class="ghost" data-action="edit-user" data-user="${escapeHtml(JSON.stringify(row))}">编辑</button>${["LIMITED","SUSPENDED","DISABLED","PENDING_DELETION"].includes(row.account_status) ? `<button class="primary" data-action="restore-user" data-id="${escapeHtml(row.id)}">恢复账号</button>` : ""}${row.restriction_reason ? `<button class="danger" data-action="clear-restriction" data-id="${escapeHtml(row.id)}">解除预约限制</button>` : ""}</div>`}</td>
        </tr>`).join("")}
      </tbody></table></div>` : '<div class="empty">没有符合条件的用户</div>'}
      ${drilldownPagination(result, "users", filters)}`;
  },

  async canonicalUsers(filters = {}) {
    const params = new URLSearchParams(Object.entries(filters).filter(([key]) => key !== "title"));
    const result = await api(`/api/v1/admin/canonical-users?${params}`);
    const title = filters.title || "用户明细";
    content.innerHTML = `
      <div class="drilldown-header"><button type="button" class="ghost" data-action="back-overview">← 返回工作台</button><strong>${escapeHtml(title)}</strong><span class="muted">共 ${result.total} 个用户</span></div>
      ${result.items.length ? `<div class="table-wrap"><table><thead><tr><th>用户</th><th>类型 / 状态</th><th>首次使用</th><th>最近活跃</th><th>关联设备</th><th>安装实例</th></tr></thead><tbody>
        ${result.items.map((row) => `<tr><td><strong>${escapeHtml(row.displayName)}</strong><br><span class="subtle">用户编号：${escapeHtml(row.identityCode)}</span>${row.phoneMasked ? `<br><span class="subtle">${escapeHtml(row.phoneMasked)}</span>` : ""}</td><td>${badge(row.identityType)}<br><span class="subtle">${escapeHtml(statusText(row.status))}</span></td><td>${formatTime(row.firstSeenAt)}</td><td>${formatTime(row.lastActiveAt)}</td><td>${row.deviceCount}</td><td>${row.installationCount}</td></tr>`).join("")}
      </tbody></table></div>` : '<div class="empty">暂无符合条件的数据</div>'}
      ${drilldownPagination(result, "canonicalUsers", filters)}`;
  },

  async devices(filters = {}) {
    const params = new URLSearchParams(Object.entries(filters).filter(([key]) => key !== "title"));
    const result = await api(`/api/v1/admin/devices?${params}`);
    const title = filters.title || "设备明细";
    content.innerHTML = `
      <div class="drilldown-header"><button type="button" class="ghost" data-action="back-overview">← 返回工作台</button><strong>${escapeHtml(title)}</strong><span class="muted">共 ${result.total} 台设备</span></div>
      ${result.items.length ? `<div class="table-wrap"><table><thead><tr><th>设备业务编号</th><th>首次出现</th><th>最近活跃</th><th>首次版本</th><th>最近版本</th><th>安装实例</th><th>注册用户关联</th></tr></thead><tbody>
        ${result.items.map((row) => `<tr><td><strong>${escapeHtml(row.deviceCode)}</strong></td><td>${formatTime(row.firstSeenAt)}</td><td>${formatTime(row.lastSeenAt)}</td><td>${escapeHtml(row.firstVersionName || "—")} · ${escapeHtml(row.firstVersionCode || "—")}</td><td>${escapeHtml(row.lastVersionName || "—")} · ${escapeHtml(row.lastVersionCode || "—")}</td><td>${row.installationCount}</td><td>${row.linkedRegisteredUser ? '<span class="badge ok">已关联</span>' : '<span class="badge">未关联</span>'}</td></tr>`).join("")}
      </tbody></table></div>` : '<div class="empty">暂无符合条件的数据</div>'}
      ${drilldownPagination(result, "devices", filters)}`;
  },

  async installations(filters = {}) {
    const params = new URLSearchParams(Object.entries(filters).filter(([key]) => key !== "title"));
    const result = await api(`/api/v1/admin/installations?${params}`);
    const title = filters.title || "安装实例明细";
    content.innerHTML = `
      <div class="drilldown-header"><button type="button" class="ghost" data-action="back-overview">← 返回工作台</button><strong>${escapeHtml(title)}</strong><span class="muted">共 ${result.total} 个安装实例</span></div>
      ${result.items.length ? `<div class="table-wrap"><table><thead><tr><th>安装业务标识</th><th>所属设备</th><th>首次出现</th><th>最近活动</th><th>App 版本</th><th>安装类型</th></tr></thead><tbody>
        ${result.items.map((row) => `<tr><td><strong>${escapeHtml(row.installationCode)}</strong></td><td>${escapeHtml(row.deviceCode)}</td><td>${formatTime(row.firstSeenAt)}</td><td>${formatTime(row.lastActiveAt)}</td><td>${escapeHtml(row.versionName || "—")} · ${escapeHtml(row.versionCode || "—")}</td><td>${row.isReinstallation ? '<span class="badge warn">重新安装</span>' : '<span class="badge ok">首次安装</span>'}</td></tr>`).join("")}
      </tbody></table></div>` : '<div class="empty">暂无符合条件的数据</div>'}
      ${drilldownPagination(result, "installations", filters)}`;
  },

  async admins() {
    const rows = await api("/api/v1/admin/admins");
    content.innerHTML = `<div class="toolbar"><button class="primary" data-action="create-admin">创建运营管理员</button></div>
      <div class="table-wrap"><table><thead><tr><th>管理员</th><th>角色</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody>
      ${rows.map(row => {
        const isSelf = state.admin && row.id === state.admin.id;
        const editCell = isSelf
          ? '<span class="badge">当前账号</span>'
          : `<button class="ghost" data-action="edit-admin" data-admin="${escapeHtml(JSON.stringify(row))}">编辑</button>`;
        return `<tr><td><strong>${escapeHtml(row.nickname)}</strong><br><span class="subtle">${escapeHtml(row.username)} · ${escapeHtml(row.id)}</span></td><td>${escapeHtml(roleLabels[row.adminRole] || row.adminRole)}</td><td>${row.enabled ? '<span class="badge ok">启用</span>' : '<span class="badge bad">停用</span>'}</td><td>${formatTime(row.createdAt)}</td><td>${editCell}</td></tr>`;
      }).join("")}
      </tbody></table></div>`;
  },

  async rules() {
    const rules = await api("/api/v1/admin/reservation-rules");
    content.innerHTML = `<article class="card"><h3>体育预约规则</h3><p class="muted">所有限制均由服务端执行。管理员取消不计入用户主动取消次数。</p>
      <form id="rules-form" class="rule-grid">
        <label>每人每天最多有效预约<input type="number" min="1" max="20" name="maxActivePerDay" value="${rules.max_active_per_day}" required></label>
        <label>每天最多主动取消<input type="number" min="1" max="20" name="maxUserCancellationsPerDay" value="${rules.max_user_cancellations_per_day}" required></label>
        <label>开场前不可取消（分钟）<input type="number" min="0" max="1440" name="cancelCutoffMinutes" value="${rules.cancel_cutoff_minutes}" required></label>
        <label>超限后限制时长（小时）<input type="number" min="1" max="720" name="restrictionHoursAfterExcessCancel" value="${rules.restriction_hours_after_excess_cancel}" required></label>
        <label>预约资料规则<select name="reservationProfileMode"><option value="OFF" ${rules.reservation_profile_mode === "OFF" ? "selected" : ""}>关闭</option><option value="OPTIONAL" ${rules.reservation_profile_mode === "OPTIONAL" ? "selected" : ""}>可选</option><option value="REQUIRED" ${rules.reservation_profile_mode === "REQUIRED" ? "selected" : ""}>必填</option></select></label>
        <label>修改原因（可选）<input name="reason" maxlength="200" placeholder="说明本次规则调整原因"></label><button class="primary">保存预约规则</button>
      </form></article>`;
  },

  async venues() {
    state.venues = await api("/api/v1/admin/venues");
    content.innerHTML = `
      <div class="grid two-columns">
        ${state.venues.map((venue) => `
          <article class="card">
            <div class="card-header">
              <div>
                <h3>${escapeHtml(venue.name)}</h3>
                <p class="subtle">${venue.sport === "BADMINTON" ? "羽毛球" : "乒乓球"} · ${venue.court_count} ${escapeHtml(venue.unit_label)} · ${venue.price_yuan} 元/小时</p>
              </div>
              ${badge(venue.operating_status)}
            </div>
            <p class="subtle">${escapeHtml(venue.maintenance_note || "暂无维护说明")}</p>
            <div class="slot-list">
              ${venue.timeSlots.map((slot) => `<button class="slot ${slot.enabled ? "" : "off"}" data-action="toggle-slot" data-id="${escapeHtml(slot.id)}" data-enabled="${slot.enabled}">${escapeHtml(slot.timeSlot)}</button>`).join("")}
            </div>
            <div class="actions section-title">
              <button class="primary" data-action="inventory" data-id="${escapeHtml(venue.id)}">管理日期库存</button>
              <button class="ghost" data-action="edit-venue" data-id="${escapeHtml(venue.id)}">编辑场馆</button>
              <button class="ghost" data-action="add-slot" data-id="${escapeHtml(venue.id)}">新增时间段</button>
            </div>
          </article>`).join("")}
      </div>`;
  },

  async reservations(filters = {}) {
    state.filters = filters;
    const normalized = {...filters};
    if (normalized.cancelledToday) { normalized.cancelledSince = new Date().setHours(0,0,0,0); delete normalized.cancelledToday; }
    normalized.paged = "true";
    normalized.page = normalized.page || "1";
    normalized.pageSize = "50";
    const params = new URLSearchParams(normalized);
    const [result, venues] = await Promise.all([
      api(`/api/v1/admin/reservations?${params}`),
      state.venues.length ? Promise.resolve(state.venues) : api("/api/v1/admin/venues"),
    ]);
    const rows = result.items || [];
    state.venues = venues;
    content.innerHTML = `${filterSummary(filters)}
      <form id="reservation-filter" class="toolbar">
        <label class="compact">场馆<select name="venueId"><option value="">全部场馆</option>${venues.map((v) => `<option value="${escapeHtml(v.id)}" ${filters.venueId === v.id ? "selected" : ""}>${escapeHtml(v.name)}</option>`).join("")}</select></label>
        <label class="compact">日期<input type="date" name="date" value="${escapeHtml(filters.date || "")}"></label>
        <label class="compact">状态<select name="status"><option value="">全部状态</option><option value="ACTIVE" ${filters.status === "ACTIVE" ? "selected" : ""}>已预约</option><option value="CANCELLED" ${filters.status === "CANCELLED" ? "selected" : ""}>已取消</option></select></label>
        <label class="compact">核验<select name="verificationStatus"><option value="">全部核验状态</option><option value="NOT_REQUIRED" ${filters.verificationStatus === "NOT_REQUIRED" ? "selected" : ""}>无需核验</option><option value="PENDING" ${filters.verificationStatus === "PENDING" ? "selected" : ""}>待核验</option><option value="VERIFIED" ${filters.verificationStatus === "VERIFIED" ? "selected" : ""}>已核验</option><option value="REJECTED" ${filters.verificationStatus === "REJECTED" ? "selected" : ""}>核验未通过</option></select></label>
        <label class="compact">场地号<input type="number" min="1" name="courtNumber" value="${escapeHtml(filters.courtNumber || "")}"></label>
        <label>用户<input name="user" value="${escapeHtml(filters.user || "")}" placeholder="完整手机号、昵称、内部 ID、预约学号或姓名"></label>
        <label>预约编号<input name="reservationId" value="${escapeHtml(filters.reservationId || "")}" placeholder="完整预约编号"></label>
        <button class="primary">筛选</button>
        <button type="button" class="ghost" data-action="clear-reservation-filter">清空</button>
      </form>
      ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>预约与场馆</th><th>日期 / 时段 / 场地</th><th>用户完整资料</th><th>模拟金额</th><th>预约 / 核验状态</th><th>操作</th></tr></thead><tbody>
        ${rows.map((row) => `<tr><td><strong>${escapeHtml(row.venue_name)}</strong><br><span class="subtle">预约编号：${escapeHtml(row.id)}<br>内部用户 ID：${escapeHtml(row.user_id)}</span></td><td>${escapeHtml(row.booking_date)}<br>${escapeHtml(row.time_slot)}<br>${row.court_number} ${escapeHtml(row.unit_label)}</td><td><strong>${escapeHtml(row.nickname || "未设置昵称")}</strong><br>手机号：${escapeHtml(row.phone || "未填写")}<br>预约姓名：${escapeHtml(row.reservation_name_snapshot || row.reservation_name || "未填写")}<br>预约学号：${escapeHtml(row.reservation_student_id_snapshot || row.reservation_student_id || "未填写")}</td><td>${row.price_yuan} 元<br><span class="subtle">模拟金额</span></td><td>${badge(row.status)}<br>${badge(row.verification_status || "NOT_REQUIRED")}<br><span class="subtle">${row.cancelled_by === "ADMIN" ? "管理员取消" : escapeHtml(row.cancel_reason || "")}</span></td><td><div class="actions"><button class="ghost" data-action="reservation-details" data-id="${escapeHtml(row.id)}">查看详情</button><button class="ghost" data-action="reservation-profile-details" data-id="${escapeHtml(row.id)}">核验资料</button>${row.status === "ACTIVE" ? `<button class="danger" data-action="cancel-reservation" data-id="${escapeHtml(row.id)}">取消预约</button>` : ""}</div></td></tr>`).join("")}
      </tbody></table></div>` : '<div class="empty">没有符合条件的预约</div>'}
      <div class="pagination"><span>共 ${result.total || 0} 条 · 第 ${result.page || 1} 页</span><div class="actions"><button class="ghost" data-action="reservations-page" data-page="${Math.max(1, (result.page || 1) - 1)}" ${result.page <= 1 ? "disabled" : ""}>上一页</button><button class="ghost" data-action="reservations-page" data-page="${(result.page || 1) + 1}" ${(result.page || 1) * (result.pageSize || 50) >= (result.total || 0) ? "disabled" : ""}>下一页</button></div></div>`;
  },

  async lost(filters = {}) {
    const params = new URLSearchParams(filters);
    const [posts, reports] = await Promise.all([
      api(`/api/v1/admin/lost-found?${params}`),
      api("/api/v1/admin/lost-found-reports"),
    ]);
    content.innerHTML = `${filterSummary(filters)}
      <form id="lost-filter" class="toolbar">
        <label class="compact">类型<select name="type"><option value="">全部</option><option value="LOST" ${filters.type === "LOST" ? "selected" : ""}>寻物</option><option value="FOUND" ${filters.type === "FOUND" ? "selected" : ""}>招领</option></select></label>
        <label class="compact">状态<input name="status" value="${escapeHtml(filters.status || "")}" placeholder="如 WAITING"></label>
        <label>关键词<input name="q" value="${escapeHtml(filters.q || "")}" placeholder="物品、地点或描述"></label>
        <label>用户<input name="user" value="${escapeHtml(filters.user || "")}" placeholder="完整手机号、昵称、内部 ID 或预约学号"></label>
        <button class="primary">筛选</button>
      </form>
      ${posts.length ? `<div class="toolbar"><button type="button" class="danger" data-action="bulk-delete-lost">批量删除已选内容</button><span class="muted">仅作用于已选失物内容，不会删除公告、资料、管理员或系统配置。</span></div><div class="table-wrap"><table><thead><tr><th><input type="checkbox" data-action="toggle-lost-all" aria-label="全选当前失物"></th><th>物品</th><th>类型</th><th>地点 / 日期</th><th>发布者</th><th>状态</th><th>举报</th><th>操作</th></tr></thead><tbody>
        ${posts.map((post) => `<tr><td><input type="checkbox" class="lost-select" value="${escapeHtml(post.id)}" aria-label="选择 ${escapeHtml(post.item_name)}"></td><td><strong>${escapeHtml(post.item_name)}</strong><br><span class="subtle">${escapeHtml(post.category)}</span></td><td>${badge(post.type)}</td><td>${escapeHtml(post.location)}<br><span class="subtle">${escapeHtml(post.event_date)}</span></td><td><strong>${escapeHtml(userLabel(post))}</strong><br><span class="subtle">内部用户 ID：${escapeHtml(post.owner_user_id || post.user_id || "—")}</span></td><td><select data-action="lost-status" data-id="${escapeHtml(post.id)}" data-type="${post.type}">${lostStatusOptions(post.type, post.status)}</select></td><td>${post.open_reports || 0}</td><td><div class="actions"><button class="ghost" data-action="lost-details" data-id="${escapeHtml(post.id)}">查看详情</button><button class="danger" data-action="delete-lost" data-id="${escapeHtml(post.id)}">删除</button></div></td></tr>`).join("")}
      </tbody></table></div>` : '<div class="empty">没有符合条件的失物信息</div>'}
      <h3 class="section-title">举报处理</h3>
      ${reports.length ? `<div class="table-wrap"><table><thead><tr><th>物品</th><th>原因</th><th>提交者</th><th>时间</th><th>状态</th></tr></thead><tbody>
        ${reports.map((report) => `<tr><td>${escapeHtml(report.item_name)}</td><td>${escapeHtml(report.reason)}</td><td><span class="subtle">${escapeHtml(userLabel(report))}</span></td><td>${formatTime(report.created_at)}</td><td><select data-action="report-status" data-id="${escapeHtml(report.id)}">${options(["OPEN","REVIEWED","CLOSED"], report.status)}</select></td></tr>`).join("")}
      </tbody></table></div>` : '<div class="empty">暂无举报</div>'}`;
  },

  async resources(filters = {}) {
    const itemParams = new URLSearchParams({
      q: filters.q || "",
      status: filters.status || "",
      category: filters.category || "",
    });
    const complaintParams = new URLSearchParams({
      status: filters.complaintStatus || "",
      user: filters.user || "",
    });
    const [items, complaints] = await Promise.all([
      api(`/api/v1/admin/resources?${itemParams}`),
      api(`/api/v1/admin/resource-complaints?${complaintParams}`),
    ]);
    content.innerHTML = `
      <article class="card">
        <h3>新增资料</h3>
        <p class="muted">只发布拥有权利、已获授权或明确可公开传播的外部链接。本阶段不允许学生上传文件，也不收取费用。</p>
        <form id="resource-create" class="stack">
          <div class="grid two-columns">
            <label>标题<input name="title" maxlength="160" required></label>
            <label>分类<input name="category" maxlength="80" placeholder="例如：考试资料" required></label>
            <label>来源标识<input name="sourceLabel" maxlength="100" placeholder="例如：课程教师公开页"></label>
            <label>适用范围<input name="audienceScope" maxlength="200" placeholder="例如：大一 · 高等数学"></label>
            <label>标签（逗号分隔）<input name="tags" maxlength="300" placeholder="高数,复习,公开资料"></label>
            <label>外部链接<input name="externalUrl" type="url" placeholder="https://..." required></label>
            <label>封面链接（可选）<input name="coverUrl" type="url" placeholder="https://..."></label>
            <label>排序<input name="sortOrder" type="number" value="0"></label>
            <label>发布状态<select name="publishStatus">${options(["DRAFT","PUBLISHED","UNLISTED"], "DRAFT")}</select></label>
            <label>可见范围<select name="visibility"><option value="MEMBERS">登录用户</option><option value="PUBLIC">预留公开</option></select></label>
          </div>
          <label>简介<textarea name="summary" maxlength="2000"></textarea></label>
          <label>使用说明<textarea name="usageNote" maxlength="1000">请确认资料来源合法并遵守版权与使用规则。</textarea></label>
          <label class="checkbox-row"><input type="checkbox" name="isFree" checked> 免费资料（付费能力仅预留，不启用）</label>
          <div><button class="primary">创建资料</button></div>
        </form>
      </article>
      <h3 class="section-title">资料条目</h3>
      <form id="resource-filter" class="toolbar">
        <label>关键词<input name="q" value="${escapeHtml(filters.q || "")}" placeholder="标题、简介或标签"></label>
        <label class="compact">分类<input name="category" value="${escapeHtml(filters.category || "")}"></label>
        <label class="compact">状态<select name="status">${options(["DRAFT","PUBLISHED","UNLISTED"], filters.status, true)}</select></label>
        <label>投诉用户<input name="user" value="${escapeHtml(filters.user || "")}" placeholder="完整手机号、昵称、内部 ID 或预约学号"></label>
        <label class="compact">投诉状态<select name="complaintStatus">${options(["OPEN","PROCESSING","RESOLVED","CLOSED"], filters.complaintStatus, true)}</select></label>
        <button class="primary">筛选</button>
      </form>
      ${items.length ? `<div class="table-wrap"><table><thead><tr><th>资料</th><th>分类 / 标签</th><th>来源</th><th>状态</th><th>排序</th><th>操作</th></tr></thead><tbody>
        ${items.map((item) => `<tr>
          <td><strong>${escapeHtml(item.title)}</strong><br><span class="subtle">${escapeHtml(item.summary || "—")}</span></td>
          <td>${escapeHtml(item.category)}<br><span class="subtle">${escapeHtml((item.tags || []).join(" · "))}</span></td>
          <td>${escapeHtml(item.source_label || "—")}<br><span class="subtle">${escapeHtml(item.external_url)}</span></td>
          <td>${badge(item.publish_status)}<br><span class="subtle">${item.is_free ? "免费" : "待配置付费"}</span></td>
          <td>${item.sort_order}</td>
          <td><div class="actions"><button class="ghost" data-action="edit-resource" data-resource="${escapeHtml(JSON.stringify(item))}">编辑</button><button class="${item.publish_status === "PUBLISHED" ? "danger" : "primary"}" data-action="toggle-resource" data-id="${escapeHtml(item.id)}" data-status="${escapeHtml(item.publish_status)}">${item.publish_status === "PUBLISHED" ? "下架" : "发布"}</button><button class="danger" data-action="delete-resource" data-id="${escapeHtml(item.id)}">删除</button></div></td>
        </tr>`).join("")}
      </tbody></table></div>` : '<div class="empty">没有符合条件的资料</div>'}
      <h3 class="section-title">侵权投诉与下架请求</h3>
      ${complaints.length ? `<div class="table-wrap"><table><thead><tr><th>资料</th><th>原因</th><th>用户</th><th>时间</th><th>处理</th></tr></thead><tbody>
        ${complaints.map((row) => `<tr><td>${escapeHtml(row.title)}</td><td><strong>${escapeHtml(row.reason)}</strong><br><span class="subtle">${escapeHtml(row.detail || "")}</span></td><td>${escapeHtml(userLabel(row))}<br><span class="subtle">${escapeHtml(row.phone || row.phone_masked || "")}</span></td><td>${formatTime(row.created_at)}</td><td><select data-action="resource-complaint-status" data-id="${escapeHtml(row.id)}">${options(["OPEN","PROCESSING","RESOLVED","CLOSED"], row.status)}</select></td></tr>`).join("")}
      </tbody></table></div>` : '<div class="empty">暂无资料投诉</div>'}`;
  },

  async announcements() {
    const rows = await api("/api/v1/admin/announcements");
    announcementRows = rows;
    content.innerHTML = `
      <article class="card">
        <h3>发布新公告</h3>
        <form id="announcement-form" class="stack">
          ${announcementFields()}
          <label>标题<input name="title" maxlength="100" required></label>
          <label>内容<textarea name="content" maxlength="4000" required></textarea></label>
          <label class="checkbox-row"><input type="checkbox" name="enabled" checked> 发布后立即在 App 配置接口中可见</label>
          <label class="checkbox-row"><input type="checkbox" name="serviceVisible" checked> 显示在 App 服务页“服务动态”</label>
          <label class="checkbox-row"><input type="checkbox" name="notificationEnabled"> 发送系统通知（App 同步后提醒）</label>
          <label class="checkbox-row"><input type="checkbox" name="popupEnabled"> 首次获取时显示 App 内弹窗</label>
          <p class="muted">系统通知和弹窗默认关闭；开启后由 App 启动或回到前台时同步生效。</p>
          <div><button class="primary">发布公告</button></div>
        </form>
      </article>
      <h3 class="section-title">公告记录</h3>
      <div class="grid">
        ${rows.length ? rows.map((row) => `<article class="card"><div class="card-header"><div><h3>${escapeHtml(row.title)}</h3><span class="subtle">${formatTime(row.published_at)}</span></div><div>${row.enabled ? badge("OPEN") : badge("CLOSED")}${row.serviceVisible ? badge("服务页") : badge("不显示")}${row.notificationEnabled ? badge("通知") : ""}${row.popupEnabled ? badge("弹窗") : ""}</div></div><p>${escapeHtml(row.content).replaceAll("\n", "<br>")}</p><div class="actions"><button class="ghost" data-action="edit-announcement" data-id="${escapeHtml(row.id)}" data-title="${escapeHtml(row.title)}" data-content="${escapeHtml(row.content)}" data-enabled="${row.enabled}" data-service-visible="${row.serviceVisible}" data-notification-enabled="${row.notificationEnabled}" data-popup-enabled="${row.popupEnabled}">编辑</button><button class="${row.enabled ? "danger" : "primary"}" data-action="toggle-announcement" data-id="${escapeHtml(row.id)}" data-enabled="${row.enabled}">${row.enabled ? "撤回" : "重新发布"}</button><button class="danger" data-action="delete-announcement" data-id="${escapeHtml(row.id)}">删除</button></div></article>`).join("") : '<div class="empty">还没有公告</div>'}
      </div>`;
  },

  async websiteAnnouncements() {
    const rows = await api("/api/v1/admin/website-announcements");
    content.innerHTML = `
      <article class="card">
        <h3>发布网站公告</h3>
        <p class="muted">网站公告只显示在 未来配置的产品官网，不会进入 App 公告或 App 服务动态。</p>
        <form id="website-announcement-form" class="stack">
          <label>标题<input name="title" maxlength="100" required></label>
          <label>内容<textarea name="content" maxlength="4000" required></textarea></label>
          <label class="checkbox-row"><input type="checkbox" name="enabled" checked> 发布后立即在官网显示</label>
          <div><button class="primary">发布网站公告</button></div>
        </form>
      </article>
      <h3 class="section-title">网站公告记录</h3>
      <div class="grid">
        ${rows.length ? rows.map((row) => `<article class="card"><div class="card-header"><div><h3>${escapeHtml(row.title)}</h3><span class="subtle">${formatTime(row.published_at || row.publishedAt)}</span></div><div>${row.enabled ? badge("OPEN") : badge("CLOSED")}</div></div><p>${escapeHtml(row.content).replaceAll("\n", "<br>")}</p><div class="actions"><button class="ghost" data-action="edit-website-announcement" data-id="${escapeHtml(row.id)}" data-title="${escapeHtml(row.title)}" data-content="${escapeHtml(row.content)}" data-enabled="${row.enabled}">编辑</button><button class="${row.enabled ? "danger" : "primary"}" data-action="toggle-website-announcement" data-id="${escapeHtml(row.id)}" data-enabled="${row.enabled}">${row.enabled ? "撤回" : "重新发布"}</button><button class="danger" data-action="delete-website-announcement" data-id="${escapeHtml(row.id)}">删除</button></div></article>`).join("") : '<div class="empty">还没有网站公告</div>'}
      </div>`;
  },

  async config(prefetched = null) {
    // navigate(page, filters) always supplies an object. Only a response that
    // actually contains updatePolicy is an intentional post-save prefetched
    // server readback; an empty navigation filter must still fetch the API.
    const data = prefetched?.updatePolicy
      ? prefetched
      : await api("/api/v1/admin/config");
    const version = data.version || {};
    const policy = data.updatePolicy || {};
    const appConfig = data.appConfig || {};
    const activeAppConfig = appConfig.active || {};
    const appConfigRevisions = appConfig.revisions || [];
    const availableVersions = data.availableVersions || [];
    const configuredCode = Number(version.latest_version_code || 1);
    const metadataState = version.metadata_verified ? "已自动校验" : "尚未完成自动校验";
    const minimumCode = Number(policy.minimumSupportedVersionCode || 1);
    const forceEnabled = Boolean(policy.forceUpdateEnabled);
    const affectedVersions = forceEnabled
      ? `versionCode ${Math.max(1, minimumCode - 1)} 及以下`
      : "无（强制更新已关闭）";
    content.innerHTML = `
      <div class="grid two-columns">
        <article class="card">
          <h3>Android Release Metadata</h3>
          <p class="muted">这里只维护已经发布的 APK 事实，不控制当前是否强制更新。</p>
          <form id="version-form" class="stack">
            <label>最新发布版本名称（给用户看的版本）<input name="latestVersionName" value="${escapeHtml(version.latest_version_name || "")}" placeholder="例如 1.0.4" required></label>
            <label>Android 内部版本号（只用于递增比较）<input type="number" min="1" name="latestVersionCode" value="${configuredCode}" required><span class="subtle">后台展示为 V${escapeHtml(version.latest_version_name || "—")}（${configuredCode}）。客户端更新只比较这个整数。</span></label>
            <label>更新说明<textarea name="updateMessage">${escapeHtml(version.update_message || "")}</textarea></label>
            <label>正式 APK 下载地址<input name="downloadUrl" value="${escapeHtml(version.download_url || "")}" placeholder="https://..." required></label>
            <label>发布状态<select name="status">${options(["DRAFT","PUBLISHED","DISABLED"], version.status || "DRAFT")}</select></label>
            <label>APK SHA-256（自动读取）<input name="apkSha256" maxlength="64" value="${escapeHtml(version.apk_sha256 || "")}" readonly></label>
            <label>APK 大小（自动读取）<input type="number" min="1" name="apkSize" value="${version.apk_size || ""}" readonly></label>
            <label>正式包名（自动校验）<input name="packageName" value="${escapeHtml(version.package_name || "com.mychuannong.app")}" readonly></label>
            <label>正式签名证书 SHA-256（自动校验）<input name="signingCertificateSha256" value="${escapeHtml(version.signing_certificate_sha256 || "")}" readonly></label>
            <input type="hidden" name="metadataVerified" value="${version.metadata_verified ? "true" : "false"}">
            <p class="muted">APK 元数据状态：<strong>${metadataState}</strong>。Draft 可暂缺发布材料；保存为 Published 时，服务端必须验证下载文件大小、SHA-256、包名与正式签名证书。</p>
            <button class="primary">保存并校验版本配置</button>
          </form>
        </article>
        <article class="card">
          <h3>Update Policy 实时控制</h3>
          <p class="muted">保存后会立即重新 GET 服务器状态；页面只显示回读确认后的结果。</p>
          <form id="update-policy-form" class="stack">
            <div class="sub-card">
              <strong>当前最新版本</strong>
              <p>V${escapeHtml(version.latest_version_name || "—")} / versionCode ${configuredCode}</p>
            </div>
            <label class="checkbox-row"><input type="checkbox" name="updateEnabled" ${policy.updateEnabled ? "checked" : ""}> 开启更新检查</label>
            <label class="checkbox-row"><input type="checkbox" name="forceUpdateEnabled" ${forceEnabled ? "checked" : ""}> 开启强制更新</label>
            <label>最低支持版本<select name="minimumSupportedVersionCode">${releaseVersionOptions(availableVersions, minimumCode)}</select><span class="subtle">强制更新开启时，仅低于此 versionCode 的旧客户端会被阻止；关闭强制更新后所有更新都可跳过。</span></label>
            <input type="hidden" name="expectedPolicyRevision" value="${Number(policy.policyRevision || 0)}">
            <div class="sub-card">
              <p><strong>当前策略 Revision：</strong>${Number(policy.policyRevision || 0)}</p>
              <p><strong>最后修改时间：</strong>${formatTime(policy.updatedAt)}</p>
              <p><strong>预计受影响版本：</strong>${escapeHtml(affectedVersions)}</p>
            </div>
            <button class="primary">保存并回读服务器策略</button>
          </form>
        </article>
        <article class="card">
          <h3>功能开放与维护</h3>
          <p class="muted">关闭或维护时，App 仍保留入口，并向用户显示这里填写的说明。</p>
          <div class="stack">
            ${data.flags.filter((flag) => ["secondhand","lost_found","resource_library"].includes(flag.key)).map((flag) => `
              <form class="feature-form sub-card" data-key="${escapeHtml(flag.key)}">
                <div class="card-header"><strong>${flag.key === "secondhand" ? "二手好物" : flag.key === "lost_found" ? "失物招领" : "资料库"}</strong>${badge(flag.payload?.status || (flag.enabled ? "OPEN" : "CLOSED"))}</div>
                <label>运行状态<select name="status">${options(["OPEN","CLOSED","MAINTENANCE"], flag.payload?.status || (flag.enabled ? "OPEN" : "CLOSED"))}</select></label>
                <label>用户提示<textarea name="maintenanceNote" placeholder="例如：场馆规则调整中，预计今晚恢复">${escapeHtml(flag.payload?.maintenanceNote || "")}</textarea></label>
                <label>预计恢复时间（可留空）<input type="datetime-local" name="expectedRestoreAt" value="${datetimeLocal(flag.payload?.expectedRestoreAt)}"></label>
                <label>修改备注（可选）<input name="changeNote" maxlength="300" value="${escapeHtml(flag.changeNote || "")}" placeholder="仅用于管理员审计"></label>
                <p class="muted">最后修改：${formatTime(flag.updatedAt)} · ${escapeHtml(flag.updatedBy || "系统初始化")}</p>
                ${flag.key === "resource_library" ? `<label class="checkbox-row"><input type="checkbox" name="allowGuestBrowsing" ${flag.payload?.allowGuestBrowsing ? "checked" : ""}> 允许未登录用户只浏览公开资料</label>` : ""}
                <button class="primary">保存状态</button>
              </form>`).join("")}
          </div>
        </article>
        <article class="card">
          <h3>Remote Config 动态配置</h3>
          <p class="muted">只允许发布已通过服务端白名单、URL 安全和大小限制校验的配置。App 会按 Server → Cache → Built-in Default 使用，并保留 last-known-good。</p>
          <div class="sub-card">
            <strong>当前生效版本</strong>
            <p>Config V${Number(appConfig.configVersion || 0)} · Revision ${Number(appConfig.activeRevision || 0)} · ${escapeHtml(activeAppConfig.configHash || "—")}</p>
            <p class="subtle">更新时间：${formatTime(activeAppConfig.updatedAt)} · 状态：${escapeHtml(activeAppConfig.status || "—")}</p>
          </div>
          <form id="app-config-draft-form" class="stack">
            <label>配置 JSON（只填写 config 对象）<textarea name="configJson" rows="18" spellcheck="false" required>${escapeHtml(JSON.stringify(activeAppConfig.config || {}, null, 2))}</textarea></label>
            <div class="grid two-columns">
              <label>最低 App versionCode<input type="number" min="0" name="minAppVersionCode" value="${Number(activeAppConfig.minAppVersionCode || 0)}"></label>
              <label>最高 App versionCode（可留空）<input type="number" min="0" name="maxAppVersionCode" value="${activeAppConfig.maxAppVersionCode ?? ""}"></label>
            </div>
            <label>草稿备注（可选）<input name="reason" maxlength="300" placeholder="例如：Config V2 调整首页入口顺序"></label>
            <button class="primary">校验并保存草稿</button>
          </form>
          <form id="app-config-activate-form" class="stack">
            <label>激活草稿<select name="revision" required>${appConfigRevisions.filter((row) => row.status === "DRAFT").map((row) => `<option value="${Number(row.revision)}">Config V${Number(row.configVersion)} · Revision ${Number(row.revision)}</option>`).join("") || '<option value="">暂无 DRAFT</option>'}</select></label>
            <input type="hidden" name="expectedActiveRevision" value="${Number(appConfig.activeRevision || 0)}">
            <button class="primary" ${appConfigRevisions.some((row) => row.status === "DRAFT") ? "" : "disabled"}>激活选中配置</button>
          </form>
          <form id="app-config-rollback-form" class="stack">
            <label>回滚目标<select name="targetRevision" required>${appConfigRevisions.filter((row) => Number(row.revision) !== Number(appConfig.activeRevision)).map((row) => `<option value="${Number(row.revision)}">Config V${Number(row.configVersion)} · Revision ${Number(row.revision)} · ${escapeHtml(row.status || "")}</option>`).join("") || '<option value="">暂无可回滚版本</option>'}</select></label>
            <input type="hidden" name="expectedActiveRevision" value="${Number(appConfig.activeRevision || 0)}">
            <button class="danger" ${appConfigRevisions.some((row) => Number(row.revision) !== Number(appConfig.activeRevision)) ? "" : "disabled"}>回滚为新的 Config 版本</button>
          </form>
          <div class="sub-card"><strong>最近配置 Revision</strong><ul>${appConfigRevisions.slice(0, 8).map((row) => `<li>Revision ${Number(row.revision)} · Config V${Number(row.configVersion)} · ${escapeHtml(row.status || "")} · ${escapeHtml(row.configHash || "")}</li>`).join("") || "<li>暂无记录</li>"}</ul></div>
        </article>
      </div>`;
  },

  async feedback(filters = {}) {
    const params = new URLSearchParams(filters);
    const rows = await api(`/api/v1/admin/feedback?${params}`);
    content.innerHTML = `${filterSummary(filters)}
      <form id="feedback-filter" class="toolbar">
        <label class="compact">状态<select name="status">${feedbackStatusOptions(filters.status, true)}</select></label>
        <label class="compact">类型<select name="category">${feedbackCategoryOptions(filters.category, true)}</select></label>
        <label>关键词<input name="q" value="${escapeHtml(filters.q || "")}" placeholder="标题、内容或反馈编号"></label>
        <label>用户<input name="user" value="${escapeHtml(filters.user || "")}" placeholder="完整手机号、昵称或内部 ID"></label>
        <label class="compact">开始日期<input type="date" name="from" value="${escapeHtml(filters.from || "")}"></label>
        <label class="compact">结束日期<input type="date" name="to" value="${escapeHtml(filters.to || "")}"></label>
        <label class="compact">时间顺序<select name="sort"><option value="DESC" ${filters.sort !== "ASC" ? "selected" : ""}>最新优先</option><option value="ASC" ${filters.sort === "ASC" ? "selected" : ""}>最早优先</option></select></label>
        <button class="primary">筛选</button>
      </form>
      ${rows.length ? `<div class="table-wrap"><table class="feedback-table"><thead><tr><th>编号</th><th>类型</th><th>状态</th><th>用户</th><th>内容摘要</th><th>提交时间</th><th>最后更新</th><th>操作</th></tr></thead><tbody>
        ${rows.map((row) => `<tr><td class="mono-cell">${escapeHtml(row.id.slice(0, 8))}</td><td>${escapeHtml(feedbackCategoryText(row.category))}</td><td>${feedbackBadge(row.workflow_status)}</td><td><strong>${escapeHtml(row.nickname || "川农同学")}</strong><br><span class="subtle">${escapeHtml(row.phone_masked || "未绑定手机号")}</span></td><td class="feedback-summary"><strong>${escapeHtml(row.title || "未命名反馈")}</strong><span>${escapeHtml(row.content)}</span></td><td>${formatTime(row.created_at)}</td><td>${formatTime(row.updated_at)}</td><td><button class="ghost" data-action="feedback-details" data-id="${escapeHtml(row.id)}">查看详情</button></td></tr>`).join("")}
      </tbody></table></div>` : '<div class="empty">暂无用户反馈</div>'}`;
  },

  async lostReports(filters = {}) {
    const rows = await api(`/api/v1/admin/lost-found-reports?${new URLSearchParams({status:filters.status || "",user:filters.user || ""})}`);
    content.innerHTML = `${linkedFilterSummary(filters)}
      ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>物品</th><th>原因</th><th>提交用户</th><th>时间</th><th>状态</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.item_name || row.post_id)}</td><td>${escapeHtml(row.reason)}</td><td>${escapeHtml(row.nickname || row.phone || row.phone_masked || "用户")}</td><td>${formatTime(row.created_at)}</td><td><select data-action="report-status" data-id="${escapeHtml(row.id)}">${options(["OPEN","REVIEWED","CLOSED"], row.status)}</select></td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">暂无符合条件的失物举报</div>'}`;
  },

  async lostClaims(filters = {}) {
    const rows = await api(`/api/v1/admin/lost-found-claims?${new URLSearchParams({user:filters.user || ""})}`);
    content.innerHTML = `${linkedFilterSummary(filters)}${rows.length ? `<div class="table-wrap"><table><thead><tr><th>物品</th><th>申请用户</th><th>时间</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.item_name || row.post_id)}</td><td>${escapeHtml(row.nickname || row.phone || row.phone_masked || "用户")}</td><td>${formatTime(row.created_at)}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">暂无认领申请</div>'}`;
  },

  async resourceComplaints(filters = {}) {
    const params = new URLSearchParams(filters);
    const rows = await api(`/api/v1/admin/resource-complaints?${params}`);
    content.innerHTML = `${Object.keys(filters).length ? `<div class="filter-summary"><span>当前筛选：${escapeHtml(Object.values(filters).join(" · "))}</span><button class="ghost" data-action="clear-page-filter">清除筛选</button></div>` : ""}
      <form id="resource-complaint-filter" class="toolbar"><label class="compact">状态<select name="status">${options(["OPEN","PROCESSING","RESOLVED","CLOSED"], filters.status, true)}</select></label><label>用户<input name="user" value="${escapeHtml(filters.user || "")}" placeholder="完整手机号、昵称或内部 ID"></label><button class="primary">筛选</button></form>
      ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>资料</th><th>原因</th><th>用户</th><th>时间</th><th>状态</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.resource_title || row.resource_id)}</td><td>${escapeHtml(row.reason)}<br><span class="subtle">${escapeHtml(row.detail || "")}</span></td><td>${escapeHtml(row.nickname || row.phone_masked || "用户")}</td><td>${formatTime(row.created_at)}</td><td><select data-action="resource-complaint-status" data-id="${escapeHtml(row.id)}">${options(["OPEN","PROCESSING","RESOLVED","CLOSED"], row.status)}</select></td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">暂无符合条件的资料投诉</div>'}`;
  },

  async resourceActivity(filters = {}) {
    const rows = await api(`/api/v1/admin/resource-activity?${new URLSearchParams({user:filters.user || "",kind:filters.kind || "views"})}`);
    content.innerHTML = `${linkedFilterSummary(filters)}${rows.length ? `<div class="table-wrap"><table><thead><tr><th>资料</th><th>用户</th><th>次数</th><th>时间</th></tr></thead><tbody>${rows.map(row => `<tr><td>${escapeHtml(row.title)}<br><span class="subtle">${escapeHtml(row.resource_id)}</span></td><td>${escapeHtml(row.nickname)} · ${escapeHtml(row.phone || row.phone_masked || "")}</td><td>${row.view_count || 1}</td><td>${formatTime(row.last_viewed_at || row.created_at)}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">暂无记录</div>'}`;
  },

  async userEvents(filters = {}) {
    const normalized = {...filters};
    if (normalized.today) { normalized.from = new Date().setHours(0,0,0,0); delete normalized.today; }
    if (normalized.last24h) { normalized.from = Date.now()-86400000; delete normalized.last24h; }
    const params = new URLSearchParams({...normalized, limit: "100"});
    const data = await api(`/api/v1/admin/user-events?${params}`);
    const filterLabels = {...filters};
    if (filterLabels.firstLoginToday) filterLabels.firstLoginToday = "今日首次登录账号";
    const summary = Object.keys(filters).length ? `<div class="filter-summary"><span>当前筛选：${escapeHtml(Object.values(filterLabels).join(" · "))}</span><button class="ghost" data-action="clear-page-filter">清除筛选</button></div>` : "";
    content.innerHTML = `${summary}<article class="card"><p class="muted">仅记录我的川农账号使用后端服务所需的最小事件，默认保留 ${data.retentionDays} 天；不包含教务账号、课表、成绩、密码、Cookie、Session 或完整 Token。</p></article>
      <form id="user-event-filter" class="toolbar"><label>用户<input name="user" value="${escapeHtml(filters.user || "")}" placeholder="内部 ID、昵称或完整手机号"></label><label class="compact">模块<input name="module" value="${escapeHtml(filters.module || "")}" placeholder="如 ACCOUNT"></label><label class="compact">事件<input name="eventType" value="${escapeHtml(filters.eventType || "")}" placeholder="如 LOGIN_FAILURE"></label><label class="compact">结果<select name="result">${options(["SUCCESS","FAILURE","DENIED"], filters.result, true)}</select></label><button class="primary">筛选</button></form>
      <p class="muted">共 ${data.total} 条，本页最多显示 ${data.limit} 条。</p>
      ${data.items.length ? `<div class="table-wrap"><table><thead><tr><th>时间</th><th>用户</th><th>模块 / 事件</th><th>结果</th><th>目标</th><th>风险</th></tr></thead><tbody>${data.items.map((row) => `<tr><td>${formatTime(row.created_at)}</td><td>${escapeHtml(row.nickname || "已注销用户")}<br><span class="subtle">${escapeHtml(row.phone_masked || row.user_id || "—")}</span></td><td>${escapeHtml(row.module)}<br><span class="subtle">${escapeHtml(row.event_type)}</span></td><td>${badge(row.result)}</td><td>${escapeHtml(row.target_type || "—")} · ${escapeHtml(row.target_id || "—")}</td><td>${escapeHtml(row.risk_flag || "—")}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">没有符合条件的用户行为记录</div>'}`;
  },

  async riskCandidates(filters = {}) {
    const rows = await api(`/api/v1/admin/risk-candidates?${new URLSearchParams(filters)}`);
    content.innerHTML = `${Object.keys(filters).length ? `<div class="filter-summary"><span>当前筛选：${escapeHtml(filters.status || "")}</span><button class="ghost" data-action="clear-page-filter">清除筛选</button></div>` : ""}<article class="card"><p class="muted">系统只生成可解释的人工复核候选，不会自动封禁或注销账号。</p></article>
      ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>用户</th><th>触发规则</th><th>次数 / 时间范围</th><th>账号状态</th><th>处理</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.nickname)}<br><span class="subtle">${escapeHtml(row.phone_masked || row.user_id)}</span></td><td>${escapeHtml(row.rule_label)}<br><span class="subtle">${escapeHtml(row.rule_key)}</span></td><td>${row.trigger_count} 次<br><span class="subtle">${formatTime(row.window_started_at)} — ${formatTime(row.window_ended_at)}</span></td><td>${accountBadge(row.account_status)} · ${escapeHtml(row.status)}</td><td><button class="ghost" data-action="review-risk" data-id="${escapeHtml(row.id)}">人工处理</button></td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">暂无异常候选</div>'}`;
  },

  async adminAudit() {
    const rows = await api("/api/v1/admin/audit-logs?limit=200");
    content.innerHTML = `<article class="card"><p class="muted">仅超级管理员可访问。只显示真实管理员 Actor 执行的管理动作，不保存密码、完整 Token、教务凭据或敏感字段原文。</p></article>${rows.length ? `<div class="table-wrap"><table><thead><tr><th>时间</th><th>管理员</th><th>操作</th><th>对象</th><th>最小摘要</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${formatTime(row.created_at)}</td><td>${escapeHtml(row.actor_name || "管理员")}</td><td>${escapeHtml(row.action)}</td><td>${escapeHtml(row.entity_type)} · ${escapeHtml(row.entity_id || "—")}</td><td class="feedback-summary"><span>${escapeHtml(row.details_json || "{}")}</span></td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">暂无管理员操作记录</div>'}`;
  },
};

function options(values, selected, allowBlank = false) {
  return `${allowBlank ? '<option value="">全部</option>' : ""}${values.map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${escapeHtml(statusText(value))}</option>`).join("")}`;
}

function drilldownPagination(result, page, filters) {
  if (!result.total || result.total <= result.limit) return "";
  const previousOffset = Math.max(0, result.offset - result.limit);
  const nextOffset = result.offset + result.limit;
  const previousFilter = {...filters, offset:String(previousOffset)};
  const nextFilter = {...filters, offset:String(nextOffset)};
  return `<div class="pagination"><span>显示 ${result.offset + 1}–${Math.min(result.total, result.offset + result.items.length)} / ${result.total}</span><div class="actions"><button type="button" class="ghost" data-action="drilldown-page" data-page="${page}" data-filter="${escapeHtml(JSON.stringify(previousFilter))}" ${result.offset === 0 ? "disabled" : ""}>上一页</button><button type="button" class="ghost" data-action="drilldown-page" data-page="${page}" data-filter="${escapeHtml(JSON.stringify(nextFilter))}" ${nextOffset >= result.total ? "disabled" : ""}>下一页</button></div></div>`;
}

function releaseVersionOptions(versions, selected) {
  const items = [...(versions || [])];
  if (!items.some((item) => Number(item.version_code) === Number(selected))) {
    items.push({
      version_code: selected,
      version_name: "当前配置版本",
      status: "DRAFT",
    });
  }
  return items
    .sort((a, b) => Number(b.version_code) - Number(a.version_code))
    .map((item) => `<option value="${Number(item.version_code)}" ${Number(item.version_code) === Number(selected) ? "selected" : ""}>V${escapeHtml(item.version_name)}（${Number(item.version_code)}）</option>`)
    .join("");
}

function lostStatusOptions(type, selected) {
  const values = type === "LOST"
    ? [["SEARCHING", "寻找中"], ["FOUND", "已找回"]]
    : [["WAITING", "等待认领"], ["CLAIMED", "已认领"], ["RETURNED", "已归还"]];
  return values.map(([value, label]) => `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`).join("");
}

function openDialog(title, html, onConfirm, confirmText = "保存") {
  const dialog = $("#dialog");
  const form = $("#dialog-form");
  const confirmButton = $("#dialog-confirm");
  $("#dialog-content").innerHTML = `<h3>${escapeHtml(title)}</h3>${html}`;
  confirmButton.textContent = confirmText;
  confirmButton.type = "submit";
  confirmButton.disabled = false;
  dialog.returnValue = "";
  dialog.showModal();
  const cancelButton = $("#dialog-cancel");
  if (cancelButton) {
    cancelButton.onclick = () => dialog.close("cancel");
  }
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (event.submitter?.value === "cancel") {
      dialog.close("cancel");
      return;
    }
    confirmButton.disabled = true;
    try {
      await onConfirm(new FormData(form));
      dialog.close("default");
    } catch (error) {
      toast(error.message, true);
      confirmButton.disabled = false;
    }
  };
  dialog.onclose = () => {
    clearTimeout(state.phoneRevealTimer);
    clearTimeout(state.profileRevealTimer);
    state.phoneRevealTimer = null;
    state.profileRevealTimer = null;
    form.onsubmit = null;
    const cancelButton = $("#dialog-cancel");
    if (cancelButton) cancelButton.onclick = null;
  };
}

async function openInventory(venueId) {
  const venue = state.venues.find((item) => item.id === venueId);
  if (!venue) return;
  const today = new Date().toISOString().slice(0, 10);
  openDialog(
    `${venue.name} · 日期库存`,
    `<label>日期<input id="inventory-date" type="date" value="${today}"></label><div id="inventory-content" class="empty">正在加载…</div>`,
    async () => {},
    "关闭",
  );
  $("#dialog-confirm").type = "button";
  $("#dialog-confirm").addEventListener("click", () => $("#dialog").close("cancel"), { once: true });
  const load = async () => {
    const selectedDate = $("#inventory-date").value;
    const blocked = await api(`/api/v1/admin/inventory?venueId=${encodeURIComponent(venue.id)}&date=${selectedDate}`);
    const blockedMap = new Map(blocked.map((item) => [`${item.time_slot}-${item.court_number}`, item]));
    $("#inventory-content").className = "inventory-grid";
    $("#inventory-content").innerHTML = venue.timeSlots.map((slot) =>
      Array.from({ length: venue.court_count }, (_, index) => {
        const court = index + 1;
        const item = blockedMap.get(`${slot.timeSlot}-${court}`);
        return `<button type="button" class="inventory-cell ${item ? "blocked" : ""}" data-action="toggle-inventory" data-venue="${escapeHtml(venue.id)}" data-date="${selectedDate}" data-slot="${escapeHtml(slot.timeSlot)}" data-court="${court}" data-block-id="${escapeHtml(item?.id || "")}"><strong>${court} ${escapeHtml(venue.unit_label)}</strong><span class="subtle">${escapeHtml(slot.timeSlot)}<br>${item ? "已关闭" : "可预约"}</span></button>`;
      }).join("")
    ).join("");
  };
  $("#inventory-date").addEventListener("change", load);
  await load();
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#login-error").textContent = "";
  try {
    const data = await api("/api/v1/auth/login", {
      method: "POST",
      body: {
        username: $("#login-username").value,
        password: $("#login-password").value,
      },
    });
    if (data.user.role !== "ADMIN") throw new Error("该账号没有管理员权限");
    state.token = data.token;
    sessionStorage.setItem("myChuannongAdminToken", state.token);
    $("#login-password").value = "";
    await showApp();
  } catch (error) {
    $("#login-error").textContent = error.message;
  }
});

$("#logout-button").addEventListener("click", async () => {
  try { await api("/api/v1/auth/logout", { method: "POST", body: {} }); } catch (_) {}
  showLogin();
});

$("#nav").addEventListener("click", (event) => {
  const button = event.target.closest("[data-page]");
  if (button) navigate(button.dataset.page);
});

content.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const values = Object.fromEntries(new FormData(form));
  try {
    if (form.id === "reservation-filter") return renderers.reservations(values);
    if (form.id === "user-filter") return renderers.users(values);
    if (form.id === "lost-filter") return renderers.lost(values);
    if (form.id === "feedback-filter") return renderers.feedback(values);
    if (form.id === "resource-filter") return renderers.resources(values);
    if (form.id === "resource-complaint-filter") return renderers.resourceComplaints(values);
    if (form.id === "user-event-filter") return renderers.userEvents(values);
    if (form.id === "resource-create") {
      await api("/api/v1/admin/resources", {
        method: "POST",
        body: {
          ...values,
          tags: values.tags,
          sortOrder: Number(values.sortOrder || 0),
          isFree: form.isFree.checked,
        },
      });
      toast("资料已创建");
      return renderers.resources();
    }
    if (form.id === "rules-form") {
      await api("/api/v1/admin/reservation-rules", {
        method: "PUT",
        body: {
          maxActivePerDay: Number(values.maxActivePerDay),
          maxUserCancellationsPerDay: Number(values.maxUserCancellationsPerDay),
          cancelCutoffMinutes: Number(values.cancelCutoffMinutes),
          restrictionHoursAfterExcessCancel: Number(values.restrictionHoursAfterExcessCancel),
          reservationProfileMode: values.reservationProfileMode,
          reason: values.reason,
        },
      });
      toast("预约规则已保存");
      return renderers.rules();
    }
    if (form.classList.contains("feature-form")) {
      const status = values.status;
      await api(`/api/v1/admin/flags/${encodeURIComponent(form.dataset.key)}`, {
        method: "PUT",
        body: {
          enabled: status !== "CLOSED",
          changeNote: values.changeNote,
          payload: {
            status,
            maintenanceNote: values.maintenanceNote,
            expectedRestoreAt: values.expectedRestoreAt
              ? new Date(values.expectedRestoreAt).getTime()
              : null,
            ...(form.dataset.key === "resource_library"
              ? { allowGuestBrowsing: form.allowGuestBrowsing.checked }
              : {}),
          },
        },
      });
      toast("功能状态已保存");
      return renderers.config();
    }
    if (form.id === "announcement-form") {
      const metadata = await announcementMetadata(new FormData(form));
      await api("/api/v1/admin/announcements", {
        method: "POST",
        body: {
          ...values,
          ...metadata,
          enabled: form.enabled.checked,
          serviceVisible: form.serviceVisible.checked,
          notificationEnabled: form.notificationEnabled.checked,
          popupEnabled: form.popupEnabled.checked,
        },
      });
      toast("公告已发布");
      return renderers.announcements();
    }
    if (form.id === "website-announcement-form") {
      await api("/api/v1/admin/website-announcements", {
        method: "POST",
        body: { ...values, enabled: form.enabled.checked },
      });
      toast("网站公告已发布");
      return renderers.websiteAnnouncements();
    }
    if (form.id === "version-form") {
      await api("/api/v1/admin/version/android", {
        method: "PUT",
        body: {
          ...values,
          latestVersionCode: Number(values.latestVersionCode),
          apkSize: values.apkSize ? Number(values.apkSize) : null,
        },
      });
      toast("版本配置已保存");
      return renderers.config();
    }
    if (form.id === "update-policy-form") {
      const requested = {
        updateEnabled: form.updateEnabled.checked,
        forceUpdateEnabled: form.forceUpdateEnabled.checked,
        minimumSupportedVersionCode: Number(values.minimumSupportedVersionCode),
        expectedPolicyRevision: Number(values.expectedPolicyRevision),
      };
      const saved = await api("/api/v1/admin/update-policy/android", {
        method: "PUT",
        body: requested,
      });
      const confirmed = await api("/api/v1/admin/config");
      const actual = confirmed.updatePolicy || {};
      if (
        actual.policyRevision !== saved.policyRevision ||
        actual.updateEnabled !== requested.updateEnabled ||
        actual.forceUpdateEnabled !== requested.forceUpdateEnabled ||
        actual.minimumSupportedVersionCode !== requested.minimumSupportedVersionCode
      ) {
        throw new Error("服务器策略回读不一致，保存结果未确认");
      }
      toast(`更新策略已保存并回读确认（Revision ${actual.policyRevision}）`);
      return renderers.config(confirmed);
    }
    if (form.id === "app-config-draft-form") {
      let config;
      try {
        config = JSON.parse(values.configJson);
      } catch (_) {
        throw new Error("配置 JSON 格式无效");
      }
      const validated = await api("/api/v1/admin/app-config/validate", {
        method: "POST",
        body: { config },
      });
      await api("/api/v1/admin/app-config/revisions", {
        method: "POST",
        body: {
          config: validated.config,
          minAppVersionCode: Number(values.minAppVersionCode || 0),
          maxAppVersionCode: values.maxAppVersionCode ? Number(values.maxAppVersionCode) : null,
          reason: values.reason || "",
        },
      });
      toast(`Remote Config 已校验并保存（${validated.configHash}）`);
      return renderers.config();
    }
    if (form.id === "app-config-activate-form") {
      const saved = await api("/api/v1/admin/app-config/activate", {
        method: "POST",
        body: {
          revision: Number(values.revision),
          expectedActiveRevision: Number(values.expectedActiveRevision),
        },
      });
      const confirmed = await api("/api/v1/admin/config");
      if (confirmed.appConfig?.activeRevision !== saved.activeRevision) {
        throw new Error("Remote Config 激活回读不一致，结果未确认");
      }
      toast(`Remote Config 已激活（Config V${confirmed.appConfig.configVersion}）`);
      return renderers.config(confirmed);
    }
    if (form.id === "app-config-rollback-form") {
      const saved = await api("/api/v1/admin/app-config/rollback", {
        method: "POST",
        body: {
          targetRevision: Number(values.targetRevision),
          expectedActiveRevision: Number(values.expectedActiveRevision),
        },
      });
      const confirmed = await api("/api/v1/admin/config");
      if (confirmed.appConfig?.activeRevision !== saved.activeRevision) {
        throw new Error("Remote Config 回滚回读不一致，结果未确认");
      }
      toast(`Remote Config 已回滚为新的 Config V${confirmed.appConfig.configVersion}`);
      return renderers.config(confirmed);
    }
  } catch (error) {
    toast(error.message, true);
  }
});

content.addEventListener("change", async (event) => {
  const target = event.target;
  try {
    if (target.classList.contains("user-select")) {
      refreshUserBulkActions();
      return;
    }
    if (target.dataset.action === "lost-status") {
      await api(`/api/v1/admin/lost-found/${encodeURIComponent(target.dataset.id)}`, {
        method: "PATCH",
        body: { status: target.value },
      });
      toast("失物状态已更新");
    }
    if (target.dataset.action === "report-status") {
      await api(`/api/v1/admin/lost-found-reports/${encodeURIComponent(target.dataset.id)}`, {
        method: "PATCH",
        body: { status: target.value },
      });
      toast("举报处理状态已更新");
    }
    if (target.dataset.action === "feedback-status") {
      await api(`/api/v1/admin/feedback/${encodeURIComponent(target.dataset.id)}`, {
        method: "PATCH",
        body: { status: target.value },
      });
      toast("反馈状态已更新");
    }
    if (target.dataset.action === "resource-complaint-status") {
      await api(`/api/v1/admin/resource-complaints/${encodeURIComponent(target.dataset.id)}`, {
        method: "PATCH",
        body: { status: target.value },
      });
      toast("资料投诉状态已更新");
    }
  } catch (error) {
    toast(error.message, true);
    navigate(state.page);
  }
});

$("#dialog-content").addEventListener("click", (event) => {
  const deleteButton = event.target.closest('[data-action="delete-user-account"]');
  if (deleteButton) {
    const user = JSON.parse(deleteButton.dataset.user);
    $("#dialog").close();
    openDeleteUserAccountDialog(user);
    return;
  }
  const button = event.target.closest('[data-action="user-related"]');
  if (!button) return;
  $("#dialog").close();
  navigate(button.dataset.page, {
    user: button.dataset.userId,
    userLabel: button.dataset.userLabel,
    kind: button.dataset.kind || "",
  });
});

content.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  try {
    if (action === "reload") return navigate(state.page);
    if (action === "metric-nav") {
      state.overviewScrollY = window.scrollY;
      return navigate(button.dataset.page, JSON.parse(button.dataset.filter || "{}"));
    }
    if (action === "back-overview") return navigate("overview");
    if (action === "drilldown-page") return navigate(button.dataset.page, JSON.parse(button.dataset.filter || "{}"));
    if (action === "clear-page-filter") return navigate(state.page, {});
    if (action === "return-user-profile") {
      await navigate("users", {q: button.dataset.userId});
      document.querySelector(`[data-action="user-details"][data-id="${CSS.escape(button.dataset.userId)}"]`)?.click();
      return;
    }
    if (action === "clear-reservation-filter") return renderers.reservations();
    if (action === "toggle-lost-all") {
      document.querySelectorAll(".lost-select").forEach((checkbox) => {
        checkbox.checked = button.checked;
      });
      return;
    }
    if (action === "toggle-user-all") {
      document.querySelectorAll(".user-select:not(:disabled)").forEach((checkbox) => {
        checkbox.checked = button.checked;
      });
      refreshUserBulkActions();
      return;
    }
    if (action === "bulk-limit-users") return openBulkUserAction("limit");
    if (action === "bulk-restore-users") return openBulkUserAction("restore");
    if (action === "bulk-delete-users") return openBulkUserAction("delete");
    if (action === "bulk-purge-users") return openBulkUserAction("purge");
    if (action === "reservations-page") {
      return renderers.reservations({...state.filters, page: button.dataset.page});
    }
    if (action === "create-admin") {
      return openDialog("创建运营管理员", `<div class="stack"><label>用户名<input name="username" maxlength="48" required></label><label>显示名称<input name="nickname" maxlength="40" required></label><label>初始密码<input name="password" type="password" minlength="12" required></label><label>角色<select name="adminRole"><option value="SPORTS_OPERATOR">体育预约运营管理员</option><option value="LOST_FOUND_OPERATOR">失物招领运营管理员</option><option value="LIBRARY_OPERATOR">资料库运营管理员</option><option value="CONTENT_OPERATOR">内容运营管理员</option></select></label><p class="muted">权限由服务端按角色强制执行，隐藏菜单不等于授权。</p></div>`, async form => {
        await api("/api/v1/admin/admins", {method:"POST", body:Object.fromEntries(form)});
        toast("运营管理员已创建"); renderers.admins();
      });
    }
    if (action === "edit-admin") {
      const item = JSON.parse(button.dataset.admin);
      return openDialog("编辑管理员", `<div class="stack"><p><strong>${escapeHtml(item.nickname)}</strong> · ${escapeHtml(item.username)}</p><label>显示名称<input name="nickname" maxlength="40" value="${escapeHtml(item.nickname)}"></label><label>角色<select name="adminRole">${["SUPER_ADMIN","SPORTS_OPERATOR","LOST_FOUND_OPERATOR","LIBRARY_OPERATOR","CONTENT_OPERATOR"].map(role => `<option value="${role}" ${role===item.adminRole?"selected":""}>${roleLabels[role] || role}</option>`).join("")}</select></label><label class="checkbox-row"><input type="checkbox" name="enabled" ${item.enabled?"checked":""}> 启用此管理员</label><label>重置密码（留空不修改）<input type="password" name="password" minlength="12"></label><p class="muted">不能停用或降级最后一个启用的超级管理员；不会修改当前登录账号自身。</p></div>`, async form => {
        await api(`/api/v1/admin/admins/${encodeURIComponent(item.id)}`, {method:"PATCH",body:{nickname:form.get("nickname"),adminRole:form.get("adminRole"),enabled:form.has("enabled")}});
        if (form.get("password")) await api(`/api/v1/admin/admins/${encodeURIComponent(item.id)}/reset-password`, {method:"POST",body:{password:form.get("password")}});
        toast("管理员已更新"); renderers.admins();
      });
    }
    if (action === "feedback-details") {
      const details = await api(`/api/v1/admin/feedback/${encodeURIComponent(button.dataset.id)}`);
      const item = details.feedback;
      const timeline = details.events.length
        ? details.events.map((event) => `<li><strong>${escapeHtml(event.actor_name || (event.actor_role === "USER" ? "用户" : "管理员"))}</strong> · ${escapeHtml(event.action)} · ${formatTime(event.created_at)}<br><span class="subtle">${escapeHtml(event.old_status ? `${feedbackStatusText(event.old_status)} → ${feedbackStatusText(event.new_status)}` : feedbackStatusText(event.new_status))}${event.public_message ? ` · 已更新用户可见回复` : ""}</span></li>`).join("")
        : "<li class=\"subtle\">暂无处理记录</li>";
      const audits = details.audit.length
        ? details.audit.map((row) => `<li><strong>${escapeHtml(row.actor_name || "管理员")}</strong> · ${escapeHtml(row.action)} · ${formatTime(row.created_at)}</li>`).join("")
        : "<li class=\"subtle\">暂无管理审计</li>";
      return openDialog("反馈详情", `
        <div class="stack feedback-detail">
          <div class="card-header"><div>${feedbackBadge(item.workflow_status)} <span class="badge">${escapeHtml(feedbackCategoryText(item.category))}</span></div><span class="subtle">#${escapeHtml(item.id)}</span></div>
          <div><strong>${escapeHtml(item.title || "未命名反馈")}</strong><p class="long-text">${escapeHtml(item.content)}</p></div>
          <div class="grid two-columns">
            <p class="subtle">用户：${escapeHtml(item.nickname || "川农同学")} · ${escapeHtml(item.phone_masked || "未绑定手机号")}</p>
            <p class="subtle">提交：${formatTime(item.created_at)}<br>更新：${formatTime(item.updated_at)}</p>
          </div>
          <p class="subtle">当前版本暂未支持反馈附件。</p>
          <label>处理状态<select name="status">${feedbackStatusOptions(item.workflow_status)}</select></label>
          <label>对用户可见的处理说明/回复<textarea name="publicReply" maxlength="2000" placeholder="用户将在“我的反馈”中看到">${escapeHtml(item.public_reply || "")}</textarea><span class="subtle">已解决或已关闭时必填。</span></label>
          <label>仅管理员可见的内部备注<textarea name="internalNote" maxlength="2000" placeholder="排查过程、交接事项等">${escapeHtml(item.internal_note || "")}</textarea><span class="subtle">此内容不会返回给用户端。</span></label>
          <div><h4>处理时间线</h4><ol class="timeline">${timeline}</ol></div>
          <div><h4>管理审计</h4><ol class="timeline">${audits}</ol></div>
        </div>`,
        async (form) => {
          await api(`/api/v1/admin/feedback/${encodeURIComponent(item.id)}`, {
            method: "PATCH",
            body: {
              status: form.get("status"),
              publicReply: form.get("publicReply"),
              internalNote: form.get("internalNote"),
            },
          });
          toast("反馈处理结果已保存");
          renderers.feedback();
        },
        "保存处理结果",
      );
    }
    if (action === "edit-user") {
      const user = JSON.parse(button.dataset.user);
      return openDialog("编辑用户", `
        <div class="stack">
          <label>昵称<input name="nickname" maxlength="40" value="${escapeHtml(user.nickname || "川农同学")}" required></label>
          <label>账号状态<select name="accountStatus">${accountStatusOptions(user.account_status)}</select></label>
          <label>处理原因<input name="reason" maxlength="300" placeholder="变更状态时必须填写"></label>
          <label>暂停时长<select name="suspensionDuration"><option value="7_DAYS">7 天</option><option value="30_DAYS">30 天</option><option value="PERMANENT">永久</option></select></label>
          <p class="muted">账号状态选择“受限”时应用暂停时长；选择“正常”即可恢复。受限账号仍可登录并使用本地功能，但失物招领、资料库及其他需服务端授权的功能会被拒绝。</p>
          <p class="muted">预约姓名和学号只能由用户本人确认并保存。管理员可在详情页按权限受控查看，但不能代替用户填写或伪造同意记录。</p>
        </div>`,
        async (form) => {
          await api(`/api/v1/admin/users/${encodeURIComponent(user.id)}`, {
            method: "PATCH",
            body: {
              nickname: form.get("nickname"),
              accountStatus: form.get("accountStatus"),
              reason: form.get("reason"),
              suspensionDuration: form.get("suspensionDuration"),
            },
          });
          toast("用户资料已保存");
          renderers.users();
        });
    }
    if (action === "restore-user") {
      if (!confirm("确认恢复该用户的账号访问权限？")) return;
      await api(`/api/v1/admin/users/${encodeURIComponent(button.dataset.id)}`, {
        method: "PATCH",
        body: { accountStatus: "ACTIVE", reason: "管理员手动恢复账号" },
      });
      toast("用户账号已恢复");
      return renderers.users(state.filters);
    }
    if (action === "review-risk") {
      return openDialog("处理异常候选", `<div class="stack"><label>处理结果<select name="status">${options(["WATCHING","IGNORED","CLOSED"], "WATCHING")}</select></label><label>处理原因（可选）<textarea name="reason" maxlength="300" placeholder="可留空直接处理"></textarea></label><p class="muted">此操作不会自动封禁账号；如需限制或禁用，请进入“用户与权限”单独处理。</p></div>`, async (form) => {
        await api(`/api/v1/admin/risk-candidates/${encodeURIComponent(button.dataset.id)}`, {method:"PATCH", body:{status:form.get("status"),reason:form.get("reason")}});
        toast("异常候选已更新");
        renderers.riskCandidates(state.filters);
      }, "确认处理");
    }
    if (action === "user-details") {
      const details = await api(`/api/v1/admin/users/${encodeURIComponent(button.dataset.id)}/details`);
      const u = details.user;
      return openDialog("用户档案", `
        <div class="stack">
          <p><strong>${escapeHtml(u.nickname)}</strong> · ${escapeHtml(u.phone || "无手机号")}</p>
          <p>用户编号：<strong>${escapeHtml(u.user_code || "待生成")}</strong></p>
          <details><summary>技术信息</summary><p class="muted">内部 UUID：${escapeHtml(u.id)}</p></details>
          <p>账号状态：${accountBadge(u.account_status)} · 原因：${escapeHtml(u.account_status_reason || "无")}</p>
          <p class="muted">注册：${formatTime(u.created_at)} · 最近活跃：${formatTime(u.last_active_at)} · 状态生效：${formatTime(u.account_status_changed_at)} · 到期：${formatTime(u.account_status_expires_at)}</p>
          <div class="grid two-columns">
            ${relatedCard("预约记录", details.reservations.length, "reservations", u)}
            ${relatedCard("失物发布", details.lostFound.length, "lost", u)}
            ${relatedCard("失物举报", details.lostReports.length, "lostReports", u)}
            ${relatedCard("认领申请", details.lostClaims.length, "lostClaims", u, "claims")}
            ${relatedCard("资料浏览", details.resourceActivity.views, "resourceActivity", u, "views")}
            ${relatedCard("资料收藏", details.resourceActivity.favorites, "resourceActivity", u, "favorites")}
            ${relatedCard("资料投诉", details.resourceActivity.complaints, "resourceComplaints", u)}
            ${relatedCard("用户反馈", details.feedback.length, "feedback", u)}
            ${relatedCard("用户行为记录", details.behaviorEvents.length, "userEvents", u)}
          </div>
          <p>预约资料：<strong id="reservation-profile-value">${escapeHtml(u.reservation_name || "未填写")} · ${escapeHtml(u.reservation_student_id || "未填写")}</strong></p>
          <p class="muted">用户确认：${u.reservation_profile_consented_at ? formatTime(u.reservation_profile_consented_at) : "未确认"} · 最后更新：${formatTime(u.reservation_profile_updated_at)}</p>
          <p class="muted">限制状态：${escapeHtml(details.restriction?.reason || "无限制")}</p>
          <p class="muted">超级管理员可在管理工作台查看已收集的完整账号与预约资料；密码、哈希、Token、Session、Cookie 和教务数据永不展示。</p>
          ${u.account_status === "DELETED" ? '<p class="muted">该账号个人数据已清理，不能恢复；用户可使用原手机号重新注册。</p>' : `<button type="button" class="danger" data-action="delete-user-account" data-user="${escapeHtml(JSON.stringify(u))}">注销账号</button>`}
        </div>`, async () => {}, "关闭");
    }
    if (action === "clear-restriction") {
      if (!confirm("确认解除该用户的预约与取消限制？")) return;
      await api(`/api/v1/admin/users/${encodeURIComponent(button.dataset.id)}/clear-restrictions`, {
        method: "POST",
        body: {},
      });
      toast("用户限制已解除");
      return renderers.users();
    }
    if (action === "inventory") return openInventory(button.dataset.id);
    if (action === "toggle-slot") {
      await api(`/api/v1/admin/time-slots/${encodeURIComponent(button.dataset.id)}`, {
        method: "PATCH",
        body: { enabled: button.dataset.enabled !== "true" },
      });
      toast("时间段状态已更新");
      return renderers.venues();
    }
    if (action === "edit-venue") {
      const venue = state.venues.find((item) => item.id === button.dataset.id);
      return openDialog("编辑场馆", `
        <div class="stack">
          <label>场馆名称<input name="name" value="${escapeHtml(venue.name)}" required></label>
          <label>场地数量<input name="courtCount" type="number" min="1" max="100" value="${venue.court_count}" required></label>
          <label>每小时价格（元）<input name="priceYuan" type="number" min="0" value="${venue.price_yuan}" required></label>
          <label>运行状态<select name="operatingStatus">${options(["OPEN","CLOSED","MAINTENANCE"], venue.operating_status)}</select></label>
          <label>维护说明<textarea name="maintenanceNote">${escapeHtml(venue.maintenance_note || "")}</textarea></label>
          <label>预计恢复时间（可留空）<input type="datetime-local" name="expectedRestoreAt" value="${datetimeLocal(venue.expected_restore_at)}"></label>
          <label class="checkbox-row"><input name="enabled" type="checkbox" ${venue.enabled ? "checked" : ""}> 在服务端启用此场馆</label>
        </div>`,
        async (form) => {
          await api(`/api/v1/admin/venues/${encodeURIComponent(venue.id)}`, {
            method: "PATCH",
            body: {
              name: form.get("name"),
              courtCount: Number(form.get("courtCount")),
              priceYuan: Number(form.get("priceYuan")),
              operatingStatus: form.get("operatingStatus"),
              maintenanceNote: form.get("maintenanceNote"),
              expectedRestoreAt: form.get("expectedRestoreAt")
                ? new Date(form.get("expectedRestoreAt")).getTime()
                : null,
              enabled: form.has("enabled"),
            },
          });
          toast("场馆配置已保存");
          renderers.venues();
        });
    }
    if (action === "add-slot") {
      return openDialog("新增开放时间段", `<div class="grid two-columns"><label>开始时间<input name="startTime" type="time" required></label><label>结束时间<input name="endTime" type="time" required></label></div>`,
        async (form) => {
          await api(`/api/v1/admin/venues/${encodeURIComponent(button.dataset.id)}/time-slots`, {
            method: "POST",
            body: { startTime: form.get("startTime"), endTime: form.get("endTime") },
          });
          toast("时间段已添加");
          renderers.venues();
        });
    }
    if (action === "reservation-details") {
      const details = await api(`/api/v1/admin/reservations/${encodeURIComponent(button.dataset.id)}`);
      const item = details.reservation;
      const history = details.sportsHistory || [];
      openDialog("预约详情", `<div class="stack">
        <p><strong>${escapeHtml(item.venue_name)}</strong> · ${escapeHtml(item.booking_date)} ${escapeHtml(item.time_slot)} · ${item.court_number} ${escapeHtml(item.unit_label)}</p>
        <p>预约编号：${escapeHtml(item.id)}<br>内部用户 ID：${escapeHtml(item.user_id)}</p>
        <p>用户：${escapeHtml(item.nickname || "未设置昵称")}<br>完整手机号：${escapeHtml(item.phone || "未填写")}<br>预约姓名：${escapeHtml(item.reservation_name_snapshot || item.reservation_name || "未填写")}<br>预约学号：${escapeHtml(item.reservation_student_id_snapshot || item.reservation_student_id || "未填写")}</p>
        <p>状态：${escapeHtml(statusText(item.status))} · ${escapeHtml(statusText(item.verification_status || "NOT_REQUIRED"))} · 模拟金额 ${item.price_yuan} 元</p>
        <h4>该用户体育预约历史（${history.length}）</h4>
        ${history.length ? `<div class="record-list">${history.map(row => `<div><strong>${escapeHtml(row.booking_date)} ${escapeHtml(row.time_slot)}</strong> · ${escapeHtml(row.venue_name)} ${row.court_number}${escapeHtml(row.unit_label)} · ${escapeHtml(statusText(row.status))}</div>`).join("")}</div>` : '<div class="empty">暂无其他体育预约记录</div>'}
      </div>`, async () => {}, "关闭");
      $("#dialog-confirm").type = "button";
      $("#dialog-confirm").addEventListener("click", () => $("#dialog").close("cancel"), {once:true});
      return;
    }
    if (action === "reservation-profile-details") {
      const result = await api(`/api/v1/admin/reservations/${encodeURIComponent(button.dataset.id)}/profile`, {
        method: "POST", body: {},
      });
      return openDialog("核验预约资料", `<div class="stack">
        <p>用户：<strong>${escapeHtml(result.nickname || "未设置昵称")}</strong><br>完整手机号：${escapeHtml(result.phone || "未填写")}<br>预约姓名：${escapeHtml(result.name || "未填写")}<br>预约学号：${escapeHtml(result.studentId || "未填写")}</p>
        <label>核验结果<select name="status"><option value="VERIFIED">确认核验通过</option><option value="PENDING">保持待核验</option><option value="REJECTED">核验未通过</option><option value="NOT_REQUIRED">无需核验</option></select></label>
        <label>核验备注（可选）<textarea name="note" maxlength="300" placeholder="可留空"></textarea></label>
        <p class="muted">资料仅用于本次体育预约处理；查看与核验均写入不含敏感原文的管理员审计。</p>
      </div>`, async form => {
        await api(`/api/v1/admin/reservations/${encodeURIComponent(button.dataset.id)}/verify`, {
          method: "POST", body: {status: form.get("status"), note: form.get("note")},
        });
        toast("核验状态已更新");
        renderers.reservations(state.filters);
      }, "保存核验结果");
    }
    if (action === "cancel-reservation") {
      return openDialog("取消预约", `<div class="stack"><p>取消后该场地将重新可预约，且不会计入用户主动取消次数。</p><label>处理备注（可选）<textarea name="reason" maxlength="200" placeholder="可直接确认取消"></textarea></label></div>`, async form => {
        await api(`/api/v1/admin/reservations/${encodeURIComponent(button.dataset.id)}/cancel`, {
          method: "POST", body: {reason: form.get("reason")},
        });
        toast("预约已取消");
        renderers.reservations(state.filters);
      }, "确认取消预约");
    }
    if (action === "delete-lost") {
      if (!confirm("确认永久删除这条失物信息？关联的收藏、认领和举报记录也会删除。")) return;
      await api(`/api/v1/admin/lost-found/${encodeURIComponent(button.dataset.id)}`, { method: "DELETE" });
      toast("失物信息已删除");
      return renderers.lost();
    }
    if (action === "lost-details") {
      const item = await api(`/api/v1/admin/lost-found/${encodeURIComponent(button.dataset.id)}`);
      const image = item.image_name
        ? `<img class="lost-image-preview" src="/uploads/${encodeURIComponent(item.image_name)}" alt="${escapeHtml(item.item_name)}的失物图片">`
        : '<div class="empty">该内容未上传图片</div>';
      return openDialog("失物详情", `
        <div class="stack">
          ${image}
          <div><strong>${escapeHtml(item.item_name)}</strong><p class="long-text">${escapeHtml(item.description || "无详细描述")}</p></div>
          <div class="grid two-columns">
            <p class="subtle">类型：${escapeHtml(item.type)}<br>分类：${escapeHtml(item.category)}<br>状态：${escapeHtml(statusText(item.status))}</p>
            <p class="subtle">地点：${escapeHtml(item.location)}<br>日期：${escapeHtml(item.event_date)}<br>发布时间：${formatTime(item.created_at)}</p>
          </div>
          <p class="subtle">发布用户：${escapeHtml(userLabel(item))}<br>内部用户 ID：${escapeHtml(item.owner_user_id || item.user_id || "—")}</p>
          <p class="subtle">认领说明：${escapeHtml(item.claim_note || "无")}</p>
        </div>`, async () => {}, "关闭");
    }
    if (action === "bulk-delete-lost") {
      const ids = Array.from(document.querySelectorAll(".lost-select:checked"), (item) => item.value);
      if (!ids.length) throw new Error("请先选择需要删除的失物内容");
      if (!confirm(`将删除 ${ids.length} 条失物内容。确认进入第二次核验？`)) return;
      const confirmation = `DELETE ${ids.length} ITEMS`;
      return openDialog("批量删除失物内容", `<div class="stack"><p>该操作仅删除已选失物内容，关联图片和业务记录会同步清理。</p><label>处理原因<textarea name="reason" maxlength="300" required></textarea></label><label>再次输入确认文本 <strong>${confirmation}</strong><input name="confirmation" autocomplete="off" required></label></div>`, async (form) => {
        await api("/api/v1/admin/lost-found/bulk-delete", {
          method: "POST",
          body: {ids, reason: form.get("reason"), confirmation: form.get("confirmation")},
        });
        toast(`已删除 ${ids.length} 条失物内容`);
        renderers.lost();
      }, "确认批量删除");
    }
    if (action === "edit-resource") {
      const item = JSON.parse(button.dataset.resource);
      return openDialog("编辑资料", `
        <div class="stack">
          <div class="grid two-columns">
            <label>标题<input name="title" maxlength="160" value="${escapeHtml(item.title)}" required></label>
            <label>分类<input name="category" maxlength="80" value="${escapeHtml(item.category)}" required></label>
            <label>来源标识<input name="sourceLabel" value="${escapeHtml(item.source_label || "")}"></label>
            <label>适用范围<input name="audienceScope" value="${escapeHtml(item.audience_scope || "")}"></label>
            <label>标签<input name="tags" value="${escapeHtml((item.tags || []).join(","))}"></label>
            <label>外部链接<input name="externalUrl" type="url" value="${escapeHtml(item.external_url)}" required></label>
            <label>封面链接<input name="coverUrl" type="url" value="${escapeHtml(item.cover_url || "")}"></label>
            <label>排序<input name="sortOrder" type="number" value="${item.sort_order || 0}"></label>
            <label>发布状态<select name="publishStatus">${options(["DRAFT","PUBLISHED","UNLISTED"], item.publish_status)}</select></label>
            <label>可见范围<select name="visibility"><option value="MEMBERS" ${item.visibility === "MEMBERS" ? "selected" : ""}>登录用户</option><option value="PUBLIC" ${item.visibility === "PUBLIC" ? "selected" : ""}>预留公开</option></select></label>
          </div>
          <label>简介<textarea name="summary">${escapeHtml(item.summary || "")}</textarea></label>
          <label>使用说明<textarea name="usageNote">${escapeHtml(item.usage_note || "")}</textarea></label>
          <label class="checkbox-row"><input type="checkbox" name="isFree" ${item.is_free ? "checked" : ""}> 免费资料</label>
        </div>`,
        async (form) => {
          await api(`/api/v1/admin/resources/${encodeURIComponent(item.id)}`, {
            method: "PATCH",
            body: {
              ...Object.fromEntries(form),
              sortOrder: Number(form.get("sortOrder") || 0),
              isFree: form.has("isFree"),
            },
          });
          toast("资料已更新");
          renderers.resources();
        });
    }
    if (action === "toggle-resource") {
      const next = button.dataset.status === "PUBLISHED" ? "UNLISTED" : "PUBLISHED";
      if (next === "UNLISTED" && !confirm("确认下架此资料？App 刷新后将不再显示。")) return;
      await api(`/api/v1/admin/resources/${encodeURIComponent(button.dataset.id)}`, {
        method: "PATCH",
        body: { publishStatus: next },
      });
      toast(next === "PUBLISHED" ? "资料已发布" : "资料已下架");
      return renderers.resources();
    }
    if (action === "delete-resource") {
      if (!confirm("确认永久删除此资料？收藏、浏览与投诉关联记录也会删除。")) return;
      await api(`/api/v1/admin/resources/${encodeURIComponent(button.dataset.id)}`, {
        method: "DELETE",
      });
      toast("资料已删除");
      return renderers.resources();
    }
    if (action === "toggle-announcement") {
      const next = button.dataset.enabled !== "true";
      if (!next && !confirm("确认撤回这条公告？App 将不再获取到它。")) return;
      await api(`/api/v1/admin/announcements/${encodeURIComponent(button.dataset.id)}`, {
        method: "PATCH",
        body: { enabled: next },
      });
      toast(next ? "公告已重新发布" : "公告已撤回");
      return renderers.announcements();
    }
    if (action === "delete-announcement") {
      if (!confirm("确认永久删除这条公告？删除后 App 将不再显示，且无法从页面恢复。")) return;
      await api(`/api/v1/admin/announcements/${encodeURIComponent(button.dataset.id)}`, {
        method: "DELETE",
      });
      toast("公告已删除");
      return renderers.announcements();
    }
    if (action === "edit-announcement") {
      const row = announcementRows.find(item => item.id === button.dataset.id) || {};
      return openDialog("编辑公告", `<div class="stack">${announcementFields(row)}<label>标题<input name="title" value="${escapeHtml(button.dataset.title)}" required></label><label>内容<textarea name="content" required>${escapeHtml(button.dataset.content)}</textarea></label><label class="checkbox-row"><input type="checkbox" name="serviceVisible" ${button.dataset.serviceVisible === "true" ? "checked" : ""}> 显示在 App 服务页“服务动态”</label><label class="checkbox-row"><input type="checkbox" name="notificationEnabled" ${button.dataset.notificationEnabled === "true" ? "checked" : ""}> 发送系统通知（App 同步后提醒）</label><label class="checkbox-row"><input type="checkbox" name="popupEnabled" ${button.dataset.popupEnabled === "true" ? "checked" : ""}> 首次获取时显示 App 内弹窗</label><p class="muted">编辑不会因为公告 ID 不变而自动重新通知已经处理过的设备。</p></div>`,
        async (form) => {
          await api(`/api/v1/admin/announcements/${encodeURIComponent(button.dataset.id)}`, {
            method: "PATCH",
            body: {
              ...await announcementMetadata(form),
              title: form.get("title"),
              content: form.get("content"),
              enabled: button.dataset.enabled === "true",
              serviceVisible: form.has("serviceVisible"),
              notificationEnabled: form.has("notificationEnabled"),
              popupEnabled: form.has("popupEnabled"),
            },
          });
          toast("公告已更新");
          renderers.announcements();
        });
    }
    if (action === "toggle-website-announcement") {
      const next = button.dataset.enabled !== "true";
      if (!next && !confirm("确认撤回这条网站公告？官网将不再显示它。")) return;
      await api(`/api/v1/admin/website-announcements/${encodeURIComponent(button.dataset.id)}`, {
        method: "PATCH",
        body: { enabled: next },
      });
      toast(next ? "网站公告已重新发布" : "网站公告已撤回");
      return renderers.websiteAnnouncements();
    }
    if (action === "delete-website-announcement") {
      if (!confirm("确认永久删除这条网站公告？删除后官网将不再显示，且无法从页面恢复。")) return;
      await api(`/api/v1/admin/website-announcements/${encodeURIComponent(button.dataset.id)}`, {
        method: "DELETE",
      });
      toast("网站公告已删除");
      return renderers.websiteAnnouncements();
    }
    if (action === "edit-website-announcement") {
      return openDialog("编辑网站公告", `<div class="stack"><label>标题<input name="title" value="${escapeHtml(button.dataset.title)}" required></label><label>内容<textarea name="content" required>${escapeHtml(button.dataset.content)}</textarea></label><label class="checkbox-row"><input type="checkbox" name="enabled" ${button.dataset.enabled === "true" ? "checked" : ""}> 在官网显示</label></div>`,
        async (form) => {
          await api(`/api/v1/admin/website-announcements/${encodeURIComponent(button.dataset.id)}`, {
            method: "PATCH",
            body: { title: form.get("title"), content: form.get("content"), enabled: form.enabled.checked },
          });
          toast("网站公告已更新");
          renderers.websiteAnnouncements();
        });
    }
  } catch (error) {
    toast(error.message, true);
  }
});

$("#dialog-content").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  try {
    if (button.dataset.action === "reveal-user-phone") {
      const reason = $("#phone-reveal-reason")?.value?.trim() || "";
      if (reason.length < 4) throw new Error("请填写具体查看原因");
      if (!confirm("仅在处理账号查找或用户主动报号等必要场景查看。确认继续？")) return;
      const result = await api(`/api/v1/admin/users/${encodeURIComponent(button.dataset.id)}/phone`, {
        method: "POST",
        body: { reason },
      });
      const output = $("#user-phone-value");
      if (output) output.textContent = result.phone;
      clearTimeout(state.phoneRevealTimer);
      state.phoneRevealTimer = setTimeout(() => {
        const current = $("#user-phone-value");
        if (current) current.textContent = button.dataset.masked || "已遮罩";
        state.phoneRevealTimer = null;
      }, Math.max(1, Number(result.expiresInSeconds || 60)) * 1000);
      toast("完整手机号已临时显示，60 秒后自动遮罩");
      return;
    }
    if (button.dataset.action === "reveal-reservation-profile") {
      const reason = $("#profile-reveal-reason")?.value?.trim() || "";
      if (reason.length < 4) throw new Error("请填写具体查看原因");
      if (!confirm("仅在处理预约核验或账号查找等必要场景查看。确认继续？")) return;
      const result = await api(`/api/v1/admin/users/${encodeURIComponent(button.dataset.id)}/reservation-profile`, {
        method: "POST",
        body: { reason },
      });
      const output = $("#reservation-profile-value");
      if (output) output.textContent = `${result.name} · ${result.studentId}`;
      clearTimeout(state.profileRevealTimer);
      state.profileRevealTimer = setTimeout(() => {
        const current = $("#reservation-profile-value");
        if (current) current.textContent = button.dataset.masked || "已遮罩";
        state.profileRevealTimer = null;
      }, Math.max(1, Number(result.expiresInSeconds || 60)) * 1000);
      toast("完整预约资料已临时显示，60 秒后自动遮罩");
      return;
    }
    if (button.dataset.action === "toggle-inventory") {
      if (button.dataset.blockId) {
        await api(`/api/v1/admin/inventory/block/${encodeURIComponent(button.dataset.blockId)}`, { method: "DELETE" });
        toast("该场地已恢复开放");
      } else {
        await api("/api/v1/admin/inventory/block", {
          method: "POST",
          body: {
            venueId: button.dataset.venue,
            date: button.dataset.date,
            timeSlot: button.dataset.slot,
            courtNumber: Number(button.dataset.court),
            reason: "后台临时关闭",
          },
        });
        toast("该场地已关闭");
      }
      $("#dialog").close("cancel");
      openInventory(button.dataset.venue);
    }
  } catch (error) {
    toast(error.message, true);
  }
});

window.addEventListener("DOMContentLoaded", () => { if (state.token) showApp(); else showLogin(); });
