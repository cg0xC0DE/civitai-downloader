import sys
sys.path.insert(0, '.')

from server import wait_for_comfyui

print('Testing wait_for_comfyui (10s timeout)...')
ready, data = wait_for_comfyui(timeout=10)
print('Ready:', ready)
if ready:
    print('Queue:', data)
else:
    print('ComfyUI 未就绪，这是预期的行为')
    print('当 ComfyUI 启动后，API 将正常工作')
