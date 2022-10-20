import struct
import threading
from collections import deque
import io
import time

from jhsiao.ipc import sockets, polling







class Proxy(object):
    bstruct = struct.Struct('4B')
    ustruct = struct.Struct('>L')
    def __init__(
        self, ip='0.0.0.0', port=3128,
        allowed=[('10.36.0.0', 16), ('192.168.0.0', 16)],
        disallowed=()):
        """Initialize.

        ip, port: bind address
        allowed: if given, a sequence of allowed ips (ip, mask)
        disallowed: if given, a sequence of disallowed ips.
        """
        self.addr = (ip, port)
        self.allowed = [
            (self.ip2bytes(net), 32-mask) for net, mask in allowed]
        disallowed = [
            (self.ip2bytes(net), 32-mask) for net, mask in disallowed]

        self.running = threading.Event()

    def loop(self):
        running = self.running
        listener = sockets.bind(self.addr)
        listener.listen(5)
        poller = polling.Poller()
        poller.register(listener, 'r')
        while running.is_set():
            r, w, x = poller.poll(1)
            if r:
                s, a = listener.accept()
                # TODO
                # handle the connection...








    @classmethod
    def ip2bytes(cls, ip):
        return cls.ustruct.unpack(
            cls.bstruct.pack(*map(int, ip.split('.'))))[0]
