from __future__ import print_function
__all__ = ['MultiForwarder']
import io
import threading
import traceback
import time

from jhsiao.ipc import polling, pollable

class SameSrc(object):
    """Dummy class to hash/compare equal to a same input."""
    def __init__(self, src):
        self.src = src
        self.fileno = self.src.fileno
    def __hash__(self):
        return self.fileno()
    def __eq__(self, o):
        return self.src is o.src

class Forwarder(SameSrc):
    """Class for 1-direction forwarding."""
    def __init__(self, src, dst):
        super(Forwarder, self).__init__(src)
        self.readinto = src.readinto
        self.dst = dst
        self.o = io.BufferedWriter(dst)
        self.flushtime = None
        self.flush = self.o.flush
        self.write = self.o.write

    def __call__(self, state, flushtime):
        """Forward a chunk.

        buf: buffer to use for forwarding.
        flushtime: timeout for flushing.
        Return True if failure (src closed or failed to write to dst).
        """
        buf = state._buf
        try:
            amt = self.readinto(buf)
        except Exception:
            state._wpending.discard(self)
            return True
        if amt:
            try:
                self.write(buf[:amt])
            except Exception:
                state._wpending.discard(self)
                return True
            state._wpending.add(self)
            self.flushtime = flushtime
            return False
        else:
            state._wpending.discard(self)
            return True

    def __repr__(self):
        return '{}->{}'.format(self.src.name, self.dst.name)

    def close(self, state):
        """Close a forwarder.

        because duplex, src could also be a dst
        dst could also be a src.
        If dst is a src, just SHUT_WR
        remove it if applicable on some other iteration
        if src is also a dst, no need to close
        eventually its src will be closed after this call
        and it will be a dst that is not a src and so closed
        at that point.
        """
        src = self.src
        dst = self.dst
        try:
            state._poller.unregister(self)
        except Exception:
            traceback.print_exc()
        try:
            self.flush()
        except Exception:
            traceback.print_exc()
        with state.lock:
            forwards = state.forwards
            dsts = state.dsts
            forwards.discard(self)
            dsts.discard(dst.fileno())
            dst_is_src = SameSrc(dst) in forwards
            src_is_dst = src.fileno() in dsts
        if dst_is_src:
            print('shut down dst', dst.name)
            try:
                self.o.detach()
            except Exception:
                traceback.print_exc()
            try:
                dst.shutdown(dst.SHUT_WR)
            except Exception:
                traceback.print_exc()
        else:
            try:
                print('closing dst', dst.name)
                self.o.close()
            except Exception:
                traceback.print_exc()
        if src_is_dst:
            print('shut down src', src.name)
            try:
                src.shutdown(src.SHUT_RD)
            except Exception:
                traceback.print_exc()
        else:
            try:
                print('closing src', src.name)
                src.close()
            except Exception:
                pass

class StopError(Exception):
    pass
class MultiForwarder(object):
    """Forward data from multiple pairs."""
    class Ev(pollable.Pollable):
        def __call__(self, state, flushtime):
            self.clear()
            with state.lock:
                forwards = state.forwards
                if not state.running:
                    raise StopError()
                poller = state._poller
                for f in state.pending:
                    print('adding', f)
                    forwards.add(f)
                    poller.register(f, 'r')
                del state.pending[:]
            return False
        def close(self, state=None):
            if state is not None:
                try:
                    state._poller.unregister(self)
                except Exception:
                    traceback.print_exc()
            super(MultiForwarder.Ev, self).close()

    def __init__(self, flushdelay=0.01):
        self.lock = threading.Lock()
        self.pending = []
        self.ev = self.Ev()
        self.running = True
        self.forwards = set()
        self.dsts = set()
        self.t = threading.Thread(target=self.loop)
        # loop-only variables
        self._flushdelay = flushdelay
        self._poller = polling.Poller()
        self._poller.register(self.ev, 'r')
        self._wpending = set()
        self._buf = memoryview(bytearray(io.DEFAULT_BUFFER_SIZE))
        self.t.start()

    def add(self, f1, f2, duplex=True):
        """Add Sockfiles."""
        with self.lock:
            if not self.running:
                raise RuntimeError('Cannot add if loop has stopped.')
            if f2.fileno() in self.dsts:
                raise ValueError('Already forwarding into {}'.format(f2.fileno()))
            elif SameSrc(f1) in self.forwards:
                raise ValueError('Already forwarding from {}'.format(f1.fileno()))
            self.pending.append(Forwarder(f1, f2))
            self.dsts.add(f2.fileno())
            if duplex:
                self.pending.append(Forwarder(f2, f1))
                self.dsts.add(f1.fileno())
            self.ev.set()

    def loop(self):
        poller = self._poller
        flushdelay = self._flushdelay
        wpending = self._wpending
        ptime = None
        try:
            while 1:
                r, w, x = poller.poll(ptime)
                now = time.time()
                if r:
                    endtime = now + flushdelay
                    toremove = [item for item in r if item(self, endtime)]
                    for thing in toremove:
                        thing.close(self)
                else:
                    endtime = None
                toflush = wpending.difference(r)
                for f in toflush:
                    tm = f.flushtime
                    if tm <= now:
                        wpending.discard(f)
                        try:
                            f.flush()
                        except Exception:
                            f.close(self)
                    elif endtime is None or tm < endtime:
                        endtime = tm
                ptime = None if endtime is None else endtime - now
        except StopError:
            print('loop exit')
        except Exception:
            print('loop error')
            traceback.print_exc()
        finally:
            with self.lock:
                self.running = False
                toclose = list(self.pending)
                del self.pending[:]
                toclose.extend(self.forwards)
                self.forwards.clear()
            for f in toclose:
                f.close(self)
            poller.close()

    def close(self):
        if self.t is not None:
            with self.lock:
                if self.running:
                    self.running = False
                    self.ev.set()
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
