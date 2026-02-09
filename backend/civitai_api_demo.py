#This is an example that uses the websockets api and the SaveImageWebsocket node to get images directly without
#them being saved to disk

import websocket #NOTE: # pip install websocket-client (https://github.com/websocket-client/websocket-client)
import uuid
import json
import urllib.request
import urllib.parse
import random
import time
import winsound

def get_workflow_api(workflow_name):
    with open('workflow/'+workflow_name+'.json', 'r', encoding='utf-8') as file:
        return file.read()    
    
def get_config(config_name):
    with open('workflow/'+config_name+'.config', 'r', encoding='utf-8') as file:
        return file.read()  
    
def wait_for_comfyui_ready(server_address, timeout=300, interval=5):
    """等待 ComfyUI 服务就绪（避免模型加载期间 503）"""
    url = "http://{}/system_stats".format(server_address)
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = urllib.request.urlopen(url, timeout=10)
            if resp.getcode() == 200:
                print("[ComfyUI] 服务就绪")
                return True
        except urllib.error.HTTPError as e:
            print("[ComfyUI] 等待就绪... HTTP {}".format(e.code))
        except Exception as e:
            print("[ComfyUI] 等待就绪... {}".format(e))
        time.sleep(interval)
    print("[ComfyUI] 等待超时（{}秒）".format(timeout))
    return False

def queue_prompt(prompt,client_id,server_address):
    p = {"prompt": prompt, "client_id": client_id}
    data = json.dumps(p).encode('utf-8')
    req =  urllib.request.Request("http://{}/prompt".format(server_address), data=data)
    return json.loads(urllib.request.urlopen(req).read())

def get_image(filename, subfolder, folder_type,server_address):
    data = {"filename": filename, "subfolder": subfolder, "type": folder_type}
    url_values = urllib.parse.urlencode(data)
    with urllib.request.urlopen("http://{}/view?{}".format(server_address, url_values)) as response:
        return response.read()

def get_history(prompt_id,server_address):
    with urllib.request.urlopen("http://{}/history/{}".format(server_address, prompt_id)) as response:
        return json.loads(response.read())

def get_images(ws,prompt,client_id,server_address):
    prompt_id = queue_prompt(prompt,client_id,server_address)['prompt_id']
    output_images = {}
    current_node = ""
    while True:
        out = ws.recv()
        if isinstance(out, str):
            print(out)
            message = json.loads(out)
            if message['type'] == 'executing':
                data = message['data']
                if data['prompt_id'] == prompt_id:
                    if data['node'] is None:
                        break #Execution is done
                    else:
                        current_node = data['node']
        else:
            if current_node == '12':
                images_output = output_images.get(current_node, [])
                images_output.append(out[8:])
                output_images[current_node] = images_output

    return output_images

def run_comfyui_local(workflow_name,workflow_params_name,server_address = "127.0.0.1:8188"):
    workflow = json.loads(get_workflow_api(workflow_name))
    workflow_params = json.loads(get_config(workflow_params_name))

    # set checkpoint
    for ckpt in workflow_params['checkpoint']:
        if ckpt['load']:
            workflow['1']['inputs']['ckpt_name'] = ckpt['name']

    # set lora loader 1
    lora_loader_count = 1
    for lora in workflow_params['loras_i']:
        if lora['load']:
            workflow['2']['inputs']['lora_0' + str(lora_loader_count)] = lora['name']
            workflow['2']['inputs']['strength_0' + str(lora_loader_count)] = lora['strength']
            lora_loader_count+=1

    # set lora loader 2
    lora_loader_count = 1
    for lora in workflow_params['loras_ii']:
        if lora['load']:    
            workflow['2']['inputs']['lora_0' + str(lora_loader_count)] = lora['name']
            workflow['2']['inputs']['strength_0' + str(lora_loader_count)] = lora['strength']
            lora_loader_count+=1
        
    # set lora loader 3
    lora_loader_count = 1
    for lora in workflow_params['loras_iii']:
        if lora['load']:    
            workflow['2']['inputs']['lora_0' + str(lora_loader_count)] = lora['name']
            workflow['2']['inputs']['strength_0' + str(lora_loader_count)] = lora['strength']
            lora_loader_count+=1

    # set prompts
    workflow['6']['inputs']['text'] = workflow_params['prompts']['positive']
    workflow['7']['inputs']['text'] = workflow_params['prompts']['negative']
    workflow['7']['inputs']['batch_size'] = 4

    # set latent
    workflow['8']['inputs']['width'] = workflow_params['latent']['width']
    workflow['8']['inputs']['height'] = workflow_params['latent']['height']
    workflow['8']['inputs']['batch_size'] = workflow_params['latent']['batch']

    # set ksampler
    if workflow_params['ksampler']['random_seed']:
        workflow['9']['inputs']['noise_seed'] = random.randint(10**6, 10**18)
    else:
        workflow['9']['inputs']['noise_seed'] = random.choice(workflow_params['ksampler']['noise_seed'])
    workflow['9']['inputs']['steps'] = workflow_params['ksampler']['steps']
    workflow['9']['inputs']['cfg'] = workflow_params['ksampler']['cfg']
    workflow['9']['inputs']['sampler_name'] = workflow_params['ksampler']['sampler_name']

    # set savePath
    user_id = "dva419"
    workflow['13']['inputs']['string'] = user_id + '-' + str(uuid.uuid4())

    # 等待 ComfyUI 就绪
    if not wait_for_comfyui_ready(server_address):
        print("[ComfyUI] 服务未就绪，放弃执行")
        return

    client_id = str(uuid.uuid4())
    ws = websocket.WebSocket()
    ws.connect("ws://{}/ws?clientId={}".format(server_address, client_id))
    images = get_images(ws, workflow,client_id,server_address)

    # 保存在本地
    # for node_id in images:
    #     for image_data in images[node_id]:
    #         from PIL import Image
    #         import io
    #         image = Image.open(io.BytesIO(image_data))
    #         image.show()
    print(uuid.uuid4())
    winsound.Beep(500, 200) 

def run_comfyui(workflow_name,workflow_config_name,positive_prompt,creator,server_address = "127.0.0.1:8188"):
    workflow = json.loads(get_workflow_api(workflow_name))
    workflow_params = json.loads(get_config(workflow_config_name))

    # set checkpoint
    for ckpt in workflow_params['checkpoint']:
        if ckpt['load']:
            workflow['1']['inputs']['ckpt_name'] = ckpt['name']

    # set lora loader 1
    lora_loader_count = 1
    for lora in workflow_params['loras_i']:
        if lora['load']:
            workflow['2']['inputs']['lora_0' + str(lora_loader_count)] = lora['name']
            workflow['2']['inputs']['strength_0' + str(lora_loader_count)] = lora['strength']
            lora_loader_count+=1

    # set lora loader 2
    lora_loader_count = 1
    for lora in workflow_params['loras_ii']:
        if lora['load']:    
            workflow['2']['inputs']['lora_0' + str(lora_loader_count)] = lora['name']
            workflow['2']['inputs']['strength_0' + str(lora_loader_count)] = lora['strength']
            lora_loader_count+=1
        
    # set lora loader 3
    lora_loader_count = 1
    for lora in workflow_params['loras_iii']:
        if lora['load']:    
            workflow['2']['inputs']['lora_0' + str(lora_loader_count)] = lora['name']
            workflow['2']['inputs']['strength_0' + str(lora_loader_count)] = lora['strength']
            lora_loader_count+=1

    # set prompts
    if workflow_config_name == 'default':
        positive_prompts = workflow_params['prompts']['positive'] + positive_prompt
    else:
        positive_prompts = workflow_params['prompts']['positive'].replace('######',positive_prompt)
    workflow['6']['inputs']['text'] = positive_prompts
    workflow['7']['inputs']['text'] = workflow_params['prompts']['negative']
    workflow['7']['inputs']['batch_size'] = 1

    # set latent
    workflow['8']['inputs']['width'] = workflow_params['latent']['width']
    workflow['8']['inputs']['height'] = workflow_params['latent']['height']
    workflow['8']['inputs']['batch_size'] = workflow_params['latent']['batch']

    # set ksampler
    if workflow_params['ksampler']['random_seed']:
        workflow['9']['inputs']['noise_seed'] = random.randint(10**6, 10**18)
    else:
        workflow['9']['inputs']['noise_seed'] = random.choice(workflow_params['ksampler']['noise_seed'])
    workflow['9']['inputs']['steps'] = workflow_params['ksampler']['steps']
    workflow['9']['inputs']['cfg'] = workflow_params['ksampler']['cfg']
    workflow['9']['inputs']['sampler_name'] = workflow_params['ksampler']['sampler_name']

    # 等待 ComfyUI 就绪
    if not wait_for_comfyui_ready(server_address):
        print("[ComfyUI] 服务未就绪，放弃执行")
        return None

    client_id=str(uuid.uuid4())
    ws = websocket.WebSocket()
    ws.connect("ws://{}/ws?clientId={}".format(server_address, client_id))
    images = get_images(ws, workflow,client_id,server_address)

    # 保存到指定位置
    try:
        for node_id in images:
            for idx, image_data in enumerate(images[node_id]):
                from PIL import Image
                import io
                image = Image.open(io.BytesIO(image_data))
                # 保存图片，可以将文件命名为 nodeID 的一个组成部分
                save_path = f'C:\\ComfyUI_windows_portable\\ComfyUI\\output\\'
                save_url = save_path + '{creator}_{image_id}.png'.format(creator=creator, image_id=uuid.uuid4())
                image.save(save_url)  # 以 PNG 格式保存，也可以根据需要修改文件格式
    except:
        print("image saved!")
            
    print(uuid.uuid4())
    return save_url

run_comfyui_local("default","default")