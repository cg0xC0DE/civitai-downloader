# -*- coding: utf-8 -*-
"""
美学分析管线 — 从 AI 绘画作品中提取结构化美学蓝图

流程：
  1. 读取图片 PNG 元数据（ComfyUI workflow）→ 提取生成参数
  2. 从 model_index 收集所有模型的触发词
  3. 组装 LLM prompt（图片 + 参数 + 触发词 + 用户主观描述）
  4. 调用 GPT-5.2 Vision → 返回结构化 JSON
  5. 保存到 aesthetic_blueprints.json

用法：
  from llm.aesthetic import analyze
  result = analyze(image_source="https://xxx/image.png", user_why_good="色彩对比很强")
"""

import os
import sys
import json
import re
import struct
import zlib
import urllib.request

_BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from llm.llm_client import chat_with_image_json
from util.azure_utils import _azure_available
_LOCAL_PATH = os.path.join(_BACKEND_DIR, 'cache', 'aesthetic_blueprints.json')
_BLOB_CONTAINER = 'civitaidl'
_BLOB_SUBFOLDER = 'data'
_BLOB_FILENAME = 'aesthetic_blueprints.json'
_AESTHETIC_MODEL = 'gpt-5.2'
_NSFW_MASK_TOKEN = 'XXXXXX'

_NSFW_HINT_WORDS = frozenset({
    'nsfw', 'adult', 'explicit', 'erotic', 'sexual', 'fetish',
    'hentai', 'ecchi', 'ero', 'lewd',
    'nudity', 'nude', 'naked', 'topless', 'bottomless',
    'underwear', 'panty', 'panties', 'thong',
    'crotch', 'cameltoe',
    'breast', 'breasts', 'boob', 'boobs', 'nipple', 'nipples', 'areola',
    'vagina', 'genital', 'pussy', 'pubic',
    '露出', '下体', '羞耻', '羞辱', '羞耻play', '淫', '内裤', '裸', '全裸', 'エロ'
})


def _get_blob():
    from azure_blob import BlobStorage
    return BlobStorage(container=_BLOB_CONTAINER)


# ============================================================
# 第 1 步：读取 PNG 元数据
# ============================================================

def _read_png_text_chunks(data: bytes) -> dict:
    """
    从 PNG 二进制数据中提取所有 tEXt / iTXt 文本块。
    ComfyUI 在 tEXt 块中存储 "prompt"（API 格式）和 "workflow"（UI 格式）。
    返回 { key: value } dict。
    """
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        return {}

    result = {}
    offset = 8

    while offset < len(data):
        if offset + 8 > len(data):
            break
        length = struct.unpack('>I', data[offset:offset+4])[0]
        chunk_type = data[offset+4:offset+8]

        chunk_data = data[offset+8:offset+8+length]

        if chunk_type == b'tEXt':
            # tEXt: keyword\0text
            sep = chunk_data.find(b'\x00')
            if sep >= 0:
                key = chunk_data[:sep].decode('latin-1')
                value = chunk_data[sep+1:].decode('utf-8', errors='replace')
                result[key] = value

        elif chunk_type == b'iTXt':
            # iTXt: keyword\0compression_flag\0compression_method\0language\0translated\0text
            sep = chunk_data.find(b'\x00')
            if sep >= 0:
                key = chunk_data[:sep].decode('latin-1')
                rest = chunk_data[sep+1:]
                if len(rest) >= 2:
                    comp_flag = rest[0]
                    # Skip compression_method, language, translated_keyword
                    rest = rest[2:]  # skip comp_flag + comp_method
                    # language tag
                    sep2 = rest.find(b'\x00')
                    if sep2 >= 0:
                        rest = rest[sep2+1:]
                        # translated keyword
                        sep3 = rest.find(b'\x00')
                        if sep3 >= 0:
                            text_data = rest[sep3+1:]
                            if comp_flag:
                                try:
                                    text_data = zlib.decompress(text_data)
                                except Exception:
                                    pass
                            result[key] = text_data.decode('utf-8', errors='replace')

        elif chunk_type == b'IEND':
            break

        # 跳到下一个 chunk（length + 4字节CRC）
        offset += 8 + length + 4

    return result


def read_image_metadata(image_source) -> dict:
    """
    从图片中读取 ComfyUI 元数据。
    image_source: 文件路径 / URL / bytes

    返回 { 'prompt': dict, 'workflow': dict } 或空 dict。
    """
    if isinstance(image_source, (bytes, bytearray)):
        data = image_source
    elif isinstance(image_source, str):
        if image_source.startswith(('http://', 'https://')):
            req = urllib.request.Request(image_source, headers={
                'User-Agent': 'Mozilla/5.0'
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
        elif os.path.isfile(image_source):
            with open(image_source, 'rb') as f:
                data = f.read()
        else:
            return {}
    else:
        return {}

    chunks = _read_png_text_chunks(data)
    result = {}

    for key in ('prompt', 'workflow'):
        raw = chunks.get(key, '')
        if raw:
            try:
                result[key] = json.loads(raw)
            except json.JSONDecodeError:
                result[key] = raw

    return result


# ============================================================
# 第 2 步：从 ComfyUI prompt JSON 提取生成参数
# ============================================================

def parse_gen_params(prompt_data: dict) -> dict:
    """
    从 ComfyUI API 格式的 prompt JSON 中提取关键生成参数。
    返回：
    {
        'checkpoint': str,
        'loras': [ { 'name': str, 'weight': float }, ... ],
        'positive_prompt': str,
        'negative_prompt': str,
        'sampler': str,
        'steps': int,
        'cfg': float,
        'width': int,
        'height': int,
        'seed': int,
    }
    """
    if not isinstance(prompt_data, dict):
        return {}

    result = {
        'checkpoint': '',
        'loras': [],
        'positive_prompt': '',
        'negative_prompt': '',
        'sampler': '',
        'steps': 0,
        'cfg': 0.0,
        'width': 0,
        'height': 0,
        'seed': 0,
    }

    # 辅助：按 class_type 找节点
    def find_nodes(class_type):
        return [(nid, node) for nid, node in prompt_data.items()
                if isinstance(node, dict) and node.get('class_type') == class_type]

    # Checkpoint
    for nid, node in find_nodes('CheckpointLoaderSimple'):
        result['checkpoint'] = node.get('inputs', {}).get('ckpt_name', '')
        break

    # KSampler
    positive_nid = negative_nid = None
    for nid, node in find_nodes('KSampler'):
        inputs = node.get('inputs', {})
        result['sampler'] = inputs.get('sampler_name', '')
        result['steps'] = inputs.get('steps', 0)
        result['cfg'] = inputs.get('cfg', 0.0)
        result['seed'] = inputs.get('seed', 0)
        # 追溯 positive / negative CLIPTextEncode 节点
        pos_ref = inputs.get('positive')
        neg_ref = inputs.get('negative')
        if isinstance(pos_ref, list):
            positive_nid = str(pos_ref[0])
        if isinstance(neg_ref, list):
            negative_nid = str(neg_ref[0])
        break

    # Positive / Negative prompt（从 CLIPTextEncode 获取）
    if positive_nid and positive_nid in prompt_data:
        result['positive_prompt'] = prompt_data[positive_nid].get('inputs', {}).get('text', '')
    if negative_nid and negative_nid in prompt_data:
        result['negative_prompt'] = prompt_data[negative_nid].get('inputs', {}).get('text', '')

    # EmptyLatentImage 尺寸
    for nid, node in find_nodes('EmptyLatentImage'):
        inputs = node.get('inputs', {})
        result['width'] = inputs.get('width', 0)
        result['height'] = inputs.get('height', 0)
        break

    # LoRA — 支持多种节点类型
    lora_types = [
        'LoraLoader',
        'Lora Loader Stack (rgthree)',
        'Power Lora Loader (rgthree)',
    ]
    for lora_type in lora_types:
        for nid, node in find_nodes(lora_type):
            inputs = node.get('inputs', {})

            if lora_type == 'LoraLoader':
                name = inputs.get('lora_name', '')
                weight = inputs.get('strength_model', 1.0)
                if name:
                    result['loras'].append({'name': name, 'weight': weight})

            elif 'Stack' in lora_type or 'Power' in lora_type:
                # rgthree Stack: lora_01..lora_N, strength_01..strength_N
                for i in range(1, 20):
                    key_name = f'lora_{i:02d}'
                    key_str = f'strength_{i:02d}'
                    name = inputs.get(key_name, '')
                    if not name or name == 'None':
                        continue
                    weight = inputs.get(key_str, 1.0)
                    result['loras'].append({'name': name, 'weight': weight})

    return result


# ============================================================
# 第 3 步：收集触发词
# ============================================================

def collect_trigger_words(gen_params: dict) -> dict:
    """
    从 model_index 中查找 checkpoint 和 LoRA 的触发词。
    返回 { 'model_name': [trigger_words] } 形式的 dict。

    注意：仅按文件名匹配（无 versionId），因为 PNG metadata 里没有 versionId。
    """
    from util import model_index
    model_index._ensure_loaded()

    tw_map = {}

    # 遍历索引找匹配
    all_entries = model_index._by_version

    # 收集所有要查的文件名
    names_to_find = {}
    ckpt = gen_params.get('checkpoint', '')
    if ckpt:
        names_to_find[os.path.basename(ckpt).lower()] = ckpt

    for lora in gen_params.get('loras', []):
        name = lora.get('name', '')
        if name:
            names_to_find[os.path.basename(name).lower()] = name

    # 从索引中按文件名查找
    for vid, entry in all_entries.items():
        fn = entry.get('filename', '').lower()
        if not fn:
            continue
        for search_fn, original_name in names_to_find.items():
            if fn == search_fn or fn.endswith(search_fn):
                tw = entry.get('trigger_words', [])
                if tw:
                    tw_map[original_name] = tw

    return tw_map


# ============================================================
# 第 4 步：组装 LLM Prompt
# ============================================================

_SYSTEM_PROMPT = """你是一位 AI 绘画美学分析师。你的任务是分析 AI 生成的图片，结合其生成参数和用户的主观感受，输出结构化的美学蓝图。

## 分析要求

1. **plain_text（纯净提示词）**：从原始 positive_prompt 中剥离所有模型触发词（我会提供已知触发词列表），只保留纯粹的创意描述。触发词可能跳跃穿插、不连续，请仔细识别。如果有疑似触发词但不在已知列表中（编码感强、非自然语言的 token），也请尝试剥离并在 notes 中说明。

2. **why_good（为什么好看）**：结合你对图片的视觉分析（构图、光影、色彩、氛围、叙事性）和用户提供的主观感受，融合形成一段中文美学分析。重点关注：
   - 色彩对比与和谐
   - 光影的情绪表达
   - 构图与视觉动线
   - 角色姿态与心理暗示
   - 材质感与渲染风格
   - 整体氛围与叙事张力

3. **output_prompt_plain（优化后的纯净提示词）**：基于 plain_text，适当优化和补充描述，使其更加精准和完整，但不加入任何模型特定的触发词。

4. **output_prompt_with_model（完整重建提示词）**：在 output_prompt_plain 基础上，附加模型、LoRA、Sampler 等完整参数信息，格式参考示例。

5. **LoRA 影响识别与反推补偿词（重点）**：
   - 很多风格、角色、服饰、动作、姿态、背景、环境、材质效果，可能主要来自 LoRA，而非通用提示词本身。
   - 你必须判断每个显著视觉特征是否“强依赖 LoRA”。
   - 若强依赖 LoRA，请在 `lora_inferred_additional_prompts` 中返回一组“跨平台可迁移”的附加提示词（英文短语优先），用于在缺失该 LoRA 时尽量复现效果。
   - 这些附加词应是语义化描述，而不是简单重复 LoRA 文件名；例如 LoRA 名包含 DavinciStyle，应反推出如：renaissance oil painting, da vinci style sfumato, warm chiaroscuro lighting 等。
   - 若某个特征并非 LoRA 主导，不要硬加。

6. **其他字段**：work_title（4-8个中文字的诗意标题）、base_combo（模型组合简称）、image_type（anime/2.5d/realistic/concept 等）、tags（5-8个英文标签）、vibe（一句中文氛围描述）。

7. **NSFW 提示词遮罩规则（必须执行）**：
   - 如果你输出的提示词（plain_text / output_prompt_plain / output_prompt_with_model）包含 NSFW 或敏感性行为描述，不要直接输出高危词本体。
   - 必须对相关词语做“局部遮罩”：把词语改为 `XXXXXX词语XXXXXX`（词语前后都加六个 X）。
   - 只遮罩敏感词本身，不要给整段文本统一加前缀。
   - 非 NSFW 内容不要遮罩。

## 输出格式
请严格输出 JSON 对象，包含以下字段：
work_title, plain_text, why_good, output_prompt_plain, output_prompt_with_model, lora_inferred_additional_prompts, lora_dependency_notes, base_combo, image_type, tags, vibe

其中：
- lora_inferred_additional_prompts: string[]，长度 0~12，每项为可直接拼接到提示词中的短语。
- lora_dependency_notes: string，简要说明哪些效果可能主要来自 LoRA、你如何反推。

不要输出 JSON 以外的任何内容。"""


def _contains_nsfw_signal(text: str) -> bool:
    s = str(text or '').strip().lower()
    if not s:
        return False
    return any(k in s for k in _NSFW_HINT_WORDS)


def _needs_nsfw_mask(blueprint: dict, gen_params: dict, user_why_good: str = '') -> bool:
    for k in ('plain_text', 'output_prompt_plain', 'output_prompt_with_model'):
        s = str(blueprint.get(k, '') or '').strip()
        if _NSFW_MASK_TOKEN in s:
            return True

    check_texts = [
        gen_params.get('positive_prompt', ''),
        gen_params.get('negative_prompt', ''),
        blueprint.get('plain_text', ''),
        blueprint.get('output_prompt_plain', ''),
        blueprint.get('output_prompt_with_model', ''),
        blueprint.get('why_good', ''),
        user_why_good,
        ' '.join(blueprint.get('tags', []) if isinstance(blueprint.get('tags', []), list) else []),
        blueprint.get('lora_dependency_notes', ''),
    ]
    return any(_contains_nsfw_signal(x) for x in check_texts)


def _is_ascii_word(s: str) -> bool:
    return bool(s) and all(('a' <= ch.lower() <= 'z') or ('0' <= ch <= '9') or ch == '_' for ch in s)


def _mask_nsfw_terms(text: str) -> str:
    s = str(text or '').strip()
    if not s:
        return s

    # 先去掉模型可能输出的不规范遮罩，再按统一规则重做
    s = s.replace(_NSFW_MASK_TOKEN, '')

    words = sorted(_NSFW_HINT_WORDS, key=len, reverse=True)
    for w in words:
        if not w:
            continue
        esc = re.escape(w)
        if _is_ascii_word(w):
            # 英文词按词边界匹配，避免误伤普通单词片段
            pattern = re.compile(rf'(?i)(?<!{_NSFW_MASK_TOKEN})\b{esc}\b(?!{_NSFW_MASK_TOKEN})')
        else:
            # 中日韩词语按子串匹配
            pattern = re.compile(rf'(?i)(?<!{_NSFW_MASK_TOKEN}){esc}(?!{_NSFW_MASK_TOKEN})')
        s = pattern.sub(lambda m: f'{_NSFW_MASK_TOKEN}{m.group(0)}{_NSFW_MASK_TOKEN}', s)
    return s


def _apply_nsfw_prompt_mask(blueprint: dict, gen_params: dict, user_why_good: str = '') -> dict:
    if not isinstance(blueprint, dict):
        return blueprint
    if not _needs_nsfw_mask(blueprint, gen_params, user_why_good=user_why_good):
        blueprint['nsfw_masked'] = False
        return blueprint

    masked_any = False
    for k in ('plain_text', 'output_prompt_plain', 'output_prompt_with_model'):
        if k in blueprint:
            old = str(blueprint.get(k, '') or '')
            new = _mask_nsfw_terms(old)
            blueprint[k] = new
            if new != old:
                masked_any = True
    blueprint['nsfw_masked'] = masked_any
    return blueprint


def _build_user_message(gen_params: dict, trigger_words: dict,
                        user_why_good: str) -> str:
    """组装发给 LLM 的用户消息文本。"""
    parts = []

    # 生成参数
    parts.append("## 生成参数")
    parts.append(f"- Checkpoint: {gen_params.get('checkpoint', '未知')}")

    loras = gen_params.get('loras', [])
    if loras:
        lora_strs = [f"{l['name']} ({l.get('weight', 1.0)})" for l in loras]
        parts.append(f"- LoRA: {'; '.join(lora_strs)}")

    parts.append(f"- Sampler: {gen_params.get('sampler', '未知')}")
    parts.append(f"- Steps: {gen_params.get('steps', '?')}, CFG: {gen_params.get('cfg', '?')}")
    parts.append(f"- Size: {gen_params.get('width', '?')}×{gen_params.get('height', '?')}")
    parts.append(f"- Seed: {gen_params.get('seed', '?')}")

    parts.append(f"\n## 原始 Positive Prompt\n{gen_params.get('positive_prompt', '（无）')}")
    parts.append(f"\n## 原始 Negative Prompt\n{gen_params.get('negative_prompt', '（无）')}")

    # 触发词
    parts.append("\n## 已知模型触发词")
    if trigger_words:
        for model_name, words in trigger_words.items():
            parts.append(f"- {os.path.basename(model_name)}: {', '.join(words)}")
    else:
        parts.append("（索引中未找到触发词，请根据经验识别疑似触发词）")

    # 用户主观描述
    parts.append(f"\n## 用户主观感受\n{user_why_good or '（用户未提供主观描述，请仅基于视觉分析）'}")

    return '\n'.join(parts)


def _fallback_blueprint(image_source, gen_params: dict, reason: str = '') -> dict:
    """当 LLM 返回不可解析内容时的兜底结构。"""
    pos = str(gen_params.get('positive_prompt', '') or '')
    neg = str(gen_params.get('negative_prompt', '') or '')
    ckpt = str(gen_params.get('checkpoint', '') or '')

    lora_desc = []
    for l in gen_params.get('loras', []) or []:
        if not isinstance(l, dict):
            continue
        n = str(l.get('name', '') or '').strip()
        if not n:
            continue
        w = l.get('weight', 1.0)
        lora_desc.append(f"{n} ({w})")

    with_model = '\n'.join([
        f"Checkpoint: {ckpt or 'unknown'}",
        f"LoRA: {'; '.join(lora_desc) if lora_desc else 'none'}",
        f"Sampler: {gen_params.get('sampler', 'unknown')} | Steps: {gen_params.get('steps', '?')} | CFG: {gen_params.get('cfg', '?')}",
        f"Size: {gen_params.get('width', '?')}x{gen_params.get('height', '?')} | Seed: {gen_params.get('seed', '?')}",
        f"Positive prompt: {pos}",
        f"Negative prompt: {neg}",
    ])

    return {
        'work_title': '分析降级结果',
        'plain_text': pos,
        'why_good': 'LLM 未返回可解析 JSON，已返回基于参数的降级结果。',
        'output_prompt_plain': pos,
        'output_prompt_with_model': with_model,
        'lora_inferred_additional_prompts': [],
        'lora_dependency_notes': f'fallback: {reason}'.strip(),
        'base_combo': os.path.basename(ckpt) if ckpt else 'unknown',
        'image_type': 'unknown',
        'tags': [],
        'vibe': '',
        'image_source': image_source if isinstance(image_source, str) else '',
        'base_model': ckpt,
        'lora_list': lora_desc,
    }


# ============================================================
# 第 5 步：主分析函数
# ============================================================

def analyze(image_source, user_why_good: str = '',
            gen_params_override: dict = None) -> dict:
    """
    美学分析主入口。

    Args:
        image_source: 图片来源 — URL / 文件路径 / bytes
        user_why_good: 用户的主观感受描述
        gen_params_override: 可选，预解析的生成参数（跳过 PNG metadata 读取）

    Returns:
        完整的 aesthetic_blueprint dict
    """
    # 1. 提取生成参数
    if gen_params_override:
        gen_params = gen_params_override
    else:
        metadata = read_image_metadata(image_source)
        prompt_data = metadata.get('prompt', {})
        gen_params = parse_gen_params(prompt_data) if prompt_data else {}

    # 2. 收集触发词
    trigger_words = collect_trigger_words(gen_params) if gen_params else {}

    # 3. 组装 LLM 消息
    user_text = _build_user_message(gen_params, trigger_words, user_why_good)

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]

    # 4. 调用 LLM（带图片 + 强制 JSON）
    print(f"[Aesthetic] 调用 {_AESTHETIC_MODEL} 分析中...")
    raw_reply = chat_with_image_json(
        messages=messages,
        image=image_source,
        user_text=user_text,
        model=_AESTHETIC_MODEL,
        max_tokens=1500,
        temperature=0.3,
    )

    # 5. 解析 JSON
    blueprint = None
    parse_reason = ''
    try:
        if isinstance(raw_reply, dict):
            blueprint = raw_reply
        elif isinstance(raw_reply, str):
            txt = raw_reply.strip()
            if txt:
                blueprint = json.loads(txt)
        else:
            parse_reason = f'unsupported reply type: {type(raw_reply)}'
    except Exception as e:
        parse_reason = str(e)

    if blueprint is None and isinstance(raw_reply, str):
        # 尝试提取 JSON 块
        try:
            import re
            m = re.search(r'\{[\s\S]*\}', raw_reply)
            if m:
                blueprint = json.loads(m.group())
        except Exception as e:
            parse_reason = parse_reason or str(e)

    if blueprint is None:
        preview = (raw_reply[:200] if isinstance(raw_reply, str) else str(raw_reply)[:200])
        parse_reason = parse_reason or f'empty/non-json reply: {preview}'
        blueprint = _fallback_blueprint(image_source, gen_params, reason=parse_reason)

    # 6. 补全非 LLM 负责的字段
    blueprint['image_source'] = image_source if isinstance(image_source, str) else ''
    blueprint['base_model'] = gen_params.get('checkpoint', '')

    lora_entries = []
    for l in gen_params.get('loras', []):
        name = l.get('name', '')
        weight = l.get('weight', 1.0)
        if weight != 1.0:
            lora_entries.append(f"{name} ({weight})")
        else:
            lora_entries.append(name)
    blueprint['lora_list'] = lora_entries

    # 7. 规范化 LoRA 反推字段，避免上游返回结构不稳定
    inferred = blueprint.get('lora_inferred_additional_prompts', [])
    if not isinstance(inferred, list):
        inferred = []
    inferred = [str(x).strip() for x in inferred if str(x).strip()]
    blueprint['lora_inferred_additional_prompts'] = inferred[:12]
    blueprint['lora_dependency_notes'] = str(blueprint.get('lora_dependency_notes', '') or '').strip()

    # 8. NSFW 提示词遮罩（服务端兜底，避免模型漏加前缀）
    blueprint = _apply_nsfw_prompt_mask(blueprint, gen_params, user_why_good=user_why_good)

    print(f"[Aesthetic] 分析完成: {blueprint.get('work_title', '?')}")
    return blueprint


# ============================================================
# 第 6 步：保存蓝图
# ============================================================

def save_blueprint(blueprint: dict) -> str:
    """
    保存蓝图（本地 + Azure 双写）。
    返回本地路径。
    """
    existing = load_blueprints()
    # 同一作品重复分析时，用最新结果覆盖旧的
    img_src = blueprint.get('image_source', '')
    if img_src:
        existing = [b for b in existing if b.get('image_source') != img_src]
    existing.append(blueprint)
    text = json.dumps(existing, ensure_ascii=False, indent=2)

    # 1. 始终写本地
    os.makedirs(os.path.dirname(_LOCAL_PATH), exist_ok=True)
    with open(_LOCAL_PATH, 'w', encoding='utf-8') as f:
        f.write(text)

    # 2. 有 Azure 时同步
    if _azure_available():
        try:
            blob = _get_blob()
            blob.put_json(_BLOB_SUBFOLDER, _BLOB_FILENAME, existing, indent=2)
        except Exception as e:
            print(f"[Aesthetic] Azure 写入失败（本地已保存）: {e}")

    print(f"[Aesthetic] 蓝图已保存，当前共 {len(existing)} 条")
    return _LOCAL_PATH


def load_blueprints() -> list:
    """加载所有蓝图（本地优先，Azure 回退迁移）。"""
    # 1. 读本地
    if os.path.exists(_LOCAL_PATH):
        try:
            with open(_LOCAL_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass

    # 2. 本地为空，尝试从 Azure 迁移
    if _azure_available():
        try:
            blob = _get_blob()
            data = blob.get_json(_BLOB_SUBFOLDER, _BLOB_FILENAME)
            if isinstance(data, list) and data:
                os.makedirs(os.path.dirname(_LOCAL_PATH), exist_ok=True)
                with open(_LOCAL_PATH, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"[Aesthetic] 从 Azure 迁移 {len(data)} 条蓝图到本地")
                return data
        except Exception as e:
            print(f"[Aesthetic] Azure 读取失败: {e}")

    return []
