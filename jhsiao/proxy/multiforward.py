import io
import threading
import traceback
import time

from jhsiao.ipc import polling

class MultiForwarder(object):
    """Forward data from multiple pairs."""
    def __init__(self, flushdelay=0.01):
        self.flushdelay = flushdelay
        self.pairs = {}
        self.pending = []
        self.cond = threading.Condition()
        self.running = True
        self.t = threading.Thread(target=self.loop)
        self.t.start()

    def add(self, sock1, sock2, duplex=True):
        with self.cond:
            self.pending.append((sock1, sock2))
            if duplex:
                self.pending.append((sock2, sock1))
            self.cond.notify()

    def _has_clients(self):
        return self.pending or not self.running

    @classmethod
    def _forward_chunks(
        cls, ready, buf, pairs, pending, lastwrites, flushtime, poller):
        """Read chunks from ready and write to dsts.

        Return if any were closed.
        """
        ret = False
        for src in ready:
            dst = pairs.get(src, None)
            if dst is None:
                continue
            amt = src.readinto(buf)
            if amt:
                try:
                    dst.write(buf[:amt])
                except Exception:
                    traceback.print_exc()
                    ret = cls._close(src, pairs, pending, lastwrites, True, poller) or ret
                else:
                    lastwrites[src] = flushtime
                    pending.add(src)
            else:
                ret = cls._close(src, pairs, pending, lastwrites, False, poller) or ret
        return ret

    @classmethod
    def _close(cls, src, pairs, pending, lastwrites, closeall, poller):
        """Remove forwarding, checking for cycles.

        closeall: close every related socket.
        """
        dst = pairs.get(src, None)
        if dst is None:
            return False
        while dst is not None:
            del pairs[src]
            pending.discard(src)
            lastwrites.pop(src, None)
            try:
                dst.flush()
            except Exception:
                traceback.print_exc()
                closeall = True
            else:
                try:
                    dst.raw.shutdown(dst.raw.SHUT_WR)
                except Exception:
                    traceback.print_exc()
                    closeall = True
            poller.unregister(src)
            if closeall or not [d for d in pairs.values() if d.raw is src]:
                try:
                    src.close()
                except Exception:
                    traceback.print_exc()
            if dst.raw not in pairs:
                try:
                    dst.close()
                except Exception:
                    traceback.print_exc()
                return True
            elif closeall:
                src = dst.raw
                dst = pairs[src]
            else:
                return True
        return True

    def loop(self):
        pairs = self.pairs
        cond = self.cond
        pending = self.pending
        poller = polling.Poller()
        flushdelay = self.flushdelay
        buf = memoryview(bytearray(io.DEFAULT_BUFFER_SIZE))
        wpending = set()
        lastwrites = {}
        ptime = 1
        try:
            while 1:
                with cond:
                    if not pairs:
                        cond.wait_for(self._has_clients)
                while 1:
                    with cond:
                        if not self.running:
                            return
                        if pending:
                            for s1, s2 in pending:
                                pairs[s1] = io.BufferedWriter(s2)
                                poller.register(s1, 'r')
                            del pending[:]
                    r, w, x = poller.poll(ptime)
                    now = time.time()
                    if r:
                        endtime = now + flushdelay
                        if self._forward_chunks(
                            r, buf, pairs, wpending, lastwrites, endtime, poller) and not pairs:
                            break
                    else:
                        endtime = now+1
                    toflush = wpending.difference(r)
                    check = False
                    for src in toflush:
                        tm = lastwrites.get(src, None)
                        if tm < now:
                            try:
                                pairs[src].flush()
                            except Exception:
                                traceback.print_exc()
                                check = self._close(src, pairs, wpending, lastwrites, True, poller) or check
                            else:
                                wpending.discard(src)
                        elif tm < endtime:
                            endtime = tm
                    if check and not pairs:
                        break
                    ptime = endtime - now
        finally:
            for src in list(pairs):
                self._close(src, pairs, wpending, lastwrites, True, poller)
            poller.close()

    def close(self):
        if self.t is not None:
            with self.cond:
                self.running = False
                self.cond.notify()
            self.t.join()
            self.t = None

    def __del__(self):
        self.close()


if __name__ == '__main__':
    import sys
    import socket
    from jhsiao.ipc import sockets

    if sys.version_info.major > 2:
        inp = input
    else:
        inp = raw_input
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
        print('done')

    t = threading.Thread(target=_reader, args=(sockets.Sockfile(s3, 'rb'),))
    t.start()

    val = inp('>>>')
    with sockets.Sockfile(c1, 'wb') as f:
        while val != 'q':
            f.write((val+'\n').encode('utf-8'))
            f.flush()
            val = inp('>>>')
    t.join()
    inp('return to close')
    forwarder.close()
