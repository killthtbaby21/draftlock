# draftlock

用 Claude 逐段改写**你自己写的初稿**，让文字更自然、少一些模板腔。改写时锁定事实，标题和参考文献原样保留，每处改动都在对照报告里列出来，由你决定用不用。

*English summary below.*

## 和其他"降 AI 率"工具有什么不同

- **事实锁定**：改写前，自动提取每段里的数字、日期、提交编号、引用 `(Author, 2026, p. 3)`、网址、引号里的原话、缩写（如 `NFR-04`、`SAPOL`）和人名、产品名；改写后逐项核对。少了或多了任何一项，先带着错误说明重试一次；还不对，这一段就**保留原文**，并在报告里写明原因。
- **不碰不该碰的**：标题、参考文献部分、太短的行、带超链接、域代码、图片或混合格式的段落默认跳过；Word 文件的样式、页眉页脚、链接都不变。
- **对照报告**：生成一个 HTML 报告，左边原文，右边逐词标出改动，下面列出锁定并保留的事实。
- **模仿你自己的文风**：用 `--style` 给它一两篇你以前自己写的文章，它会往你的写法上靠。
- 中文默认两轮（先改书面腔，再去套话），英文一轮。

## 安装

需要 Python 3.10 或以上。

```bash
git clone https://github.com/<your-account>/draftlock.git
cd draftlock
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

然后设置 Anthropic API key（在 [console.anthropic.com](https://console.anthropic.com) 创建）：

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

## 使用

先预览，不调用 API，也不花钱。会列出哪些段会改、哪些跳过，以及每段锁定了哪些事实：

```bash
draftlock report.docx --dry-run
```

正式改写：

```bash
draftlock report.docx
```

会生成三个文件：

- `report.draftlock.docx`：改写后的文档
- `report.report.html`：对照报告，用浏览器打开
- `report.report.json`：机器可读的记录

### 常用参数

| 参数 | 作用 |
|---|---|
| `--only 4,9-12` | 只改这些段（段号见报告或 `--dry-run`），比如检测器标出来的段落 |
| `--lock "Password Checker"` | 额外锁定某个词，可以重复使用；`--lock-file terms.txt` 每行一个 |
| `--style my_old_essay.docx` | 给一篇你自己以前写的文章，用来模仿文风，可以重复使用 |
| `--protect "^Figure \d"` | 匹配这个正则的段落一律不改 |
| `--lang zh` / `--lang en` | 指定语言（默认自动判断） |
| `--rounds 1` | 改写轮数（默认中文 2 轮、英文 1 轮） |
| `--model claude-sonnet-5-5` | 换模型（默认 `claude-opus-5-5`） |
| `--effort low` | 让模型少想一点、更快更便宜（默认 `medium`） |
| `--workers 4` | 同时处理几段 |
| `--mock` | 用离线的假模型跑一遍流程，不需要 API key |

完整参数见 `draftlock --help`。

### 费用

一篇 2,000 词左右的英文报告，用默认的 `claude-opus-5-5` 大约要几十美分；用 `--model claude-haiku-5-5` 会便宜很多。每次运行结束都会打印实际用量和估算费用。

## 原理

1. 读取 `.docx`、`.txt` 或 `.md`，把每个段落标为"改写"或"保护"。
2. 每段（太长的段按整句切开）连同前后文发给 Claude，并附上这段必须保留的事实清单。
3. 检查输出：长度变化不超过约 50%，没有加 Markdown，没有拆成多段，事实一项不少、也没有新增。
4. 不通过就带着具体问题重试一次；还是不通过就保留原文。
5. 写回 Word：只替换段落文字，保留段落样式和第一个文字块的格式。

如果安全分类器拒绝了某段，API 会自动在 Anthropic 推荐的备用模型上重跑（server-side fallbacks），可以用 `--no-fallbacks` 关掉。

## 请负责任地使用

这个工具是为**修改你自己写的稿子**设计的：作者是你，观点和经历是你的，它只帮你调整措辞。它不能保证通过任何 AI 检测器，那些检测器本身也不稳定。很多学校规定，用 AI 改写过的作业要声明，用工具掩盖作业由 AI 生成通常也算违规。提交前，请看清楚你的课程对 AI 使用有什么规定，并按要求声明。

## 开发

```bash
pip install -e ".[test]"
pytest
```

---

## English summary

`draftlock` rewrites your own draft paragraph by paragraph with Claude so it reads less like template text, while:

- **locking facts**: numbers, dates, IDs, citations, URLs, quotations, acronyms and names are extracted before the rewrite and checked after it. A paragraph that loses or invents a fact is retried once, then kept as written.
- **leaving structure alone**: headings, the reference list, short lines and paragraphs with links, fields or mixed formatting are skipped; Word styles, headers, footers and hyperlinks are preserved.
- **showing every change**: an HTML report with a word-level diff for each paragraph.
- **matching your voice**: pass your own earlier writing with `--style`.

```bash
pip install -e .
export ANTHROPIC_API_KEY="sk-ant-..."
draftlock report.docx --dry-run   # preview, no API calls
draftlock report.docx             # writes report.draftlock.docx + report.report.html
```

It is meant for editing writing you authored. It does not guarantee any AI-detector result. Follow your institution's rules on AI assistance and disclose its use where required.

## License

MIT
