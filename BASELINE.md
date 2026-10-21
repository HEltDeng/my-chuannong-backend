# V1.0.5 产品基线恢复记录

本文件取代 preview.1 的“通用卡片白名单导出”策略。preview.1 Git 历史保留；本轮在 `codex/restore-v105-adaptation` 恢复实际产品主体，不把完整源码放入 unused/reference。

Android 来源：`D:/Codex/01_App-应用开发/001_MyChangLi-我的长理/02_Worktrees/my-changli-v1.0.4`；分支 `codex/v1.0.5-dynamic-config-android`；HEAD `1d19370dc9a22817b0286372f5f663a3e36ae889`；已验收实现归档 `d56fc5efc8f1584309a7bc348035780e2c816c27`。

Backend/Admin 来源：`D:/Codex/01_App-应用开发/001_MyChangLi-我的长理/01_Repositories/CodexWorkspace-projects/my-changli-backend`；分支 `codex/v1.0.5-dynamic-config-backend`；HEAD `30a1c5fb37e7803ecadb2fd43b5b05c34f61eb37`。

母版 APK 实际核验：com.mychangli.app / 1.0.5 / 37；7,163,474 字节；APK SHA-256 `936223F8A7D7E40DBB1929433190CE325073AE689E1BA36C4B90B7E56B538E0A`。这是 APK 文件摘要，不是 Git commit，也不是签名证书摘要。

验收依据：指定历史任务中 2026-10-01 的用户视觉验收和同一 APK 发布记录；源码组件逐项与当前文件核对。当前轮未访问/操作原生产服务。历史同一 APK 配置 V1→V2→回滚的完整实机证据未补齐，不将继承类的存在等同于该验收完成。

完整必要 Android 产品源码、依赖声明、资源与 Backend/Admin 源码受控导出；排除旧 Git、AAR 学校网络实现、密钥、数据库、缓存、构建产物与旧部署工作流。数据模型从原学校库源码提取保留形状，川农网络解析独立隔离。原始 Word 和用户截图仅本地。

## 实际运行组件映射

母版列路径相对于 Android 母版；目标列相对于本仓库。

| 功能 | 母版文件 | 川农文件 | 迁移方式 |
|---|---|---|---|
| 悬浮导航与左右切页 | `app\src\main\java\com\mychangli\app\core\main\ui\MainScreen.kt` | `app\src\main\java\com\mychuannong\app\core\main\ui\MainScreen.kt` | 原文件整体继承，命名空间替换 |
| 胶囊、透底与选中态 | `app\src\main\java\com\mychangli\app\core\main\ui\MainBottomBar.kt` | `app\src\main\java\com\mychuannong\app\core\main\ui\MainBottomBar.kt` | 原文件整体继承；颜色来自绿色主题 |
| 首页课程/绩点/作业/考试 | `app\src\main\java\com\mychangli\app\overview\ui\compose\OverviewScreen.kt` | `app\src\main\java\com\mychuannong\app\overview\ui\compose\OverviewScreen.kt` | 原组件继承；增加原下拉刷新连接、无校历状态 |
| 首页状态管理 | `app\src\main\java\com\mychangli\app\overview\viewmodel\OverviewViewModel.kt` | `app\src\main\java\com\mychuannong\app\overview\viewmodel\OverviewViewModel.kt` | 原 ViewModel + 本机同步 revision 订阅 |
| 首页仓储 | `app\src\main\java\com\mychangli\app\overview\data\OverviewRepository.kt` | `app\src\main\java\com\mychuannong\app\overview\data\OverviewRepository.kt` | 原数据模型与组合逻辑；未确认绩点不计算 |
| 服务页 | `app\src\main\java\com\mychangli\app\feature\common\ui\FeatureFragment.kt` | `app\src\main\java\com\mychuannong\app\feature\common\ui\FeatureFragment.kt` | 原页面、学习/生活分组、增加川农公开学校服务 |
| 公告轮播 | `app\src\main\java\com\mychangli\app\feature\common\compose_ui\ServiceDynamicsSection.kt` | `app\src\main\java\com\mychuannong\app\feature\common\compose_ui\ServiceDynamicsSection.kt` | 原组件与数据消费逻辑 |
| 功能图标分色 | `app\src\main\java\com\mychangli\app\feature\common\compose_ui\FunctionPalette.kt` | `app\src\main\java\com\mychuannong\app\feature\common\compose_ui\FunctionPalette.kt` | 原功能辅助色保留 |
| 个人中心 | `app\src\main\java\com\mychangli\app\profileSettings\ui\compose\ProfileSettingsRoute.kt` | `app\src\main\java\com\mychuannong\app\profileSettings\ui\compose\ProfileSettingsRoute.kt` | 原账号卡、官网横幅、分组设置与路由 |
| 原生课表 | `app\src\main\java\com\mychangli\app\feature\timetable\ui\TimeTableActivity.kt` | `app\src\main\java\com\mychuannong\app\feature\timetable\ui\TimeTableActivity.kt` | 原网格/编辑/详情/手势；移除首次清库及绑定拦截 |
| 课表状态 | `app\src\main\java\com\mychangli\app\feature\timetable\viewmodel\TimeTableViewModel.kt` | `app\src\main\java\com\mychuannong\app\feature\timetable\viewmodel\TimeTableViewModel.kt` | 原 Room 数据/手动课程；刷新改接 SchoolSyncEngine |
| 原生成绩 | `app\src\main\java\com\mychangli\app\feature\common\ui\ScoreInquiryActivity.kt` | `app\src\main\java\com\mychuannong\app\feature\common\ui\ScoreInquiryActivity.kt` | 原列表/详情；未知统计显示 --，不套用母版重修口径 |
| 原生考试 | `app\src\main\java\com\mychangli\app\feature\common\viewmodel\ExamArrangementViewModel.kt` | `app\src\main\java\com\mychuannong\app\feature\common\viewmodel\ExamArrangementViewModel.kt` | 原状态与列表映射；刷新改接 SchoolSyncEngine |
| 学校绑定 | `app\src\main\java\com\mychangli\app\settings\ui\BindingUserActivity.kt` | `app\src\main\java\com\mychuannong\app\settings\ui\BindingUserActivity.kt` | 原布局；学校密码输入改为学校网页登录，身份须真实验证 |
| 提醒/小组件 | `app\src\main\java\com\mychangli\app\feature\schedule\reminder\CourseReminderScheduler.kt` | `app\src\main\java\com\mychuannong\app\feature\schedule\reminder\CourseReminderScheduler.kt` | 原调度能力；作息未确认不生成虚构提醒 |
| 动态配置 | `app\src\main\java\com\mychangli\app\core\config\AppConfigModels.kt` | `app\src\main\java\com\mychuannong\app\core\config\AppConfigModels.kt` | 原版本、缓存、安全校验；未配置产品官网、删除旧资源 |

平台账号、Room、MMKV、主题、原图标、动画、原生列表、内容模块和后端原有权限/配置/公告/审计一并继承。学校网络的 SSOWebviewDialog 和 MoocCoursePageActivity 专用环节明确替换：不复制旧 DOM、密码 Bridge 和 Cookie 导出；保留上层路由与原生课程作业模块，缺少真实来源时显示待适配。
