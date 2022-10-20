from jhsiao.proxy import proxy


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument(
        'bindaddr', help='ip:port, either part is optional',
        default='0.0.0.0:3128', nargs='?')
    args = p.parse_args()
    idx = args.bindaddr.find(':')
    if idx < 0:
        if '.' in args.bindaddr:
            addr = (args.bindaddr, 3128)
        else:
            addr = ('0.0.0.0', int(args.bindaddr))
    else:
        addr = (args.bindaddr[:idx], int(args.bindaddr[idx+1:]))

    print(addr)
