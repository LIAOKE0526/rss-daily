# 中文主题日报

抓取 RSS / Atom → 北京时间最近 48 小时 → 去重 → 由兼容 API 按事件合并、选择主题并编写 → 本地 Markdown、HTML、RSS。没有数据库、Docker、后台服务或新界面。

dot 或 Codex 任务的入口见 [AGENTS.md](AGENTS.md)。完整汇总规则只有一份，位于 [prompts/daily.txt](prompts/daily.txt)；API 路径由程序读取，dot 路径则由代理先读取。这些规则只作用于本项目。让 dot 在仓库根目录创建任务，并明确要求先读 `AGENTS.md`，不能假定它继承原来的聊天。

## 交给 dot 云端运行

dot 负责摘编，Python 负责抓取和渲染，这条路径不需要 DeepSeek 或 OpenAI API key。仓库保存代码和规则；把它交给 dot 不等于已经建立定时任务或发布手机 RSS。

在 dot 的云端环境检出仓库，按实际系统安装 Python 3.13 和依赖。Linux/macOS 示例：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python daily.py --preview
```

接着由 dot 读取 `output/fetch.articles.json` 和 `prompts/daily.txt`，生成本期摘编 JSON，例如 `output/drafts/2026-11-24.digest.json`，再运行：

```bash
python daily.py --snapshot output/fetch.articles.json --digest output/drafts/2026-11-24.digest.json
```

日期文件名应按本次材料生成。dot 应交付日报正文及可下载文件，不只发送云端的 `file:///...` 路径。日常执行不需要再次运行示例稿，也不用改 `first_issue.json`。

仓库自带已认可的 [第四版样稿](examples/daily-v4.md)、[HTML 样稿](examples/daily-v4.html)及固定原始材料。可离线重现样稿，验证环境与渲染，无需 API：

```bash
python daily.py --snapshot examples/2026-11-23.articles.json --digest first_issue.json --output replay
python -m unittest discover -s tests -v
```

当前验收来自 Windows 本机；云端需重新实测新闻源的可访问性。`output/`、`.venv/`、日志和本机 `revisions/` 历史不上传，前三版仍保留在原电脑。GitHub 仓库中的代码、规则和样稿是交接材料，历史日报需由实际运行环境另行保存。

## 查看本期

- `output/latest.html`：可双击打开的本期最新稿；当前为第四版摘编稿。
- `output/review-v4.html`：第四版独立审阅文件，便于与前版对照。
- `output/latest.md`：相同内容的 Markdown。
- `output/feed.xml`：每个北京时间日期一期，重跑同日替换该期，保留最近 60 期。
- `output/preview.html` / `preview.md`：本次实际无密钥运行产生的预览，明确写明尚未生成 AI 摘要。
- `output/issues/2026-11-23/articles.json`：本期真实抓取的完整材料快照，含原文 URL、时间、正文状态和来源失败记录。
- `ACCEPTANCE.md`：本机验收记录，区分真实网络抓取与离线测试数据。

原 Windows 本机首次验收时没有 `DEEPSEEK_API_KEY`。首期 5 主题稿是 Codex 助手依据上述真实材料编写的编辑样稿，保存于 `first_issue.json`，并非 DeepSeek API 调用结果。常规运行在缺少密钥时只输出抓取预览，不伪装成已生成日报。

## 启动

在 PowerShell 中运行：

```powershell
Set-Location D:\Workspace\rss-daily
.\run.ps1 -Open
```

如果 Windows 的执行策略拦截本地脚本，可以仅对这一次进程运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Workspace\rss-daily\run.ps1 -Open
```

首次启动会在这个目录创建 `.venv` 并安装 `requirements.txt`；原 Windows 本机已安装，新检出的环境仍需安装。后续运行复用环境。`-Preview` 始终只抓取、不调用 API。`-Open` 打开本次生成的成稿或预览。日常成功运行会更新 `latest.*` 和当天归档；首期编辑稿与固定材料分别保存在 `first_issue.json` 和 `examples/2026-11-23.articles.json`，不会被滚动抓取替换。

## API 与密钥

编辑 `config.json` 的 `api.base_url`、`api.model`、`api.key_env`。兼容接口使用 `POST /chat/completions`，地址可以是根地址、带 `/v1` 的地址或完整 `/chat/completions` 地址。默认按 [DeepSeek 官方文档](https://api-docs.deepseek.com/)配置 `https://api.deepseek.com` 与 `deepseek-flash`。模型名称以你自己的服务为准。

密钥只从 `api.key_env` 指定的进程环境变量读取；程序不读取密钥文件，不把密钥写入配置、抓取快照、HTML 或错误日志。不建议在命令行直接粘贴密钥以免进入终端历史。可在 Windows“编辑账户的环境变量”里增加 `DEEPSEEK_API_KEY`，然后重新打开 PowerShell，使用以下命令只检查是否存在：

```powershell
[bool]$env:DEEPSEEK_API_KEY
```

默认 `api.request_options` 关闭 DeepSeek 思考模式，以便将输出额度用于日报正文。自定义兼容服务若不接受 `thinking` 参数，将该项改为 `{}`；也可填入服务支持的其他请求参数。API 地址和模型也可通过环境变量临时覆盖：

```powershell
$env:RSS_API_BASE_URL = 'https://你的兼容服务/v1'
$env:RSS_API_MODEL = '你的模型名'
.\run.ps1 -Open
```

有密钥时，会把选入的新闻标题、链接、公开正文片段/摘要发送到你配置的 API；失败或引用格式不合法时保留抓取并输出明确标注的 `preview.*`。已有成功日报时，无密钥、预览或 API 失败不会覆盖成稿、`latest.*` 或 RSS；初次没有成稿时，`latest.*` 和 RSS 可以先容纳清楚标注的预览。当前自动化路径没有模型联网工具，不声称搜索了未提供的材料。

## 来源与手动加 RSS

编辑 `config.json` 的 `feeds` 数组即可。支持 RSS 2.0、RSS 1.0 和 Atom。最少格式：

```json
{"name":"你的来源", "publisher":"实际媒体名", "url":"https://example.com/feed.xml", "enabled":true}
```

仅本次临时添加，不修改配置：

```powershell
.\run.ps1 -FeedUrl 'https://example.com/feed.xml' -FeedName '我的来源'
```

本次启用了 BBC 中文、BBC 国际、FT、知识分子、机核、卫报国际、The Verge、量子位、爱范儿、Eurogamer。机器之心的候选地址跳至数据服务页面；财新、财经、三联的已查候选不可用，因此保留为禁用条目并写明原因。没有使用第三方付费全文镜像；没有改变已有阅读器或订阅。

`publisher` 用来避免把同一媒体的不同频道算成多家来源。`access: "summary_only"` 适合付费媒体公开 RSS，本次 FT 使用此设置。其他源会尝试普通匿名 GET 提取公开文章；网页出现付费标记、登录/访问失败或正文提取不足时保留摘要并说明。

知识分子的 RSS 将东八区写为 `CST`，已经结合第一方文章日期核实，单独配置了 `date_timezone_aliases: {"CST":"+0800"}`。其他来源不套用这个解释。优先采用 RSS 更新时间，缺失时用发布时间；缺失所有时间和晚于运行时刻的条目跳过，绝不以抓取时间冒充发布时间。RSS 在窗口前发布但窗口内更新的文章可以入选，原始时间字段保存在快照中。

## 改汇总方式

直接编辑 `prompts/daily.txt`。当前按用户认可的“摘编、删减和整合”方式处理：中文尽量保留原句，删除重复和无关枝节，只作必要衔接；英文先选句再忠实译摘。复杂主题通常 450–750 字，不为降重做同义改写，不添加自己的评论、趋势判断或建议，也不逐一介绍媒体的报道方式。证据 ID 内部保留，显示为 `[1]`、`[2]` 等数字引用，逐一对应原文链接。摘要和付费材料只使用已经获得的公开文字。前三版保留于 `revisions/v1/` 至 `revisions/v3/`，当前审阅稿见 `output/review-v4.html`。

本期用户明确将 `D:/Workspace/rss-daily/examples/2026-11-23.articles.json` 内的原文提供为编辑材料；第四版使用这批固定材料，黎曼段沿用用户已认可的摘编正文。这项材料说明针对这个快照，不把以后新抓取的内容自动当作用户已经提供的材料。若模型再次因版权或引用规则无法完成摘编，prompt 要求返回具体受限材料和原因，程序明确显示“版权引用限制”，保留已有成稿及 RSS，不静默改为同义改写。用户可在获知具体情况后提供适用的本地材料再继续。

`max_article_fetches` 默认 80，按媒体轮流选择公开网页提取，其余仍保留 RSS 材料；`llm_max_articles` 默认 140，均衡选取媒体以避免高产源挤占输入；`llm_article_chars` 限制每篇输入片段。这些值可在配置中改，所有窗口内条目的元数据和已有 RSS 内容仍保存于快照。候选关键词分组仅用于缺少 API 时浏览材料，明确标为待确认，不当作同事件合并结论。

复用抓取快照来重跑 API，不必重新联网抓取：

```powershell
.\.venv\Scripts\python.exe daily.py --snapshot output\fetch.articles.json
```

重现首期编辑稿：

```powershell
.\.venv\Scripts\python.exe daily.py --snapshot examples\2026-11-23.articles.json --digest first_issue.json --output replay
```

## 本地 RSS 和下一步

`feed.xml` 的每一期正文包含完整主题段落及原文链接。阅读链接为 `file:///...`，仅在文件所在的运行环境有效；生成 XML 不代表手机能访问。程序不开放端口、不部署网站，也不设置 Windows 定时任务。GitHub 私有仓库用于保存代码、规则和固定样稿，不承担日报发布或手机 RSS 托管；定时和实际交付方式另按用户给 dot 的指令设置。

验收测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
