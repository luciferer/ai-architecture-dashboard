# AI 架构看板

只读看板，用于发现 MCP 注册、Skills、插件、Hooks，以及用户显式提供的 Codex 或 Claude JSONL 观察记录。默认启动使用合成演示数据；不会扫描用户目录或读取本机配置。

## 快速启动

需要 Python 3.11 或更新版本。Python 程序只使用标准库；浏览器图形库已随仓库提供。

```sh
python3 --version
python3 manage.py
```

打开 <http://127.0.0.1:8768> 查看合成组件演示。打开 <http://127.0.0.1:8768/?view=runtime> 查看合成的“会话 → 代理 → 工具”分支。按 Ctrl-C 停止服务。

更改端口：

```sh
python3 manage.py --port 8877
```

## 显式选择真实采集输入

真实采集必须通过 `--live` 显式启用，并提供配置根目录。项目根和 trace 文件参数可以重复指定。程序不会从本仓库路径推断输入，也不会改写客户端设置。

```sh
python3 manage.py \
  --live \
  --dotfiles-root /path/to/config-root \
  --user-root /path/to/user-config-root \
  --project-root /path/to/project-a \
  --project-root /path/to/project-b \
  --trace-file /path/to/codex-session.jsonl \
  --trace-file /path/to/claude-session.jsonl
```

`--no-default-projects` 关闭受限的默认项目发现。默认发现只查看 `DOTFILES_ROOT/workspaces/*` 和 `DOTFILES_ROOT/workspaces/codex/projects/*` 两组目录的第一层；显式提供的 `--project-root` 仍会纳入范围。程序不会递归遍历任意项目目录或依赖目录。

采集器只按两个显式根目录下已知的 Codex、Claude Code、Claude Desktop 和 Antigravity 文件布局查找配置，并且只从已允许的项目根读取项目 MCP 元数据。它不会启动 MCP 命令或安装 Hook。版本化插件缓存中的条目会标注为缓存元数据，不代表当前已安装或正在使用。

## 运行观察与隐私

只有通过 `--trace-file` 显式提供的 JSONL 文件才会被读取。解析器限制文件大小、物理行长度和事件数量，只保留白名单元数据，例如会话和代理标识的哈希、工具名称、时间、状态、耗时及 trace 明确提供的代理父级关系。提示词、工具参数、工具结果、凭据和未知载荷字段不会进入图数据或导出。“已报告完成”表示 trace 中的状态记录，不代表工具结果经过独立验证；未知父级关系保持未知。

服务只监听 IPv4 回环地址 `127.0.0.1`，只提供只读图和导出接口。它不会连接模型、MCP 服务器、凭据 broker 或远程服务。JSON 与 Mermaid 导出包含与看板相同的白名单图元数据。

## 测试

使用 Python 3.11 或更新版本运行基于合成 fixture 的测试：

```sh
python3 -m unittest discover -s tests -v
```

测试会在临时目录中生成合成配置树和 trace 文件，不需要读取真实客户端设置或日志。

## 项目许可

项目代码采用 [MIT License](LICENSE)。随仓库提供的 Cytoscape.js 保留其原有 MIT 许可与版权声明，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
