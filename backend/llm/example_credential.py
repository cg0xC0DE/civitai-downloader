# -*- coding: utf-8 -*-
"""
LLM API 凭证配置
请在此处填入你的 API Key，此文件已加入 .gitignore，不会被提交。
"""

# OpenAI API Key
OPENAI_API_KEY = ""

# 可选：自定义 API Base URL（用于兼容第三方代理/中转）
# 留空则使用 OpenAI 官方地址 https://api.openai.com/v1
OPENAI_API_BASE = ""

# 说明：
# - 某些功能（如美学分析）会在代码中固定使用指定模型，不读取 OPENAI_MODEL。
# - 若需全局默认模型，请使用环境变量 OPENAI_MODEL。
