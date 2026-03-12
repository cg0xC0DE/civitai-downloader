# -*- coding: utf-8 -*-
"""
LLM 客户端 — 封装 OpenAI Chat Completions API（兼容第三方代理）

提供两个核心方法：
  chat(messages, **kwargs)            → 纯文本对话
  chat_with_image(messages, image, **kwargs) → 带图像的对话（Vision）

用法示例：
  from llm.llm_client import chat, chat_with_image

  # 纯文本
  reply = chat([
      {"role": "system", "content": "你是一位美学分析师"},
      {"role": "user",   "content": "分析这幅画的构图"},
  ])

  # 带图像（接受 bytes / base64 str / 文件路径 / URL）
  reply = chat_with_image(
      messages=[{"role": "system", "content": "分析图片"}],
      image="path/to/image.png",
      user_text="请从美学角度分析",
  )
"""

import os
import sys
import json
import base64
import urllib.request
import urllib.error

# ============ 加载凭证 ============

def _load_credential():
    """从 credential.py 读取配置，返回 (api_key, api_base, model)。OPENAI_MODEL 可省略。"""
    try:
        from llm import credential as cred
    except ImportError:
        # 兼容直接运行时的路径
        _dir = os.path.dirname(__file__)
        if _dir not in sys.path:
            sys.path.insert(0, _dir)
        import credential as cred

    api_key = getattr(cred, "OPENAI_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    api_base = (getattr(cred, "OPENAI_API_BASE", "") or os.environ.get("OPENAI_API_BASE", "")
                or "https://api.openai.com/v1")
    model = getattr(cred, "OPENAI_MODEL", "") or os.environ.get("OPENAI_MODEL", "gpt-4o")

    return api_key, api_base.rstrip("/"), model


# ============ 图像处理 ============

def _prepare_image(image):
    """
    将多种图像输入统一为 OpenAI Vision API 所需的 image_url dict。
    支持：
      - URL 字符串（http/https）→ 直接引用
      - 文件路径 → 读取并 base64 编码
      - bytes → base64 编码
      - base64 字符串 → 直接使用
    """
    if isinstance(image, str):
        if image.startswith(("http://", "https://")):
            return {"type": "image_url", "image_url": {"url": image}}
        if os.path.isfile(image):
            with open(image, "rb") as f:
                data = f.read()
            ext = os.path.splitext(image)[1].lower()
            mime = {".png": "image/png", ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg", ".webp": "image/webp",
                    ".gif": "image/gif"}.get(ext, "image/png")
            b64 = base64.b64encode(data).decode("ascii")
            return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        # 假定是 base64 字符串
        return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}}

    if isinstance(image, (bytes, bytearray)):
        b64 = base64.b64encode(image).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}

    raise ValueError(f"不支持的 image 类型: {type(image)}")


# ============ API 调用 ============

def _call_api(messages, model=None, temperature=0.7, max_tokens=4096,
              response_format=None):
    """
    底层 API 调用，使用 urllib（无额外依赖）。
    返回 assistant 消息文本。
    """
    api_key, api_base, default_model = _load_credential()

    if not api_key:
        raise RuntimeError("LLM API Key 未配置。请在 backend/llm/credential.py 中填入 OPENAI_API_KEY")

    url = f"{api_base}/chat/completions"
    model = model or default_model

    # gpt-5 / o 系列模型使用 max_completion_tokens 且不支持自定义 temperature
    _new_param_models = ('gpt-5', 'o1', 'o3', 'o4')
    use_new = any(model.startswith(p) for p in _new_param_models)
    body = {
        "model": model,
        "messages": messages,
        "max_completion_tokens" if use_new else "max_tokens": max_tokens,
    }
    if not use_new:
        body["temperature"] = temperature
    if response_format:
        body["response_format"] = response_format

    data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"LLM API 错误 {e.code}: {body_text[:500]}") from e

    choices = result.get("choices", [])
    if not choices:
        raise RuntimeError(f"LLM API 返回无 choices: {json.dumps(result, ensure_ascii=False)[:300]}")

    return choices[0]["message"]["content"]


# ============ 公共接口 ============

def chat(messages, model=None, temperature=0.7, max_tokens=4096,
         response_format=None):
    """
    纯文本对话。

    Args:
        messages: OpenAI 格式的消息列表
        model: 模型名称，默认使用 credential.py 中配置
        temperature: 采样温度
        max_tokens: 最大生成 token 数
        response_format: 可选，如 {"type": "json_object"} 强制 JSON 输出

    Returns:
        str: assistant 回复文本
    """
    return _call_api(messages, model=model, temperature=temperature,
                     max_tokens=max_tokens, response_format=response_format)


def chat_with_image(messages, image, user_text="请分析这张图片",
                    model=None, temperature=0.7, max_tokens=4096,
                    response_format=None):
    """
    带图像的对话（Vision）。

    图像会作为最后一条 user 消息的 content 追加。

    Args:
        messages: 基础消息列表（通常只含 system message）
        image: 图像源 — URL / 文件路径 / bytes / base64 字符串
        user_text: 与图像一起发送的文本提示
        model: 模型名称
        temperature: 采样温度
        max_tokens: 最大生成 token 数
        response_format: 可选，如 {"type": "json_object"} 强制 JSON 输出

    Returns:
        str: assistant 回复文本
    """
    image_part = _prepare_image(image)
    vision_message = {
        "role": "user",
        "content": [
            {"type": "text", "text": user_text},
            image_part,
        ],
    }

    full_messages = list(messages) + [vision_message]

    return _call_api(full_messages, model=model, temperature=temperature,
                     max_tokens=max_tokens, response_format=response_format)


def chat_json(messages, model=None, temperature=0.3, max_tokens=4096):
    """
    强制 JSON 输出的对话（适用于结构化分析结果）。
    注意：system 或 user 消息中必须包含 "json" 关键字，否则 API 会报错。
    """
    return chat(messages, model=model, temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"})


def chat_with_image_json(messages, image, user_text="请分析这张图片",
                         model=None, temperature=0.3, max_tokens=4096):
    """
    带图像 + 强制 JSON 输出的对话。
    """
    return chat_with_image(messages, image, user_text=user_text,
                           model=model, temperature=temperature,
                           max_tokens=max_tokens,
                           response_format={"type": "json_object"})
