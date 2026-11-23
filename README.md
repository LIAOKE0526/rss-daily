# 中文主题日报

抓取 RSS / Atom → 北京时间最近 48 小时 → 去重 → 按事件生成独立 Reader 草稿 → 本地 Markdown、HTML、RSS 与待交付清单。没有数据库、Docker、后台服务或新界面。

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

接着由 dot 读取 `output/fetch.articles.json` 和 `prompts/daily.txt`，按事件生成摘编 JSON，例如 `output/drafts/2026-11-24.digest.json`，再运行：

```bash
python daily.py --snapshot output/fetch.articles.json --digest output/drafts/2026-11-24.digest.json
```

日期文件名应按本次材料生成。有效摘编会在 `output/reader/` 为每篇事件生成独立 Markdown/HTML 草稿，草稿仅含该事件正文与来源，不含整轮状态；程序也会写入 `output/delivery.json` 和 `output/chat.md`。dot 从每篇本地草稿创建单独 Reader 文档，重新打开并核对标题与正文；只有核对通过后，才在对应主题中写入真实 `reader_summary_url` 和 `reader_summary_verified: true`，再用同一抓取快照和 JSON 渲染。`delivery.json` 记录草稿路径和链接状态；未核实链接的条目为 pending，不能进入 `chat.md`。日常执行不需要再次运行示例稿，也不用改 `first_issue.json`。

`output/chat.md` 只包含已核实 Reader 摘要的 Markdown 标题链接，每行一个，不含简介、图片或附件。预览不产生可交付聊天结果；仅在本轮命令成功并核对本轮输出后才可交付，命令失败时不得发送残留的旧 `chat.md` 或其他旧文件。dot 负责正式发布和发送；发送清单正文，不附加本地日报文件或云端文件路径。若本轮是预览或没有事件，`chat.md` 清空、旧成稿保留；零篇不发送通知，也不生成空日报。文本 API 不能创建或核实 Reader 文档，不能提供有效的摘要链接。

仓库自带已认可的 [第四版样稿](examples/daily-v4.md)、[HTML 样稿](examples/daily-v4.html)及固定原始材料。可离线重现样稿，验证环境与渲染，无需 API：

```bash
python daily.py --snapshot examples/2026-11-23.articles.json --digest first_issue.json --output replay
python -m unittest discover -s tests -v
```

当前验收来自 Windows 本机；云端需重新实测新闻源的可访问性。`output/`、`.venv/`、日志和本机 `revisions/` 历史不上传，前三版仍保留在原电脑。GitHub 仓库中的代码、规则和样稿是交接材料，历史日报需由实际运行环境另行保存。

## 查看本期与交付状态

- `output/reader/001.md`、`001.html` 等：每篇一个事件的 Reader 草稿，只含该篇正文和来源，不含整轮状态。
- `output/delivery.json`：本轮每篇草稿的本地路径及 Reader 链接状态；只按本次成功运行的清单取稿，不扫描可能保留旧稿的 `reader/` 目录。
- `output/chat.md`：已核实 Reader 摘要的 Markdown 标题链接清单；待核验草稿不在其中。
- `output/latest.md`、`latest.html`：本地合并归档，不能当作单事件 Reader 文档或聊天附件。
- `output/feed.xml`：本地合并 RSS 归档；每个北京时间日期一期，重跑同日替换该期，保留最近 60 期。文件中的链接仅在所在运行环境有效，不代表已发布手机 RSS。
- `output/preview.html` / `preview.md`：本轮预览，不是已完成的事件稿。
- `output/issues/`：按日保留的材料与合并归档。
- `ACCEPTANCE.md`：本机验收记录，区分真实网络抓取与离线测试数据。

原 Windows 本机首次验收时没有 `DEEPSEEK_API_KEY`。首期 5 主题稿是 Codex 助手依据上述真实材料编写的编辑样稿，保存于 `first_issue.json`，并非 DeepSeek API 调用结果。常规运行在缺少密钥时只输出抓取预览，不伪装成已生成日报。

## 启动

在 PowerShell 中进入仓库根目录后运行：

```powershell
.\run.ps1 -Open
```

如果 Windows 的执行策略拦截本地脚本，可以仅对这一次进程运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Open
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

有密钥时，会把选入的新闻标题、链接、公开正文片段/摘要发送到你配置的文本 API；失败或引用格式不合法时保留抓取并输出明确标注的 `preview.*`。文本 API 只生成文字，不能创建或核实 Reader 文档；程序会剥离它给出的 `reader_summary_url`、`reader_summary_verified`。只有 dot 创建 Reader 后重新打开，并核对标题和正文，才可填写这两个字段。已有成功日报时，无密钥、预览或 API 失败不会覆盖成稿、`latest.*` 或 RSS；初次没有成稿时，`latest.*` 和 RSS 可以先容纳清楚标注的预览。当前自动化路径没有模型联网工具，不声称搜索了未提供的材料。

## 来源与手动加 RSS

编辑 `config.json` 的 `feeds` 数组即可。支持 RSS 2.0、RSS 1.0 和 Atom。最少格式：

```json
{"name":"你的来源", "publisher":"实际媒体名", "url":"https://example.com/feed.xml", "enabled":true}
```

仅本次临时添加，不修改配置：

```powershell
.\run.ps1 -FeedUrl 'https://example.com/feed.xml' -FeedName '我的来源'
```

启用来源以 `config.json` 为准。机器之心已改用 BestBlogs 的公开公众号 RSS，保留可取得的正文和 RSS 图片；阮一峰使用含周刊正文的官方 Atom。虎嗅和量子位保留已有订阅。财新、财经、三联的已查候选不可用，继续保留为禁用条目并写明原因。

动漫/漫画/二次元新增 Anime News Network（英文）和 Comic Natalie（日文）；美股泛读新增 CNBC 美国要闻和 MarketWatch（英文）。MarketWatch 使用公开 RSS 摘要；其他来源按已有规则提取可访问正文。`subscriptions.opml` 同步包含这些 RSS/Atom 订阅，不改变 Reader 已有订阅。

科研源包括 Nuclear Fusion、Nature Machine Intelligence、Journal of Instrumentation（JINST）、IEEE TCAD，以及 arXiv 的两组合并分类：`physics.plasm-ph+physics.ins-det`（等离子体、仪器与探测器）和 `cs.AR+eess.SP+cs.LG`（硬件架构、信号处理、机器学习）。这些源覆盖核聚变、核诊断、FPGA、AI 及其交叉研究。arXiv 是分类论文更新，不能视为人工精选或同行评审认证；科研 RSS 主要提供摘要，不能据此声称已读全文。Nature Machine Intelligence 的提要位于 RSS `content`，按默认方式保留，不将该字段自动认作全文。期刊目录可能按批次或月度更新，近 48 小时没有条目不等于订阅失败。

已按后续要求新增虎嗅、IT之家官方 RSS，以及界面新闻的商业、财经、中国、天下、科技五个第一方栏目。界面栏目使用 `format: "jiemian_html"` 读取官网列表，不依赖第三方全文镜像，统一 `publisher: "界面新闻"`。列表的“今天/昨天/月日”按源站响应日期解释，精度保留到分钟；“几分钟前”等不可靠相对时间仅在取得原文明确发布时间后使用。RSS 和栏目页返回条数受源站限制，48 小时筛选不保证穷尽媒体在该窗口的全部文章。

## 入选新闻的图片候选

正常抓取会保留 RSS 图片信息，并复用已有正文请求提取公开分享图和正文图，不为图片另行请求所有文章。条目 `images` 中的 `url`、`source_page`、`alt`、`caption`、`origin` 是候选资料，dot 仍须实际看图、检查广告与事件匹配。运行及筛选规则详见 `prompts/daily.txt`。

候选不足时，仅对已入选的文章补取：

```bash
python daily.py --snapshot output/fetch.articles.json --image-articles A001 A007
```

结果另存 `output/image.articles.json`，保留已有候选且不修改输入快照；不会调用模型、下载图片文件或更新成稿。选择合适的 Reader 插图后，在对应主题的 `images` 数组写入图地址、来源页面、图注和 `after_paragraph`，使用 `--snapshot output/image.articles.json --digest 本期稿件文件` 渲染。无图可以完全省略 `images`；所有候选都不合适时仍正常输出文字。普通文本 API 的图片选择不会直接发布，只有 dot 审核后通过 `--digest` 提供的图片才会渲染。

`publisher` 用来避免把同一媒体的不同频道算成多家来源。`access: "summary_only"` 适合付费媒体公开 RSS，本次 FT 使用此设置。其他源会尝试普通匿名 GET 提取公开文章；网页出现付费标记、登录/访问失败或正文提取不足时保留摘要并说明。

知识分子的 RSS 将东八区写为 `CST`，已经结合第一方文章日期核实，单独配置了 `date_timezone_aliases: {"CST":"+0800"}`。其他来源不套用这个解释。优先采用 RSS 更新时间，缺失时用发布时间；缺失所有时间和晚于运行时刻的条目跳过，绝不以抓取时间冒充发布时间。RSS 在窗口前发布但窗口内更新的文章可以入选，原始时间字段保存在快照中。

## 改汇总方式

直接编辑 `prompts/daily.txt`，它是唯一规则正文。每轮可有零篇、一篇或多篇，不设固定篇数上限；每篇只写一个具体事件，并成为独立 Reader 文档，标题为“【两字类别】具体事件”。保留长段原文摘编、删减和整合为主的方式：允许较长原句和段落，不强制同义改写、不限制为短引，也不添加自己的评论。复杂事件的 Reader 正文通常 450–750 字，最长约为对应旧稿的两倍；多源证据和不同报道的事实差异按材料保留，最终每篇选 1–5 条核心来源。没有聊天简介字段。英文关键事实自然融入中文正文；不另建独立全文译文文档，已存在且实际可读的 Reader 中文报道、译文或摘编仍可作为来源链接。仅摘要和英文原文等内容状态按 prompt 标明。前三版保留于 `revisions/v1/` 至 `revisions/v3/`；`examples/daily-v4.md` 与 `first_issue.json` 是旧稿，只作篇幅和摘编风格参考，旧稿标题不作为当前输出格式的示例。

本期用户明确将 `examples/2026-11-23.articles.json` 内的原文提供为编辑材料；第四版使用这批固定材料，黎曼段沿用用户已认可的摘编正文。这项材料说明针对这个快照，不把以后新抓取的内容自动当作用户已经提供的材料。若模型再次因版权或引用规则无法完成摘编，prompt 要求返回具体受限材料和原因，程序明确显示“版权引用限制”，保留已有成稿及 RSS，不静默改为同义改写。用户可在获知具体情况后提供适用的本地材料再继续。

`max_article_fetches` 默认 80，按媒体轮流选择公开网页提取，其余仍保留 RSS 材料；`llm_max_articles` 默认 140，均衡选取媒体以避免高产源挤占输入；`llm_article_chars` 限制每篇输入片段。这些值可在配置中改，所有窗口内条目的元数据和已有 RSS 内容仍保存于快照。候选关键词分组仅用于缺少 API 时浏览材料，明确标为待确认，不当作同事件合并结论。

复用抓取快照来重跑 API，不必重新联网抓取：

```powershell
.\.venv\Scripts\python.exe daily.py --snapshot output\fetch.articles.json
```

重现首期编辑稿：

```powershell
.\.venv\Scripts\python.exe daily.py --snapshot examples\2026-11-23.articles.json --digest first_issue.json --output replay
```

## 本地归档与正式交付

`latest.md`、`latest.html` 和 `feed.xml` 是本地合并归档，不是单事件 Reader 成品，不能作为聊天附件或替代 Reader 摘要链接。阅读链接通常是 `file:///...`，仅在文件所在的运行环境有效；生成 XML 不代表手机能访问。程序不开放端口、不部署网站，也不设置 Windows 定时任务。GitHub 仓库用于保存代码、规则和固定样稿，不承担日报发布或手机 RSS 托管。dot 负责创建 Reader、核验摘要链接，并按用户指令正式发布或发送；聊天内容仅为 `output/chat.md` 中逐行列出的已核实 Reader 标题链接。零篇时不发通知。

验收测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
