from __future__ import print_function
__all__ = ['MultiForwarder']
import io
import threading
import traceback
import time

from jhsiao.ipc import polling, pollable

class Forwarder(object):
    """Class for 1-direction forwarding."""
    def __init__(self, src, dst):
        self.src = src
        self.fileno = src.fileno
        self.readinto = src.readinto
        self.dst = dst
        self.o = io.BufferedWriter(dst)
        self.flushtime = None
        self.flush = self.o.flush
        self.write = self.o.write

    def __repr__(self):
        return '{}->{}'.format(self.src.name, self.dst.name)

    def __call__(self, multi, flushtime):
        """Forward a chunk.

        buf: buffer to use for forwarding.
        flushtime: timeout for flushing.
        Return True if failure (src closed or failed to write to dst).
        """
        buf = multi._buf
        try:
            amt = self.readinto(buf)
        except Exception:
            traceback.print_exc()
            return True
        if amt:
            try:
                self.write(buf[:amt])
            except Exception:
                traceback.print_exc()
                return True
            multi._wpending.add(self)
            self.flushtime = flushtime
            return False
        else:
            return True

    def close(self, multi):
        """Close a forwarder.

        Remove from corresponding sets and close
        if no longer in use, else shutdown.
        """
        src = self.src
        dst = self.dst
        try:
            multi._poller.unregister(self)
        except Exception:
            traceback.print_exc()
        try:
            self.flush()
        except Exception:
            traceback.print_exc()
        with multi.lock:
            srcs = multi.srcs
            dsts = multi.dsts
            sfd = src.fileno()
            dfd = dst.fileno()
            multi._wpending.discard(self)
            srcs.pop(sfd, None)
            dsts.discard(dfd)
            dst_is_src = dfd in srcs
            src_is_dst = sfd in dsts
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

class StopForwarding(Exception):
    pass
class Event(pollable.Pollable):
    def __call__(self, multi, flushtime):
        with multi.lock:
            self.clear()
            if not multi.running:
                raise StopForwarding()
            poller = multi._poller
            for f in multi.pending:
                print('adding', f)
                poller.register(f, 'r')
            del multi.pending[:]
        return False

    def close(self, multi=None):
        if multi is not None:
            try:
                multi._poller.unregister(self)
            except Exception:
                traceback.print_exc()
        super(Event, self).close()
class MultiForwarder(object):
    """Forward data from multiple pairs."""
    def __init__(self, flushdelay=0.01):
        self.lock = threading.Lock()
        self.pending = []
        self.ev = Event()
        self.running = True
        self.srcs = {}
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
        pairs = [(f1, f2), (f2, f1)] if duplex else [(f1,f2)]
        with self.lock:
            if not self.running:
                raise RuntimeError('Cannot add if loop has stopped.')
            for src, dst in pairs:
                if dst.fileno() in self.dsts:
                    raise ValueError('Already forwarding into {}'.format(dst.name))
                elif src.fileno() in self.srcs:
                    raise ValueError('Already forwarding from {}'.format(src.name))
            for src, dst in pairs:
                f = Forwarder(src, dst)
                self.pending.append(f)
                self.srcs[src.fileno()] = f
                self.dsts.add(dst.fileno())
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
                for f in wpending.difference(r):
                    tm = f.flushtime
                    if tm <= now:
                        try:
                            f.flush()
                        except Exception:
                            f.close(self)
                        else:
                            wpending.discard(f)
                    elif endtime is None or tm < endtime:
                        endtime = tm
                ptime = None if endtime is None else endtime - now
        except StopForwarding:
            print('loop exit')
        except Exception:
            print('loop error')
            traceback.print_exc()
        finally:
            with self.lock:
                self.running = False
                toclose = list(self.pending)
                del self.pending[:]
                toclose.extend(self.srcs.values())
                self.srcs.clear()
            for f in toclose:
                f.close(self)
            poller.unregister(self.ev)
            self.ev.close()
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
