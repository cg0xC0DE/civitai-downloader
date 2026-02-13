#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试工作流参数注入：验证 basic 和 upscale 两个工作流
使用相同的注入逻辑都能正确设置参数。
"""
import json
import os
import sys
import copy

sys.path.insert(0, os.path.dirname(__file__))

WF_DIR = os.path.join(os.path.dirname(__file__), 'workflows')
BASIC_FILE = 'xl.text2img.basic.json'
UPSCALE_FILE = 'xl.text2img.triple.upscale.json'

# 模拟参数
TEST_PARAMS = {
    'checkpoint': 'testModel_v1.safetensors',
    'positive_prompt': 'TEST_POSITIVE_PROMPT',
    'negative_prompt': 'TEST_NEGATIVE_PROMPT',
    'steps': 30,
    'cfg': 5.5,
    'sampler': 'dpmpp_2m',
    'scheduler': 'karras',
    'seed': 42,
    'width': 1024,
    'height': 1536,
}


def load_workflow(filename):
    path = os.path.join(WF_DIR, filename)
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def find_nodes_by_type(workflow, class_type):
    results = []
    for nid, node in workflow.items():
        if isinstance(node, dict) and node.get('class_type') == class_type:
            results.append((nid, node))
    results.sort(key=lambda x: int(x[0]) if x[0].isdigit() else float('inf'))
    return results


def convert_ui_to_api_simple(raw):
    """简化版 UI→API 转换（不依赖 ComfyUI object_info）"""
    nodes = raw.get('nodes', [])
    links_arr = raw.get('links', [])
    link_map = {}
    for link in links_arr:
        link_map[link[0]] = (link[1], link[2])

    # KSampler widget order: seed, control, steps, cfg, sampler_name, scheduler, denoise
    KSAMPLER_WIDGETS = ['seed', '_control', 'steps', 'cfg', 'sampler_name', 'scheduler', 'denoise']
    CLIP_TEXT_WIDGETS = ['text']
    CHECKPOINT_WIDGETS = ['ckpt_name']
    EMPTY_LATENT_WIDGETS = ['width', 'height', 'batch_size']
    CLIP_LAST_LAYER_WIDGETS = ['stop_at_clip_layer']
    LORA_STACK_WIDGETS = []  # complex, skip for this test
    LATENT_UPSCALE_WIDGETS = ['upscale_method', 'scale_by']
    VAE_DECODE_WIDGETS = []

    WIDGET_MAP = {
        'KSampler': KSAMPLER_WIDGETS,
        'CLIPTextEncode': CLIP_TEXT_WIDGETS,
        'CheckpointLoaderSimple': CHECKPOINT_WIDGETS,
        'EmptyLatentImage': EMPTY_LATENT_WIDGETS,
        'CLIPSetLastLayer': CLIP_LAST_LAYER_WIDGETS,
        'LatentUpscaleBy': LATENT_UPSCALE_WIDGETS,
        'VAEDecode': VAE_DECODE_WIDGETS,
    }

    api = {}
    for node in nodes:
        nid = str(node['id'])
        class_type = node['type']
        inputs = {}

        for inp in node.get('inputs', []):
            link_id = inp.get('link')
            if link_id is not None and link_id in link_map:
                from_node, from_slot = link_map[link_id]
                inputs[inp['name']] = [str(from_node), from_slot]

        wv = node.get('widgets_values')
        widget_names = WIDGET_MAP.get(class_type)
        if wv and isinstance(wv, list) and widget_names:
            wv_idx = 0
            for wname in widget_names:
                if wv_idx >= len(wv):
                    break
                if wname.startswith('_'):
                    wv_idx += 1
                    continue
                if wname not in inputs:
                    inputs[wname] = wv[wv_idx]
                wv_idx += 1

        api[nid] = {'class_type': class_type, 'inputs': inputs}

    return api


def inject_params(workflow, params):
    """模拟 server.py 的参数注入逻辑"""
    # Checkpoint
    ckpt_nodes = find_nodes_by_type(workflow, 'CheckpointLoaderSimple')
    if ckpt_nodes:
        ckpt_nodes[0][1]['inputs']['ckpt_name'] = params['checkpoint']

    # KSampler (primary = first found)
    sampler_nodes = find_nodes_by_type(workflow, 'KSampler')
    positive_nid = negative_nid = None
    if sampler_nodes:
        s_inputs = sampler_nodes[0][1]['inputs']
        s_inputs['steps'] = params['steps']
        s_inputs['cfg'] = params['cfg']
        s_inputs['sampler_name'] = params['sampler']
        s_inputs['scheduler'] = params['scheduler']
        s_inputs['seed'] = params['seed']
        pos_ref = s_inputs.get('positive')
        neg_ref = s_inputs.get('negative')
        if isinstance(pos_ref, list):
            positive_nid = str(pos_ref[0])
        if isinstance(neg_ref, list):
            negative_nid = str(neg_ref[0])

    if positive_nid and positive_nid in workflow:
        workflow[positive_nid]['inputs']['text'] = params['positive_prompt']
    if negative_nid and negative_nid in workflow:
        workflow[negative_nid]['inputs']['text'] = params['negative_prompt']

    # EmptyLatentImage
    size_nodes = find_nodes_by_type(workflow, 'EmptyLatentImage')
    if size_nodes:
        size_nodes[0][1]['inputs']['width'] = params['width']
        size_nodes[0][1]['inputs']['height'] = params['height']

    return sampler_nodes, positive_nid, negative_nid


def test_workflow(filename, label):
    print(f"\n{'='*60}")
    print(f"TEST: {label} ({filename})")
    print(f"{'='*60}")
    errors = []

    raw = load_workflow(filename)
    api = convert_ui_to_api_simple(raw)

    # 1. 确认 KSampler 节点顺序
    ks_nodes = find_nodes_by_type(api, 'KSampler')
    ks_ids = [nid for nid, _ in ks_nodes]
    print(f"  KSampler nodes (order): {ks_ids}")
    
    if ks_ids[0] != '1':
        errors.append(f"FAIL: Primary KSampler should be node 1, got {ks_ids[0]}")
    else:
        print(f"  ✅ Primary KSampler is node 1")

    # Check denoise of primary KSampler
    primary_denoise = ks_nodes[0][1]['inputs'].get('denoise')
    print(f"  Primary KSampler denoise: {primary_denoise}")
    if primary_denoise != 1:
        errors.append(f"FAIL: Primary KSampler denoise should be 1, got {primary_denoise}")
    else:
        print(f"  ✅ Primary KSampler denoise=1 (full generation)")

    # 2. 注入参数
    wf = copy.deepcopy(api)
    sampler_nodes, pos_nid, neg_nid = inject_params(wf, TEST_PARAMS)

    # 3. 验证注入结果
    primary = wf['1']['inputs']
    checks = [
        ('steps', TEST_PARAMS['steps']),
        ('cfg', TEST_PARAMS['cfg']),
        ('sampler_name', TEST_PARAMS['sampler']),
        ('scheduler', TEST_PARAMS['scheduler']),
        ('seed', TEST_PARAMS['seed']),
    ]
    for key, expected in checks:
        actual = primary.get(key)
        if actual != expected:
            errors.append(f"FAIL: KSampler[1].{key} = {actual!r}, expected {expected!r}")
        else:
            print(f"  ✅ KSampler[1].{key} = {actual}")

    # Checkpoint
    ckpt = wf['4']['inputs'].get('ckpt_name')
    if ckpt != TEST_PARAMS['checkpoint']:
        errors.append(f"FAIL: Checkpoint = {ckpt!r}, expected {TEST_PARAMS['checkpoint']!r}")
    else:
        print(f"  ✅ Checkpoint = {ckpt}")

    # Prompt
    if pos_nid and pos_nid in wf:
        pos_text = wf[pos_nid]['inputs'].get('text')
        if pos_text != TEST_PARAMS['positive_prompt']:
            errors.append(f"FAIL: Positive prompt not set correctly")
        else:
            print(f"  ✅ Positive prompt set (node {pos_nid})")
    else:
        errors.append(f"FAIL: Positive CLIPTextEncode not found")

    if neg_nid and neg_nid in wf:
        neg_text = wf[neg_nid]['inputs'].get('text')
        if neg_text != TEST_PARAMS['negative_prompt']:
            errors.append(f"FAIL: Negative prompt not set correctly")
        else:
            print(f"  ✅ Negative prompt set (node {neg_nid})")
    else:
        errors.append(f"FAIL: Negative CLIPTextEncode not found")

    # Size
    size = wf['5']['inputs']
    if size.get('width') != TEST_PARAMS['width'] or size.get('height') != TEST_PARAMS['height']:
        errors.append(f"FAIL: Size = {size.get('width')}x{size.get('height')}")
    else:
        print(f"  ✅ Size = {size['width']}x{size['height']}")

    # 4. Upscale-specific: verify upscale KSamplers NOT overwritten
    if len(ks_ids) > 1:
        print(f"\n  --- Upscale KSampler passes ---")
        for nid, node in ks_nodes[1:]:
            inp = wf[nid]['inputs']
            denoise = inp.get('denoise')
            # These should NOT have been changed by inject_params
            print(f"  KSampler[{nid}]: denoise={denoise}, steps={inp.get('steps')}, cfg={inp.get('cfg')}")
            if denoise == TEST_PARAMS['seed']:  # sanity: seed should not appear in denoise
                errors.append(f"FAIL: Upscale KSampler[{nid}] was incorrectly overwritten")
            # Verify denoise is preserved (original value, not changed)
            orig = api[nid]['inputs'].get('denoise')
            if denoise == orig:
                print(f"  ✅ KSampler[{nid}] denoise preserved at {denoise}")
            else:
                errors.append(f"FAIL: KSampler[{nid}] denoise changed from {orig} to {denoise}")

    # 5. VAEDecode + SaveImage check
    vae_nodes = find_nodes_by_type(wf, 'VAEDecode')
    save_nodes = find_nodes_by_type(wf, 'SaveImage')
    print(f"\n  VAEDecode nodes: {[n[0] for n in vae_nodes]}")
    print(f"  SaveImage nodes: {[n[0] for n in save_nodes]}")
    if not vae_nodes:
        errors.append("FAIL: No VAEDecode node")
    else:
        print(f"  ✅ VAEDecode found")

    # Summary
    print(f"\n  {'='*40}")
    if errors:
        print(f"  ❌ {len(errors)} FAILURES:")
        for e in errors:
            print(f"    - {e}")
    else:
        print(f"  ✅ ALL TESTS PASSED")
    return len(errors)


def test_denoise_override():
    """Test that upscale_denoise overrides the 3 upscale KSampler passes"""
    print(f"\n{'='*60}")
    print(f"TEST: UPSCALE DENOISE OVERRIDE")
    print(f"{'='*60}")
    errors = []

    raw = load_workflow(UPSCALE_FILE)
    wf = convert_ui_to_api_simple(raw)
    
    # Inject with custom denoise values
    sampler_nodes = find_nodes_by_type(wf, 'KSampler')
    inject_params(wf, TEST_PARAMS)
    
    # Apply denoise override (simulating server logic)
    upscale_denoise = [0.35, 0.40, 0.50]
    if len(sampler_nodes) > 1:
        upscale_passes = sampler_nodes[1:]
        for i, (nid, node) in enumerate(upscale_passes):
            if i < len(upscale_denoise):
                node['inputs']['denoise'] = upscale_denoise[i]

    # Verify
    for i, (nid, node) in enumerate(sampler_nodes[1:]):
        actual = node['inputs'].get('denoise')
        expected = upscale_denoise[i]
        if abs(actual - expected) > 0.001:
            errors.append(f"FAIL: KSampler[{nid}] denoise={actual}, expected {expected}")
        else:
            print(f"  ✅ KSampler[{nid}] denoise={actual} (pass {i+1})")

    # Primary should still be denoise=1
    primary_denoise = sampler_nodes[0][1]['inputs'].get('denoise')
    if primary_denoise != 1:
        errors.append(f"FAIL: Primary KSampler denoise={primary_denoise}, expected 1")
    else:
        print(f"  ✅ Primary KSampler denoise={primary_denoise} (unchanged)")

    print(f"\n  {'='*40}")
    if errors:
        print(f"  ❌ {len(errors)} FAILURES:")
        for e in errors:
            print(f"    - {e}")
    else:
        print(f"  ✅ ALL TESTS PASSED")
    return len(errors)


if __name__ == '__main__':
    total_errors = 0
    total_errors += test_workflow(BASIC_FILE, 'BASIC')
    total_errors += test_workflow(UPSCALE_FILE, 'UPSCALE')
    total_errors += test_denoise_override()

    print(f"\n{'='*60}")
    if total_errors == 0:
        print("🎉 ALL WORKFLOWS PASSED")
    else:
        print(f"💥 {total_errors} TOTAL FAILURES")
    print(f"{'='*60}")
    sys.exit(1 if total_errors else 0)
