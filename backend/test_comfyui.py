import sys
sys.path.insert(0, '.')
from server import wait_for_comfyui, get_comfyui_checkpoints

print('Testing ComfyUI connection...')
ready, data = wait_for_comfyui(timeout=5)
print('Ready:', ready)
if ready:
    print('Queue:', data)
    print('\nTesting get_comfyui_checkpoints...')
    checkpoints = get_comfyui_checkpoints()
    print('Checkpoints count:', len(checkpoints))
    for ckpt in checkpoints[:3]:
        print('  -', ckpt[:50])
else:
    print('ComfyUI not ready yet')
