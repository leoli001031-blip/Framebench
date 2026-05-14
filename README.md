# Framebench / 拉片工作台

Framebench is a lightweight desktop workbench for film analysis, shot references, and storyboard drafts.

它面向创作者的日常拉片流程：上传一段视频，让模型拆解镜头与节奏，保留可复用的参考素材，再生成可以继续修改的分镜脚本。

## 功能展示

### 1. 上传与分析

上传本地视频后，Framebench 会保留历史分析记录，并提供一个简单的进度视图，方便回到之前的项目。

![Recent analysis list](docs/screenshots/01-analysis.png)

### 2. 镜头报告

报告页不强制固定模板，给大模型保留发挥空间；同时用关键帧、时长和镜头段落把内容落到可读的画面细节上。

![Shot report with keyframes and notes](docs/screenshots/02-report.png)

### 3. 参考仓库

完成分析的视频可以沉淀到仓库里，作为后续分镜、风格参考或项目复盘的素材池。

![Reference library](docs/screenshots/03-library.png)

### 4. 分镜工作台

在分镜页选择参考项目，生成一版可继续编辑的分镜草稿。

![Storyboard workspace](docs/screenshots/04-storyboard.png)

### 5. 分镜脚本

分镜输出会尽量保留创作可用的信息，例如镜头描述、时长、画面提示词、音乐节奏和参考来源。

![Generated storyboard script detail](docs/screenshots/06-storyboard-script.png)

### 6. 本地设置

模型、API Key 和服务参数可以在前端直接编辑；历史记录和密钥保存在本机，不会被打进安装包或源码仓库。

![Local model and API settings](docs/screenshots/05-settings.png)

## 主要功能

- 上传本地视频并生成镜头级分析。
- 在进度页查看处理状态。
- 阅读自由结构的 AI 报告，而不是被固定维度限制。
- 保存轻量参考仓库，支持后续复用。
- 从参考视频生成分镜草稿与视觉提示词。
- 在应用内编辑模型配置与 API Key。
- 以 macOS 桌面应用形式运行，并带有后端服务启动逻辑。

## 技术栈

- Frontend: React, TypeScript, Vite, Electron
- Backend: FastAPI, SQLite
- Runtime: local desktop app with a bundled Python backend

## 本地开发

```bash
./start.sh
```

开发脚本会同时启动 FastAPI 后端和 Vite 前端。新的环境变量使用 `FRAMEBENCH_*` 前缀；旧的 `FILM_MASTER_*` 变量仍然保留兼容。

## 构建安装包

```bash
cd frontend
npm run electron:build
```

打包后的应用会优先复用已有的兼容数据目录，因此从早期 Film Master / 拉片工作台版本迁移过来时，历史记录和 API Key 不会因为改名丢失。

## 本地数据

Framebench 会把历史记录、生成报告、任务文件和 API 设置保存在用户本机。源码仓库和安装包不包含这些私人数据。

常见 macOS 数据位置：

- `~/Library/Application Support/Framebench`
- 早期版本遗留的兼容数据目录

## License

MIT
