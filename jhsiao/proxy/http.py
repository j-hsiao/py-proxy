"""Parsing http1.

Parsing uses binary file
because using TextIOWrapper may read more than needed so cannot then
switch to using binary because some data was already grabbed by the
text wrapper.
"""
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
        if not line:
            raise HTTPError(-1, 'Connection Closed')
        elif not line.endswith(b'\n'):
            raise HTTPError(414, 'Request-URI Too Long')
        match = self.pattern.match(line.decode('utf-8'))
        if match is None:
            raise HTTPError(-2, 'Not Http')
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
            if line == b'\r\n' or line == b'\n':
                break
            stripped = line.strip().decode('utf-8')
            try:
                header, value = stripped.split(':', 1)
            except ValueError:
                raise HTTPError(-3, 'Bad header: {!r}'.format(line))
            self.info[header.strip().lower()].append(value.strip())

    def __str__(self):
        lines = [': '.join((k, ','.join(v))) for k, v in self.info.items()]
        return '\r\n'.join(lines)

    def __getitem__(self, header):
        """Get original separated values as a list."""
        return self.info[header]

    def items(self):
        for k, v in self.info.items():
            yield (k, ','.join(v))

    def get(self, key, default=None):
        """Get comma joined values."""
        val = self.info.get(key)
        if val is None:
            return default
        else:
            return ','.join(val)

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
