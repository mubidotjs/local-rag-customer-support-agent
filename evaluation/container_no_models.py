"""Pytest-only known-model socket guard; never installed in runtime images."""
import json
from pathlib import Path
import socket

attempts=[]
original_connect=socket.socket.connect
original_connect_ex=socket.socket.connect_ex

def guarded(original):
    def call(sock,address):
        tcp=isinstance(address,tuple) and len(address)>=2 and address[1]==11434
        uds=isinstance(address,(str,bytes)) and str(address).rstrip("'").endswith('ollama.sock')
        if tcp or uds:
            attempts.append('model_tcp' if tcp else 'model_uds')
            raise AssertionError('No actual Ollama sockets authorized in offline CI')
        return original(sock,address)
    return call

def pytest_sessionstart(session):
    attempts.clear()
    socket.socket.connect=guarded(original_connect)
    socket.socket.connect_ex=guarded(original_connect_ex)

def pytest_sessionfinish(session,exitstatus):
    socket.socket.connect=original_connect
    socket.socket.connect_ex=original_connect_ex
    target=Path('.local/container-network-proof.json'); target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps({'kind':'Known model socket guard; Python test process only',
        'blocked_tcp_port':11434,'blocked_unix_socket_name':'ollama.sock','attempted_model_connections':len(attempts),
        'violations':attempts,'native_server_fixture_mode':'disabled','containers_executed':0},indent=2)+'\n',encoding='utf-8')
    if attempts: session.exitstatus=1
