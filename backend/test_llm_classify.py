# -*- coding: utf-8 -*-
"""
LLM 模型子分类准确性测试

从各 LoRA / Checkpoint 子分类中各选 1-2 个模型，
分别调用 DeepSeek / MiniMax / OpenAI / Gemini 进行分类判断，
对比准确率并估算成本。

用法: python test_llm_classify.py
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
import traceback

# ============ 测试用例 ============
# ground_truth_subtype 是模型在本地仓库中的实际存放子目录
TEST_CASES = [
    # --- LoRA: xl-style ---
    {"model_id": "929497", "type": "LORA", "ground_truth": "xl-style",
     "name": "Aesthetic Quality Modifiers - Masterpiece", "baseModel": "Pony",
     "tags": ["style", "aesthetic", "quality", "pony", "illustrious"],
     "description": "Improves the overall aesthetic quality of generated images."},
    {"model_id": "1197562", "type": "LORA", "ground_truth": "xl-style",
     "name": "0__11Xx | Shiiro's Styles | Niji", "baseModel": "Illustrious",
     "tags": ["style", "anime", "niji", "illustrious"],
     "description": "Anime art style LoRA for Illustrious."},

    # --- LoRA: xl-character ---
    {"model_id": "666554", "type": "LORA", "ground_truth": "xl-character",
     "name": "Black Myth: Wukong Character LoRA", "baseModel": "Pony",
     "tags": ["character", "game character", "wukong", "pony"],
     "description": "Character LoRA for Black Myth Wukong game characters."},
    {"model_id": "279195", "type": "LORA", "ground_truth": "xl-character",
     "name": "Hinata Hyuga fan costumes", "baseModel": "Pony",
     "tags": ["character", "naruto", "hinata", "anime character"],
     "description": "Hinata Hyuga character LoRA from Naruto series."},

    # --- LoRA: xl-background ---
    {"model_id": "245668", "type": "LORA", "ground_truth": "xl-background",
     "name": "0046 dirty alley (pony and 1.5)", "baseModel": "Pony",
     "tags": ["background", "scenery", "alley", "environment"],
     "description": "Background/environment LoRA for generating dirty alley scenes."},

    # --- LoRA: xl-enhance ---
    {"model_id": "1377820", "type": "LORA", "ground_truth": "xl-enhance",
     "name": "Add Micro Details - Concept (Illustrious | Pony | NoobAI)", "baseModel": "Illustrious",
     "tags": ["enhance", "detail", "quality", "concept"],
     "description": "Adds micro-level details to improve image quality and realism."},

    # --- LoRA: xl-face ---
    {"model_id": "420063", "type": "LORA", "ground_truth": "xl-face",
     "name": "=w= (Frieren Elf Expression) | Concept Lora", "baseModel": "Pony",
     "tags": ["face", "expression", "concept", "frieren"],
     "description": "Facial expression concept LoRA, specifically the =w= elf expression."},
    {"model_id": "1507836", "type": "LORA", "ground_truth": "xl-face",
     "name": "Asian girl make-up", "baseModel": "Illustrious",
     "tags": ["face", "makeup", "asian", "beauty"],
     "description": "Realistic Asian girl makeup styles for face generation."},

    # --- LoRA: xl-pose ---
    {"model_id": "1107767", "type": "LORA", "ground_truth": "xl-pose",
     "name": "(O.D.O.R.) - feet_anime_illustrious", "baseModel": "Illustrious",
     "tags": ["pose", "feet", "anime", "body"],
     "description": "Pose/body part LoRA for specific foot poses in anime style."},

    # --- LoRA: xl-slider ---
    {"model_id": "438059", "type": "LORA", "ground_truth": "xl-slider",
     "name": "Dynamic Poses slider PONYXL", "baseModel": "Pony",
     "tags": ["slider", "pose", "dynamic", "concept"],
     "description": "Slider LoRA that adjusts the dynamism of poses on a scale."},
    {"model_id": "279487", "type": "LORA", "ground_truth": "xl-slider",
     "name": "[SDXL/SD1.5] Torogao_Ahegao_Slider_LoRA", "baseModel": "SDXL 1.0",
     "tags": ["slider", "expression", "concept"],
     "description": "Slider LoRA for controlling facial expression intensity."},

    # --- LoRA: xl-suit ---
    {"model_id": "443650", "type": "LORA", "ground_truth": "xl-suit",
     "name": "0469 Stellar Blade (Skin Suit)", "baseModel": "Pony",
     "tags": ["clothing", "outfit", "bodysuit", "game"],
     "description": "Clothing/outfit LoRA for Stellar Blade character skin suits."},
    {"model_id": "424525", "type": "LORA", "ground_truth": "xl-suit",
     "name": "Back Zipper", "baseModel": "Pony",
     "tags": ["clothing", "zipper", "outfit"],
     "description": "Clothing detail LoRA for back zipper outfits."},

    # --- LoRA: xl-nsfw ---
    {"model_id": "110337", "type": "LORA", "ground_truth": "xl-nsfw",
     "name": "Sex Machine", "baseModel": "Pony",
     "tags": ["nsfw", "adult", "concept"],
     "description": "NSFW concept LoRA."},

    # --- LoRA: 1.5 ---
    {"model_id": "108649", "type": "LORA", "ground_truth": "1.5",
     "name": "Genshin Impact All In One Character Lora", "baseModel": "SD 1.5",
     "tags": ["character", "genshin impact", "anime", "game"],
     "description": "All-in-one character LoRA for Genshin Impact on SD 1.5."},

    # --- LoRA: 1.5-nsfw ---
    {"model_id": "329702", "type": "LORA", "ground_truth": "1.5-nsfw",
     "name": "After Sex | Broken | Used Condom", "baseModel": "SD 1.5",
     "tags": ["nsfw", "adult", "concept"],
     "description": "NSFW concept LoRA for SD 1.5."},
]

# ============ 子分类定义（提供给 LLM）============
SUBTYPE_DEFINITIONS = """
Available subtypes for model classification:

## Checkpoint subtypes:
- "1.5" — SD 1.5 base models
- "xl" — SDXL, Pony, Illustrious, NoobAI and other XL-based models
- "flux" — Flux.1 series models
- "wan" — Wan Video models

## LoRA subtypes:
- "xl-style" — Art style, aesthetic, quality improvement LoRAs (e.g. anime style, painting style, aesthetic modifiers)
- "xl-character" — Specific character LoRAs (anime characters, game characters, real persons)
- "xl-background" — Background, scenery, environment LoRAs
- "xl-enhance" — Detail enhancement, quality boost, micro-detail LoRAs
- "xl-face" — Face-related: expressions, makeup, face features
- "xl-pose" — Body poses, specific body parts, action LoRAs
- "xl-slider" — Slider/scale LoRAs that adjust a property on a continuous scale
- "xl-suit" — Clothing, outfits, costumes, accessories
- "xl-nsfw" — NSFW/adult content LoRAs (for XL-based models)
- "1.5" — Any LoRA for SD 1.5 base (non-NSFW)
- "1.5-nsfw" — NSFW LoRA for SD 1.5

## Prefix rules:
- "xl-" prefix: for models with baseModel = SDXL, Pony, Illustrious, NoobAI, or similar XL architectures
- "1.5" prefix: for models with baseModel = SD 1.5
- "flux-" prefix: for models with baseModel = Flux.1 series
- If baseModel indicates SD 1.5 and model is NSFW → "1.5-nsfw"; otherwise → "1.5"
- If baseModel indicates XL-family and model is NSFW → "xl-nsfw"
"""

SYSTEM_PROMPT = f"""You are a Stable Diffusion model classifier. Given a model's metadata, determine its subtype for local file organization.

{SUBTYPE_DEFINITIONS}

IMPORTANT classification logic:
1. First determine the base architecture prefix from baseModel field (xl- / 1.5 / flux-)
2. Then determine the category suffix from model name, tags, and description
3. "slider" LoRAs are those that explicitly control a property on a sliding scale
4. "enhance" LoRAs improve quality/detail without changing style/content
5. "style" LoRAs change the overall artistic style or aesthetic
6. "character" LoRAs represent specific named characters
7. "suit" LoRAs are for specific clothing/outfit/costume items
8. "face" LoRAs focus on facial features, expressions, or makeup
9. "pose" LoRAs control body positioning or specific body part rendering
10. "background" LoRAs generate specific environments or scenery

Respond with ONLY a JSON object: {{"subtype": "<subtype_string>"}}
If you cannot determine a single clear subtype, respond: {{"subtype": null, "candidates": ["a", "b"]}}
"""


# ============ LLM 调用封装 ============

def _call_openai_compatible(api_key, api_base, model, messages, timeout=30):
    """调用 OpenAI 兼容 API，返回 (response_text, usage_dict)"""
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    text = result["choices"][0]["message"]["content"]
    usage = result.get("usage", {})
    return text, usage


def _call_gemini(api_key, model, prompt, timeout=30):
    """调用 Gemini API，返回 (response_text, usage_dict)"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 200, "temperature": 0.1,
                             "responseMimeType": "application/json"},
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    parts = result.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    usage = result.get("usageMetadata", {})
    return text, usage


# ============ Provider 配置 ============

def _load_cred():
    """从 credential.py 读取所有已定义的变量"""
    cred = {}
    try:
        llm_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm")
        if llm_dir not in sys.path:
            sys.path.insert(0, llm_dir)
        import importlib
        import credential
        importlib.reload(credential)  # 确保最新
        for k in dir(credential):
            if k.isupper():
                cred[k] = getattr(credential, k)
    except Exception as e:
        print(f"  ⚠️ credential.py 加载失败: {e}")
    return cred


def get_providers():
    """返回可用的 LLM providers 列表"""
    cred = _load_cred()
    print(f"  credential.py 变量: {[k for k in cred if k.isupper()]}")
    providers = []

    # DeepSeek
    ds_key = os.environ.get("DEEPSEEK_API_KEY") or cred.get("DEEPSEEK_API_KEY", "")
    if ds_key:
        providers.append({
            "name": "DeepSeek",
            "type": "openai",
            "api_key": ds_key,
            "api_base": cred.get("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1"),
            "model": cred.get("DEEPSEEK_MODEL", "deepseek-chat"),
            "pricing": {"input": 0.27, "output": 1.10},  # $/M tokens
        })

    # MiniMax
    mm_key = os.environ.get("MINIMAX_API_KEY") or cred.get("MINIMAX_API_KEY", "")
    if mm_key:
        providers.append({
            "name": "MiniMax",
            "type": "openai",
            "api_key": mm_key,
            "api_base": cred.get("MINIMAX_API_BASE", "https://api.minimax.chat/v1"),
            "model": cred.get("MINIMAX_MODEL", "MiniMax-Text-01"),
            "pricing": {"input": 1.00, "output": 4.00},  # $/M tokens (estimate)
        })

    # OpenAI
    oai_key = os.environ.get("OPENAI_API_KEY") or cred.get("OPENAI_API_KEY", "")
    if oai_key:
        model = cred.get("OPENAI_MODEL") or "gpt-4o-mini"
        # 针对不同模型估算价格
        if "gpt-4o-mini" in model:
            pricing = {"input": 0.15, "output": 0.60}
        elif "gpt-4o" in model:
            pricing = {"input": 2.50, "output": 10.00}
        else:
            pricing = {"input": 1.00, "output": 3.00}  # 粗估
        providers.append({
            "name": "OpenAI",
            "type": "openai",
            "api_key": oai_key,
            "api_base": cred.get("OPENAI_API_BASE") or "https://api.openai.com/v1",
            "model": model,
            "pricing": pricing,
        })

    # Gemini
    gemini_key = os.environ.get("GEMINI_API_KEY") or cred.get("GEMINI_API_KEY", "")
    if gemini_key:
        providers.append({
            "name": "Gemini",
            "type": "gemini",
            "api_key": gemini_key,
            "model": cred.get("GEMINI_MODEL", "gemini-2.0-flash"),
            "pricing": {"input": 0.075, "output": 0.30},  # $/M tokens
        })

    return providers


def classify_one(provider, test_case):
    """用指定 provider 分类一个测试用例，返回 (predicted_subtype, usage, latency_ms)"""
    user_msg = json.dumps({
        "model_type": test_case["type"],
        "model_name": test_case["name"],
        "baseModel": test_case["baseModel"],
        "tags": test_case["tags"],
        "description": test_case["description"],
    }, ensure_ascii=False)

    t0 = time.time()
    try:
        if provider["type"] == "openai":
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Classify this model (respond in json):\n{user_msg}"},
            ]
            text, usage = _call_openai_compatible(
                provider["api_key"], provider["api_base"], provider["model"], messages
            )
        elif provider["type"] == "gemini":
            prompt = f"{SYSTEM_PROMPT}\n\nClassify this model (respond in json):\n{user_msg}"
            text, usage = _call_gemini(provider["api_key"], provider["model"], prompt)
        else:
            return None, {}, 0

        latency = int((time.time() - t0) * 1000)

        # Parse response
        try:
            resp = json.loads(text.strip())
            predicted = resp.get("subtype")
        except json.JSONDecodeError:
            predicted = f"PARSE_ERROR: {text[:80]}"

        return predicted, usage, latency

    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        return f"ERROR: {e}", {}, latency


def estimate_cost(usage, pricing, provider_type):
    """估算单次调用成本 (USD)"""
    if provider_type == "gemini":
        inp = usage.get("promptTokenCount", 0)
        out = usage.get("candidatesTokenCount", 0)
    else:
        inp = usage.get("prompt_tokens", 0)
        out = usage.get("completion_tokens", 0)
    cost = (inp * pricing["input"] + out * pricing["output"]) / 1_000_000
    return cost, inp, out


# ============ 主测试 ============

def main():
    providers = get_providers()
    if not providers:
        print("❌ 无可用 LLM provider！请设置以下环境变量：")
        print("  DEEPSEEK_API_KEY, MINIMAX_API_KEY, GEMINI_API_KEY")
        print("  或在 llm/credential.py 中配置 OPENAI_API_KEY")
        return

    print(f"📋 测试 {len(TEST_CASES)} 个模型 × {len(providers)} 个 LLM")
    print(f"🤖 Providers: {', '.join(p['name'] + ' (' + p['model'] + ')' for p in providers)}")
    print("=" * 90)

    results = {p["name"]: {"correct": 0, "wrong": 0, "error": 0, "total_cost": 0.0,
                            "total_input": 0, "total_output": 0, "details": []}
               for p in providers}

    for i, tc in enumerate(TEST_CASES):
        print(f"\n[{i+1}/{len(TEST_CASES)}] {tc['name'][:50]}")
        print(f"  type={tc['type']} baseModel={tc['baseModel']} → 期望: {tc['ground_truth']}")

        for p in providers:
            predicted, usage, latency = classify_one(p, tc)
            cost, inp, out = estimate_cost(usage, p["pricing"], p["type"])

            r = results[p["name"]]
            r["total_cost"] += cost
            r["total_input"] += inp
            r["total_output"] += out

            if predicted and not str(predicted).startswith("ERROR") and not str(predicted).startswith("PARSE"):
                match = (predicted == tc["ground_truth"])
                icon = "✅" if match else "❌"
                if match:
                    r["correct"] += 1
                else:
                    r["wrong"] += 1
                r["details"].append({
                    "model": tc["name"][:40], "expected": tc["ground_truth"],
                    "predicted": predicted, "match": match,
                })
            else:
                icon = "⚠️"
                r["error"] += 1
                r["details"].append({
                    "model": tc["name"][:40], "expected": tc["ground_truth"],
                    "predicted": str(predicted), "match": False,
                })

            print(f"  {icon} {p['name']:12s}: {str(predicted):20s}  ({latency}ms, ${cost:.6f})")

            time.sleep(0.3)  # rate limit courtesy

    # ============ 汇总 ============
    print("\n" + "=" * 90)
    print("📊 汇总结果")
    print("=" * 90)

    for p in providers:
        r = results[p["name"]]
        total = r["correct"] + r["wrong"] + r["error"]
        acc = r["correct"] / max(total - r["error"], 1) * 100
        print(f"\n🤖 {p['name']} ({p['model']})")
        print(f"   准确率: {r['correct']}/{total - r['error']} = {acc:.1f}%")
        print(f"   错误数: {r['error']}")
        print(f"   本次总成本: ${r['total_cost']:.6f}")
        print(f"   Token 用量: input={r['total_input']}, output={r['total_output']}")

        # 估算大规模使用成本
        avg_cost = r["total_cost"] / max(total, 1)
        print(f"   单次平均: ${avg_cost:.6f}")
        print(f"   预估 100 次: ${avg_cost * 100:.4f}")
        print(f"   预估 1000 次: ${avg_cost * 1000:.4f}")

        # 打印错误详情
        wrongs = [d for d in r["details"] if not d["match"]]
        if wrongs:
            print(f"   ❌ 错误分类:")
            for w in wrongs:
                print(f"      {w['model']}: 期望 {w['expected']}, 得到 {w['predicted']}")

    # 输出 JSON 报告
    report_path = os.path.join(os.path.dirname(__file__), "cache", "classify_test_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n📄 详细报告已保存: {report_path}")


if __name__ == "__main__":
    main()
