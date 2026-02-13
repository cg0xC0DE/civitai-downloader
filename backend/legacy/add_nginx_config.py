import re

# 读取 nginx.conf
with open('C:/nginx-1.28.1/conf/nginx.conf', 'r', encoding='utf-8') as f:
    content = f.read()

# 检查是否已存在 civitaidl 配置
if 'civitaidl' in content:
    print('配置已存在')
else:
    # 添加 Civitai Downloader 配置
    new_config = '''
        # Civitai Downloader
        location /civitaidl/ {
            root   C:/workplace/civitai-downloader;
            index  index.html;
        }

        location /civitaidl-service/ {
            rewrite ^/civitaidl-service/(.*) /$1 break;
            proxy_pass http://127.0.0.1:53133/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }
'''
    
    # 在 #access_log 后面添加
    content = content.replace(
        '#access_log  logs/host.access.log  main;',
        '#access_log  logs/host.access.log  main;' + new_config
    )
    
    with open('C:/nginx-1.28.1/conf/nginx.conf', 'w', encoding='utf-8') as f:
        f.write(content)
    
    print('配置已添加')
