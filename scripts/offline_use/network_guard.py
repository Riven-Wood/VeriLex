"""Opt-in pytest guard: allow loopback IPC, reject all external sockets/DNS.

Run via PYTHONPATH=scripts/offline_use:runtime python -m pytest -p network_guard.
No production network setting is changed. HTTP mocks remain deterministic.
"""
import ipaddress
import socket
import pytest


def local(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def external_network_disabled(monkeypatch):
    connect, connect_ex, getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    def check(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not local(address[0]):
            raise OSError('offline-use QA: external network disabled')
    def guarded_connect(sock, address):
        check(sock, address)
        return connect(sock, address)
    def guarded_connect_ex(sock, address):
        check(sock, address)
        return connect_ex(sock, address)
    def guarded_dns(host, *args, **kwargs):
        if host is not None and not local(host):
            raise OSError('offline-use QA: external DNS disabled')
        return getaddrinfo(host, *args, **kwargs)
    monkeypatch.setattr(socket.socket, 'connect', guarded_connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', guarded_connect_ex)
    monkeypatch.setattr(socket, 'getaddrinfo', guarded_dns)
