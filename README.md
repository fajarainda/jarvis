# 小智联网查询 MCP

本工具能够为小智提供联网查询的能力，例如查询新闻、热播电视剧、今日金价等。

在大多数情况下，直接向小智提问并不会触发联网查询。您需要在提问时加上“联网查询”或“查询”等关键词。

## 使用说明

### 1. 环境配置

请确保您已安装 Python 环境。

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 参数配置

首先，请复制示例配置文件 `config_sample.json` 并重命名为 `config.json`。

然后，您需要修改 `config.json` 文件，填入您自己的 `MCP_ENDPOINT` 和 `ZHIPU_API_KEY`。

### 4. 启动程序

```bash
python run.py
```

## 来源

本工具的实现参考了以下飞书文档：
[小智接入联网查询教程mcp](https://x0ntkzs0zde.feishu.cn/docx/JKFXd8bLYo6YZtxz9ORcbnA8nbe)# jarvis
