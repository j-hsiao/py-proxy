from jhsiao.proxy.proxy import Proxy
import argparse
import sys
import time
if sys.version_info.major > 2:
    inp = input
else:
    inp = raw_input

def mask2pair(item):
    parts = item.rsplit('/', 1)
    if len(parts) == 2:
        return parts[0], int(parts[1])
    else:
        return parts[0], 32

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument(
        'bindaddr', help='ip:port, either part is optional',
        default='0.0.0.0:3128', nargs='?')
    p.add_argument(
        '-b', '--block', help='sequence of ip/subnetmask to block, eg. 1.2.3.4/24',
        nargs='*', action='append')
    p.add_argument(
        '-a', '--allow', help='sequence of ip/subnetmask to allow, eg. 1.2.3.4/24',
        nargs='*', action='append')
    p.add_argument(
        '-m', '--max',
        help=(
            'maximum outstanding requests.  Once reached, start denying requests'
            ' with 503 Service Unavailable'), type=int, default=None)
    p.add_argument(
        '-t', '--threads', help='number of handler threads', type=int, default=1)
    p.add_argument(
        '--timeout', help='timeout in seconds', type=float, default=60)
    args = p.parse_args()
    idx = args.bindaddr.find(':')
    if idx < 0:
        if '.' in args.bindaddr:
            ip, port = (args.bindaddr, 3128)
        else:
            ip, port = ('0.0.0.0', int(args.bindaddr))
    else:
        ip, port = (args.bindaddr[:idx], int(args.bindaddr[idx+1:]))
    kwargs = dict(ip=ip, port=port)
    if args.allow:
        kwargs['allowed'] = list(map(mask2pair, args.allow))
    if args.block:
        kwargs['blocked'] = list(map(mask2pair, args.block))
    kwargs['maxsize'] = args.max
    kwargs['numthreads'] = args.threads
    kwargs['timeout'] = args.timeout
    p = Proxy(**kwargs)
    p.start()
    # run() does not respond to keyboard interrupt
    # neither does thread.join()
    try:
        while p.t.is_alive():
            time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        p.stop()
