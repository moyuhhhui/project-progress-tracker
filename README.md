# 公司项目进度追踪

公司内部项目工作台：项目与多个里程碑、进展汇报、缺项草稿与完整信息自动保存、提醒队列和只读大屏。

后端为 Python / FastAPI / SQLite，前端为 Vue 3 / TypeScript / Element Plus / Vite。`项目蓝图.md` 是已确认的需求依据，施工时不修改。

## 当前可用范围

- 公司内部共享工作台：设置 `TRACKER_SHARED_USER_ID` 后，网页和大屏直接打开，无需输入访问密钥；网页与群消息共用公司内部共享身份，无需绑定码。
- 创建项目、维护成员和里程碑、汇报进度、调整计划及状态。缺项或有歧义时保留草稿；完整信息通过权限和字段校验后自动保存正式项目，无需二次确认。草稿替换、业务写入及审计在同一事务中完成。
- AI 网页输入、缺项追问、持久化草稿及自动保存。解析器使用 `langchain_deepseek.ChatDeepSeek` 原生接入，只提出固定字段，没有数据库和任意工具执行权限。
- 临期、逾期和待更新计算，工作日覆盖、暂停、每日节点去重、发送前复核及异常记录。
- 只读大屏只展示获准公开项目，15 秒刷新、20 秒轮播，长列表分页；60 秒未成功刷新显示数据可能过期。

企业微信使用官方 Python SDK 长连接接收群内 @消息。公司内部共享模式下无需绑定码即可提交文本；群回复提供处理结果及详情入口，完整信息自动保存。模型、机器人接收与主动提醒分别显式开启。

## 首次启动（Windows PowerShell）

以下命令在项目目录执行。已发现本机 Python 为 `D:\python\python.exe`，Node 为 22.17.1。项目不会自行安装依赖。

```powershell
Set-Location 'C:\Users\21313\Desktop\公司项目进度追踪'
& 'D:\python\python.exe' -c "import fastapi, uvicorn, pydantic, httpx"
```

只有缺少 Python 包时，由操作者自行执行：

```powershell
& 'D:\python\python.exe' -m pip install -r requirements.txt
```

前端依赖由操作者安装。直接进入 `frontend`，避免 npm 的 `--prefix` 参数在本机未正确生效：

```powershell
Set-Location 'C:\Users\21313\Desktop\公司项目进度追踪\frontend'
npm install
npm run build
Set-Location ..
```

初始化首位管理员（仅空用户库可运行），命令会在本机终端显示新密钥，妥善保存：

```powershell
$env:TRACKER_DB = 'C:\Users\21313\Desktop\公司项目进度追踪\data\tracker.sqlite3'
& 'D:\python\python.exe' -m backend.manage init-admin --name '管理员'
& 'D:\python\python.exe' -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

启动 Web 服务前，将 `TRACKER_SHARED_USER_ID` 设置为启用的管理员系统 ID（当前本机已写入 `.env`，通过 `python -m dotenv -f .env run -- python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000` 加载）。浏览器直接打开 [本机工作台](http://127.0.0.1:8000)，无需登录。构建结果由后端提供；如果后端启动时尚未生成 `frontend/dist`，构建后需重启后端。

项目支持选填“对接单位、对接人、联系方式”（电话、微信或邮箱）。可在新建或编辑项目时填写，也可通过网页助手或群消息提供；对接人不必是系统成员，不填写不影响项目保存。项目详情可查看这些信息。

首次使用建议：在成员管理中创建项目责任人 → 直接通过工作台或群机器人录入项目和事项 → 补齐负责人及日期。完整项目默认在大屏展示，可在项目中关闭“大屏可见”；不完整草稿不会进入大屏。直接打开 `/#display` 即可。

共享模式不展示或分发成员访问密钥。所有网页访问者可管理项目、成员和设置，并查看、补充团队草稿；网页保存的审计记录归属共享账号，原草稿保留提交者。企业微信群内仍校验成员权限，普通成员只能汇报本人负责的节点；普通成员首次立项只能由本人负责。

## 开发启动

两个终端分别运行后端与前端：

```powershell
# 终端一：项目根目录
& 'D:\python\python.exe' -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# 终端二：frontend 目录
npm run dev
```

打开 [前端开发页面](http://127.0.0.1:5173)。Vite 将 `/api` 代理到本机 8000 端口。默认监听本机，不会部署到公网。

## 配置

`.env.example` 只列出变量，**程序不会自动加载 `.env`**。在启动各进程前，用 PowerShell 环境变量或部署环境注入。另开终端时须重新设置，尤其是 `TRACKER_DB`；Web 和 worker 必须指向同一个数据库文件。

| 变量 | 默认/作用 |
| --- | --- |
| `TRACKER_DB` | 默认相对当前工作目录的 `data/tracker.sqlite3`；建议使用绝对路径 |
| `TRACKER_AI_ENABLED` | 只有精确为 `true` 才启用 AI |
| `DEEPSEEK_API_KEY` | DeepSeek 开放平台 API 密钥，仅服务端使用 |
| `DEEPSEEK_MODEL` | 明确指定有权限使用的模型，例如 `deepseek-v4-flash`；运行时不内置默认型号 |
| `TRACKER_WECOM_SEND_ENABLED` | 只有精确为 `true` 才允许主动发送 |
| `WECOM_CORP_ID` | 企业 ID |
| `WECOM_APP_SECRET` | 自建应用 Secret |
| `WECOM_AGENT_ID` | 自建应用 AgentId，整数 |
| `TRACKER_WECOM_BOT_ENABLED` | 只有精确为 `true` 才允许启动智能机器人接收进程 |
| `WECOM_BOT_ID` / `WECOM_BOT_SECRET` | API 模式智能机器人的长连接 ID / Secret，与自建应用凭证不同 |
| `TRACKER_WEB_URL` | 员工能访问的工作台 http(s) 根地址，不含账号、密钥、路径前缀、查询参数或片段 |

启用 AI 需要上述三个 AI 变量同时有效。这表示允许将当前消息和权限范围内的项目、节点、成员候选发送给 DeepSeek 官方 API；不要直接把示例当作真实凭证。设置后重启后端和机器人接收进程。旧 `DASHSCOPE_*` 配置不再启用模型。模型输出格式不合法、内容为空、被截断、超时或权限不足时不会保存正式项目，可继续手动录入。

在 [DeepSeek 开放平台](https://platform.deepseek.com/api_keys) 创建 API Key，并在启动服务的 PowerShell 7 终端设置：

```powershell
$env:DEEPSEEK_API_KEY = Read-Host '输入 DeepSeek API Key' -MaskInput
$env:DEEPSEEK_MODEL = 'deepseek-v4-flash'
$env:TRACKER_AI_ENABLED = 'true'
```

示例型号及 JSON 输出参数已依据 [DeepSeek 官方文档](https://api-docs.deepseek.com/) 和 [JSON 输出说明](https://api-docs.deepseek.com/guides/json_mode/)核对。该解析任务显式关闭思考模式，请求 JSON 对象，输出上限为 8192 tokens；本地模拟接口测试不代表真实模型准确率评测或账户连通。模型请求固定发往 `https://api.deepseek.com`。

网页 AI 流程：提交自然语言 → 缺项时保留草稿并提示补充 → 信息齐全且校验通过后自动保存。未提及字段不覆盖；清除阻碍、下一步或预计日期必须明确选择清除。“预计完成”仅写预计日期，不修改计划截止。汇报 100% 不代表已验收，节点完成仍需状态确认。

待补充草稿 30 分钟过期。保存时在事务内重新校验权限和版本，并发补充只允许一个后继。重复消息只处理一次，兼容的旧确认接口不会重复写入；缓存重放会重新核对权限和读取当前草稿状态。页面无响应时先检查草稿列表，避免用新消息编号再次创建同一业务。

## 企业微信：群内 @录入

接收通道依据 [企业微信官方 Python SDK](https://github.com/WecomTeam/wecom-aibot-python-sdk) 的长连接、消息事件和流式回复接口。配置前在企业微信创建支持 API 模式、长连接的智能机器人，取得 BotID 和 Secret，并添加到测试群。普通群 Webhook 只有发消息能力，不能用作此接收通道。机器人权限、群类型和真实回调的 UserID 仍需在企业账号验证。

1. 由操作者安装可选 SDK，不影响已有网页及提醒进程：

   ```powershell
   Set-Location 'C:\Users\21313\Desktop\公司项目进度追踪'
   & 'D:\python\python.exe' -m pip install -r requirements-wecom.txt
   ```

2. 按前文初始化管理员、启动 Web 服务并配置 AI。在“成员与设置”中，为员工绑定真实企业微信 UserID（不是姓名），分配项目成员和节点职责。停用、大屏或未绑定账号不能通过机器人录入。
3. 在接收进程终端设置下面变量；AI 配置也须在这个终端存在。密钥只在本机输入，不要贴进聊天、截图或提交到代码库。

   ```powershell
   $env:TRACKER_DB = 'C:\Users\21313\Desktop\公司项目进度追踪\data\tracker.sqlite3'
   $env:TRACKER_WECOM_BOT_ENABLED = 'true'
   $env:WECOM_BOT_ID = Read-Host '输入机器人 BotID'
   $env:WECOM_BOT_SECRET = Read-Host '输入机器人长连接 Secret' -MaskInput
   $env:TRACKER_WEB_URL = Read-Host '输入员工可访问的工作台根地址'
   $env:TRACKER_AI_ENABLED = 'true'
   $env:DEEPSEEK_API_KEY = Read-Host '输入 DeepSeek API Key' -MaskInput
   $env:DEEPSEEK_MODEL = 'deepseek-v4-flash'
   & 'D:\python\python.exe' -m backend.wecom_worker --check
   & 'D:\python\python.exe' -m backend.wecom_worker
   ```

   `-MaskInput` 需要 PowerShell 7；本机可先运行 `D:\powershell\7\pwsh.exe`。`.env` 不会自动加载。`--check` 仅报告布尔配置状态，不联网、不写数据库或输出密钥；有缺项时退出码为 2。不要把仅电脑本机能访问的 `127.0.0.1` 当作员工手机可访问的网址。局域网或 HTTPS 访问按实际部署环境设置，本实现未发布公网服务。

4. 每个机器人只运行一个接收进程；Web、接收进程与提醒 worker 使用同一数据库绝对路径。接收进程与主动提醒独立，开启接收不会开启定时催办。长连接由服务器向企业微信发起，不需要搭建公网消息回调接口；网页查看和补充仍需要员工能访问 Web 服务。

公司内部共享模式无需账号绑定；群成员直接 @机器人发送项目安排。关闭共享模式时，由管理员在成员管理页面配置企业微信 UserID。

本机联调时，网页可先使用 `http://127.0.0.1:8000`，该地址仅限运行服务的这台电脑访问。群内会直接显示识别出的操作类型，网页查看完整草稿。当前本机首次管理员及普通成员访问密钥保存在受 `.gitignore` 排除的 `data/local-access.json`，不要把此文件发到群里。

员工使用示例：

- `@机器人 P0001 的方案评审目标完成 60%，已完成初稿，下一步等待评审。`
- 点击机器人返回的 `/#draft=完整编号` 链接，可查看已保存详情或待补充字段。所有工作台访问者均可查看和补充团队草稿；信息齐全后自动保存，无需点确认，群回复不包含项目详情或访问密钥。
- 缺字段时，在网页查看追问后填写补充，或在原群发送 `@机器人 补充 完整草稿编号 补充说明`。必须为同一人、同一群、同一机器人发起的草稿。完整编号位于回复链接 `#draft=` 后；不要只用列表中的短编号。网页生成的草稿在网页继续补充。
- 群内“确认”“取消”“帮助”返回工作台及补充说明，不交给模型执行；业务无需二次确认。查询会返回项目列表入口，不在群里公开项目详情。引用消息不作为额外指令读取；图片、语音、文件和私聊不作为本版录入来源。

模型提取候选字段，由服务端校验后保存或保留待补充草稿；重复回调使用独立于网页消息的持久化编号去重，每人每分钟至多 10 条新解析。草稿及会话绑定持久化，自动保存仍检查发起人、当前权限、有效期及对象版本。解析被中断或回复失败时先检查网页草稿，不自动重发业务操作。

管理页与 `/api/status` 显示接收进程状态：`not_started` 未启动、`connecting` 连接中、`authenticated` 认证成功、`disconnected` 已断开、`error` 连接或回复异常、`offline` 心跳过期、`stopped` 已停止。每 20 秒续租，90 秒无心跳标记离线；同库重复启动会被拒绝。认证成功不代表业务闭环和长期主动提醒已经验收。

真实验收顺序：在测试群发一条文本 → 收到处理结果链接 → 完整信息自动保存并上屏 → 再检查重复消息和断线重连。

## 提醒进程与日历

管理员设置页面维护发送开始/结束小时、截止小时及日期覆盖。默认公司时区为 UTC+08:00（Asia/Shanghai），工作日为周一至周五，发送窗口为 09:00（含）至 18:00（不含），日期截止时刻为 18:00。**没有自动导入法定节假日**；节假日填为休息日、调休日填为工作日。

独立终端运行提醒进程，每 10 分钟检查一次；先保持发送开关关闭：

```powershell
Set-Location 'C:\Users\21313\Desktop\公司项目进度追踪'
$env:TRACKER_DB = 'C:\Users\21313\Desktop\公司项目进度追踪\data\tracker.sqlite3'
$env:TRACKER_WECOM_SEND_ENABLED = 'false'
& 'D:\python\python.exe' -m backend.worker --once
# 持续检查时去掉 --once
& 'D:\python\python.exe' -m backend.worker
```

Web 服务不会自动启动 worker。管理员页面“扫描提醒”只更新队列，不发送外部消息。主动发送需要显式开启发送开关且填全企微变量；只有具备真实权限验证和授权后再开启。

- 临期从截止时刻前一个工作日起计算；逾期是严格超过截止时刻；待更新从计划开始、有效当前汇报、开始或恢复时间中适用的较晚时间顺延节点设定的工作日数。
- 历史补报不重置当前汇报时间，也不覆盖当前进度。项目暂停或节点暂停停止自动催办，恢复不自动延长计划截止。
- 同一节点每天最多一条成功提醒，多种原因合并；同一负责人的多个节点按消息大小合并。未启用或未绑定人员进入异常记录，不扩大收件范围。
- `queued` 为待发送，`blocked` 为配置/人员阻塞，`failed` 为明确失败（最多三次尝试，至少间隔 10 分钟），`sending` 为发送中，`uncertain` 为结果不确定并停止自动重试，`accepted` 仅表示平台接受，不表示员工已读，`cancelled` 为条件失效。
- 管理员仅在人工核对平台结果后，才把 `uncertain` 记录标记为已接受；不要把“未知”直接当作送达。

## 测试与构建

在根目录运行后端测试（临时 SQLite、受控时钟和测试解析器，不需要真实 AI/企微凭证，也不会发真实消息）：

```powershell
& 'D:\python\python.exe' -m unittest discover -s backend/tests -v
```

覆盖权限与越权、字段与日期校验、草稿确认幂等和版本冲突、历史补报、事务回滚、AI 缓存撤权及模型异常、大屏数据边界、工作日/暂停/提醒去重与发送异常。

在前端目录运行：

```powershell
npm test
npm run build
```

前端测试验证字段补丁、显式清空、分页和过期计算；构建执行 TypeScript / Vue 类型检查及 Vite 打包。测试解析器与测试发送器不等于真实模型评测或平台连通。实际大屏分辨率、长期运行、断网恢复及真实员工接收提醒仍需现场验收。

DeepSeek 切换后重新执行后端测试，108 项全部通过。新增测试使用本机已安装的 `langchain-deepseek 1.0.1` 与模拟 HTTP 传输，核对官方地址、认证头、JSON 格式、非思考模式、空内容及截断错误处理，以及状态接口不返回 API Key；未调用真实付费模型接口。本次未修改前端代码。

企业微信模拟回调测试覆盖群消息处理、同群补充、消息去重、撤权、群回复数据边界、进程租约和 SDK 接口；真实长连接状态可在管理页面查看。1080p 大屏布局仍建议在公司实体屏幕检查观看距离和可读性。

当前工具链有非阻断提示：Starlette 的 httpx 测试客户端弃用提示、Node 类型剥离实验特性提示，以及 Vite 提示 Element Plus 主包超过 500 KB；不影响上述测试和构建成功，不代表已经完成依赖安全审计。

需要复现浏览器验收时，先构建前端，再在根目录运行：

```powershell
& 'D:\python\python.exe' -m backend.tests.browser_fixture
```

打开 [临时验收页面](http://127.0.0.1:8765)。测试密钥分别是 `browser-test-admin`、`browser-test-member`、`browser-test-display`。此夹具仅监听本机，使用独立临时库，强制关闭 AI 和企微发送；示例项目明确标为测试数据，停止进程会清理临时库。不要把这些公开测试密钥用于正式环境。

## 备份与恢复

用 SQLite 在线备份命令生成一致性快照；目标必须不存在，命令拒绝覆盖：

```powershell
& 'D:\python\python.exe' -m backend.manage backup 'data\backups\tracker-20260904.sqlite3'
```

恢复时先停止 Web 和 worker，保留当前库作回退。把备份复制为一个新的数据库文件，例如 `data\restored.sqlite3`，将两个进程的 `TRACKER_DB` 都设为该文件绝对路径，再启动并检查成员、项目、历史和草稿。备份包含访问密钥哈希、业务和原始汇报文本，按公司内部数据保存。

## 当前限制

- 尚未初始化正式管理员、接通真实模型或企微、部署公网。启动命令由操作者按需执行；测试使用临时账号和临时数据。
- 用户已接受群内 @，本版实现群文本接收、缺项补充与完整信息自动保存，不提供免 @监听、私聊录入或群卡片按钮确认；真实机器人身份映射、回调和长期主动提醒能力待企业账号实测。
- 单机 SQLite 试点设计；持续提醒依赖服务机器和 worker 持续运行，电脑休眠时不会继续检查。使用一个提醒 worker，数据库租约和任务状态用于防止重复处理。
- AI 契约测试主要验证后端权限和失败边界；未完成蓝图中的真实模型脱敏样例评测，不能据此承诺自然语言识别准确率。
- 访问密钥是持有即授权的凭证。成员管理、提醒设置等管理动作由管理员 API 校验权限，页面二次确认；项目和节点变更使用后端持久化草稿。HTTP API 文档页面默认关闭。
- 未实现法定日历自动同步、多机高可用、SSO、原始文本定期清理或公网发布。生产访问方式、数据保留和备份责任按公司实际环境另行配置。
