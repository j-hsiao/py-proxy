import io

from jhsiao.ipc import sockets
from jhsiao.tests import simple

from jhsiao.proxy.proxy import Proxy
from jhsiao.proxy import http

def test_proxy():
    p = Proxy()
    p.start()
    try:
        sock = sockets.connect(('localhost', p.addr[1]))
        sock.settimeout(1)
        f = sockets.Sockfile(sock, 'rwb')
        w = io.BufferedWriter(f)
        r = io.BufferedReader(f)
        try:
            w.write(
                b'GET http://localhost:8000 HTTP/1.1\r\n'
                b'Content-Length: 0\r\n\r\n')
            w.flush()
            responseline = r.readline()
            headers = http.Headers(r)
            print(responseline)
            for k, v in headers.items():
                print(k, ': ', repr(v))
            responselength = headers.get('content-length')
            if responselength is not None:
                data = r.read(int(responselength))
                print(data.decode('utf-8'))
                f.shutdown(f.SHUT_WR)
            else:
                f.shutdown(f.SHUT_WR)
                print(r.read().decode('utf-8'))
            r.read()
        finally:
            r.detach()
            try:
                w.detach()
            except Exception:
                pass
            f.close()
    finally:
        p.stop()

def test_connect():
    p = Proxy()
    p.start()
    l = sockets.bind(('localhost', 0))
    l.listen(1)
    l.settimeout(1)
    port = l.getsockname()[1]
    sock = sockets.connect(('localhost', p.addr[1]))
    sock.settimeout(1)
    f = sockets.Sockfile(sock, 'rwb')
    try:
        r = io.BufferedReader(f)
        w = io.BufferedWriter(f)
        w.write(
            'CONNECT localhost:{} HTTP/1.1\r\n\r\n'.format(port).encode('utf-8'))
        w.flush()
        print('response line', r.readline())
        for k, v in http.Headers(r).items():
            print(': '.join(k, v))
        s, a  = l.accept()
        sf = sockets.Sockfile(s, 'rwb')
        sr = io.BufferedReader(sf)
        sw = io.BufferedWriter(sf)
        try:
            w.write(b'hello world!')
            w.flush()
            assert sr.read(12) == b'hello world!'
            sw.write(b'goodbye world')
            sw.flush()
            assert r.read(len(b'goodbye world')) == b'goodbye world'
        finally:
            sw.detach()
            sr.detach()
            r.detach()
            w.detach()
            s.close()
            f.close()
    finally:
        l.close()
        p.stop()

if __name__ == '__main__':
    simple(globals())
