import sys
import io
import socket
from jhsiao.ipc import sockets
from jhsiao.proxy.multiforward import MultiForwarder
import threading

if sys.version_info.major > 2:
    inp = input
else:
    inp = raw_input

def test_multiforward():
    forwarder = MultiForwarder()

    l = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    l.bind(('localhost', 0))
    l.listen(5)
    addr, port = l.getsockname()
    c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c3 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    c1.connect(('localhost', port))
    s1, _ = l.accept()
    c2.connect(('localhost', port))
    s2, _ = l.accept()
    c3.connect(('localhost', port))
    s3, _ = l.accept()
    l.close()

    c1.settimeout(1)
    s1.settimeout(1)
    c2.settimeout(1)
    s2.settimeout(1)
    c3.settimeout(1)
    s3.settimeout(10)

    forwarder.add(
        sockets.Sockfile(s1, 'rb'),
        sockets.Sockfile(c2, 'wb'), False)
    forwarder.add(
        sockets.Sockfile(s2, 'rb'),
        sockets.Sockfile(c3, 'wb'), False)

    def _reader(f):
        block = memoryview(bytearray(io.DEFAULT_BUFFER_SIZE))
        amt = f.readinto(block)
        while amt != 0:
            if amt is None:
                print('timed out')
            else:
                print(block[:amt].tobytes())
            amt = f.readinto(block)
        print('stream end')

    t = threading.Thread(target=_reader, args=(sockets.Sockfile(s3, 'rb'),))
    t.start()

    val = inp('>>>')
    with sockets.Sockfile(c1, 'wb') as f:
        while val != 'q':
            f.write((val+'\n').encode('utf-8'))
            f.flush()
            val = inp('>>>')
    t.join()
    forwarder.close()

if __name__ == '__main__':
    from jhsiao.tests import simple
    simple(globals())
