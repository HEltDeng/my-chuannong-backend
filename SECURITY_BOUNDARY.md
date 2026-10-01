# 隔离与敏感数据审计

母版审计范围：settings/ui/BindingUserActivity.kt（原生绑定）、widget/dialog/SSOWebviewDialog.kt（学校认证）、core/security/SchoolSessionCoordinator.kt（会话）、common/data/local/mmkv/StudentInfoManager.kt（学生信息）、feature/timetable/viewmodel/TimeTableViewModel.kt、feature/common/ui/ScoreInquiryActivity.kt、ExamArrangementActivity.kt（学校解析）、core/PlanetApplication.kt（启动任务）、overview/data/OverviewRepository.kt。以上均未复制。CSUSTDataGet AAR 未复制也未依赖。

客户端仅导出通用 Compose 卡片组件和工程 wrapper。清单没有 FileProvider、自定义权限、Deep/App Link、广播、通知渠道、桌面小组件、学校数据数据库或后台同步任务，因此没有旧身份残留；仅主 Activity exported，学校 WebView Activity 不对外导出。新版 AndroidX 自带签名级动态接收器权限由本 applicationId 派生。allowBackup=false，数据和 WebView 位于独立包沙箱，无共享 UID。

旧生产 URL、旧 APK 更新地址、长理网站、寝室数据、校历、开学日期、用户和密钥不在新运行时源码及资源中。原 LICENSE/来源声明允许保留来源名称，不能当运行时残留盲目替换。

学校网站 iframe/脚本内部行为由网站自身控制。本产品不注册 JS Bridge、不 evaluateJavascript、不调用 getCookie、不导出 Cookie，不记录跳转参数。不承诺第三方网站完全不处理个人信息。隐私说明对应当前行为。

后端无学校认证中间层，无学校数据抓取或出站网络代码。空 SQLite 数据库独立初始化；凭据和数据库被忽略并在提交前检查实际暂存文件。原始采集表及联系人仅保留项目 03_LocalReferences，本目录不属于任何新 Git 仓库。
