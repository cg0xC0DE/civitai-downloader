# -*- coding: utf-8 -*-
"""
SubtypeClassifier — 使用 LLM 判断模型子分类（本地存放路径）

优先级: Gemini → DeepSeek → 返回 None（跳过）
"""

import json
import os
import sys
import urllib.request
import urllib.error

# ============ 子分类定义 ============

CHECKPOINT_SUBTYPES = ["1.5", "xl", "flux", "wan"]
LORA_SUBTYPES = [
    "xl-style", "xl-character", "xl-background", "xl-enhance",
    "xl-face", "xl-pose", "xl-slider", "xl-suit", "xl-nsfw",
    "1.5", "1.5-nsfw",
]

# baseModel → 架构前缀映射（确定性规则，不需要 LLM）
_BASE_MODEL_PREFIX = {
    "sd 1.5": "1.5", "sd1.5": "1.5", "sd 1.4": "1.5",
    "sdxl": "xl", "sdxl 1.0": "xl", "pony": "xl", "illustrious": "xl",
    "noobai": "xl", "animagine": "xl",
    "flux": "flux", "flux.1 s": "flux", "flux.1 d": "flux", "flux.1 dev": "flux",
    "wan": "wan",
}

SYSTEM_PROMPT = """You are a Stable Diffusion model classifier for local file organization.

Available LoRA subtypes (for XL-family: SDXL, Pony, Illustrious, NoobAI):
- "xl-style" — Art style, aesthetic, quality improvement
- "xl-character" — Specific character (anime, game, real person)
- "xl-background" — Background, scenery, environment
- "xl-enhance" — Detail enhancement, quality boost
- "xl-face" — Face features, expressions, makeup
- "xl-pose" — Body poses, specific body parts, actions
- "xl-slider" — Slider LoRAs that adjust a property on a continuous scale
- "xl-suit" — Clothing, outfits, costumes, accessories
- "xl-nsfw" — NSFW/adult content

Available LoRA subtypes (for SD 1.5):
- "1.5" — General (non-NSFW)
- "1.5-nsfw" — NSFW/adult content

Available Checkpoint subtypes:
- "1.5", "xl", "flux", "wan"

Rules:
1. Determine architecture prefix from baseModel (xl- / 1.5 / flux-)
2. For LoRA, determine category from name, tags, description
3. "slider" = explicitly controls a property on a sliding scale
4. "enhance" = improves quality/detail without changing style/content
5. "style" = changes overall artistic style or aesthetic
6. "character" = specific named characters
7. "suit" = specific clothing/outfit/costume
8. "face" = facial features, expressions, makeup
9. "pose" = body positioning or body part rendering
10. "background" = specific environments or scenery

Respond ONLY: {"subtype": "<value>"}
If uncertain, respond: {"subtype": null, "reason": "..."}"""


def _load_credentials():
    """加载 LLM credentials"""
    cred = {}
    try:
        llm_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm")
        if llm_dir not in sys.path:
            sys.path.insert(0, llm_dir)
        import importlib
        import credential
        importlib.reload(credential)
        for k in dir(credential):
            if k.isupper():
                cred[k] = getattr(credential, k)
    except Exception:
        pass
    return cred


def _call_openai_compatible(api_key, api_base, model, messages, timeout=30):
    """调用 OpenAI 兼容 API"""
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 100,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]


def _call_gemini(api_key, model, prompt, timeout=30):
    """调用 Gemini API"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 100,
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    parts = result.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts).strip()


def _get_prefix_from_base_model(base_model: str) -> str:
    """从 baseModel 字段确定性地推导架构前缀"""
    if not base_model:
        return ""
    bm = base_model.lower().strip()
    for key, prefix in _BASE_MODEL_PREFIX.items():
        if key in bm:
            return prefix
    # 通用 fallback
    if "xl" in bm or "pony" in bm or "illustrious" in bm:
        return "xl"
    if "1.5" in bm or "1.4" in bm:
        return "1.5"
    if "flux" in bm:
        return "flux"
    return ""


def classify_checkpoint(base_model: str) -> str:
    """
    Checkpoint 分类 — 纯规则，不需要 LLM。
    返回子目录名，如 "xl", "1.5", "flux"
    """
    prefix = _get_prefix_from_base_model(base_model)
    if prefix in CHECKPOINT_SUBTYPES:
        return prefix
    return "xl"  # 默认 xl


def classify_lora(name: str, base_model: str, tags: list = None,
                  description: str = "", model_type: str = "LORA") -> str | None:
    """
    LoRA 分类 — 先用规则确定前缀，再用 LLM 判断类别后缀。

    返回:
        子目录名（如 "xl-character"）或 None（LLM 不确定时）
    """
    prefix = _get_prefix_from_base_model(base_model)

    # SD 1.5 的 LoRA 只有 "1.5" / "1.5-nsfw" 两种
    if prefix == "1.5":
        # 简单规则判断 NSFW
        _nsfw_kw = ["nsfw", "sex", "nude", "hentai", "porn", "erotic", "xxx"]
        _text = (name + " " + description + " " + " ".join(tags or [])).lower()
        if any(kw in _text for kw in _nsfw_kw):
            return "1.5-nsfw"
        return "1.5"

    # flux / wan 的 LoRA → 暂时不细分，直接返回 None（需要人工）
    if prefix in ("flux", "wan"):
        return None

    # XL 系需要 LLM 判断子分类
    if not prefix:
        prefix = "xl"  # 未知 baseModel 默认归入 xl

    user_msg = json.dumps({
        "model_type": model_type,
        "model_name": name,
        "baseModel": base_model,
        "tags": tags or [],
        "description": description[:200],
    }, ensure_ascii=False)

    cred = _load_credentials()

    # 尝试顺序：Gemini → DeepSeek
    providers = []

    gemini_key = os.environ.get("GEMINI_API_KEY") or cred.get("GEMINI_API_KEY", "")
    if gemini_key:
        providers.append(("Gemini", "gemini", gemini_key,
                          cred.get("GEMINI_MODEL", "gemini-2.0-flash")))

    ds_key = os.environ.get("DEEPSEEK_API_KEY") or cred.get("DEEPSEEK_API_KEY", "")
    if ds_key:
        providers.append(("DeepSeek", "openai", ds_key,
                          cred.get("DEEPSEEK_MODEL", "deepseek-chat")))

    # OpenAI 作为最后兜底
    oai_key = os.environ.get("OPENAI_API_KEY") or cred.get("OPENAI_API_KEY", "")
    if oai_key:
        oai_base = cred.get("OPENAI_API_BASE") or "https://api.openai.com/v1"
        providers.append(("OpenAI", "openai_full", oai_key,
                          cred.get("OPENAI_MODEL") or "gpt-4o-mini"))

    for pname, ptype, pkey, pmodel in providers:
        try:
            if ptype == "gemini":
                prompt = f"{SYSTEM_PROMPT}\n\nClassify this model:\n{user_msg}"
                text = _call_gemini(pkey, pmodel, prompt)
            elif ptype == "openai":
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Classify this model:\n{user_msg}"},
                ]
                ds_base = cred.get("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
                text = _call_openai_compatible(pkey, ds_base, pmodel, messages)
            elif ptype == "openai_full":
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Classify this model:\n{user_msg}"},
                ]
                text = _call_openai_compatible(pkey, oai_base, pmodel, messages)
            else:
                continue

            resp = json.loads(text.strip())
            subtype = resp.get("subtype")

            if subtype and subtype in LORA_SUBTYPES:
                print(f"[Classifier] {pname} → {subtype} (model: {name[:40]})")
                return subtype
            else:
                reason = resp.get("reason", "unknown subtype")
                print(f"[Classifier] {pname} 不确定: {reason} (model: {name[:40]})")
                # 继续尝试下一个 provider
                continue

        except Exception as e:
            print(f"[Classifier] {pname} 调用失败: {e}")
            continue

    # 所有 provider 都失败或不确定
    print(f"[Classifier] ⚠️ 无法确定子分类: {name[:40]}")
    return None


def get_type_subtype(model_type: str, base_model: str, name: str = "",
                     tags: list = None, description: str = "") -> str | None:
    """
    统一入口：根据模型类型返回 "大类.子类" 格式（如 "lora.xl-character", "ckpt.xl"）

    返回 None 表示无法确定，应跳过。
    """
    mt = model_type.lower()

    if mt in ("checkpoint", "ckpt"):
        sub = classify_checkpoint(base_model)
        return f"ckpt.{sub}" if sub else None

    elif mt in ("lora", "locon"):
        sub = classify_lora(name, base_model, tags, description, model_type)
        return f"lora.{sub}" if sub else None

    elif mt in ("textualinversion", "embedding"):
        return "embedding._root"  # embedding 统一放根目录

    return None
