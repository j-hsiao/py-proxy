from __future__ import print_function
__all__ = ['Proxy']
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

def name(f):
    name = f.name
    if isinstance(f.name, tuple):
        return '{}:{}'.format(*name)
    else:
        return name

class StopServing(Exception): pass

class Server(object):
    bstruct = struct.Struct('4B')
    ustruct = struct.Struct('>L')
    def __init__(self, proxy):
        self.socket = sockets.bind(proxy.addr)
        print('bound to', proxy.addr)
        self.socket.listen(5)
        self.fileno = self.socket.fileno
        self.timeout = proxy.timeout

        self.allowed = [
            (self.ip2bytes(net), 32-mask) for net, mask in proxy.allowed]
        self.blocked = [
            (self.ip2bytes(net), 32-mask) for net, mask in proxy.blocked]

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

    def __call__(self, proxy):
        c, addr = self.socket.accept()
        c.settimeout(self.timeout)
        client = Client(c)
        if (self.matchip(addr[0], self.blocked)
                or not self.matchip(addr[0], self.allowed)):
            proxy.log('blocked ip', addr[0])
            self._trysend(client, b'HTTP/1.1 403 Forbidden\r\n\r\n')
            return
        with proxy.cond:
            proxy.poller.register(client, proxy.poller.OFLAGS|proxy.poller.RFLAGS)

    @staticmethod
    def _trysend(client, data):
        try:
            client.w.write(data)
        except Exception:
            traceback.print_exc()
        try:
            client.w.flush()
        except Exception:
            traceback.print_exc()
        try:
            client.close()
        except Exception:
            traceback.print_exc()


    def close(self, proxy):
        self.socket.close()


class Event(pollable.Pollable):
    def __call__(self, proxy):
        poller = proxy.poller
        with proxy.lock:
            self.clear()
            if not proxy.running:
                raise StopServing()
            if proxy.done:
                dones = list(proxy.done)
                del proxy.done[:]
        if dones:
            reflags = poller.RFLAGS|poller.OFLAGS
            for client, code in dones:
                if code == proxy.REARM:
                    try:
                        poller.modify(client, reflags)
                    except Exception:
                        traceback.print_exc()
                        code = proxy.CLOSE
                else:
                    try:
                        poller.unregister(client)
                    except Exception:
                        traceback.print_exc()
                    if code == code == proxy.FORWARD:
                        client.detach()
                    elif code == proxy.CLOSE:
                        client.close()

class Client(object):
    def __init__(self, sock):
        self.socket = sock
        self.f = sockets.Sockfile(sock, 'rwb')
        self.r = io.BufferedReader(self.f)
        self.w = io.BufferedWriter(self.f)
        self.fileno = sock.fileno

    def __call__(self, proxy):
        with proxy.cond:
            try:
                if len(proxy.q) >= proxy.maxsize:
                    self.w.write(
                        b'HTTP/1.1 503 Service Unavailable\r\n'
                        b'User-Agent: PyTestApp\r\n'
                        b'\r\n'
                    )
            except Exception:
                try:
                    proxy.poller.unregister(self)
                except Exception:
                    traceback.print_exc()
                try:
                    self.close()
                except Exception:
                    self.w.close()

            proxy.q.append(self)
            proxy.cond.notify()

    def detach(self):
        if self.f is not None:
            self.r.detach()
            try:
                self.w.detach()
            except Exception:
                traceback.print_exc()
            ret = self.f
            self.f = None
            return ret

    def close(self):
        if self.f is not None:
            self.detach().close()

class Proxy(object):
    CLOSE = 0
    REARM = 1
    FORWARD = 2
    def __init__(
        self, ip='0.0.0.0', port=3128,
        allowed=[('127.0.0.1', 32), ('10.36.0.0', 16), ('192.168.0.0', 16)],
        blocked=(), maxsize=None, numthreads=1, timeout=60):
        """Initialize.

        ip, port: bind address
        allowed: if given, a sequence of allowed ips (ip, mask)
        """
        self.maxsize = float('inf') if maxsize is None else maxsize
        self.addr = (ip, port)
        self.allowed = allowed
        self.blocked = blocked
        self.running = False
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.numthreads = numthreads
        self.timeout = timeout
        self.q = deque()
        self.done = []
        self.t = None

    def log(self, *args, **kwargs):
        now = datetime.datetime.now()
        now = now.strftime(
            '%Y-%m-%d %H:%M:%S.{:02d}: '.format(now.microsecond//10000))
        if args:
            args = list(args)
            args[0] = now+args[0]
        else:
            args = (now,)
        with self.lock:
            print(*args, **kwargs)

    def _hasitems(self):
        """Cond waiting function."""
        return bool(self.q) or not self.running

    def do_CONNECT(self, client, startline, headers):
        host, port = startline.resource.rsplit(':', 1)
        try:
            remote = sockets.Sockfile(sockets.connect((host, int(port))), 'rwb')
        except Exception:
            self.log('Failed to connect to {}:{}'.format(host, port))
            msg = traceback.format_exc().encode('utf-8')
            client.w.write((
                'HTTP/1.1 404 Not Found\r\n'
                'Content-Type: text\r\n'
                'Content-length: {}\r\n\r\n').format(len(msg)).encode('utf-8'))
            client.w.write(msg)
            client.w.flush()
            return self.CLOSE
        else:
            actual = client.f.rtell()
            supposed = client.r.tell()
            if supposed != actual:
                b = io.BufferedWriter(remote)
                b.write(client.r.read(actual-supposed))
                b.flush()
                b.detach()
            client.w.write(b'HTTP/1.1 200 OK\r\n\r\n')
            client.w.flush()
            self.forwarder.add(client.f, remote, duplex=True)
            return self.FORWARD

    def _basic(self, func, withdata, client, startline, headers):
        dlen = headers.get('content-length')
        code = self.REARM
        kwargs = dict(headers=headers)
        if dlen is None:
            if withdata:
                kwargs['data'] = client.r
                code = self.CLOSE
        else:
            kwargs['data'] = client.r.read(int(dlen))
        w = client.w
        try:
            response = func(startline.resource, timeout=self.timeout, stream=True, **kwargs)
        except Exception:
            traceback.print_exc()
            data = traceback.format_exc().encode('utf-8')
            w.write(
                b'HTTP/1.1 500 Server Error\r\n'
                b'Content-Type: text\r\n')
            w.write('Content-Length: {}\r\n\r\n'.format(len(data)).encode('utf-8'))
            w.write(data)
        else:
            self.log(name(client.f), startline.resource, response.status_code, response.reason)
            w.write(
                'HTTP/1.1 {} {}\r\n'.format(
                    response.status_code, response.reason).encode('utf-8'))
            for header in response.headers.items():
                w.write(': '.join(header).encode('utf-8'))
                w.write(b'\r\n')
            w.write(b'\r\n')
            with response as resp:
                for chunk in resp.iter_content(io.DEFAULT_BUFFER_SIZE):
                    w.write(chunk)
            w.flush()
            #client.w.write(response.content)
        return code

    def do_GET(self, *args):
        return self._basic(requests.get, False, *args)
    def do_POST(self, *args):
        return self._basic(requests.post, True, *args)
    def do_PUT(self, *args):
        return self._basic(requests.put, True, *args)

    def default(self, client, startline, headers):
        self.log(name(client.f), startline.method, 'unsupported')
        client.f.write(b'HTTP/1.1 501 Not Implemented\r\n\r\n')
        return self.REARM

    def handleloop(self):
        """Handle requests from queue.

        Clients with pending data are placed onto the queue.
        The client is removed from the queue and handled.
        Finally, it is placed into another queue for rearming.
        """
        nonempty = self._hasitems
        cond = self.cond
        q = self.q
        while 1:
            with cond:
                if q or cond.wait_for(nonempty):
                    if not self.running:
                        return
                    client = q.popleft()
            try:
                startline = Startline(client.f)
                headers = Headers(client.f)
                method = startline.method.upper()
                self.log(name(client.f), method, startline.resource)
                code = getattr(self, 'do_'+method, self.default)(client, startline, headers)
            except HTTPError as e:
                self.log(name(client.f), e.code, ':', e.args[0])
                if e.code>0:
                    f.write('HTTP/1.1 {} {}\r\n\r\n'.format(e.code, e.args[0]).encode('utf-8'))
                code = self.CLOSE if e.code < 0 else self.REARM
            except Exception:
                code = self.CLOSE
                traceback.print_exc()
            try:
                client.w.flush()
            except Exception:
                traceback.print_exc()
                code = self.CLOSE
            with self.lock:
                self.done.append((client, code))
                self.ev.set()

    def run(self):
        """Accept new connections.

        Run directly or ues start() to start in separate thread.
        """
        with self.lock:
            if self.running:
                raise RuntimeError("already running")
            self.running = True
        self.cond = threading.Condition(self.lock)
        self.ev = Event()
        server = Server(self)
        poller = self.poller = polling.Poller()
        poller.register(self.ev, poller.RFLAGS)
        poller.register(server, poller.RFLAGS)
        handlers = [
            threading.Thread(target=self.handleloop)
            for i in range(self.numthreads)]
        self.forwarder = MultiForwarder()
        for h in handlers:
            h.start()
        try:
            while 1:
                r, w, x = poller.poll()
                for thing in r:
                    thing(self)
        except (StopServing, KeyboardInterrupt):
            pass
        except Exception:
            traceback.print_exc()
        finally:
            #TODO shutdown everything
            with self.cond:
                self.running = False
                self.cond.notify_all()
            for t in handlers:
                t.join()
            self.forwarder.close()
            self.poller.unregister(self.ev)
            self.poller.unregister(server)
            self.poller.close()
            self.ev.close()

    def start(self):
        """Start server in separate thread."""
        with self.lock:
            if self.t is None:
                self.t = threading.Thread(target=self.run)
                self.t.start()

    def stop(self):
        """Stop server thread, can be restarted."""
        with self.cond:
            if self.t is None:
                return
            self.running = False
            self.ev.set()
        self.t.join()
        with self.lock:
            self.t = None
