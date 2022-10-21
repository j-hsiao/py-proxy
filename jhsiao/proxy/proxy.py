from collections import deque
import datetime
import io
import struct
import sys
import threading
import threading
import time
import traceback

import requests

from jhsiao.ipc import sockets, polling, pollable

from .http import Startline, Headers, HTTPError
from .multiforward import MultiForwarder

class Proxy(object):
    bstruct = struct.Struct('4B')
    ustruct = struct.Struct('>L')
    def __init__(
        self, ip='0.0.0.0', port=3128,
        allowed=[('10.36.0.0', 16), ('192.168.0.0', 16)],
        maxsize=None):
        """Initialize.

        ip, port: bind address
        allowed: if given, a sequence of allowed ips (ip, mask)
        """
        self.addr = (ip, port)
        self.allowed = [
            (self.ip2bytes(net), 32-mask) for net, mask in allowed]
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.end = pollable.PollableEvent(self.lock)
        self.q = deque
        self.forwarder = MultiForwarder()

        self.t = threading.Thread(target=self.loop)
        self.t.start()

    def _hasitems(self):
        """Cond waiting function."""
        return bool(self.q) or self.end.on

    def handler(self):
        """Handle requests from queue.  May have multiple."""
        nonempty = self._hasitems
        cond = self.cond
        q = self.q
        end = self.end
        while 1:
            with cond:
                if q or cond.wait_for(nonempty):
                    if end.on:
                        return
                    req = q.popleft()

    def loop(self):
        running = self.running
        listener = sockets.bind(self.addr)
        listener.listen(5)
        poller = polling.Poller()
        poller.register(listener, 'r')
        while running.is_set():
            r, w, x = poller.poll(1)
            if r:
                s, addr = listener.accept()
                if not self.matchip(addr[0], self.allowed):
                    print(datetime.datetime.now(), ': blocked ip', addr[0])
                    s.close()
                    continue
                f = sockets.Sockfile(s, 'rwb')
                try:
                    line = Startline(f)
                    if line.method == 'CONNECT':
                        host, port = line.resource.rsplit(':', 1)
                        remote = sockets.connect((host, int(port)))
                        self.forwarder.add(f, sockets.Sockfile(remote, 'rwb'))
                        print(datetime.datetime.now(), ': CONNECT', addr)
                    else:
                        print(datetime.datetime.now(), ':', line.method, addr)
                        f.write(b'HTTP/1.1 501 Not Implemented\r\n\r\n')
                        try:
                            f.close()
                        except Exception:
                            pass
                except HTTPError as e:
                    f.write('HTTP/1.1 {} {}\r\n'.format(e.code, e.message).encode('utf-8'))
                    f.close()
                except Exception:
                    traceback.print_exc()
                    try:
                        f.close()
                    except Exception:
                        pass

    @classmethod
    def matchip(cls, ip, targets):
        checkui = cls.ustruct.unpack(
            cls.bstruct.pack(*map(int, ip.split('.'))))[0]
        for uip, shift in targets:
            if (uip>>shift) == (checkui>>shift):
                return True
        return False

    @classmethod
    def ip2bytes(cls, ip):
        return cls.ustruct.unpack(
            cls.bstruct.pack(*map(int, ip.split('.'))))[0]

    def close(self):
        self.end.set()
        with self.cond:
            self.cond.notify_all()
        if self.t is not None:
            self.t.join()
        self.forwarder.close()
        for handler in self.handlers:
            handler.join()

    def __del__(self):
        self.close()
