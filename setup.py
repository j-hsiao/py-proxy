from setuptools import setup
from jhsiao.namespace import make_ns

make_ns('jhsiao')
setup(
    name='jhsiao-proxy',
    version='0.0.1',
    author='Jason Hsiao',
    author_email='oaishnosaj@gmail.com',
    description='proxy server',
    packages=['jhsiao', 'jhsiao.proxy'],
    install_requires=[
        'jhsiao-utils @ git+https://github.com/j-hsiao/py-utils.git',
        'jhsiao-ipc @ git+https://github.com/j-hsiao/py-ipc.git']
)
