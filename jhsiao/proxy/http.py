"""Parsing http1."""
import re
from collections import defaultdict
import sys
if sys.version_info.major > 2:
    from urllib.parse import unquote
else:
    from urllib import unquote
import io

class HTTPError(Exception):
    def __init__(self, code, msg):
        super(HTTPError, self).__init__(msg)
        self.code = code

class Startline(object):
    """Parse http startline."""
    pattern = re.compile(
        r'(?P<method>\w+)\s+'
        r'(?P<resource>.*)\s+'
        r'[hH][tT][tT][pP]/(?P<high>[\d]+)\.(?P<low>[\d]+)')

    def __init__(self, f):
        """Initialize.

        f: a binary file
        """
        line = f.readline(io.DEFAULT_BUFFER_SIZE)
        if not line.endswith(b'\n'):
            raise HTTPError(414, 'Request-URI Too Long')
        match = self.pattern.match(line.decode('utf-8'))
        self.method = match.group('method')
        self.version = tuple(
            map(int, (match.group('high'), match.group('low'))))
        self.resource = unquote(match.group('resource'))

class Headers(object):
    """Basic headers parsing.

    key: [values...]
    Values are just the string values of the headers, they are lists to
    handle repeated headers.
    """
    def __init__(self, f):
        """Initialize.

        f: a binary file
        """
        self.info = defaultdict(list)
        for line in f:
            if line == '\r\n' or line == '\n':
                break
            stripped = line.strip()
            header, value = stripped.split(':', 1)
            self.info[header.strip().lower()].append(value.strip())

    def __str__(self):
        lines = [': '.join((k, ','.join(v))) for k, v in self.info.items()]
        return '\r\n'.join(lines)

    def __getitem__(self, header):
        return self.info[header.lower()]

    def get(self, key, default=None):
        return self.info.get(key, default)

    def __iter__(self):
        return iter(self.info)


'HTTP/1.1 200 OK'
if __name__ == '__main__':
    sample = b'''POST /contact_form.php HTTP/1.1
Host: developer.mozilla.org
Content-Length: 64
Content-Type: application/x-www-form-urlencoded

name=Joe%20User&request=Send%20me%20one%20of%20your%20catalogue'''
    import io
    with io.BytesIO(sample) as f:
        wrapped = io.TextIOWrapper(io.BufferedReader(f))
        line = Startline(wrapped)
        print(line.method)
        print(line.version)
        print(line.resource)
        h = Headers(wrapped)
        print(h)
        print('body')
        print(wrapped.read())
        wrapped.detach().detach()
