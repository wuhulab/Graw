# -*- coding: utf-8 -*-
"""_sync.py - 同步 docker_api.py / main.py 到子节点并重启 Agent（临时脚本）"""
import time
import paramiko

HOST, PORT, USER, PWD = "154.12.30.50", 22, "root", "ouquOPHH4131"
PAIRS = [
    (r"s:\Graw\backend\app\routers\docker_api.py", "/opt/graw-agent/app/routers/docker_api.py"),
    (r"s:\Graw\backend\app\main.py", "/opt/graw-agent/app/main.py"),
]

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, PORT, USER, PWD, timeout=20)
sftp = client.open_sftp()
for local, remote in PAIRS:
    before = 0
    try:
        before = sftp.stat(remote).st_size
    except Exception:
        before = -1
    sftp.put(local, remote)
    after = sftp.stat(remote).st_size
    print(f"上传 {remote.split('/')[-1]}: {before}B -> {after}B")
sftp.close()

stdin, stdout, stderr = client.exec_command("bash /opt/graw-agent/_restart.sh", timeout=60)
stdout.channel.recv_exit_status()
time.sleep(6)
up = False
for _ in range(6):
    stdin, stdout, stderr = client.exec_command("ss -ltn | grep -q ':8000 ' && echo UP || echo DOWN", timeout=15)
    if stdout.read().decode().strip() == "UP":
        up = True
        break
    time.sleep(2)
print("Agent 8000 状态:", "UP" if up else "DOWN")
stdin, stdout, stderr = client.exec_command("tail -6 /opt/graw-agent/agent.log", timeout=15)
print(stdout.read().decode("utf-8", "replace"))
client.close()
print("完成。")