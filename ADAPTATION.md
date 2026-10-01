# 学校适配与证据清单
原始 Word 和完整提取仅本地保存，未提交负责人资料。章节引用避免伪造分页。
|功能|原实现代码/方式|目标入口|负责人记录|本轮核验与限制|打开方式|来源章节|
|---|---|---|---|---|---|---|
|我的课表|feature/common/compose_ui/FunctionCatalog.kt → 学校专用原生数据源（未迁移）|https://jiaowu.sicau.edu.cn/|负责人记录可以使用；本轮未测试学校登录|文档既勾选可用，也记录现在打不开；校园网可打开并登录，校外失败且有时可用。可能与网络环境有关。登录后请自行进入对应栏目；本产品未实现原生同步。|WEBVIEW|第三、四、五、七、八部分|
|成绩查询|feature/common/compose_ui/FunctionCatalog.kt → 学校专用原生数据源（未迁移）|https://jiaowu.sicau.edu.cn/|负责人记录可以使用；本轮未测试学校登录|文档既勾选可用，也记录现在打不开；校园网可打开并登录，校外失败且有时可用。可能与网络环境有关。登录后请自行进入对应栏目；本产品未实现原生同步。|WEBVIEW|第三、四、五、七、八部分|
|考试安排|feature/common/compose_ui/FunctionCatalog.kt → 学校专用原生数据源（未迁移）|https://jiaowu.sicau.edu.cn/|负责人记录可以使用；本轮未测试学校登录|文档既勾选可用，也记录现在打不开；校园网可打开并登录，校外失败且有时可用。可能与网络环境有关。登录后请自行进入对应栏目；本产品未实现原生同步。|WEBVIEW|第三、四、五、七、八部分|
|教务系统|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|https://jiaowu.sicau.edu.cn/|负责人记录可以使用；本轮未测试学校登录|文档既勾选可用，也记录现在打不开；校园网可打开并登录，校外失败且有时可用。可能与网络环境有关。登录后请自行进入对应栏目；本产品未实现原生同步。|WEBVIEW|第三、四、五、七、八部分|
|学校服务登录|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|https://auth.sicau.edu.cn/wui/|负责人记录可以使用；本轮未测试学校登录|学校页面自行登录，可能涉及验证码、短信或扫码。不会据页面加载判断登录成功，也不与本产品账号绑定。文档另一地址 ecology 被官网标为 OA 办公，未作为替代认证入口。|WEBVIEW|第三、四、五、七、八部分|
|校历|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|https://www.sicau.edu.cn/ggfw/rlxl.htm|负责人记录可以使用；本轮未测试学校登录|本轮从学校官网找到日历校历栏目。请核对页面适用学年；本产品暂不计算教学周。|EXTERNAL_BROWSER|第三、五、八部分及本轮官网公开核验|
|图书馆|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|https://lib.sicau.edu.cn/|尚未调查；另记网页进不去|文档第八部分尚未调查，第三部分备注网页进不去。本轮从官网找到入口；当前访问失败，不代表学校没有图书馆。|EXTERNAL_BROWSER|第三、八部分及本轮官网公开核验|
|空教室|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|无完整网页入口|有，但暂时不知道怎么进入|负责人确认有此功能，但尚不知道入口。获得完整官方入口后再接入。|UNAVAILABLE|第三、四、五、七、八部分|
|一网通办|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|https://ywtb.sicau.edu.cn/|负责人记录可以使用；本轮未测试学校登录|负责人记录可打开、可登录，同时注明手机不适配。首版使用外部浏览器，可尝试浏览器缩放；登录后未实测。|EXTERNAL_BROWSER|第三、四、五、七、八部分|
|校园缴费|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|https://op.sicau.edu.cn/|负责人记录可以使用；本轮未测试学校登录|独立校园缴费平台。文档记录使用统一身份认证；不等同于宿舍电费或校园卡充值。当前访问失败可重试或使用外部浏览器。|WEBVIEW|第三、四、五、七、八部分|
|电费|feature/common/compose_ui/FunctionCatalog.kt → 学校专用原生数据源（未迁移）|无完整网页入口|负责人记录可以使用；本轮未测试学校登录|1. 打开微信，搜索并核实「川农后勤」公众号。 2. 在公众号菜单中找到「电精灵」。 3. 按页面提示微信登录，自行选择寝室。 候选域名 bd.bdfairy.cn 缺少完整业务路径，未列入信任清单；本产品不显示电费余额。|WECHAT_GUIDE|第三、四、五、七、八部分|
|水费|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|无完整网页入口|负责人记录可以使用；本轮未测试学校登录|文档记录使用「完美校园」App。请使用学校既有指引或手机应用商店核实开发者后安装，按服务商流程操作。尚未核实官方唤起协议和下载地址，本页支持复制应用名称，不承诺一键唤起。|EXTERNAL_APP_GUIDE|第三、四、五、七、八部分|
|校园卡|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|无完整网页入口|负责人记录可以使用；本轮未测试学校登录|文档记录使用「完美校园」App。请使用学校既有指引或手机应用商店核实开发者后安装，按服务商流程操作。尚未核实官方唤起协议和下载地址，本页支持复制应用名称，不承诺一键唤起。|EXTERNAL_APP_GUIDE|第三、四、五、七、八部分|
|宿舍服务|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|无完整网页入口|负责人记录可以使用；本轮未测试学校登录|打开微信，搜索并核实「川农后勤」公众号，按实际菜单办理。文档未给出具体菜单和完整链接，暂不承诺直达报修。|WECHAT_GUIDE|第三、四、五、七、八部分|
|学工系统|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|无完整网页入口|负责人记录可以使用；本轮未测试学校登录|打开微信，搜索并核实「川农学工」服务号，再进入相应业务。请在学校页面自行完成认证；具体菜单待补充。|WECHAT_GUIDE|第三、四、五、七、八部分|
|校园地图|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|https://rsc.sicau.edu.cn/info/1014/4303.htm|第二部分已找到，八部分尚未调查，保留冲突|本轮确认教职工入职指南底部列有雅安、成都、都江堰三校区平面图附件。附件图面与时效仍待核验，仅提供来源页，不是已验收的地图导航。|EXTERNAL_BROWSER|第二、八部分及本轮公开核验|
|学校官网|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|https://www.sicau.edu.cn/|负责人记录可以使用；本轮未测试学校登录|四川农业大学官网。文档对 App 内嵌适用性仍待评估，首版使用外部浏览器。|EXTERNAL_BROWSER|第三、四、五、七、八部分|
|学校公告|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|https://www.sicau.edu.cn/|负责人记录可以使用；本轮未测试学校登录|查看学校官网公开新闻公告。文档另记学院公众号、班级QQ群，不代表本产品接收完整学校通知。|EXTERNAL_BROWSER|第三、四、五、七、八部分|
|失物招领|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|无完整网页入口|官方系统尚未调查|本产品独立自建服务，非学校官方服务。预览数据库为空，不迁移原产品用户或内容。连接本地后端后可刷新查看。|LOCAL|第三、四、五、七、八部分|
|资料服务|feature/common/compose_ui/FunctionCatalog.kt / common/ui/WebViewActivity.kt；自建服务经 CampusToolsApi|无完整网页入口|官方系统尚未调查|本产品独立自建服务，非学校官方服务。预览数据库为空，不迁移原产品用户或内容。连接本地后端后可刷新查看。|LOCAL|第三、四、五、七、八部分|

本轮本机 HTTP 核验补充：校园缴费 https://op.sicau.edu.cn/ 返回200，标题四川农业大学校园统一支付平台；先前工具访问失败保留为不同测试环境结果。教务、图书馆本机失败。公开页面200不代表WebView或登录后通过。完整结果见 school-public-probes.json。


## 原实现审计定位补充

- 课表：feature/timetable/ui/TimeTableActivity.kt、viewmodel/TimeTableViewModel.kt，依赖 CSUSTDataGet 学校数据源；未迁入。
- 成绩：feature/common/ui/ScoreInquiryActivity.kt，原学校查询链路；未迁入。
- 考试：feature/common/ui/ExamArrangementActivity.kt，原学校查询链路；未迁入。
- 绑定：settings/ui/BindingUserActivity.kt；学校会话：widget/dialog/SSOWebviewDialog.kt、core/security/SchoolSessionCoordinator.kt；存储：common/data/local/mmkv/StudentInfoManager.kt；均未迁入。
- 通用网页：common/ui/WebViewActivity.kt，保留进度/返回/外部打开思路，川农 SchoolWebActivity 移除混合内容允许、增加严格主机校验和超时等。
- 通用卡片：feature/common/compose_ui/FunctionItem.kt、FunctionSection.kt、FunctionPalette.kt，受控复制并调整包名。
- Backend/Admin：security.py RateLimiter 与 admin/styles.css 受控复制；川农 server.py 仅实现独立预览配置/会话/空列表。未声称迁移全部原产品业务。

品牌公开来源均已核验页面标题；未把官方校徽用于产品身份。首版使用本产品自制川字和叶片矢量标记；官方素材二次使用边界、地图附件图面和校历学年仍待核验。标准色暂未确认。
