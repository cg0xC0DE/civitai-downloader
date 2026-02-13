# -*- coding: utf-8 -*-
"""
图片反推 danbooru 标签 —— Gemini 优先，GPT fallback
"""

import json
import base64
import urllib.request

_REVERSE_TAG_PROMPT = """You are an expert danbooru/booru tag annotator for anime and 2.5D AI-generated images.
Given an image, output a comprehensive, comma-separated list of danbooru-style tags.

Include tags for:
- character count, gender
- hair (color, style, length)
- eyes (color, shape)
- expression, emotion
- body features
- clothing, accessories
- pose, actions
- background, setting
- lighting, atmosphere
- camera angle
- art style, quality

Rules:
- Use standard danbooru tag format: lowercase, underscores, no spaces in tags
- Order: most important/prominent features first
- Do NOT include model names, LoRA names, or any AI-generation-specific tokens
- Output ONLY the comma-separated tags, nothing else"""


def _load_image_b64(image_source):
    """将图片来源统一转为 base64 字符串"""
    if isinstance(image_source, bytes):
        return base64.b64encode(image_source).decode()
    elif isinstance(image_source, str) and image_source.startswith(('http://', 'https://')):
        req = urllib.request.Request(image_source, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return base64.b64encode(resp.read()).decode()
    else:
        with open(image_source, 'rb') as f:
            return base64.b64encode(f.read()).decode()


def _reverse_via_gemini(img_b64, max_tokens=500):
    """Gemini 反推（速度快、标签全，但 NSFW 可能被拦）"""
    from .credential import GEMINI_API_KEY, GEMINI_MODEL

    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini API Key 未配置")

    model = GEMINI_MODEL or 'gemini-2.5-flash'
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}'

    body = {
        'contents': [{'parts': [
            {'text': _REVERSE_TAG_PROMPT},
            {'inline_data': {'mime_type': 'image/jpeg', 'data': img_b64}}
        ]}],
        'generationConfig': {'maxOutputTokens': max_tokens, 'temperature': 0.3}
    }

    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data,
                                headers={'Content-Type': 'application/json'}, method='POST')

    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode('utf-8'))

    candidates = result.get('candidates', [])
    if not candidates:
        raise RuntimeError("Gemini 无候选结果")

    parts = candidates[0].get('content', {}).get('parts', [])
    text = ''.join(p.get('text', '') for p in parts).strip()
    if not text:
        raise RuntimeError("Gemini 返回空内容（安全策略拦截）")

    usage = result.get('usageMetadata', {})
    print(f"[ReverseTag] Gemini 完成: {len(text.split(','))} 标签, "
          f"in={usage.get('promptTokenCount', 0)}, out={usage.get('candidatesTokenCount', 0)}")
    return text, 'gemini'


def _reverse_via_gpt(img_b64, max_tokens=500):
    """GPT fallback（NSFW 不拦，风格标签更准）"""
    from .llm_client import _load_credential

    api_key, api_base, _ = _load_credential()
    _new_param_models = ('gpt-5', 'o1', 'o3', 'o4')

    # 使用 credential 中配置的模型
    from .credential import OPENAI_MODEL
    model = OPENAI_MODEL or 'gpt-5.2'
    use_new = any(model.startswith(p) for p in _new_param_models)

    messages = [
        {'role': 'system', 'content': _REVERSE_TAG_PROMPT},
        {'role': 'user', 'content': [
            {'type': 'text', 'text': 'Generate comprehensive danbooru-style tags for this image.'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + img_b64}}
        ]}
    ]

    body = {'model': model, 'messages': messages}
    if use_new:
        body['max_completion_tokens'] = max_tokens
    else:
        body['max_tokens'] = max_tokens
        body['temperature'] = 0.3

    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(api_base + '/chat/completions', data=data,
                                headers={'Content-Type': 'application/json',
                                         'Authorization': 'Bearer ' + api_key},
                                method='POST')

    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode('utf-8'))

    content = result['choices'][0]['message']['content'].strip()
    usage = result.get('usage', {})
    print(f"[ReverseTag] GPT 完成: {len(content.split(','))} 标签, "
          f"in={usage.get('prompt_tokens', 0)}, out={usage.get('completion_tokens', 0)}")
    return content, 'gpt'


def reverse_tag_image(image_source, max_tokens=500):
    """
    图片 danbooru 标签反推。Gemini 优先，被安全策略拦截时自动 fallback 到 GPT。

    Args:
        image_source: 图片来源 — URL / 文件路径 / bytes
        max_tokens: 最大输出 token 数

    Returns:
        tuple: (tags_str, provider) — 逗号分隔标签, 使用的模型 ('gemini'/'gpt')
    """
    img_b64 = _load_image_b64(image_source)

    # 1. 优先 Gemini
    try:
        return _reverse_via_gemini(img_b64, max_tokens)
    except Exception as e:
        print(f"[ReverseTag] Gemini 失败: {e}，切换到 GPT...")

    # 2. Fallback GPT
    return _reverse_via_gpt(img_b64, max_tokens)
