const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const admin = process.env.ADMIN_ASSET_ROOT || path.join(__dirname, '..', 'admin');

function harness() {
  const requests = [];
  const content = {innerHTML: '', children: [], prepend(node) { this.children.unshift(node); }};
  let calls = 0;
  const context = {
    content, titles: {}, state: {page: 'analytics', admin: {adminRole: 'SUPER_ADMIN'}},
    renderers: {overview: async () => { content.innerHTML = '<div>业务卡片</div>'; content.children = []; }},
    escapeHtml: text => String(text ?? '').replaceAll('&', '&amp;').replaceAll('"', '&quot;'),
    formatTime: value => String(value), URLSearchParams,
    document: {createElement: () => ({innerHTML: ''}), addEventListener: () => {}},
    api: async url => {
      requests.push(url);
      if (url.endsWith('/metrics')) return {registered: 3, school: 2, platform: 1, schoolOnly: 1};
      const days = Number(new URL(url, 'http://test.local').searchParams.get('days') || 30);
      return {asOf: 1792740000000 + ++calls * 60000, metrics: {dau: 1, wau: 2, mau: 3, calendarMonth: 3, registeredAccounts: 3, phoneVerifiedAccounts: 0, verifiedStudents: 0, todayRegistrations: 0},
        trend: Array.from({length: days}, (_, i) => ({day: '2026-10-' + String(i % 23 + 1).padStart(2,'0'), dau: i ? 1 : null, registrations: 0, foreground: i ? 1 : null})),
        definition: '统计范围', retentionDays: 180};
    }
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(admin, 'account-analytics.js'), 'utf8'), context);
  return {context, content, requests};
}

test('statistics renders, refreshes and changes windows without a template exception', async () => {
  const {context, content, requests} = harness();
  for (const days of ['7','30','90']) {
    await context.renderers.analytics({days});
    assert.match(content.innerHTML, /活跃与注册趋势/);
    assert.match(content.innerHTML, /刷新数据/);
    assert.equal((content.innerHTML.match(/class="card metric metric-link"/g) || []).length, 11);
    assert.equal((content.innerHTML.match(/<tr>/g) || []).length, Number(days) + 1);
    assert.match(content.innerHTML, new RegExp('&quot;days&quot;:&quot;' + days));
    const previous = content.innerHTML;
    await context.renderers.analytics({days});
    assert.notEqual(content.innerHTML, previous, 'refresh must render the latest response');
  }
  assert.equal(requests.length, 12, 'each refresh requests both current activity and directory counts');
});

test('overview attaches statistics without a welcome card and does not accumulate sections', async () => {
  const {context, content} = harness();
  context.state.page = 'overview';
  for (let i=0; i<2; i++) {
    await context.renderers.overview();
    assert.equal(content.children.length, 1);
    assert.match(content.children[0].innerHTML, /今日平台活跃/);
    assert.match(content.children[0].innerHTML, /刷新数据/);
  }
});

test('a late statistics response cannot replace a newer window or another page', async () => {
  const {context, content} = harness();
  const original = context.api;
  let release;
  context.api = url => url.includes('days=7') ? new Promise(resolve => {release = () => resolve(original(url));}) : original(url);
  const first = context.renderers.analytics({days:'7'});
  await context.renderers.analytics({days:'90'});
  const newest = content.innerHTML;
  release(); await first;
  assert.equal(content.innerHTML, newest);
  const pending = context.renderers.analytics({days:'7'});
  context.state.page = 'accountDirectory'; content.innerHTML = '账号列表';
  release(); await pending;
  assert.equal(content.innerHTML, '账号列表');
});

test('an API failure stays retryable and a subsequent load renders normally', async () => {
  const {context, content} = harness();
  const original = context.api;
  context.api = async () => { throw Error('网络暂不可用'); };
  await assert.rejects(context.renderers.analytics(), /网络暂不可用/);
  context.api = original;
  await context.renderers.analytics();
  assert.match(content.innerHTML, /活跃与注册趋势/);
});
